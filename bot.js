if (process.env.ALLOW_LEGACY_BOT_JS !== '1') {
  console.error('bot.js is deprecated and disabled. Use python bot.py <bot_name> via launchd.');
  process.exit(1);
}

const { Client, GatewayIntentBits, Partials, ChannelType } = require('discord.js');
const { execSync, spawn } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');

// ---------------------------------------------------------------------------
// Config
// ---------------------------------------------------------------------------

function getFromKeychain(account) {
  try {
    return execSync(
      `security find-generic-password -a "${account}" -s "discord-bot" -w`,
      { encoding: 'utf-8' },
    ).trim();
  } catch {
    return null;
  }
}

const BOT_TOKEN = getFromKeychain('general-bot-token');
if (!BOT_TOKEN) {
  console.error('Failed to get general-bot-token from keychain');
  process.exit(1);
}

const HOME_DIR = os.homedir();

// Project definitions -- add new projects here
const PROJECTS = {
  kb: {
    name: 'knowledge-hub',
    dir: path.join(HOME_DIR, 'knowledge-hub'),
    emoji: '\u{1F4DA}',
  },
  general: {
    name: 'general',
    dir: HOME_DIR,
    emoji: '\u{1F3E0}',
  },
};
const DEFAULT_PROJECT = 'general';

// Allowed user IDs (Discord snowflakes). Empty = allow all.
const ALLOWED_USERS = [];

// ---------------------------------------------------------------------------
// Session persistence
// ---------------------------------------------------------------------------

const SESSIONS_FILE = path.join(__dirname, 'sessions.json');

function loadSessions() {
  try {
    return JSON.parse(fs.readFileSync(SESSIONS_FILE, 'utf-8'));
  } catch {
    return {};
  }
}

function saveSessions(sessions) {
  fs.writeFileSync(SESSIONS_FILE, JSON.stringify(sessions, null, 2) + '\n');
}

// ---------------------------------------------------------------------------
// Codex runner
// ---------------------------------------------------------------------------

function runCodexCode(projectDir, prompt, sessionId) {
  return new Promise((resolve, reject) => {
    const args = ['exec'];
    if (sessionId) {
      args.push('resume');
    }
    args.push(
      '--json',
      '--dangerously-bypass-approvals-and-sandbox',
      '--skip-git-repo-check',
      '--config',
      'project_doc_fallback_filenames=["CLAUDE.md"]',
      '--config',
      'project_doc_max_bytes=131072',
    );
    if (sessionId) {
      args.push(sessionId, '-');
    } else {
      args.push('-');
    }

    const proc = spawn('codex', args, {
      cwd: projectDir,
      env: {
        ...process.env,
        PATH: [
          `${process.env.HOME}/.npm-global/bin`,
          `${process.env.HOME}/.local/bin`,
          `${process.env.HOME}/.local/node-v22/bin`,
          '/Applications/Codex.app/Contents/Resources',
          process.env.PATH || '',
          '/opt/homebrew/bin',
          '/usr/local/bin',
          '/usr/bin',
          '/bin',
        ].join(':'),
      },
      timeout: 300000, // 5 min
    });

    let stdout = '';
    let stderr = '';
    proc.stdin.write(prompt);
    proc.stdin.end();

    proc.stdout.on('data', (data) => { stdout += data.toString(); });
    proc.stderr.on('data', (data) => { stderr += data.toString(); });

    proc.on('close', (code) => {
      if (code === 0) {
        let threadId = sessionId || null;
        const messages = [];
        for (const line of stdout.split(/\r?\n/)) {
          if (!line.trim()) continue;
          try {
            const event = JSON.parse(line);
            if (event.type === 'thread.started') {
              threadId = event.thread_id || threadId;
            } else if (event.type === 'item.completed') {
              const item = event.item || {};
              if (item.type === 'agent_message' && item.text) {
                messages.push(String(item.text).trim());
              }
            }
          } catch {
            // Ignore non-JSON log lines.
          }
        }
        resolve({
          text: messages.join('\n\n') || '(no response)',
          sessionId: threadId,
          cost: 0,
        });
      } else {
        reject(new Error(`Codex exited ${code}: ${stderr.slice(-500)}`));
      }
    });

    proc.on('error', reject);
  });
}

// ---------------------------------------------------------------------------
// Discord message helpers
// ---------------------------------------------------------------------------

async function sendLongMessage(channel, text) {
  const chunks = [];
  let remaining = text;
  while (remaining.length > 0) {
    if (remaining.length <= 2000) {
      chunks.push(remaining);
      break;
    }
    let splitAt = remaining.lastIndexOf('\n', 2000);
    if (splitAt === -1 || splitAt < 1000) splitAt = 2000;
    chunks.push(remaining.slice(0, splitAt));
    remaining = remaining.slice(splitAt);
  }
  for (const chunk of chunks) {
    await channel.send(chunk);
  }
}

// ---------------------------------------------------------------------------
// Command parsing
// ---------------------------------------------------------------------------

function parseCommand(content) {
  // !sessions -- list active sessions
  if (content.trim() === '!sessions') {
    return { type: 'sessions' };
  }
  // !close -- close current thread session
  if (content.trim() === '!close') {
    return { type: 'close' };
  }
  // !<project> <message> -- new session with project
  const prefixMatch = content.match(/^!(\w+)\s+([\s\S]+)/);
  if (prefixMatch && PROJECTS[prefixMatch[1]]) {
    return {
      type: 'message',
      projectKey: prefixMatch[1],
      text: prefixMatch[2].trim(),
    };
  }
  // Plain message
  return { type: 'message', projectKey: null, text: content.trim() };
}

// ---------------------------------------------------------------------------
// Thread name builder
// ---------------------------------------------------------------------------

function buildThreadName(projectKey, text) {
  const proj = PROJECTS[projectKey] || PROJECTS[DEFAULT_PROJECT];
  const short = text.slice(0, 80).replace(/\n/g, ' ');
  return `${proj.emoji} ${short}`;
}

// ---------------------------------------------------------------------------
// Command handlers
// ---------------------------------------------------------------------------

async function handleSessions(channel) {
  const sessions = loadSessions();
  const entries = Object.entries(sessions);
  if (entries.length === 0) {
    await channel.send('Active sessions: none');
    return;
  }
  const lines = entries.map(([threadId, s]) => {
    const proj = PROJECTS[s.projectKey] || PROJECTS[DEFAULT_PROJECT];
    const age = Math.round((Date.now() - new Date(s.lastUsed).getTime()) / 60000);
    return `${proj.emoji} **${s.threadName}** (${s.messageCount} msgs, ${age}min ago) <#${threadId}>`;
  });
  await sendLongMessage(channel, `**Active Sessions:**\n${lines.join('\n')}`);
}

async function handleClose(thread) {
  const sessions = loadSessions();
  if (sessions[thread.id]) {
    delete sessions[thread.id];
    saveSessions(sessions);
  }
  await thread.send('Session closed.');
  await thread.setArchived(true).catch(() => {});
}

// ---------------------------------------------------------------------------
// Main handler: new session via thread
// ---------------------------------------------------------------------------

async function handleNewSession(message, projectKey, text) {
  const proj = PROJECTS[projectKey];
  const threadName = buildThreadName(projectKey, text);

  // Create thread on the user's message
  const thread = await message.startThread({
    name: threadName,
    autoArchiveDuration: 1440, // 24h
  });

  // Typing indicator
  const typingInterval = setInterval(() => {
    thread.sendTyping().catch(() => {});
  }, 5000);
  await thread.sendTyping();

  try {
    const result = await runCodexCode(proj.dir, text, null);

    clearInterval(typingInterval);

    // Save session
    if (result.sessionId) {
      const sessions = loadSessions();
      sessions[thread.id] = {
        sessionId: result.sessionId,
        projectKey,
        projectDir: proj.dir,
        threadName,
        createdAt: new Date().toISOString(),
        lastUsed: new Date().toISOString(),
        messageCount: 1,
      };
      saveSessions(sessions);
    }

    await sendLongMessage(thread, result.text);
    console.log(`[new] ${threadName} -> ${result.text.length} chars`);
  } catch (error) {
    clearInterval(typingInterval);
    console.error('[new] Error:', error.message);
    await thread.send(`Error: ${error.message.slice(0, 300)}`);
  }
}

// ---------------------------------------------------------------------------
// Main handler: continue session in thread
// ---------------------------------------------------------------------------

async function handleThreadMessage(message) {
  const sessions = loadSessions();
  const session = sessions[message.channel.id];

  if (!session) {
    // Thread exists but no session -- treat as new in default project
    await message.reply('This thread has no active session. Send a new message in the channel to start one.');
    return;
  }

  const typingInterval = setInterval(() => {
    message.channel.sendTyping().catch(() => {});
  }, 5000);
  await message.channel.sendTyping();

  try {
    const result = await runCodexCode(
      session.projectDir,
      message.content.trim(),
      session.sessionId,
    );

    clearInterval(typingInterval);

    // Update session metadata
    if (result.sessionId) {
      session.sessionId = result.sessionId;
    }
    session.lastUsed = new Date().toISOString();
    session.messageCount += 1;
    saveSessions(sessions);

    await sendLongMessage(message.channel, result.text);
    console.log(`[cont] ${session.threadName} -> ${result.text.length} chars (msg #${session.messageCount})`);
  } catch (error) {
    clearInterval(typingInterval);
    console.error('[cont] Error:', error.message);
    await message.channel.send(`Error: ${error.message.slice(0, 300)}`);
  }
}

// ---------------------------------------------------------------------------
// Main handler: DM fallback (one-shot, no session)
// ---------------------------------------------------------------------------

async function handleDM(message) {
  const proj = PROJECTS[DEFAULT_PROJECT];

  const typingInterval = setInterval(() => {
    message.channel.sendTyping().catch(() => {});
  }, 5000);
  await message.channel.sendTyping();

  try {
    const result = await runCodexCode(proj.dir, message.content.trim(), null);
    clearInterval(typingInterval);
    await sendLongMessage(message.channel, result.text);
    console.log(`[dm] ${message.author.tag} -> ${result.text.length} chars`);
  } catch (error) {
    clearInterval(typingInterval);
    console.error('[dm] Error:', error.message);
    await message.reply(`Error: ${error.message.slice(0, 300)}`);
  }
}

// ---------------------------------------------------------------------------
// Bot setup
// ---------------------------------------------------------------------------

const client = new Client({
  intents: [
    GatewayIntentBits.Guilds,
    GatewayIntentBits.GuildMessages,
    GatewayIntentBits.GuildMembers,
    GatewayIntentBits.MessageContent,
    GatewayIntentBits.DirectMessages,
  ],
  partials: [Partials.Channel, Partials.Message, Partials.User, Partials.GuildMember],
});

client.once('ready', async () => {
  console.log(`Logged in as ${client.user.tag}`);
  console.log(`Projects: ${Object.keys(PROJECTS).join(', ')}`);
  console.log(`Default: ${DEFAULT_PROJECT}`);

  // Pre-cache DM channels so we can receive DM messageCreate events
  try {
    for (const guild of client.guilds.cache.values()) {
      const members = await guild.members.fetch();
      for (const member of members.values()) {
        if (!member.user.bot) {
          await member.user.createDM();
        }
      }
    }
    console.log('DM channels initialized');
  } catch (e) {
    console.error('DM init error:', e.message);
  }
});

client.on('messageCreate', async (message) => {
  // Debug: log ALL incoming messages
  console.log(`[debug] msg from=${message.author?.tag} bot=${message.author?.bot} ch_type=${message.channel?.type} partial=${message.partial} content="${(message.content || '').slice(0, 50)}"`);

  // Fetch partial messages
  if (message.partial) {
    try { await message.fetch(); } catch { return; }
  }

  // Ignore bots
  if (message.author.bot) return;

  // Access control
  if (ALLOWED_USERS.length > 0 && !ALLOWED_USERS.includes(message.author.id)) return;

  const isDM = message.channel.type === ChannelType.DM;
  const isThread = [ChannelType.GuildPublicThread, ChannelType.GuildPrivateThread].includes(message.channel.type);
  const isGuildText = message.channel.type === ChannelType.GuildText;

  // Guild text channel -- only respond to mentions
  if (isGuildText && !message.mentions.has(client.user)) {
    // Check for prefix commands without mention
    const cmd = parseCommand(message.content);
    if (cmd.type === 'sessions') {
      await handleSessions(message.channel);
      return;
    }
    // Not mentioned and not a command -- ignore
    if (!message.content.startsWith('!')) return;
  }

  // Strip bot mention from content
  let content = message.content
    .replace(new RegExp(`<@!?${client.user.id}>`, 'g'), '')
    .trim();

  if (!content) content = 'hello';

  // Route by channel type
  if (isDM) {
    await handleDM(message);
    return;
  }

  if (isThread) {
    const cmd = parseCommand(content);
    if (cmd.type === 'close') {
      await handleClose(message.channel);
      return;
    }
    await handleThreadMessage(message);
    return;
  }

  if (isGuildText) {
    const cmd = parseCommand(content);
    if (cmd.type === 'sessions') {
      await handleSessions(message.channel);
      return;
    }
    const projectKey = cmd.projectKey || DEFAULT_PROJECT;
    const text = cmd.text || content;
    await handleNewSession(message, projectKey, text);
    return;
  }
});

// ---------------------------------------------------------------------------
// Start
// ---------------------------------------------------------------------------

console.log('Starting unified Discord bot...');
client.login(BOT_TOKEN).catch((err) => {
  console.error('Login failed:', err.message);
  process.exit(1);
});

// Graceful shutdown
process.on('SIGINT', () => {
  console.log('Shutting down...');
  client.destroy();
  process.exit(0);
});

process.on('SIGTERM', () => {
  console.log('Shutting down...');
  client.destroy();
  process.exit(0);
});
