"""قواعد مشترک فروش: مهلت سفارش، ظرفیت روزانه و ثبت تلاش پرداخت."""

from datetime import datetime

from django.conf import settings
from django.db.models import Count
from django.utils import timezone

from Ashpazkhane import clock
from Ashpazkhane.jalali import fa_digits, jalali_weekday

from .models import Order, OrderItem, PaymentAttempt


def now_local() -> datetime:
    return clock.now()


def order_deadline(jy: int, jm: int, jd: int) -> datetime:
    """آخرین لحظهٔ مجاز خرید برای یک روز: ساعت ۹ صبح همان روز."""
    day = jalali_weekday(jy, jm, jd)
    naive = datetime(day.year, day.month, day.day, settings.ORDER_CUTOFF_HOUR, 0)
    return timezone.make_aware(naive, timezone.get_current_timezone())


def is_order_closed(jy: int, jm: int, jd: int, now: datetime | None = None) -> bool:
    """آیا مهلت سفارش این روز تمام شده است؟"""
    now = now or now_local()
    return now >= order_deadline(jy, jm, jd)


def cutoff_label() -> str:
    return f"ساعت {fa_digits(settings.ORDER_CUTOFF_HOUR)} صبح همان روز"


def paid_counts(menu) -> dict[int, int]:
    """تعداد غذاهای فروخته‌شدهٔ هر روز یک ماه."""
    rows = (
        OrderItem.objects.filter(order__status=Order.Status.PAID, menu=menu)
        .values("slot")
        .annotate(total=Count("id"))
    )
    return {row["slot"]: row["total"] for row in rows}


def sold_count(menu, slot: int) -> int:
    return OrderItem.objects.filter(
        order__status=Order.Status.PAID, menu=menu, slot=slot
    ).count()


def has_capacity(entry, sold: int) -> bool:
    """ظرفیت ۰ یعنی بدون محدودیت."""
    return not entry.capacity or sold < entry.capacity


def slot_availability(entry, sold: int, now: datetime | None = None):
    """(قابل خرید بودن، دلیل بسته‌بودن) برای یک وعده."""
    if is_order_closed(entry.menu.year, entry.menu.month, entry.slot, now=now):
        return False, "closed"
    if not has_capacity(entry, sold):
        return False, "full"
    return True, ""


def gateway_code(data: dict) -> int:
    """کد پاسخ درگاه را با خیال راحت به عدد تبدیل می‌کند."""
    try:
        return int(data.get("code") or 0)
    except (AttributeError, TypeError, ValueError):
        return 0


def log_attempt(
    order: Order,
    status: str,
    *,
    amount: int | None = None,
    authority: str = "",
    gateway_code: str = "",
    ref_id: str = "",
    message: str = "",
) -> PaymentAttempt:
    return PaymentAttempt.objects.create(
        order=order,
        amount=order.amount if amount is None else amount,
        authority=authority or order.authority,
        status=status,
        gateway_code=str(gateway_code or ""),
        ref_id=str(ref_id or ""),
        message=message[:255],
    )
