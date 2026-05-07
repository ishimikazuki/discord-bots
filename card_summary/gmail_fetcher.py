"""Gmail API client. Direct REST, not MCP, since MCP is Claude-Code-only."""
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
