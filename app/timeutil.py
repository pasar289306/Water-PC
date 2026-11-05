"""تاریخ و دوره‌های زمانی: تبدیل میلادی ↔ شمسی (الگوریتم jalaali)، تجزیه ورودی کاربر، ساخت دوره‌ها.

همه تاریخ‌ها در پایگاه داده میلادی (ISO) ذخیره می‌شوند؛ تقویم فقط برای ورودی/نمایش و کلید دوره‌های گزارش‌گیری است.
"""
from __future__ import annotations

import calendar as _cal
import re
from datetime import date, datetime, timedelta

GREGORIAN, JALALI = "gregorian", "jalali"
CALENDARS = (GREGORIAN, JALALI)
GRANULARITIES = ("year", "month", "day", "custom")

JALALI_MONTHS = ["فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور",
                 "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند"]
GREGORIAN_MONTHS = ["ژانویه", "فوریه", "مارس", "آوریل", "مه", "ژوئن", "ژوئیه", "اوت", "سپتامبر", "اکتبر", "نوامبر", "دسامبر"]

_BREAKS = [-61, 9, 38, 199, 426, 686, 756, 818, 1111, 1181, 1210, 1635, 2060, 2097, 2192, 2262, 2324, 2394, 2456, 3178]
_JDN_OFFSET = 1721425  # JDN = date.toordinal() + 1721425


class DateError(ValueError):
    pass


def _div(a: int, b: int) -> int:  # تقسیم با قطع به سمت صفر (مانند جاوااسکریپت)
    q = abs(a) // abs(b)
    return q if (a >= 0) == (b > 0) else -q


def _mod(a: int, b: int) -> int:
    return a - _div(a, b) * b


def _jal_cal(jy: int, without_leap: bool = False):
    bl = len(_BREAKS)
    gy = jy + 621
    leap_j = -14
    jp = _BREAKS[0]
    if jy < jp or jy >= _BREAKS[bl - 1]:
        raise DateError(f"سال شمسی {jy} پشتیبانی نمی‌شود")
    jump = 0
    for i in range(1, bl):
        jm = _BREAKS[i]
        jump = jm - jp
        if jy < jm:
            break
        leap_j += _div(jump, 33) * 8 + _div(_mod(jump, 33), 4)
        jp = jm
    n = jy - jp
    leap_j += _div(n, 33) * 8 + _div(_mod(n, 33) + 3, 4)
    if _mod(jump, 33) == 4 and jump - n == 4:
        leap_j += 1
    leap_g = _div(gy, 4) - _div((_div(gy, 100) + 1) * 3, 4) - 150
    march = 20 + leap_j - leap_g
    if without_leap:
        return {"gy": gy, "march": march}
    if jump - n < 6:
        n = n - jump + _div(jump + 4, 33) * 33
    leap = _mod(_mod(n + 1, 33) - 1, 4)
    if leap == -1:
        leap = 4
    return {"leap": leap, "gy": gy, "march": march}


def is_jalali_leap(jy: int) -> bool:
    return _jal_cal(jy)["leap"] == 0


def jalali_month_len(jy: int, jm: int) -> int:
    if not 1 <= jm <= 12:
        raise DateError("ماه شمسی باید بین ۱ تا ۱۲ باشد")
    if jm <= 6:
        return 31
    if jm <= 11:
        return 30
    return 30 if is_jalali_leap(jy) else 29


def j2g(jy: int, jm: int, jd: int) -> date:
    if not (1 <= jm <= 12 and 1 <= jd <= jalali_month_len(jy, jm)):
        raise DateError(f"تاریخ شمسی نامعتبر: {jy}/{jm}/{jd}")
    r = _jal_cal(jy, True)
    mar = date(r["gy"], 3, r["march"]).toordinal() + _JDN_OFFSET
    jdn = mar + (jm - 1) * 31 - _div(jm, 7) * (jm - 7) + jd - 1
    return date.fromordinal(jdn - _JDN_OFFSET)


def g2j(d: date) -> tuple[int, int, int]:
    jdn = d.toordinal() + _JDN_OFFSET
    gy = d.year
    jy = gy - 621
    r = _jal_cal(jy)
    jdn1f = date(gy, 3, r["march"]).toordinal() + _JDN_OFFSET
    k = jdn - jdn1f
    if k >= 0:
        if k <= 185:
            return jy, 1 + _div(k, 31), _mod(k, 31) + 1
        k -= 186
    else:
        jy -= 1
        k += 179
        if r["leap"] == 1:
            k += 1
    return jy, 7 + _div(k, 30), _mod(k, 30) + 1


# ---------------------------------------------------------------- تجزیه و قالب‌دهی
_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩٫٬", "01234567890123456789.,")


def ascii_digits(s) -> str:
    return str(s).translate(_DIGITS).strip()


def need_calendar(cal: str | None) -> str:
    cal = (cal or GREGORIAN).strip().lower()
    if cal not in CALENDARS:
        raise DateError("تقویم باید gregorian یا jalali باشد")
    return cal


def parse_date(value, calendar: str = GREGORIAN) -> date:
    """ورودی: رشته 'YYYY-MM-DD' یا 'YYYY/MM/DD' (ارقام فارسی هم مجاز)، date/datetime یا سریال اکسل (عدد)."""
    calendar = need_calendar(calendar)
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if 20000 < value < 80000:  # سریال تاریخ اکسل (مبدأ 1899-12-30)
            return date(1899, 12, 30) + timedelta(days=int(value))
        raise DateError(f"مقدار «{value}» تاریخ معتبر نیست")
    s = ascii_digits(value)
    if re.fullmatch(r"\d{5}(\.0+)?", s) and 20000 < float(s) < 80000:
        return date(1899, 12, 30) + timedelta(days=int(float(s)))
    m = re.fullmatch(r"(\d{4})\s*[-/.]\s*(\d{1,2})\s*[-/.]\s*(\d{1,2})(?:[T ].*)?", s)
    if not m:
        raise DateError(f"قالب تاریخ «{s}» نامعتبر است (مثال: 1404/03/15 یا 2025-06-05)")
    y, mo, d = (int(x) for x in m.groups())
    try:
        if calendar == JALALI:
            if not 1200 <= y <= 1700:
                raise DateError(f"سال شمسی «{y}» خارج از محدوده است")
            return j2g(y, mo, d)
        if not 1700 <= y <= 2600:
            raise DateError(f"سال میلادی «{y}» خارج از محدوده است")
        return date(y, mo, d)
    except DateError:
        raise
    except ValueError:
        raise DateError(f"تاریخ «{s}» وجود ندارد")


def fmt_date(d: date, calendar: str = GREGORIAN) -> str:
    if need_calendar(calendar) == JALALI:
        y, m, dd = g2j(d)
        return f"{y:04d}/{m:02d}/{dd:02d}"
    return d.isoformat()


def iso(d: date) -> str:
    return d.isoformat()


def from_iso(s: str) -> date:
    return date.fromisoformat(s)


# ---------------------------------------------------------------- ماه و سال در یک تقویم
def month_key(d: date, calendar: str) -> str:
    if need_calendar(calendar) == JALALI:
        y, m, _ = g2j(d)
    else:
        y, m = d.year, d.month
    return f"{y:04d}-{m:02d}"


def month_bounds(key: str, calendar: str) -> tuple[date, date]:
    y, m = (int(x) for x in key.split("-"))
    if need_calendar(calendar) == JALALI:
        return j2g(y, m, 1), j2g(y, m, jalali_month_len(y, m))
    return date(y, m, 1), date(y, m, _cal.monthrange(y, m)[1])


def year_bounds(y: int, calendar: str) -> tuple[date, date]:
    if need_calendar(calendar) == JALALI:
        return j2g(y, 1, 1), j2g(y, 12, jalali_month_len(y, 12))
    return date(y, 1, 1), date(y, 12, 31)


def next_month_key(key: str) -> str:
    y, m = (int(x) for x in key.split("-"))
    return f"{y + (m == 12):04d}-{(m % 12) + 1:02d}"


def month_keys_between(start: date, end: date, calendar: str) -> list[str]:
    keys, k, last = [], month_key(start, calendar), month_key(end, calendar)
    while k <= last:
        keys.append(k)
        k = next_month_key(k)
    return keys


def period_label(start: date, end: date, granularity: str, calendar: str) -> str:
    """برچسب کوتاه یک دوره برای نمایش (در تقویم انتخابی)."""
    if granularity == "year":
        return str(g2j(start)[0] if calendar == JALALI else start.year)
    if granularity == "month":
        return month_key(start, calendar)
    if granularity == "day":
        return fmt_date(start, calendar)
    return f"{fmt_date(start, calendar)} تا {fmt_date(end, calendar)}"


MAX_PERIODS = 4000


def make_periods(start: date, end: date, granularity: str, calendar: str, step_days: int | None = None) -> list[dict]:
    """دوره‌های کامل تقویمی که بازه [start, end] را می‌پوشانند. granularity: year | month | day | custom(+step_days)."""
    calendar = need_calendar(calendar)
    if granularity not in GRANULARITIES:
        raise DateError("granularity باید یکی از year, month, day, custom باشد")
    if end < start:
        raise DateError("تاریخ پایان قبل از تاریخ شروع است")
    out: list[dict] = []
    if granularity == "day":
        d = start
        while d <= end:
            out.append((d, d))
            d += timedelta(days=1)
            if len(out) > MAX_PERIODS:
                raise DateError(f"تعداد دوره‌ها بیش از {MAX_PERIODS} است")
    elif granularity == "month":
        for k in month_keys_between(start, end, calendar):
            out.append(month_bounds(k, calendar))
            if len(out) > MAX_PERIODS:
                raise DateError(f"تعداد دوره‌ها بیش از {MAX_PERIODS} است")
    elif granularity == "year":
        ys = g2j(start)[0] if calendar == JALALI else start.year
        ye = g2j(end)[0] if calendar == JALALI else end.year
        out = [year_bounds(y, calendar) for y in range(ys, ye + 1)]
    else:
        step = int(step_days or 0)
        if step < 1:
            raise DateError("برای بازه دلخواه، طول گام (روز) لازم است")
        d = start
        while d <= end:
            e = min(d + timedelta(days=step - 1), end)
            out.append((d, e))
            d = e + timedelta(days=1)
            if len(out) > MAX_PERIODS:
                raise DateError(f"تعداد دوره‌ها بیش از {MAX_PERIODS} است")
    return [{"start": iso(s), "end": iso(e), "days": (e - s).days + 1,
             "label": period_label(s, e, granularity, calendar)} for s, e in out]


def days_between(start: date, end: date) -> int:
    return (end - start).days + 1
