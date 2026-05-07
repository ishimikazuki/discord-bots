import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from discord_test_auth import (
    is_allowed_bot_test_message,
    parse_id_set,
    strip_test_nonce,
)


def test_parse_id_set_accepts_comma_and_space_separated_values():
    assert parse_id_set("123, 456 789, nope") == {123, 456, 789}


def test_allowed_bot_test_message_requires_bot_author_id_and_nonce():
    assert is_allowed_bot_test_message(
        author_is_bot=True,
        author_id=123,
        content="hello NONCE",
        nonce="NONCE",
        allowed_author_ids={123},
    )
    assert not is_allowed_bot_test_message(
        author_is_bot=True,
        author_id=123,
        content="hello",
        nonce="NONCE",
        allowed_author_ids={123},
    )
    assert not is_allowed_bot_test_message(
        author_is_bot=False,
        author_id=123,
        content="hello NONCE",
        nonce="NONCE",
        allowed_author_ids={123},
    )


def test_strip_test_nonce_removes_one_occurrence():
    assert strip_test_nonce("NONCE hello NONCE", "NONCE") == "hello NONCE"
