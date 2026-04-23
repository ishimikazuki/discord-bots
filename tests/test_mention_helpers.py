"""Tests for mention detection and content stripping helpers."""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from mention_helpers import is_bot_addressed, strip_mentions


# ---------------------------------------------------------------------------
# is_bot_addressed
# ---------------------------------------------------------------------------

def test_user_mention_detected():
    assert is_bot_addressed(
        user_mention_ids={42},
        role_mention_ids=set(),
        bot_user_id=42,
        bot_role_ids={100, 200},
    ) is True


def test_role_mention_detected():
    assert is_bot_addressed(
        user_mention_ids=set(),
        role_mention_ids={100},
        bot_user_id=42,
        bot_role_ids={100, 200},
    ) is True


def test_different_user_mention_ignored():
    assert is_bot_addressed(
        user_mention_ids={999},
        role_mention_ids=set(),
        bot_user_id=42,
        bot_role_ids={100},
    ) is False


def test_unrelated_role_mention_ignored():
    assert is_bot_addressed(
        user_mention_ids=set(),
        role_mention_ids={777},
        bot_user_id=42,
        bot_role_ids={100, 200},
    ) is False


def test_no_mentions_returns_false():
    assert is_bot_addressed(
        user_mention_ids=set(),
        role_mention_ids=set(),
        bot_user_id=42,
        bot_role_ids={100},
    ) is False


def test_bot_with_no_roles_still_detects_user_mention():
    assert is_bot_addressed(
        user_mention_ids={42},
        role_mention_ids=set(),
        bot_user_id=42,
        bot_role_ids=set(),
    ) is True


# ---------------------------------------------------------------------------
# strip_mentions
# ---------------------------------------------------------------------------

def test_strip_role_mention():
    result = strip_mentions(
        "<@&1493204478902534255> テスト",
        user_ids={42},
        role_ids={1493204478902534255},
    )
    assert result == "テスト"


def test_strip_user_mention():
    result = strip_mentions(
        "<@42> hello",
        user_ids={42},
        role_ids=set(),
    )
    assert result == "hello"


def test_strip_nickname_user_mention():
    result = strip_mentions(
        "<@!42> hello",
        user_ids={42},
        role_ids=set(),
    )
    assert result == "hello"


def test_strip_combined_user_and_role():
    result = strip_mentions(
        "<@42> <@&100> do stuff",
        user_ids={42},
        role_ids={100},
    )
    assert result == "do stuff"


def test_strip_leaves_foreign_mentions():
    # Don't strip mentions that aren't ours
    result = strip_mentions(
        "<@&777> hello <@999>",
        user_ids={42},
        role_ids={100},
    )
    assert result == "<@&777> hello <@999>"


def test_strip_handles_no_mentions():
    assert strip_mentions("just text", user_ids={42}, role_ids={100}) == "just text"
