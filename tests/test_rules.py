"""Шесть проверок бизнес-правил без PostgreSQL и работающего HTTP-сервиса."""

from datetime import datetime, timedelta

import pytest

from app.rules import cancelled_share, intervals_overlap, load_percent, service_fits_slot, validate_booking

START = datetime(2026, 10, 5, 9)
END = START + timedelta(hours=1)


def test_service_duration_must_fit_available_slot():
    assert service_fits_slot(60, START, END)
    assert not service_fits_slot(61, START, END)
    assert not service_fits_slot(0, START, END)
    with pytest.raises(ValueError, match="Продолжительность"):
        validate_booking(START, END, 90, 1, [1], [])


def test_specialist_must_offer_requested_service():
    with pytest.raises(ValueError, match="не оказывает"):
        validate_booking(START, END, 30, 2, [1], [])
    assert validate_booking(START, END, 30, 1, [1], []) == START + timedelta(minutes=30)


def test_overlapping_appointments_are_rejected():
    existing = [(START + timedelta(minutes=15), END)]
    assert intervals_overlap(START, END, *existing[0])
    with pytest.raises(ValueError, match="пересекается"):
        validate_booking(START, END, 30, 1, [1], existing)


def test_adjacent_appointments_do_not_overlap():
    previous = (START - timedelta(minutes=30), START)
    following = (START + timedelta(minutes=30), END)
    assert not intervals_overlap(START, following[0], *previous)
    assert not intervals_overlap(START, following[0], *following)
    assert validate_booking(START, END, 30, 1, [1], [previous, following]) == following[0]


def test_specialist_load_uses_minutes_and_handles_empty_period():
    assert load_percent(90, 240) == 37.5
    assert load_percent(30, 135) == 22.22
    assert load_percent(0, 0) == 0


def test_cancelled_share_is_fraction_of_all_appointments():
    assert cancelled_share(2, 5) == pytest.approx(0.4)
    assert cancelled_share(0, 5) == 0
    assert cancelled_share(0, 0) == 0
