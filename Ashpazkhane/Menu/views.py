import re

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from Ashpazkhane import clock
from Ashpazkhane.jalali import fa_digits, format_jalali, is_off_day, to_jalali, toman

from . import zarinpal
from .models import MenuEntry, MonthlyMenu, Order, OrderItem, PaymentAttempt
from .services import (
    cutoff_label,
    gateway_code,
    has_capacity,
    is_order_closed,
    log_attempt,
    paid_counts,
    sold_count,
)


def _today_key():
    return to_jalali(clock.today())


def _menu_url(menu):
    return f"/?m={menu.year}-{menu.month:02d}#menu"


def _pending_order(user, create=False):
    order = (
        Order.objects.filter(user=user, status=Order.Status.PENDING)
        .order_by("-created_at")
        .first()
    )
    if order is None and create:
        order = Order.objects.create(user=user)
    return order


def _is_past(menu, slot):
    return (menu.year, menu.month, slot) < _today_key()


def _is_paid(user, menu, slot):
    return OrderItem.objects.filter(
        order__user=user,
        order__status=Order.Status.PAID,
        menu=menu,
        slot=slot,
    ).exists()


AUTHORITY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,63}$")


def _valid_authority(value: str) -> bool:
    return bool(value and AUTHORITY_PATTERN.fullmatch(value))


@login_required
def cart(request):
    order = _pending_order(request.user)

    if order:
        removed = 0
        for item in list(order.items.all()):
            if is_order_closed(item.menu_year, item.menu_month, item.slot):
                item.delete()
                removed += 1
        if removed:
            messages.warning(
                request,
                f"{fa_digits(removed)} وعده که مهلت سفارش آن‌ها تمام شده بود از سبد حذف شد.",
            )

    items = list(order.items.all()) if order else []
    total = sum(item.cost for item in items)

    return render(
        request,
        "orders/cart.html",
        {
            "items": items,
            "items_count_fa": fa_digits(len(items)),
            "total": total,
            "total_fa": toman(total),
            "is_sandbox": settings.ZARINPAL_SANDBOX,
        },
    )


@login_required
@require_POST
def add_to_cart(request, menu_id, slot):
    menu = get_object_or_404(MonthlyMenu, pk=menu_id)
    entry = MenuEntry.objects.filter(menu=menu, slot=slot).first()

    if is_off_day(menu.year, menu.month, slot):
        messages.error(request, "پنجشنبه و جمعه آشپزخانه تعطیل است.")
        return redirect(_menu_url(menu))

    if entry is None:
        messages.error(request, "برای این روز وعده‌ای ثبت نشده است.")
        return redirect(_menu_url(menu))

    if _is_past(menu, slot):
        messages.error(request, "این روز گذشته است و قابل خرید نیست.")
        return redirect(_menu_url(menu))

    if is_order_closed(menu.year, menu.month, slot):
        messages.error(
            request,
            f"مهلت سفارش این روز تمام شده است. هر وعده تا {cutoff_label()} قابل خرید است.",
        )
        return redirect(_menu_url(menu))

    if not has_capacity(entry, sold_count(menu, slot)):
        messages.error(request, "ظرفیت این روز تکمیل شده است.")
        return redirect(_menu_url(menu))

    if _is_paid(request.user, menu, slot):
        messages.info(request, "این وعده را قبلاً خریداری کرده‌اید.")
        return redirect("orders:cart")

    order = _pending_order(request.user, create=True)
    _, created = OrderItem.objects.get_or_create(
        order=order,
        menu=menu,
        slot=slot,
        defaults={
            "menu_year": menu.year,
            "menu_month": menu.month,
            "name": entry.name,
            "cost": entry.cost,
        },
    )

    if created:
        messages.success(request, f"«{entry.name}» به سبد خرید اضافه شد.")
    else:
        messages.info(request, "این وعده از قبل در سبد خرید شماست.")

    return redirect("orders:cart")


@login_required
@require_POST
def add_month(request, menu_id):
    menu = get_object_or_404(MonthlyMenu, pk=menu_id)
    order = _pending_order(request.user, create=True)

    paid_slots = set(
        OrderItem.objects.filter(
            order__user=request.user,
            order__status=Order.Status.PAID,
            menu=menu,
        ).values_list("slot", flat=True)
    )
    cart_slots = set(order.items.filter(menu=menu).values_list("slot", flat=True))
    sold = paid_counts(menu)

    added = 0
    for entry in menu.slots.all():
        if is_off_day(menu.year, menu.month, entry.slot):
            continue
        if entry.slot in paid_slots or entry.slot in cart_slots:
            continue
        if _is_past(menu, entry.slot):
            continue
        if is_order_closed(menu.year, menu.month, entry.slot):
            continue
        if not has_capacity(entry, sold.get(entry.slot, 0)):
            continue

        OrderItem.objects.create(
            order=order,
            menu=menu,
            slot=entry.slot,
            menu_year=menu.year,
            menu_month=menu.month,
            name=entry.name,
            cost=entry.cost,
        )
        added += 1

    if added:
        messages.success(request, f"{added} وعده از این ماه به سبد خرید اضافه شد.")
    else:
        messages.info(
            request,
            "وعدهٔ قابل خریدی از این ماه باقی نمانده است.",
        )

    return redirect("orders:cart")


@login_required
@require_POST
def remove_item(request, item_id):
    item = get_object_or_404(
        OrderItem,
        pk=item_id,
        order__user=request.user,
        order__status=Order.Status.PENDING,
    )
    item.delete()
    messages.success(request, "وعده از سبد خرید حذف شد.")
    return redirect("orders:cart")


@login_required
@require_POST
def checkout(request):
    removed = 0
    blocked = 0

    # اعتبارسنجی دوبارهٔ سبد پیش از رفتن به درگاه، داخل یک تراکنش قفل‌شده
    # تا ظرفیت و مهلت هم‌زمان توسط کاربر دیگری دور زده نشود.
    with transaction.atomic():
        order = (
            Order.objects.select_for_update()
            .filter(user=request.user, status=Order.Status.PENDING)
            .order_by("-created_at")
            .first()
        )

        if order is None:
            messages.error(request, "سبد خرید شما خالی است.")
            return redirect("orders:cart")

        items = list(order.items.all())
        if not items:
            messages.error(request, "سبد خرید شما خالی است.")
            return redirect("orders:cart")

        menu_ids = {item.menu_id for item in items if item.menu_id}
        # قفل‌کردن ردیف‌های منو با ترتیب ثابت برای جلوگیری از تداخل.
        list(
            MenuEntry.objects.select_for_update()
            .filter(menu_id__in=menu_ids)
            .order_by("pk")
        )

        invalid_ids = []
        for item in items:
            if (
                item.menu_id is None
                or item.cost <= 0
                or is_off_day(item.menu_year, item.menu_month, item.slot)
                or _is_past(item.menu, item.slot)
                or is_order_closed(item.menu_year, item.menu_month, item.slot)
            ):
                invalid_ids.append(item.pk)
                removed += 1
                continue

            entry = MenuEntry.objects.filter(
                menu_id=item.menu_id, slot=item.slot
            ).first()
            if entry is None:
                invalid_ids.append(item.pk)
                removed += 1
                continue

            if _is_paid(request.user, item.menu, item.slot) or not has_capacity(
                entry, sold_count(item.menu, item.slot)
            ):
                invalid_ids.append(item.pk)
                blocked += 1

        if invalid_ids:
            OrderItem.objects.filter(pk__in=invalid_ids).delete()

        items = list(order.items.all())

    if removed:
        messages.warning(
            request,
            f"{fa_digits(removed)} وعده که مهلت یا اطلاعات آن معتبر نبود از سبد حذف شد.",
        )
    if blocked:
        messages.warning(
            request,
            f"{fa_digits(blocked)} وعده به دلیل تکمیل ظرفیت یا خرید قبلی از سبد حذف شد.",
        )

    if not items:
        messages.error(request, "سبد خرید شما خالی است.")
        return redirect("orders:cart")

    total = sum(item.cost for item in items)
    order.amount = total
    order.save(update_fields=["amount"])

    callback_url = request.build_absolute_uri(reverse("orders:callback"))
    description = f"خرید {len(items)} وعده از آشپزخانه"

    try:
        result = zarinpal.request_payment(total * 10, callback_url, description)
    except zarinpal.ZarinpalError:
        log_attempt(
            order,
            PaymentAttempt.Status.FAILED,
            message="ارتباط با درگاه برقرار نشد.",
        )
        messages.error(
            request,
            "ارتباط با درگاه پرداخت برقرار نشد. لطفاً چند لحظه بعد دوباره تلاش کنید.",
        )
        return redirect("orders:cart")

    data = result.get("data") or {}
    authority = str(data.get("authority") or "")
    if gateway_code(data) == 100 and _valid_authority(authority):
        order.authority = authority
        order.save(update_fields=["authority"])
        log_attempt(
            order,
            PaymentAttempt.Status.REQUESTED,
            gateway_code=data.get("code"),
            authority=order.authority,
            message="درخواست پرداخت ساخته شد.",
        )
        return redirect(zarinpal.start_url(order.authority))

    log_attempt(
        order,
        PaymentAttempt.Status.FAILED,
        gateway_code=data.get("code"),
        message="درگاه پاسخ معتبری برنگرداند.",
    )
    messages.error(
        request,
        "درگاه پرداخت پاسخ معتبری برنگرداند. لطفاً دوباره تلاش کنید.",
    )
    return redirect("orders:cart")


@login_required
def payment_callback(request):
    authority = (request.GET.get("Authority") or "").strip()
    status = (request.GET.get("Status") or "").strip()

    if not _valid_authority(authority):
        messages.error(request, "اطلاعات بازگشتی درگاه معتبر نیست.")
        return redirect("orders:cart")

    # کد پرداخت ممکن است مربوط به یکی از تلاش‌های قبلی همان سفارش باشد.
    order = (
        Order.objects.filter(user=request.user)
        .filter(Q(authority=authority) | Q(attempts__authority=authority))
        .distinct()
        .first()
    )

    if order is None:
        messages.error(request, "سفارش موردنظر پیدا نشد.")
        return redirect("orders:cart")

    if order.status == Order.Status.PAID:
        return render(
            request,
            "orders/payment_success.html",
            {
                "order": order,
                "items": order.items.all(),
                "total_fa": toman(order.amount),
            },
        )

    if order.amount <= 0:
        messages.error(request, "مبلغ این سفارش معتبر نیست.")
        return redirect("orders:cart")

    if status != "OK":
        log_attempt(
            order,
            PaymentAttempt.Status.CANCELED,
            authority=authority,
            message="کاربر از درگاه برگشت یا پرداخت لغو شد.",
        )
        messages.error(request, "پرداخت لغو شد یا ناموفق بود. می‌توانید دوباره تلاش کنید.")
        return redirect("orders:cart")

    try:
        result = zarinpal.verify_payment(order.amount * 10, authority)
    except zarinpal.ZarinpalError:
        log_attempt(
            order,
            PaymentAttempt.Status.FAILED,
            authority=authority,
            message="خطا در تأیید پرداخت از سمت درگاه.",
        )
        messages.error(
            request,
            "تأیید پرداخت انجام نشد. اگر مبلغ کسر شده است، با مدیر تماس بگیرید.",
        )
        return redirect("orders:cart")

    data = result.get("data") or {}
    code = gateway_code(data)

    if code in (100, 101):
        with transaction.atomic():
            locked = Order.objects.select_for_update().get(pk=order.pk)
            if locked.status != Order.Status.PAID:
                locked.status = Order.Status.PAID
                locked.ref_id = str(data.get("ref_id") or "")[:64]
                locked.paid_at = timezone.now()
                locked.authority = authority
                locked.save(
                    update_fields=["status", "ref_id", "paid_at", "authority"]
                )
                log_attempt(
                    locked,
                    PaymentAttempt.Status.VERIFIED,
                    authority=authority,
                    gateway_code=code,
                    ref_id=locked.ref_id,
                    message="پرداخت تأیید شد.",
                )
            order = locked

        return render(
            request,
            "orders/payment_success.html",
            {
                "order": order,
                "items": order.items.all(),
                "total_fa": toman(order.amount),
            },
        )

    log_attempt(
        order,
        PaymentAttempt.Status.FAILED,
        authority=authority,
        gateway_code=code,
        message="پرداخت تأیید نشد.",
    )
    messages.error(request, "پرداخت تأیید نشد. لطفاً دوباره تلاش کنید.")
    return redirect("orders:cart")


@login_required
def history(request):
    orders = (
        Order.objects.filter(user=request.user, status=Order.Status.PAID)
        .prefetch_related("items")
        .order_by("-paid_at")
    )
    for order in orders:
        if order.paid_at:
            local = timezone.localtime(order.paid_at)
            order.paid_at_fa = (
                f"{format_jalali(local.date())} ساعت "
                f"{fa_digits(local.strftime('%H:%M'))}"
            )
        else:
            order.paid_at_fa = ""

    return render(request, "orders/history.html", {"orders": orders})
