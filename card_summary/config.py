"""Constants for the card_summary package. Edit before deploying."""
from __future__ import annotations
from pathlib import Path

# Discord ---------------------------------------------------------------------
# Kanojo control channel. This may be either a ForumChannel or a TextChannel;
# scheduler.post_to_kanojo_forum handles both.
KANOJO_FORUM_CHANNEL_ID: int = 1497151379393876020

# DB --------------------------------------------------------------------------
DB_PATH = Path(__file__).resolve().parent.parent / "data" / "card.sqlite3"
CONTEXT_DIR = Path(__file__).resolve().parent.parent / "data" / "card_summary" / "contexts"

# Gmail -----------------------------------------------------------------------
GMAIL_QUERY = "from:info@01epos.jp subject:カードご利用のお知らせ"
GMAIL_TOKEN_PATH = Path(__file__).resolve().parent.parent / "data" / "gmail_token.json"
GMAIL_CREDENTIALS_PATH = Path(__file__).resolve().parent.parent / "data" / "gmail_credentials.json"
GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

# Slots -----------------------------------------------------------------------
SLOTS = {
    "morning": 7,    # hour (JST)
    "afternoon": 15,
    "night": 22,
}

# Alerts ----------------------------------------------------------------------
ALERT_PACE_RATIO = 1.3       # projected month total > prev month × this → alert
ALERT_CATEGORY_RATIO = 2.0   # category total > prev-month-same-day category × this → alert
ALERT_SINGLE_TX_RATIO = 5.0  # single tx > 30-day median × this → alert

# Categories ------------------------------------------------------------------
CATEGORIES = ["食費", "交通", "サブスク", "コンビニ", "ネット通販", "衣料", "医療", "その他"]

CATEGORY_SEED: dict[str, str] = {
    # サブスク (クラウド・SaaS)
    "GOOGLE*CLOUD":     "サブスク",
    "GOOGLE":           "サブスク",
    "ANTHROPIC":        "サブスク",
    "OPENAI":           "サブスク",
    "NOTION LABS":      "サブスク",
    "NOTION":           "サブスク",
    "NETFLIX":          "サブスク",
    "SPOTIFY":          "サブスク",
    "ADOBE":            "サブスク",
    "ICLOUD":           "サブスク",
    # ネット通販
    "AMAZON":           "ネット通販",
    "RAKUTEN":          "ネット通販",
    "YAHOO":            "ネット通販",
    "アイハーブ":        "ネット通販",
    "IHERB":            "ネット通販",
    # コンビニ
    "AP/セブン":        "コンビニ",
    "SEVEN-ELEVEN":     "コンビニ",
    "AP/ローソン":      "コンビニ",
    "AP/ロ-ソン":       "コンビニ",
    "LAWSON":           "コンビニ",
    "AP/ファミリ":       "コンビニ",
    "FAMILYMART":       "コンビニ",
    # 食費 (スーパー・外食)
    "AP/サミット":       "食費",
    "AP/サミツト":       "食費",
    "AP/ケイオウストア": "食費",
    "AP/イチカクヤ":     "食費",
    "AP/スキヤ":        "食費",
    "AP/UBER":          "食費",
    "AP/コーヒー":       "食費",
    "AP/コ-ヒ":         "食費",
    "AP/フラワーショップ": "食費",
    "AP/フラワ":        "食費",
    "DEMAE-CAN":        "食費",
    "UBER EATS":        "食費",
    # 交通
    "AP/モバイルパスモ":  "交通",
    "AP/ミツワコウツウ":  "交通",
    "PASMO":            "交通",
    "SUICA":            "交通",
    "JR EAST":          "交通",
    "JR-EAST":          "交通",
    "JREAST":           "交通",
    "チャージスポット":   "交通",
    "チャ-ジスポット":   "交通",
}
