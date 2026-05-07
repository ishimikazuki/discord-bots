from pathlib import Path
import pytest
from card_summary.gmail_fetcher import authenticate, GmailAuthError


def test_authenticate_raises_when_no_creds(tmp_path):
    fake_creds = tmp_path / "no.json"
    fake_token = tmp_path / "no_token.json"
    with pytest.raises(GmailAuthError):
        authenticate(credentials_path=fake_creds, token_path=fake_token)
