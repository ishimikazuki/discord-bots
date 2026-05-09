"""Gmail API client. Direct REST, not MCP, since the bot process runs outside the agent."""
from __future__ import annotations
import logging
from pathlib import Path
from typing import Any
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from card_summary.config import GMAIL_CREDENTIALS_PATH, GMAIL_TOKEN_PATH, GMAIL_SCOPES

log = logging.getLogger(__name__)


class GmailAuthError(RuntimeError):
    pass


def authenticate(
    credentials_path: Path = GMAIL_CREDENTIALS_PATH,
    token_path: Path = GMAIL_TOKEN_PATH,
) -> Credentials:
    """Load saved creds or run OAuth flow. Token is refreshed if expired."""
    creds: Credentials | None = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), GMAIL_SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not credentials_path.exists():
                raise GmailAuthError(
                    f"Missing OAuth client credentials at {credentials_path}. "
                    f"Download from Google Cloud Console > OAuth Client > Desktop type."
                )
            flow = InstalledAppFlow.from_client_secrets_file(str(credentials_path), GMAIL_SCOPES)
            creds = flow.run_local_server(port=0)
        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(creds.to_json())
    return creds


def build_service(creds: Credentials) -> Any:
    """Return a googleapiclient gmail.users service."""
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


import base64
from datetime import datetime
from typing import Iterator


def _decode_body(payload: dict) -> str:
    """Recursively walk the payload tree to extract text/plain (or text/html as fallback)."""
    mime = payload.get("mimeType", "")
    if mime.startswith("text/"):
        data = payload.get("body", {}).get("data")
        if data:
            return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
    for part in payload.get("parts", []) or []:
        text = _decode_body(part)
        if text:
            return text
    return ""


def _to_gmail_after(iso_str: str) -> str:
    """Gmail search query takes after:YYYY/MM/DD form."""
    dt = datetime.fromisoformat(iso_str)
    return dt.strftime("%Y/%m/%d")


def fetch_new_since(service, query: str, since: str | None) -> Iterator[tuple[str, str]]:
    """Yield (message_id, body_text) for matching mails newer than `since` (ISO8601).

    `service` is a googleapiclient gmail.users service (or a Mock with the same shape).
    """
    full_query = query
    if since:
        full_query = f"{query} after:{_to_gmail_after(since)}"
    resp = service.users().messages().list(userId="me", q=full_query, maxResults=100).execute()
    msgs = resp.get("messages") or []
    for m in msgs:
        msg_id = m["id"]
        full = service.users().messages().get(userId="me", id=msg_id, format="full").execute()
        body = _decode_body(full.get("payload") or {})
        yield (msg_id, body)
