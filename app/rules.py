"""Бизнес-правила без базы данных и веб-сервера."""

from datetime import datetime, timedelta
from typing import Iterable


def intervals_overlap(start: datetime, end: datetime,
                      other_start: datetime, other_end: datetime) -> bool:
    """Интервалы полуоткрытые: соседние приёмы могут иметь общую границу."""
    return start < other_end and other_start < end


def service_fits_slot(duration_minutes: int, slot_start: datetime,
                      slot_end: datetime) -> bool:
    return duration_minutes > 0 and slot_start + timedelta(minutes=duration_minutes) <= slot_end


def validate_booking(slot_start: datetime, slot_end: datetime,
                     duration_minutes: int, service_id: int,
                     offered_service_ids: Iterable[int],
                     existing_intervals: Iterable[tuple[datetime, datetime]]) -> datetime:
    if service_id not in offered_service_ids:
        raise ValueError("Специалист не оказывает выбранную услугу")
    if not service_fits_slot(duration_minutes, slot_start, slot_end):
        raise ValueError("Продолжительность услуги превышает продолжительность слота")
    end = slot_start + timedelta(minutes=duration_minutes)
    if any(intervals_overlap(slot_start, end, start, finish)
           for start, finish in existing_intervals):
        raise ValueError("Выбранное время пересекается с существующей записью")
    return end


def load_percent(booked_minutes: float, slot_minutes: float) -> float:
    return round(100 * booked_minutes / slot_minutes, 2) if slot_minutes > 0 else 0.0


def cancelled_share(cancelled: int, total: int) -> float:
    """Доля, а не процент; UI умножает её на 100."""
    return cancelled / total if total > 0 else 0.0
