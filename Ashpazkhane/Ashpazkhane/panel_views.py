"""پنل مدیریت سایت: گزارش سفارش‌ها، کاربران و منوی ماهانه."""

from datetime import date, timedelta
from functools import wraps

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from Menu.models import MenuEntry, MonthlyMenu, Order, OrderItem, PaymentAttempt
from Menu.services import gateway_code, log_attempt, paid_counts
from Menu import zarinpal

from . import clock
from .jalali import (
    JALALI_MONTHS,
    fa_digits,
    format_jalali,
    is_off_day,
    jalali_month_length,
    jalali_to_gregorian,
    jalali_weekday,
    to_jalali,
    toman,
    weekday_index,
    weekday_name,
)

User = get_user_model()

PERIOD_LABELS = {
    "today": "امروز",
    "week": "این هفته",
    "month": "این ماه",
    "all": "همه",
}


def staff_required(view):
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(f"/login/?next={request.path}")
        if not request.user.is_staff:
            messages.error(request, "دسترسی به پنل مدیریت فقط برای مدیران است.")
            return redirect("home")
        return view(request, *args, **kwargs)

    return wrapper


def _ascii_digits(value) -> str:
    table = str.maketrans("۰۱۲۳۴۵۶۷۸۹٬,", "0123456789,,")
    return str(value or "").translate(table)


MAX_MEAL_NAME = 100
MAX_MEAL_PRICE = 100_000_000  # تومان
MAX_MEAL_CAPACITY = 100_000

EXCEL_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _excel_text(value) -> str:
    """جلوگیری از تزریق فرمول در فایل اکسل (CSV/Formula Injection)."""
    text = "" if value is None else str(value)
    if text.startswith(EXCEL_FORMULA_PREFIXES):
        return "'" + text
    return text


def _password_error(password: str, *, code: str = "", staff: bool = False) -> str:
    """اعتبارسنجی رمز عبور؛ برای حساب‌های مدیر سخت‌گیرتر است."""
    if len(password) < 8:
        return "رمز عبور باید حداقل ۸ نویسه باشد."
    if len(password) > 128:
        return "رمز عبور بیش از حد بلند است."
    if password.isdigit():
        return "رمز عبور نباید فقط عدد باشد."
    if code and password.strip() == code:
        return "رمز عبور نباید همان کد ملی باشد."

    if staff:
        from django.contrib.auth.password_validation import validate_password
        from django.core.exceptions import ValidationError

        try:
            validate_password(password)
        except ValidationError as exc:
            return " ".join(exc.messages)

    return ""


def _display_name(user) -> str:
    full = user.get_full_name().strip()
    return full or "بدون نام"


def _all_paid_rows() -> list[dict]:
    items = (
        OrderItem.objects.filter(order__status=Order.Status.PAID)
        .select_related("order__user")
        .order_by("menu_year", "menu_month", "slot", "order__user__last_name")
    )

    rows = []
    for item in items:
        gy, gm, gd = jalali_to_gregorian(item.menu_year, item.menu_month, item.slot)
        item_date = date(gy, gm, gd)
        user = item.order.user
        rows.append(
            {
                "id": item.pk,
                "order_id": item.order_id,
                "ref_id": item.order.ref_id,
                "user_id": user.pk,
                "name": _display_name(user),
                "code": user.username,
                "code_fa": fa_digits(user.username),
                "meal": item.name,
                "cost": item.cost,
                "cost_fa": toman(item.cost),
                "ref_id_fa": fa_digits(item.order.ref_id) if item.order.ref_id else "—",
                "date": item_date,
                "jalali": (item.menu_year, item.menu_month, item.slot),
                "date_fa": format_jalali(item_date),
                "date_full_fa": format_jalali(item_date, with_weekday=True),
                "weekday": weekday_name(item_date),
            }
        )

    rows.sort(key=lambda row: (row["date"], row["name"]), reverse=True)
    return rows


def _parse_month(value: str) -> tuple[int, int] | None:
    try:
        year_part, month_part = value.split("-")
        year, month = int(year_part), int(month_part)
    except (AttributeError, ValueError):
        return None
    if not (1200 <= year <= 1600 and 1 <= month <= 12):
        return None
    return year, month


def _filter_rows(rows: list[dict], params) -> list[dict]:
    today = clock.today()
    today_jalali = to_jalali(today)

    query = _ascii_digits(params.get("q", "")).strip().lower()
    month = _parse_month(params.get("month", ""))
    day_raw = _ascii_digits(params.get("day", "")).strip()
    day = int(day_raw) if day_raw.isdigit() else None
    period = params.get("period", "month")

    result = rows

    if query:
        result = [
            row
            for row in result
            if query in row["name"].lower()
            or query in row["code"].lower()
            or query in row["meal"].lower()
        ]

    if month and day:
        result = [row for row in result if row["jalali"] == (month[0], month[1], day)]
    elif month:
        result = [row for row in result if row["jalali"][:2] == month]
    elif day:
        result = [
            row
            for row in result
            if row["jalali"] == (today_jalali[0], today_jalali[1], day)
        ]
    elif period == "today":
        result = [row for row in result if row["jalali"] == today_jalali]
    elif period == "week":
        start = today - timedelta(days=weekday_index(today))
        end = start + timedelta(days=6)
        result = [row for row in result if start <= row["date"] <= end]
    elif period == "month":
        result = [row for row in result if row["jalali"][:2] == today_jalali[:2]]

    return result


def _summarize(rows: list[dict]) -> dict:
    people = len({row["user_id"] for row in rows})
    total = sum(row["cost"] for row in rows)
    return {
        "people": people,
        "people_fa": fa_digits(people),
        "meals": len(rows),
        "meals_fa": fa_digits(len(rows)),
        "total": total,
        "total_fa": toman(total),
        "days": len({row["jalali"] for row in rows}),
    }


def _group_by_day(rows: list[dict]) -> list[dict]:
    groups: dict[tuple, dict] = {}
    for row in rows:
        key = row["jalali"]
        group = groups.setdefault(
            key,
            {
                "jalali": key,
                "date_fa": row["date_full_fa"],
                "weekday": row["weekday"],
                "users": set(),
                "meals": 0,
                "total": 0,
                "month": f"{key[0]}-{key[1]:02d}",
            },
        )
        group["users"].add(row["user_id"])
        group["meals"] += 1
        group["total"] += row["cost"]

    result = []
    for group in groups.values():
        group["people"] = len(group["users"])
        group["people_fa"] = fa_digits(group["people"])
        group["meals_fa"] = fa_digits(group["meals"])
        group["total_fa"] = toman(group["total"])
        result.append(group)

    result.sort(key=lambda g: g["jalali"], reverse=True)
    return result


def _select_menus():
    return list(MonthlyMenu.objects.order_by("-year", "-month"))


def _default_menu(menus):
    today_jalali = to_jalali(clock.today())
    for menu in menus:
        if (menu.year, menu.month) == today_jalali[:2]:
            return menu
    return menus[0] if menus else None


@staff_required
def dashboard(request):
    rows = _all_paid_rows()
    today = clock.today()

    cards = []
    for period, label in (
        ("today", "امروز"),
        ("week", "این هفته"),
        ("month", "این ماه"),
    ):
        summary = _summarize(_filter_rows(rows, {"period": period}))
        summary["label"] = label
        cards.append(summary)

    total = _summarize(rows)
    latest = rows[:8]

    return render(
        request,
        "panel/dashboard.html",
        {
            "cards": cards,
            "total": total,
            "latest": latest,
            "today_fa": format_jalali(today, with_weekday=True),
            "users_count": User.objects.count(),
            "menus_count": MonthlyMenu.objects.count(),
            "pending_count": Order.objects.filter(status=Order.Status.PENDING)
            .exclude(items=None)
            .count(),
        },
    )


@staff_required
def orders(request):
    params = {
        "period": request.GET.get("period", "month"),
        "month": request.GET.get("month", ""),
        "day": request.GET.get("day", ""),
        "q": (request.GET.get("q") or "")[:100],
    }
    if params["period"] not in PERIOD_LABELS:
        params["period"] = "month"

    month = _parse_month(params["month"])
    day_raw = _ascii_digits(params["day"]).strip()
    day_number = int(day_raw) if day_raw.isdigit() else None
    if day_number is not None:
        limit = jalali_month_length(month[0], month[1]) if month else 31
        if not 1 <= day_number <= limit:
            day_number = None
            params["day"] = ""

    rows = _filter_rows(_all_paid_rows(), params)

    if request.GET.get("delivery") == "xlsx":
        if month and day_number is not None:
            return _delivery_excel(rows, month[0], month[1], day_number)

    if request.GET.get("export") == "xlsx":
        return _orders_excel(rows, params)

    menus = _select_menus()
    if month:
        selected_month = next(
            (m for m in menus if (m.year, m.month) == month), None
        )
        month_days = jalali_month_length(month[0], month[1])
        month_label_value = (
            f"{JALALI_MONTHS[month[1] - 1]} {fa_digits(month[0])}"
        )
    else:
        selected_month = None
        month_days = 31
        month_label_value = ""

    day_value = str(day_number) if day_number is not None else ""
    summary = _summarize(rows)
    groups = [] if day_value else _group_by_day(rows)

    day_options = [
        {
            "value": str(number),
            "label": f"روز {fa_digits(number)}",
            "selected": day_value == str(number),
        }
        for number in range(1, month_days + 1)
    ]

    return render(
        request,
        "panel/orders.html",
        {
            "rows": rows,
            "groups": groups,
            "summary": summary,
            "menus": menus,
            "params": params,
            "period_labels": PERIOD_LABELS,
            "day_options": day_options,
            "month_label_value": month_label_value,
            "selected_month": selected_month,
            "is_day_filter": bool(day_value),
            "day_fa": fa_digits(day_value) if day_value else "",
        },
    )


def _orders_excel(rows, params) -> HttpResponse:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    workbook = Workbook()

    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill("solid", fgColor="20664E")
    center = Alignment(horizontal="center", vertical="center")

    def style_header(sheet, row_number, count):
        for column in range(1, count + 1):
            cell = sheet.cell(row=row_number, column=column)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = center

    # ---------------------------- جزئیات ----------------------------
    detail = workbook.active
    detail.title = "جزئیات سفارش‌ها"
    detail.sheet_view.rightToLeft = True

    detail_headers = [
        "ردیف",
        "نام و نام خانوادگی",
        "کد ملی",
        "غذا",
        "تاریخ",
        "روز هفته",
        "مبلغ (تومان)",
        "شماره سفارش",
        "کد پیگیری",
    ]
    detail.append(detail_headers)
    style_header(detail, 1, len(detail_headers))

    for index, row in enumerate(rows, start=1):
        detail.append(
            [
                index,
                _excel_text(row["name"]),
                _excel_text(row["code"]),
                _excel_text(row["meal"]),
                _excel_text(row["date_fa"]),
                _excel_text(row["weekday"]),
                row["cost"],
                row["order_id"],
                _excel_text(row["ref_id"]),
            ]
        )

    detail.column_dimensions["A"].width = 7
    detail.column_dimensions["B"].width = 26
    detail.column_dimensions["C"].width = 15
    detail.column_dimensions["D"].width = 24
    detail.column_dimensions["E"].width = 18
    detail.column_dimensions["F"].width = 12
    detail.column_dimensions["G"].width = 16
    detail.column_dimensions["H"].width = 13
    detail.column_dimensions["I"].width = 18
    for row_number in range(2, detail.max_row + 1):
        detail.cell(row=row_number, column=7).number_format = "#,##0"
        detail.cell(row=row_number, column=1).alignment = center
        detail.cell(row=row_number, column=3).alignment = center
    detail.freeze_panes = "A2"

    # ---------------------------- خلاصه ----------------------------
    summary_sheet = workbook.create_sheet("خلاصه")
    summary_sheet.sheet_view.rightToLeft = True
    summary = _summarize(rows)

    summary_sheet["A1"] = "گزارش سفارش‌های آشپزخانه"
    summary_sheet["A1"].font = Font(bold=True, size=14)
    summary_sheet["A3"] = "تعداد افراد"
    summary_sheet["B3"] = summary["people"]
    summary_sheet["A4"] = "تعداد وعده"
    summary_sheet["B4"] = summary["meals"]
    summary_sheet["A5"] = "مبلغ کل (تومان)"
    summary_sheet["B5"] = summary["total"]
    summary_sheet["B5"].number_format = "#,##0"
    summary_sheet["A6"] = "تعداد روزها"
    summary_sheet["B6"] = summary["days"]

    filter_parts = []
    if params.get("month"):
        month = _parse_month(params["month"])
        if month:
            filter_parts.append(
                f"ماه: {JALALI_MONTHS[month[1] - 1]} {fa_digits(month[0])}"
            )
    if params.get("day"):
        filter_parts.append(f"روز: {fa_digits(_ascii_digits(params['day']))}")
    if params.get("q"):
        filter_parts.append(f"جست‌وجو: {_excel_text(params['q'])}")
    if not filter_parts:
        filter_parts.append(f"بازه: {PERIOD_LABELS.get(params.get('period'), 'این ماه')}")
    summary_sheet["A7"] = " | ".join(filter_parts)

    group_start = 9
    summary_headers = [
        "تاریخ",
        "روز هفته",
        "تعداد افراد",
        "تعداد وعده",
        "مبلغ (تومان)",
    ]
    for column, title in enumerate(summary_headers, start=1):
        cell = summary_sheet.cell(row=group_start, column=column, value=title)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = center

    row_number = group_start + 1
    for group in _group_by_day(rows):
        summary_sheet.cell(row=row_number, column=1, value=group["date_fa"])
        summary_sheet.cell(row=row_number, column=2, value=group["weekday"])
        summary_sheet.cell(row=row_number, column=3, value=group["people"])
        summary_sheet.cell(row=row_number, column=4, value=group["meals"])
        summary_sheet.cell(row=row_number, column=5, value=group["total"])
        summary_sheet.cell(row=row_number, column=5).number_format = "#,##0"
        row_number += 1

    for column, width in zip(range(1, 6), (20, 12, 14, 14, 18)):
        summary_sheet.column_dimensions[get_column_letter(column)].width = width

    response = HttpResponse(
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    response["Content-Disposition"] = 'attachment; filename="orders-report.xlsx"'
    workbook.save(response)
    return response


@staff_required
def users(request):
    query = (request.GET.get("q") or "").strip()[:100]
    normalized = _ascii_digits(query)

    queryset = User.objects.all()
    if normalized:
        queryset = queryset.filter(
            Q(first_name__icontains=normalized)
            | Q(last_name__icontains=normalized)
            | Q(username__icontains=normalized)
        )
    queryset = queryset.order_by("-date_joined")

    if request.method == "POST":
        first_name = (request.POST.get("first_name") or "").strip()
        last_name = (request.POST.get("last_name") or "").strip()
        code = _ascii_digits(request.POST.get("national_code") or "").strip()
        password = request.POST.get("password") or ""
        is_staff = request.POST.get("is_staff") == "on"
        if is_staff and not request.user.is_superuser:
            is_staff = False
        form_values = {
            "first_name": first_name,
            "last_name": last_name,
            "national_code": code,
            "is_staff": is_staff,
        }

        errors = []
        if not first_name or not last_name:
            errors.append("نام و نام خانوادگی را کامل وارد کنید.")
        if len(first_name) > 150 or len(last_name) > 150:
            errors.append("نام یا نام خانوادگی بیش از حد بلند است.")
        if not code.isdigit() or len(code) != 10:
            errors.append("کد ملی باید ۱۰ رقم باشد.")
        elif User.objects.filter(username=code).exists():
            errors.append("کاربری با این کد ملی از قبل وجود دارد.")
        if request.POST.get("is_staff") == "on" and not request.user.is_superuser:
            errors.append("ساخت حساب مدیر فقط توسط مدیر ارشد امکان‌پذیر است.")

        password_error = _password_error(password, code=code, staff=is_staff)
        if password_error:
            errors.append(password_error)

        if errors:
            for error in errors:
                messages.error(request, error)
        else:
            user = User.objects.create_user(
                username=code,
                password=password,
                first_name=first_name,
                last_name=last_name,
            )
            if is_staff:
                user.is_staff = True
                user.save(update_fields=["is_staff"])
            messages.success(request, f"کاربر «{first_name} {last_name}» ساخته شد.")
            return redirect("panel_users")

    else:
        form_values = {
            "first_name": "",
            "last_name": "",
            "national_code": "",
            "is_staff": False,
        }

    user_rows = []
    for user in queryset:
        joined = timezone.localtime(user.date_joined)
        last_login = (
            format_jalali(timezone.localtime(user.last_login).date())
            if user.last_login
            else "—"
        )
        user_rows.append(
            {
                "id": user.pk,
                "name": _display_name(user),
                "code": user.username,
                "code_fa": fa_digits(user.username),
                "is_staff": user.is_staff,
                "is_active": user.is_active,
                "joined_fa": format_jalali(joined.date()),
                "last_login_fa": last_login,
            }
        )

    return render(
        request,
        "panel/users.html",
        {
            "user_rows": user_rows,
            "count": len(user_rows),
            "query": query,
            "form": form_values,
        },
    )


@staff_required
def menu(request):
    menus = _select_menus()
    month_param = _parse_month(request.GET.get("m", ""))
    selected = None
    if month_param:
        selected = next(
            (m for m in menus if (m.year, m.month) == month_param), None
        )
    if selected is None:
        selected = _default_menu(menus)

    day_rows = []
    if selected:
        entries = {entry.slot: entry for entry in selected.slots.all()}
        sold = paid_counts(selected)
        total_days = jalali_month_length(selected.year, selected.month)
        for index in range(1, total_days + 1):
            off = is_off_day(selected.year, selected.month, index)
            entry = entries.get(index)
            day_rows.append(
                {
                    "day": index,
                    "day_fa": fa_digits(index),
                    "weekday": weekday_name(
                        jalali_weekday(selected.year, selected.month, index)
                    ),
                    "is_off": off,
                    "name": entry.name if entry else "",
                    "cost": entry.cost if entry else "",
                    "capacity": (entry.capacity or "") if entry else "",
                    "sold_fa": fa_digits(sold.get(index, 0)),
                    "has_entry": bool(entry),
                }
            )

        selected_label = f"{JALALI_MONTHS[selected.month - 1]} {fa_digits(selected.year)}"
    else:
        selected_label = ""

    return render(
        request,
        "panel/menu.html",
        {
            "menus": menus,
            "selected": selected,
            "selected_label": selected_label,
            "day_rows": day_rows,
            "today_jalali": to_jalali(clock.today()),
            "months": list(enumerate(JALALI_MONTHS, start=1)),
        },
    )


@staff_required
@require_POST
def menu_save(request):
    menu_obj = get_object_or_404(MonthlyMenu, pk=request.POST.get("menu_id"))
    total_days = jalali_month_length(menu_obj.year, menu_obj.month)
    saved = 0
    removed = 0
    errors = 0

    for day in range(1, total_days + 1):
        if is_off_day(menu_obj.year, menu_obj.month, day):
            continue

        name = (request.POST.get(f"name_{day}") or "").strip()
        cost_raw = (
            _ascii_digits(request.POST.get(f"cost_{day}") or "")
            .replace("٬", "")
            .replace(",", "")
            .strip()
        )
        capacity_raw = (
            _ascii_digits(request.POST.get(f"capacity_{day}") or "").strip()
        )
        capacity = int(capacity_raw) if capacity_raw.isdigit() else 0

        if not name:
            deleted, _ = MenuEntry.objects.filter(menu=menu_obj, slot=day).delete()
            removed += deleted
            continue

        if (
            len(name) > MAX_MEAL_NAME
            or not cost_raw.isdigit()
            or not 0 < int(cost_raw) <= MAX_MEAL_PRICE
            or capacity > MAX_MEAL_CAPACITY
        ):
            errors += 1
            continue

        MenuEntry.objects.update_or_create(
            menu=menu_obj,
            slot=day,
            defaults={"name": name, "cost": int(cost_raw), "capacity": capacity},
        )
        saved += 1

    if saved:
        messages.success(request, f"{fa_digits(saved)} وعده ذخیره شد.")
    if removed:
        messages.info(request, f"{fa_digits(removed)} وعده خالی شد و حذف شد.")
    if errors:
        messages.error(
            request,
            f"برای {fa_digits(errors)} روز قیمت معتبر وارد نشده بود؛ آن روزها ذخیره نشدند.",
        )

    return redirect(f"/panel/menu/?m={menu_obj.year}-{menu_obj.month:02d}")


@staff_required
@require_POST
def menu_create(request):
    year_raw = _ascii_digits(request.POST.get("year") or "").strip()
    month_raw = _ascii_digits(request.POST.get("month") or "").strip()

    if not year_raw.isdigit() or not month_raw.isdigit():
        messages.error(request, "سال و ماه را درست وارد کنید.")
        return redirect("panel_menu")

    year, month = int(year_raw), int(month_raw)
    if not (1300 <= year <= 1500 and 1 <= month <= 12):
        messages.error(request, "سال یا ماه خارج از محدودهٔ مجاز است.")
        return redirect("panel_menu")

    menu_obj, created = MonthlyMenu.objects.get_or_create(year=year, month=month)
    if created:
        messages.success(
            request,
            f"منوی {JALALI_MONTHS[month - 1]} {fa_digits(year)} ساخته شد.",
        )
    else:
        messages.info(request, "این ماه از قبل وجود دارد.")
    return redirect(f"/panel/menu/?m={menu_obj.year}-{menu_obj.month:02d}")


def _delivery_excel(rows, year: int, month: int, day: int) -> HttpResponse:
    """فایل سادهٔ تحویل غذا برای یک روز: نام، کد ملی و غذا."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    rows = sorted(rows, key=lambda row: row["name"])
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "لیست تحویل"
    sheet.sheet_view.rightToLeft = True

    title = f"لیست تحویل {JALALI_MONTHS[month - 1]} {fa_digits(year)} — روز {fa_digits(day)}"
    sheet.append([title])
    sheet["A1"].font = Font(bold=True, size=13)
    sheet.append([])

    headers = ["ردیف", "نام و نام خانوادگی", "کد ملی", "غذا"]
    sheet.append(headers)
    for column in range(1, len(headers) + 1):
        cell = sheet.cell(row=3, column=column)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="20664E")
        cell.alignment = Alignment(horizontal="center", vertical="center")

    for index, row in enumerate(rows, start=1):
        sheet.append(
            [
                index,
                _excel_text(row["name"]),
                _excel_text(row["code"]),
                _excel_text(row["meal"]),
            ]
        )

    for column, width in zip(range(1, 5), (7, 28, 15, 24)):
        sheet.column_dimensions[get_column_letter(column)].width = width
    sheet.freeze_panes = "A4"

    response = HttpResponse(
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    response["Content-Disposition"] = (
        f'attachment; filename="delivery-{year}-{month:02d}-{day:02d}.xlsx"'
    )
    workbook.save(response)
    return response


@staff_required
def finance(request):
    rows = _all_paid_rows()

    # ظرفیت هر ماه از منوها خوانده می‌شود.
    capacities: dict[str, int] = {}
    for menu_obj in MonthlyMenu.objects.prefetch_related("slots"):
        capacities[menu_obj.key] = sum(
            entry.capacity or 0
            for entry in menu_obj.slots.all()
            if not is_off_day(menu_obj.year, menu_obj.month, entry.slot)
        )

    grouped: dict[tuple[int, int], dict] = {}
    for row in rows:
        key = row["jalali"][:2]
        group = grouped.setdefault(
            key,
            {"people": set(), "meals": 0, "total": 0, "days": set()},
        )
        group["people"].add(row["user_id"])
        group["meals"] += 1
        group["total"] += row["cost"]
        group["days"].add(row["jalali"])

    totals = {
        key: value["total"] for key, value in grouped.items()
    }

    month_rows = []
    for key in sorted(grouped, reverse=True):
        year, month = key
        group = grouped[key]
        previous_year, previous_month = (
            (year, month - 1) if month > 1 else (year - 1, 12)
        )
        previous_total = totals.get((previous_year, previous_month), 0)
        change = None
        if previous_total:
            change = round((group["total"] - previous_total) / previous_total * 100)
        capacity = capacities.get(f"{year}-{month:02d}", 0)
        filled = round(group["meals"] / capacity * 100) if capacity else None
        month_rows.append(
            {
                "year": year,
                "month": month,
                "label": f"{JALALI_MONTHS[month - 1]} {fa_digits(year)}",
                "meals": group["meals"],
                "meals_fa": fa_digits(group["meals"]),
                "people": len(group["people"]),
                "people_fa": fa_digits(len(group["people"])),
                "total": group["total"],
                "total_fa": toman(group["total"]),
                "days": len(group["days"]),
                "days_fa": fa_digits(len(group["days"])),
                "capacity": capacity,
                "capacity_fa": fa_digits(capacity) if capacity else "—",
                "filled": filled,
                "filled_fa": fa_digits(filled) if filled is not None else "—",
                "change": change,
                "change_fa": fa_digits(abs(change)) if change is not None else "—",
                "change_up": bool(change and change > 0),
                "change_down": bool(change and change < 0),
                "previous_total_fa": toman(previous_total) if previous_total else "—",
                "month_param": f"{year}-{month:02d}",
            }
        )

    current = month_rows[0] if month_rows else None
    previous = None
    if current:
        previous = next(
            (
                row
                for row in month_rows
                if (row["year"] * 12 + row["month"]) < (current["year"] * 12 + current["month"])
            ),
            None,
        )

    return render(
        request,
        "panel/finance.html",
        {
            "month_rows": month_rows,
            "current": current,
            "previous": previous,
            "total_income_fa": toman(sum(row["total"] for row in month_rows)),
        },
    )


@staff_required
def payments(request):
    attempts = list(
        PaymentAttempt.objects.select_related("order__user").order_by("-created_at")[:80]
    )
    attempt_rows = []
    for attempt in attempts:
        user = attempt.order.user
        attempt_rows.append(
            {
                "id": attempt.pk,
                "order_id": attempt.order_id,
                "name": _display_name(user),
                "status": attempt.get_status_display(),
                "status_class": {
                    "verified": "tag--brand",
                    "requested": "",
                    "failed": "tag--off",
                    "canceled": "tag--off",
                }.get(attempt.status, ""),
                "amount_fa": attempt.amount_fa,
                "created_fa": attempt.created_fa,
                "authority": attempt.authority,
                "ref_id": attempt.ref_id or "—",
                "code": attempt.gateway_code or "—",
                "message": attempt.message,
            }
        )

    pending = (
        Order.objects.filter(status=Order.Status.PENDING)
        .exclude(items=None)
        .select_related("user")
        .prefetch_related("items")
        .order_by("-created_at")[:40]
    )
    pending_rows = []
    for order in pending:
        items = list(order.items.all())
        pending_rows.append(
            {
                "id": order.pk,
                "name": _display_name(order.user),
                "code_fa": fa_digits(order.user.username),
                "amount_fa": toman(sum(item.cost for item in items)),
                "items_fa": fa_digits(len(items)),
                "authority": order.authority or "",
                "created_fa": format_jalali(timezone.localtime(order.created_at).date()),
                "can_check": bool(order.authority),
            }
        )

    return render(
        request,
        "panel/payments.html",
        {
            "attempts": attempt_rows,
            "pending": pending_rows,
            "is_sandbox": settings.ZARINPAL_SANDBOX,
        },
    )


@staff_required
@require_POST
def payment_check(request, order_id):
    order = get_object_or_404(
        Order, pk=order_id, status=Order.Status.PENDING
    )

    if not order.authority:
        messages.error(request, "این سفارش کد پرداخت (Authority) ندارد.")
        return redirect("panel_payments")

    try:
        result = zarinpal.verify_payment(order.amount * 10, order.authority)
    except zarinpal.ZarinpalError:
        messages.error(request, "ارتباط با درگاه برقرار نشد. بعداً دوباره بررسی کنید.")
        return redirect("panel_payments")

    data = result.get("data") or {}
    code = gateway_code(data)

    if code in (100, 101):
        with transaction.atomic():
            locked = Order.objects.select_for_update().get(pk=order.pk)
            if locked.status != Order.Status.PAID:
                locked.status = Order.Status.PAID
                locked.ref_id = str(data.get("ref_id") or "")[:64]
                locked.paid_at = timezone.now()
                locked.save(update_fields=["status", "ref_id", "paid_at"])
                log_attempt(
                    locked,
                    PaymentAttempt.Status.VERIFIED,
                    gateway_code=code,
                    ref_id=locked.ref_id,
                    message="تأیید دستی توسط مدیر.",
                )
        messages.success(request, "پرداخت تأیید و سفارش پرداخت‌شده شد.")
    else:
        log_attempt(
            order,
            PaymentAttempt.Status.FAILED,
            gateway_code=code,
            message="بررسی دستی: پرداختی یافت نشد.",
        )
        messages.warning(
            request,
            "درگاه پرداختی برای این سفارش ثبت نکرده است. وضعیت سفارش تغییر نکرد.",
        )

    return redirect("panel_payments")


@staff_required
def user_detail(request, user_id):
    target = get_object_or_404(User, pk=user_id)

    if request.method == "POST":
        action = request.POST.get("action")

        if (
            target.is_staff
            and target != request.user
            and not request.user.is_superuser
        ):
            messages.error(
                request,
                "تغییر حساب مدیران دیگر فقط توسط مدیر ارشد امکان‌پذیر است.",
            )
            return redirect("panel_user_detail", user_id=target.pk)

        if action == "password":
            password = request.POST.get("password") or ""
            password_error = _password_error(
                password,
                code=target.username,
                staff=target.is_staff or target.is_superuser,
            )
            if password_error:
                messages.error(request, password_error)
            else:
                target.set_password(password)
                target.save(update_fields=["password"])
                messages.success(request, "رمز عبور کاربر تغییر کرد.")

        elif action == "toggle_active":
            if target == request.user:
                messages.error(request, "نمی‌توانید حساب خودتان را غیرفعال کنید.")
            elif target.is_staff and not target.is_active:
                messages.error(
                    request,
                    "برای فعال‌کردن دوبارهٔ مدیر، از بخش مدیریت پیشرفته استفاده کنید.",
                )
            else:
                target.is_active = not target.is_active
                target.save(update_fields=["is_active"])
                messages.success(
                    request,
                    "کاربر فعال شد." if target.is_active else "کاربر غیرفعال شد.",
                )

        return redirect("panel_user_detail", user_id=target.pk)

    paid_items = OrderItem.objects.filter(
        order__user=target, order__status=Order.Status.PAID
    )
    total = sum(paid_items.values_list("cost", flat=True))
    orders = (
        Order.objects.filter(user=target, status=Order.Status.PAID)
        .prefetch_related("items")
        .order_by("-paid_at")[:10]
    )

    order_rows = []
    for order in orders:
        order_rows.append(
            {
                "id": order.pk,
                "items_fa": fa_digits(order.items.count()),
                "amount_fa": toman(order.amount),
                "ref_id_fa": fa_digits(order.ref_id) if order.ref_id else "—",
                "paid_fa": (
                    format_jalali(timezone.localtime(order.paid_at).date())
                    if order.paid_at
                    else "—"
                ),
            }
        )

    return render(
        request,
        "panel/user_detail.html",
        {
            "target": target,
            "target_name": _display_name(target),
            "code_fa": fa_digits(target.username),
            "is_self": target == request.user,
            "meals_fa": fa_digits(paid_items.count()),
            "total_fa": toman(total),
            "order_rows": order_rows,
            "joined_fa": format_jalali(timezone.localtime(target.date_joined).date()),
            "last_login_fa": (
                format_jalali(timezone.localtime(target.last_login).date())
                if target.last_login
                else "—"
            ),
        },
    )
