"""Constants for the card_summary package. Edit before deploying."""
from __future__ import annotations
from pathlib import Path

# Discord ---------------------------------------------------------------------
# Set during deployment (Task 18). Use 0 as placeholder to surface mistakes early.
KANOJO_FORUM_CHANNEL_ID: int = 0  # forum channel under kanojo bot

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
    "AMAZON":           "ネット通販",
    "RAKUTEN":          "ネット通販",
    "YAHOO":            "ネット通販",
    "SEVEN-ELEVEN":     "コンビニ",
    "FAMILYMART":       "コンビニ",
    "LAWSON":           "コンビニ",
    "SUICA":            "交通",
    "JR EAST":          "交通",
    "JR-EAST":          "交通",
    "PASMO":            "交通",
    "NETFLIX":          "サブスク",
    "SPOTIFY":          "サブスク",
    "OPENAI":           "サブスク",
    "ANTHROPIC":        "サブスク",
    "UBER":             "食費",
    "DEMAE-CAN":        "食費",
}
