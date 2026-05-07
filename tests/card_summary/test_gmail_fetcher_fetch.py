from unittest.mock import MagicMock
from card_summary.gmail_fetcher import fetch_new_since

def _make_service(messages: list[dict]) -> MagicMock:
    """Build a mock that mimics service.users().messages().list().execute() etc."""
    svc = MagicMock()
    list_mock = svc.users().messages().list
    list_mock.return_value.execute.return_value = {
        "messages": [{"id": m["id"]} for m in messages]
    }
    def get_side_effect(userId: str, id: str, format: str):
        match = next(m for m in messages if m["id"] == id)
        get_call = MagicMock()
        get_call.execute.return_value = match
        return get_call
    svc.users().messages().get.side_effect = get_side_effect
    return svc

def test_fetch_new_since_returns_id_and_body():
    msgs = [
        {
            "id": "msg-1",
            "payload": {
                "mimeType": "text/plain",
                "body": {"data": "44GT44KT44Gr44Gh44Gv"},  # base64url('こんにちは')
            },
        },
    ]
    svc = _make_service(msgs)
    results = list(fetch_new_since(svc, query="from:eposcard@eposcard.co.jp", since="2026-05-01"))
    assert len(results) == 1
    msg_id, body = results[0]
    assert msg_id == "msg-1"
    assert "こんにちは" in body

def test_fetch_new_since_handles_empty():
    svc = MagicMock()
    svc.users().messages().list.return_value.execute.return_value = {}
    assert list(fetch_new_since(svc, query="x", since="2026-05-01")) == []
