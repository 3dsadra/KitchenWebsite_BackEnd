"""منبع زمان سایت.

همهٔ بخش‌ها «امروز» را از اینجا می‌گیرند تا در صورت جلو/عقب بودن ساعت
سیستم، با متغیر ``CLOCK_OFFSET_MINUTES`` در فایل ``.env`` قابل اصلاح باشد.
مقدار پیش‌فرض صفر است و روی سرور واقعی باید صفر بماند.
"""

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
