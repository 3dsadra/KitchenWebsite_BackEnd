from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.core.cache import cache
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_http_methods

from Menu.models import MonthlyMenu, Order, OrderItem
from Menu.services import cutoff_label, is_order_closed, paid_counts

from . import clock
from .jalali import (
    JALALI_MONTHS,
    WEEKDAYS,
    fa_digits,
    fa_number,
    format_jalali,
    is_off_day,
    jalali_month_length,
    jalali_weekday,
    month_label,
    to_jalali,
    toman,
    weekday_index,
    weekday_name,
)


def _safe_next(value: str | None, request=None) -> str:
    if not value:
        return "/"
    host = request.get_host() if request is not None else None
    allowed = {host} if host else None
    if url_has_allowed_host_and_scheme(
        value,
        allowed_hosts=allowed,
        require_https=request.is_secure() if request is not None else False,
    ):
        return value
    return "/"


def _client_ip(request) -> str:
    return (request.META.get("REMOTE_ADDR") or "unknown").strip()


def _login_keys(request, code: str) -> tuple[str, str]:
    ip = _client_ip(request)
    return (
        f"login:fail:{ip}:{code}",
        f"login:fail-ip:{ip}",
    )


def _login_locked(request, code: str) -> bool:
    pair_key, ip_key = _login_keys(request, code)
    return (cache.get(pair_key) or 0) >= settings.LOGIN_MAX_ATTEMPTS or (
        cache.get(ip_key) or 0
    ) >= settings.LOGIN_MAX_ATTEMPTS * 3


def _login_failed(request, code: str) -> None:
    pair_key, ip_key = _login_keys(request, code)
    for key in (pair_key, ip_key):
        try:
            cache.incr(key)
        except ValueError:
            cache.set(key, 1, settings.LOGIN_LOCKOUT_SECONDS)


def _login_reset(request, code: str) -> None:
    for key in _login_keys(request, code):
        cache.delete(key)


def _menu_cells(
    menu: MonthlyMenu,
    today: tuple[int, int, int],
    paid_slots: set[int] | None = None,
    cart_slots: set[int] | None = None,
    sold_counts: dict[int, int] | None = None,
) -> list[dict]:
    first_day = jalali_weekday(menu.year, menu.month, 1)
    days_in_month = jalali_month_length(menu.year, menu.month)
    entries = {entry.slot: entry for entry in menu.slots.all()}
    month_name = JALALI_MONTHS[menu.month - 1]
    paid_slots = paid_slots or set()
    cart_slots = cart_slots or set()
    sold_counts = sold_counts or {}

    cells: list[dict] = [{"blank": True} for _ in range(weekday_index(first_day))]

    for day in range(1, days_in_month + 1):
        current = jalali_weekday(menu.year, menu.month, day)
        current_key = (menu.year, menu.month, day)
        entry = entries.get(day)
        is_off = is_off_day(menu.year, menu.month, day)
        is_past = current_key < today
        is_paid = day in paid_slots
        in_cart = day in cart_slots
        has_meal = entry is not None and not is_off
        sold = sold_counts.get(day, 0)
        capacity = entry.capacity if has_meal else 0
        is_full = bool(capacity) and sold >= capacity
        is_closed = is_past or is_order_closed(menu.year, menu.month, day)
        remaining = max(capacity - sold, 0) if capacity else None

        cells.append(
            {
                "blank": False,
                "day": day,
                "jalali_day": fa_digits(day),
                "jalali_month": month_name,
                "jalali_year": fa_digits(menu.year),
                "weekday": weekday_name(current),
                "weekday_index": weekday_index(current),
                "iso": f"{menu.year:04d}-{menu.month:02d}-{day:02d}",
                "date_label": f"{weekday_name(current)} {fa_digits(day)} {month_name} {fa_digits(menu.year)}",
                "is_today": current_key == today,
                "is_past": is_past,
                "is_off": is_off,
                "is_closed": is_closed,
                "is_full": is_full,
                "capacity": capacity,
                "capacity_fa": fa_digits(capacity) if capacity else "",
                "remaining": remaining,
                "remaining_fa": fa_digits(remaining) if remaining is not None else "",
                "has_meal": has_meal,
                "name": entry.name if has_meal else "",
                "cost": entry.cost if has_meal else 0,
                "cost_fa": toman(entry.cost) if has_meal else "",
                "cost_short": fa_number(entry.cost) if has_meal else "",
                "is_paid": is_paid,
                "in_cart": in_cart,
                "can_buy": (
                    has_meal
                    and not is_paid
                    and not in_cart
                    and not is_closed
                    and not is_full
                ),
            }
        )

    return cells


def homepage(request):
    today = clock.today()
    jalali_year, jalali_month, jalali_day = to_jalali(today)
    today_key = (jalali_year, jalali_month, jalali_day)

    menus = list(MonthlyMenu.objects.all())

    selected = None
    requested = request.GET.get("m", "")
    if requested:
        try:
            year_part, month_part = requested.split("-")
            selected = next(
                (m for m in menus if m.year == int(year_part) and m.month == int(month_part)),
                None,
            )
        except (ValueError, AttributeError):
            selected = None

    if selected is None:
        selected = next(
            (m for m in menus if m.year == jalali_year and m.month == jalali_month),
            None,
        ) or (menus[0] if menus else None)

    chronological = sorted(menus, key=lambda m: (m.year, m.month))
    prev_menu = next_menu = None
    if selected and selected in chronological:
        index = chronological.index(selected)
        if index > 0:
            prev_menu = chronological[index - 1]
        if index < len(chronological) - 1:
            next_menu = chronological[index + 1]

    def url_for(menu):
        return f"?m={menu.year}-{menu.month:02d}"

    paid_slots: set[int] = set()
    cart_slots: set[int] = set()
    selected_in_cart_total = 0
    if selected and request.user.is_authenticated:
        paid_slots = set(
            OrderItem.objects.filter(
                order__user=request.user,
                order__status=Order.Status.PAID,
                menu=selected,
            ).values_list("slot", flat=True)
        )
        cart_qs = OrderItem.objects.filter(
            order__user=request.user,
            order__status=Order.Status.PENDING,
            menu=selected,
        )
        cart_slots = set(cart_qs.values_list("slot", flat=True))
        selected_in_cart_total = sum(cart_qs.values_list("cost", flat=True))

    sold_counts = paid_counts(selected) if selected else {}
    cells = (
        _menu_cells(selected, today_key, paid_slots, cart_slots, sold_counts)
        if selected
        else []
    )
    meals = [cell for cell in cells if not cell.get("blank") and cell["has_meal"]]
    total_cost = sum(cell["cost"] for cell in meals)
    average_cost = round(total_cost / len(meals)) if meals else 0
    buyable = [cell for cell in cells if not cell.get("blank") and cell.get("can_buy")]

    today_menu = next(
        (m for m in menus if m.year == jalali_year and m.month == jalali_month), None
    )
    today_entry = None
    if today_menu:
        today_entry = today_menu.slots.filter(slot=jalali_day).first()

    today_meal = None
    if today_entry and not is_off_day(jalali_year, jalali_month, jalali_day):
        today_meal = {
            "name": today_entry.name,
            "cost": today_entry.cost,
            "cost_fa": toman(today_entry.cost),
        }

    context = {
        "menus": menus,
        "selected": selected,
        "selected_label": month_label(selected) if selected else "بدون منو",
        "selected_is_current": bool(
            selected
            and selected.year == jalali_year
            and selected.month == jalali_month
        ),
        "prev_menu": prev_menu,
        "next_menu": next_menu,
        "prev_url": url_for(prev_menu) if prev_menu else "",
        "next_url": url_for(next_menu) if next_menu else "",
        "cells": cells,
        "meals_count": len(meals),
        "meals_count_fa": fa_digits(len(meals)),
        "total_cost": total_cost,
        "total_cost_fa": toman(total_cost),
        "average_cost_fa": toman(average_cost),
        "buyable_count": len(buyable),
        "buyable_count_fa": fa_digits(len(buyable)),
        "buyable_total": sum(cell["cost"] for cell in buyable),
        "buyable_total_fa": toman(sum(cell["cost"] for cell in buyable)),
        "paid_count": len(paid_slots),
        "paid_count_fa": fa_digits(len(paid_slots)),
        "selected_in_cart_total": selected_in_cart_total,
        "selected_in_cart_total_fa": toman(selected_in_cart_total),
        "today": today,
        "today_fa": format_jalali(today, with_weekday=True),
        "today_day_fa": fa_digits(jalali_day),
        "today_month_fa": JALALI_MONTHS[jalali_month - 1],
        "today_year_fa": fa_digits(jalali_year),
        "today_meal": today_meal,
        "today_is_off": is_off_day(jalali_year, jalali_month, jalali_day),
        "today_is_closed": is_order_closed(jalali_year, jalali_month, jalali_day),
        "cutoff_label": cutoff_label(),
        "weekdays": WEEKDAYS,
    }
    return render(request, "home.html", context)


@require_http_methods(["GET", "POST"])
def login_v(request):
    if request.user.is_authenticated:
        return redirect("/panel/" if request.user.is_staff else "/")

    next_url = _safe_next(
        request.POST.get("next") or request.GET.get("next"), request
    )
    national_code = ""

    if request.method == "POST":
        national_code = (request.POST.get("username") or "").strip()
        password = request.POST.get("password") or ""

        if _login_locked(request, national_code):
            messages.error(
                request,
                "تلاش‌های ناموفق زیاد بوده است. چند دقیقه بعد دوباره امتحان کنید.",
            )
        elif not national_code or not password:
            messages.error(request, "کد ملی و رمز عبور را کامل وارد کنید.")
        elif not national_code.isdigit() or len(national_code) != 10:
            messages.error(request, "کد ملی باید یک عدد ۱۰ رقمی باشد.")
        else:
            user = authenticate(request, username=national_code, password=password)
            if user is not None:
                _login_reset(request, national_code)
                login(request, user)
                if user.is_staff:
                    messages.success(request, "خوش آمدید! وارد پنل مدیریت شدید.")
                    return redirect("/panel/")
                messages.success(request, "خوش آمدید! ورود با موفقیت انجام شد.")
                return redirect(next_url)
            _login_failed(request, national_code)
            messages.error(request, "کد ملی یا رمز عبور نادرست است.")

    return render(
        request,
        "users/login.html",
        {"next": next_url, "national_code": national_code},
    )


def logout_v(request):
    was_staff = request.user.is_staff
    logout(request)
    messages.info(request, "از حساب خود خارج شدید.")
    return redirect("/login/" if was_staff else "/")
