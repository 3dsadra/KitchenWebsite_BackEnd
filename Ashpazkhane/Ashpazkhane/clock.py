from datetime import date, datetime, timedelta

from django.conf import settings
from django.utils import timezone


def _offset() -> timedelta:
    minutes = getattr(settings, "CLOCK_OFFSET_MINUTES", 0) or 0
    return timedelta(minutes=minutes)


def now() -> datetime:
    """زمان محلی (تهران) با احتساب اصلاح اختلاف ساعت."""
    return timezone.localtime() + _offset()


def today() -> date:
    return now().date()
