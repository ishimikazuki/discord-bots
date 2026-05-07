import asyncio
from unittest.mock import AsyncMock
import pytest
from freezegun import freeze_time
from card_summary.scheduler import _next_slot_to_run, _seconds_until

def test_next_slot_morning_after_midnight():
    with freeze_time("2026-05-07 03:00:00"):
        slot, dt = _next_slot_to_run()
    assert slot == "morning"
    assert dt.hour == 7

def test_next_slot_afternoon_after_morning():
    with freeze_time("2026-05-07 08:00:00"):
        slot, dt = _next_slot_to_run()
    assert slot == "afternoon"
    assert dt.hour == 15

def test_next_slot_night_after_afternoon():
    with freeze_time("2026-05-07 16:00:00"):
        slot, dt = _next_slot_to_run()
    assert slot == "night"
    assert dt.hour == 22

def test_next_slot_wraps_to_next_day_morning():
    with freeze_time("2026-05-07 23:00:00"):
        slot, dt = _next_slot_to_run()
    assert slot == "morning"
    assert dt.day == 8

def test_seconds_until_positive():
    with freeze_time("2026-05-07 03:00:00"):
        slot, dt = _next_slot_to_run()
        sec = _seconds_until(dt)
    assert sec == 4 * 3600
