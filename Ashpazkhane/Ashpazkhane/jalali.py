import math
from datetime import date

JALALI_MONTHS = [
    "فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور",
    "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند",
]

WEEKDAYS = ["شنبه", "یکشنبه", "دوشنبه", "سه‌شنبه", "چهارشنبه", "پنجشنبه", "جمعه"]

OFF_WEEKDAY_INDEXES = {5, 6}

_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")

_BREAKS = [
    -61, 9, 38, 199, 426, 686, 756, 818, 1111, 1181,
    1210, 1635, 2060, 2097, 2192, 2262, 2324, 2394, 2456, 3178,
]


def _tdiv(a: int, b: int) -> int:
    return a // b if a >= 0 else -((-a) // b)


def _fmod(a: int, b: int) -> int:
    return int(math.fmod(a, b))


def _jal_cal(jy: int, without_leap: bool = False) -> dict:
    gy = jy + 621
    leap_j = -14
    jp = _BREAKS[0]
    jump = 0
    jm = 0

    for i in range(1, len(_BREAKS)):
        jm = _BREAKS[i]
        jump = jm - jp
        if jy < jm:
            break
        leap_j += (jump // 33) * 8 + ((jump % 33) // 4)
        jp = jm

    n = jy - jp
    leap_j += (n // 33) * 8 + ((n % 33) + 3) // 4
    if (jump % 33) == 4 and jump - n == 4:
        leap_j += 1

    leap_g = gy // 4 - ((gy // 100 + 1) * 3) // 4 - 150
    march = 20 + leap_j - leap_g

    leap = 0
    if not without_leap:
        if jump - n < 6:
            n = n - jump + ((jump + 4) // 33) * 33
        leap = _fmod(_fmod(n + 1, 33) - 1, 4)
        if leap == -1:
            leap = 4

    return {"leap": leap, "gy": gy, "march": march}


def jalali_is_leap(jy: int) -> bool:
    return _jal_cal(jy)["leap"] == 0


def jalali_month_length(jy: int, jm: int) -> int:
    if jm <= 6:
        return 31
    if jm <= 11:
        return 30
    return 30 if jalali_is_leap(jy) else 29


def _g2d(gy: int, gm: int, gd: int) -> int:
    d = (
        _tdiv((gy + _tdiv(gm - 8, 6) + 100100) * 1461, 4)
        + (153 * ((gm + 9) % 12) + 2) // 5
        + gd
        - 34840408
    )
    d = d - (_tdiv(_tdiv(gy + 100100 + _tdiv(gm - 8, 6), 100) * 3, 4)) + 752
    return d


def _d2g(jdn: int) -> tuple[int, int, int]:
    j = 4 * jdn + 139361631
    j = j + _tdiv(_tdiv(4 * jdn + 183187720, 146097) * 3, 4) * 4 - 3908
    i = _tdiv(_fmod(j, 1461), 4) * 5 + 308
    gd = _tdiv(_fmod(i, 153), 5) + 1
    gm = _fmod(_tdiv(i, 153), 12) + 1
    gy = _tdiv(j, 1461) - 100100 + _tdiv(8 - gm, 6)
    return gy, gm, gd


def _j2d(jy: int, jm: int, jd: int) -> int:
    r = _jal_cal(jy, True)
    return _g2d(r["gy"], 3, r["march"]) + (jm - 1) * 31 - (jm // 7) * (jm - 7) + jd - 1


def jalali_to_gregorian(jy: int, jm: int, jd: int) -> tuple[int, int, int]:
    return _d2g(_j2d(jy, jm, jd))


def gregorian_to_jalali(gy: int, gm: int, gd: int) -> tuple[int, int, int]:
    g_d_m = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]
    if gy > 1600:
        jy = 979
        gy -= 1600
    else:
        jy = 0
        gy -= 621

    gy2 = gy + 1 if gm > 2 else gy
    days = (
        365 * gy
        + (gy2 + 3) // 4
        - (gy2 + 99) // 100
        + (gy2 + 399) // 400
        - 80
        + gd
        + g_d_m[gm - 1]
    )
    jy += 33 * (days // 12053)
    days %= 12053
    jy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        jy += (days - 1) // 365
        days = (days - 1) % 365
    if days < 186:
        jm = 1 + days // 31
        jd = 1 + days % 31
    else:
        jm = 7 + (days - 186) // 30
        jd = 1 + (days - 186) % 30
    return jy, jm, jd


def to_jalali(d: date) -> tuple[int, int, int]:
    return gregorian_to_jalali(d.year, d.month, d.day)


def fa_digits(value) -> str:
    return str(value).translate(_DIGITS)


def fa_number(value, separator: bool = True) -> str:
    text = f"{int(value):,}" if separator else str(int(value))
    return text.replace(",", "٬").translate(_DIGITS)


def toman(value) -> str:
    return f"{fa_number(value)} تومان"


def weekday_index(d: date) -> int:
    return (d.weekday() + 2) % 7


def weekday_name(d: date) -> str:
    return WEEKDAYS[weekday_index(d)]


def jalali_weekday(jy: int, jm: int, jd: int) -> date:
    gy, gm, gd = jalali_to_gregorian(jy, jm, jd)
    return date(gy, gm, gd)


def is_off_day(jy: int, jm: int, jd: int) -> bool:
    return weekday_index(jalali_weekday(jy, jm, jd)) in OFF_WEEKDAY_INDEXES


def format_jalali(d: date, with_year: bool = True, with_weekday: bool = False) -> str:
    jy, jm, jd = to_jalali(d)
    parts = []
    if with_weekday:
        parts.append(weekday_name(d))
    parts.append(f"{fa_digits(jd)} {JALALI_MONTHS[jm - 1]}")
    if with_year:
        parts.append(fa_digits(jy))
    return " ".join(parts)


def format_jalali_parts(jy: int, jm: int, jd: int, with_weekday: bool = False) -> str:
    parts = []
    if with_weekday:
        parts.append(weekday_name(jalali_weekday(jy, jm, jd)))
    parts.append(f"{fa_digits(jd)} {JALALI_MONTHS[jm - 1]} {fa_digits(jy)}")
    return " ".join(parts)


def month_label(menu) -> str:
    try:
        return f"{JALALI_MONTHS[menu.month - 1]} {fa_digits(menu.year)}"
    except (IndexError, TypeError):
        return f"{menu.year}/{menu.month:02d}"
