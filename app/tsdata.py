"""لایه سری زمانی.

منبع حقیقت: جدول ts_entries (هر ردیف یک مقدار برای بازه [start_date, end_date] با تاریخ دقیق میلادی و دانه‌بندی
سالانه/ماهانه/روزانه/دلخواه). از روی آن، «سطل‌های ماهانه» (ts_buckets) در تقویم گزارش‌گیری مجتمع ساخته می‌شود و جداول ماهانه
قدیمی (readings, production, storage_changes) که بقیه نرم‌افزار می‌خوانند همگام می‌شوند. توزیع بازه‌های بلندتر یا ناهم‌تراز با ماه
به‌صورت متناسب با تعداد روزها (pro-rata) انجام می‌شود و نسبت پوشش (coverage) برای هشدار داده‌های ناقص ثبت می‌شود.

همه چهار مسیر ورود داده (دستی، اکسل/CSV، API، کنتور مستقیم) از همین تابع save_entries عبور می‌کنند.
"""
from __future__ import annotations

import math
import re
from datetime import date

from . import timeutil as tu
from .db import db, rows, one

QUALITIES = ("valid", "estimated", "suspect", "invalid", "missing")
_Q_RANK = {"valid": 0, "estimated": 1, "suspect": 2}
BAD = ("invalid", "missing")
SOURCES = ("manual", "excel", "csv", "api", "meter", "legacy")
MAX_SPAN_DAYS = 3660
MAX_BATCH = 50000

# code, نام, واحد, agg, entity, nonneg, گروه, توضیح
BUILTIN_VARIABLES = [
    ("stream_volume", "حجم جریان آب", "m³", "sum", "stream", 1, "water", "جریان‌های ورودی/خروجی مجتمع؛ قابل ثبت به تفکیک کنتور"),
    ("production", "تولید صنعتی", "واحد محصول", "sum", "site", 1, "production", "مجموع تولید در بازه"),
    ("operating_hours", "ساعات کارکرد", "h", "sum", "site", 1, "production", "ساعت کارکرد مؤثر واحد"),
    ("ambient_temp", "دمای محیط", "°C", "mean", "site", 0, "weather", "میانگین دما در بازه"),
    ("storage_delta", "تغییر موجودی مخازن", "m³", "sum", "site", 0, "water", "ΔV؛ مثبت = افزایش ذخیره"),
    ("energy_kwh", "انرژی الکتریکی مصرفی", "kWh", "sum", "site", 1, "energy", "برای شدت انرژی و پمپاژ"),
    ("rainfall_mm", "بارندگی", "mm", "sum", "site", 1, "weather", "ورودی محاسبه آبیاری و آب باران"),
    ("et0_mm", "تبخیر-تعرق مرجع (ET₀)", "mm", "sum", "site", 1, "weather", "ورودی محاسبه نیاز آبیاری"),
    ("persons", "تعداد افراد/کارکنان", "نفر", "mean", "site", 1, "sanitary", "برای مصرف سرانه بهداشتی"),
]
_VAR_RE = re.compile(r"^[a-z][a-z0-9_]{1,40}$")
_SKIP_PARAMS = {"V", "t"}  # V همان حجم جریان؛ t همان ساعات کارکرد


class TsError(ValueError):
    """خطای قابل‌نمایش به کاربر (فارسی)."""


def seed_variables():
    with db() as c:
        for code, fa, unit, agg, ent, nn, grp, note in BUILTIN_VARIABLES:
            c.execute("""INSERT INTO variables(code,name_fa,unit,agg,entity,nonneg,grp,note,source) VALUES(?,?,?,?,?,?,?,?,'builtin')
                         ON CONFLICT(code) DO UPDATE SET name_fa=excluded.name_fa, unit=excluded.unit, agg=excluded.agg,
                         entity=excluded.entity, nonneg=excluded.nonneg, grp=excluded.grp, note=excluded.note
                         WHERE variables.source='builtin'""", (code, fa, unit, agg, ent, nn, grp, note))
        for p in rows(c.execute("SELECT * FROM parameters")):
            if p["code"] in _SKIP_PARAMS:
                continue
            c.execute("""INSERT OR IGNORE INTO variables(code,name_fa,unit,agg,entity,nonneg,grp,note,source)
                         VALUES(?,?,?,'mean','stream',1,?,?,'builtin')""",
                      ("param_" + p["code"], p["name"], p["unit"], "quality:" + (p["grp"] or ""), p["note"] or ""))


def site_calendar(c, site_id: int) -> str:
    r = one(c.execute("SELECT calendar FROM sites WHERE id=?", (site_id,)))
    return (r or {}).get("calendar") or tu.GREGORIAN


# ---------------------------------------------------------------- نرمال‌سازی یک ردیف ورودی
def to_float(v, what="مقدار") -> float:
    if isinstance(v, bool):
        raise TsError(f"{what} عددی نیست")
    if isinstance(v, (int, float)):
        x = float(v)
    else:
        s = tu.ascii_digits(v).replace(",", "").replace(" ", "")
        if s == "":
            raise TsError(f"{what} خالی است")
        try:
            x = float(s)
        except ValueError:
            raise TsError(f"{what} «{v}» عددی نیست")
    if not math.isfinite(x):
        raise TsError(f"{what} باید عدد متناهی باشد")
    return x


def _lookup_stream(c, site_id: int, ref) -> dict:
    if isinstance(ref, int) or (isinstance(ref, str) and ref.strip().isdigit()):
        r = one(c.execute("SELECT * FROM streams WHERE id=? AND site_id=?", (int(ref), site_id)))
    else:
        r = one(c.execute("SELECT * FROM streams WHERE name=? AND site_id=?", (str(ref).strip(), site_id)))
    if not r:
        raise TsError(f"جریان «{ref}» در این مجتمع تعریف نشده است")
    return r


def _lookup_meter(c, site_id: int, ref) -> dict:
    if isinstance(ref, int):
        r = one(c.execute("SELECT * FROM meters WHERE id=? AND site_id=?", (ref, site_id)))
    else:
        r = one(c.execute("SELECT * FROM meters WHERE code=? AND site_id=?", (str(ref).strip(), site_id)))
    if not r:
        raise TsError(f"کنتور «{ref}» در این مجتمع تعریف نشده است")
    return r


def detect_granularity(s: date, e: date, calendar: str) -> str:
    if s == e:
        return "day"
    if (s, e) == tu.month_bounds(tu.month_key(s, calendar), calendar):
        return "month"
    y = tu.g2j(s)[0] if calendar == tu.JALALI else s.year
    if (s, e) == tu.year_bounds(y, calendar):
        return "year"
    return "custom"


def resolve_range(item: dict, default_calendar: str) -> tuple[date, date, str]:
    """بازه یک ردیف: (start,end) صریح، یا period='YYYY'/'YYYY-MM'، یا start+granularity، یا فقط start (روز)."""
    cal = tu.need_calendar(item.get("calendar") or default_calendar)
    gran = item.get("granularity")
    if gran not in (None, "", *tu.GRANULARITIES):
        raise TsError("granularity باید year, month, day یا custom باشد")
    if item.get("start") not in (None, ""):
        s = tu.parse_date(item["start"], cal)
        if item.get("end") not in (None, ""):
            e = tu.parse_date(item["end"], cal)
        elif gran in ("year", "month"):
            p = tu.make_periods(s, s, gran, cal)[0]
            s, e = tu.from_iso(p["start"]), tu.from_iso(p["end"])
        else:
            e = s
    elif item.get("period"):
        per = tu.ascii_digits(item["period"])
        if re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", per):
            s, e = tu.month_bounds(per, cal)
        elif re.fullmatch(r"\d{4}", per):
            s, e = tu.year_bounds(int(per), cal)
        else:
            raise TsError("قالب دوره باید YYYY-MM یا YYYY باشد")
    else:
        raise TsError("تاریخ شروع (start) یا دوره (period) لازم است")
    if e < s:
        raise TsError("تاریخ پایان قبل از تاریخ شروع است")
    if (e - s).days + 1 > MAX_SPAN_DAYS:
        raise TsError(f"طول بازه بیش از {MAX_SPAN_DAYS} روز است")
    return s, e, gran or detect_granularity(s, e, cal)


def normalize(c, site_id: int, item: dict, default_calendar: str, variables: dict) -> dict:
    var = (item.get("variable") or "stream_volume").strip()
    v = variables.get(var)
    if not v:
        raise TsError(f"متغیر «{var}» تعریف نشده است")
    s, e, gran = resolve_range(item, default_calendar)
    stream_id = meter_id = 0
    has_stream = item.get("stream_id") not in (None, "") or item.get("stream") not in (None, "")
    has_meter = item.get("meter_id") not in (None, "") or item.get("meter") not in (None, "")
    if v["entity"] == "stream":
        if has_meter:
            m = _lookup_meter(c, site_id, item["meter_id"] if item.get("meter_id") not in (None, "") else item["meter"])
            if var != "stream_volume":
                raise TsError("ثبت به تفکیک کنتور فقط برای «حجم جریان آب» مجاز است")
            if has_stream:
                st = _lookup_stream(c, site_id, item["stream_id"] if item.get("stream_id") not in (None, "") else item["stream"])
                if st["id"] != m["stream_id"]:
                    raise TsError(f"کنتور «{m['code']}» متعلق به جریان دیگری است")
            stream_id, meter_id = m["stream_id"], m["id"]
        elif has_stream:
            st = _lookup_stream(c, site_id, item["stream_id"] if item.get("stream_id") not in (None, "") else item["stream"])
            stream_id = st["id"]
        else:
            raise TsError(f"برای «{v['name_fa']}» باید جریان (stream) یا کنتور (meter) مشخص شود")
    elif has_stream or has_meter:
        raise TsError(f"«{v['name_fa']}» متغیر سطح مجتمع است؛ جریان/کنتور نباید داده شود")
    val = to_float(item.get("value"), "مقدار")
    corr = item.get("corrected_value")
    corr = None if corr in (None, "") else to_float(corr, "مقدار اصلاح‌شده")
    unc = item.get("uncertainty")
    unc = 0.0 if unc in (None, "") else to_float(unc, "عدم قطعیت")
    if v["nonneg"] and (val < 0 or (corr is not None and corr < 0)):
        raise TsError(f"«{v['name_fa']}» نمی‌تواند منفی باشد")
    if unc < 0:
        raise TsError("عدم قطعیت نمی‌تواند منفی باشد")
    q = (item.get("quality") or "valid").strip()
    if q not in QUALITIES:
        raise TsError(f"وضعیت داده باید یکی از {list(QUALITIES)} باشد")
    return {"variable": var, "stream_id": stream_id, "meter_id": meter_id, "start": tu.iso(s), "end": tu.iso(e),
            "granularity": gran, "value": val, "corrected_value": corr, "uncertainty": unc, "quality": q,
            "note": str(item.get("note") or "")[:300]}


def _variables(c) -> dict:
    return {r["code"]: r for r in rows(c.execute("SELECT * FROM variables"))}


# ---------------------------------------------------------------- ذخیره
def _find_conflicts(c, site_id: int, e: dict) -> list[dict]:
    hits = rows(c.execute("""SELECT * FROM ts_entries WHERE site_id=? AND variable=? AND stream_id=?
                             AND start_date<=? AND end_date>=?""",
                          (site_id, e["variable"], e["stream_id"], e["end"], e["start"])))
    out = []
    for h in hits:
        if h["meter_id"] == e["meter_id"] and h["start_date"] == e["start"] and h["end_date"] == e["end"]:
            continue  # همان بازه و همان کنتور: به‌روزرسانی (upsert)
        # دو کنتور متفاوت (هر دو مشخص) جمع می‌شوند؛ در غیر این صورت هم‌پوشانی ناسازگار است
        if h["meter_id"] != e["meter_id"] and h["meter_id"] != 0 and e["meter_id"] != 0:
            continue
        out.append(h)
    return out


def save_entries(site_id: int, items: list[dict], source: str, user_id: int | None, calendar: str | None = None,
                 replace_overlaps: bool = False, c=None) -> dict:
    """ذخیره دسته‌ای. هر ردیف مستقل بررسی می‌شود؛ ردیف‌های معتبر ذخیره و خطاها با شماره ردیف برگردانده می‌شود."""
    if source not in SOURCES:
        raise TsError("منبع ورود داده نامعتبر است")
    if len(items) > MAX_BATCH:
        raise TsError(f"حداکثر {MAX_BATCH} ردیف در هر درخواست مجاز است")
    if c is None:
        with db() as c2:
            return save_entries(site_id, items, source, user_id, calendar, replace_overlaps, c=c2)
    if True:
        cal_site = site_calendar(c, site_id)
        default_cal = tu.need_calendar(calendar or cal_site)
        variables = _variables(c)
        keys: set[tuple] = set()
        errors, saved = [], 0
        for i, item in enumerate(items, start=1):
            try:
                if not isinstance(item, dict):
                    raise TsError("هر ردیف باید یک شیء باشد")
                e = normalize(c, site_id, item, default_cal, variables)
                conf = _find_conflicts(c, site_id, e)
                if conf and not replace_overlaps:
                    h = conf[0]
                    raise TsError(f"با داده ثبت‌شده‌ی بازه {h['start_date']} تا {h['end_date']} هم‌پوشانی دارد "
                                  "(همان بازه را ثبت کنید یا ابتدا آن را حذف/جایگزین کنید)")
                for h in conf:
                    c.execute("DELETE FROM ts_entries WHERE id=?", (h["id"],))
                    keys.add((h["variable"], h["stream_id"], h["meter_id"]))
                c.execute("""INSERT INTO ts_entries(site_id,variable,stream_id,meter_id,start_date,end_date,granularity,value,
                             corrected_value,uncertainty,quality,source,note,entered_by) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                             ON CONFLICT(site_id,variable,stream_id,meter_id,start_date,end_date) DO UPDATE SET
                             granularity=excluded.granularity, value=excluded.value, corrected_value=excluded.corrected_value,
                             uncertainty=excluded.uncertainty, quality=excluded.quality, source=excluded.source,
                             note=excluded.note, entered_by=excluded.entered_by, entered_at=CURRENT_TIMESTAMP""",
                          (site_id, e["variable"], e["stream_id"], e["meter_id"], e["start"], e["end"], e["granularity"],
                           e["value"], e["corrected_value"], e["uncertainty"], e["quality"], source, e["note"], user_id))
                keys.add((e["variable"], e["stream_id"], e["meter_id"]))
                saved += 1
            except (TsError, tu.DateError) as ex:
                errors.append({"line": i, "error": str(ex)})
                if len(errors) > 200:
                    errors.append({"line": i, "error": "تعداد خطاها زیاد است؛ ادامه داده نشد"})
                    break
        if keys:
            rebuild(c, site_id, keys)
        return {"saved": saved, "errors": errors}


def delete_entries(site_id: int, ids: list[int]) -> int:
    with db() as c:
        keys, n = set(), 0
        for i in ids:
            r = one(c.execute("SELECT * FROM ts_entries WHERE id=? AND site_id=?", (i, site_id)))
            if r:
                c.execute("DELETE FROM ts_entries WHERE id=?", (i,))
                keys.add((r["variable"], r["stream_id"], r["meter_id"]))
                n += 1
        if keys:
            rebuild(c, site_id, keys)
    return n


# ---------------------------------------------------------------- ساخت سطل‌های ماهانه
def _rebuild_series(c, site_id: int, var: str, stream_id: int, meter_id: int, cal: str, agg: str):
    ents = rows(c.execute("""SELECT * FROM ts_entries WHERE site_id=? AND variable=? AND stream_id=? AND meter_id=?
                             ORDER BY start_date""", (site_id, var, stream_id, meter_id)))
    buckets: dict[str, dict] = {}
    for e in ents:
        s, en = tu.from_iso(e["start_date"]), tu.from_iso(e["end_date"])
        total = tu.days_between(s, en)
        for key in tu.month_keys_between(s, en, cal):
            bs, be = tu.month_bounds(key, cal)
            od = tu.days_between(max(s, bs), min(en, be))
            b = buckets.setdefault(key, {"days": 0, "bdays": tu.days_between(bs, be), "val": 0.0, "corr": 0.0, "has_corr": False,
                                         "unc": 0.0, "wd": 0, "n": 0, "rank": 0, "bad": set(), "src": set()})
            if e["quality"] in BAD:
                b["bad"].add(e["quality"])
                continue
            share = od / total
            corr = e["corrected_value"] if e["corrected_value"] is not None else e["value"]
            if agg == "sum":
                b["val"] += e["value"] * share
                b["corr"] += corr * share
                b["unc"] += (e["uncertainty"] or 0.0) * share
            else:
                b["val"] += e["value"] * od
                b["corr"] += corr * od
                b["unc"] += (e["uncertainty"] or 0.0) * od
            b["has_corr"] = b["has_corr"] or e["corrected_value"] is not None
            b["days"] += od
            b["n"] += 1
            b["rank"] = max(b["rank"], _Q_RANK[e["quality"]])
            b["src"].add(e["source"])
    c.execute("DELETE FROM ts_buckets WHERE site_id=? AND variable=? AND stream_id=? AND meter_id=?",
              (site_id, var, stream_id, meter_id))
    for key, b in buckets.items():
        if b["days"] == 0:  # فقط ردیف‌های نامعتبر/ناموجود
            q = "invalid" if "invalid" in b["bad"] else "missing"
            c.execute("INSERT INTO ts_buckets(site_id,variable,stream_id,meter_id,period,value,corrected,coverage,n,quality,source)"
                      " VALUES(?,?,?,?,?,0,NULL,0,0,?,?)", (site_id, var, stream_id, meter_id, key, q, ""))
            continue
        cov = min(1.0, b["days"] / b["bdays"])
        val, corr, unc = b["val"], b["corr"], b["unc"]
        if agg == "mean":
            val, corr, unc = val / b["days"], corr / b["days"], unc / b["days"]
        rank = b["rank"]
        if (cov < 0.999 or b["bad"]) and rank < 1:
            rank = 1
        q = ("valid", "estimated", "suspect")[rank]
        src = next(iter(b["src"])) if len(b["src"]) == 1 else "mixed"
        c.execute("""INSERT INTO ts_buckets(site_id,variable,stream_id,meter_id,period,value,corrected,uncertainty,coverage,n,quality,source)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                  (site_id, var, stream_id, meter_id, key, val, corr if b["has_corr"] else None, unc, cov, b["n"], q, src))


def _sync_readings(c, site_id: int, stream_id: int):
    bs = rows(c.execute("SELECT * FROM ts_buckets WHERE site_id=? AND variable='stream_volume' AND stream_id=?",
                        (site_id, stream_id)))
    by: dict[str, list] = {}
    for b in bs:
        by.setdefault(b["period"], []).append(b)
    c.execute("DELETE FROM readings WHERE stream_id=?", (stream_id,))
    for period, lst in sorted(by.items()):
        use = [b for b in lst if b["quality"] not in BAD]
        if not use:
            q = "invalid" if any(b["quality"] == "invalid" for b in lst) else "missing"
            c.execute("INSERT INTO readings(stream_id,period,volume,quality,source,coverage) VALUES(?,?,0,?,'mixed',0)",
                      (stream_id, period, q))
            continue
        vol = sum(b["value"] for b in use)
        corr = sum(b["corrected"] if b["corrected"] is not None else b["value"] for b in use) \
            if any(b["corrected"] is not None for b in use) else None
        rank = max(_Q_RANK[b["quality"]] for b in use)
        if len(use) < len(lst) and rank < 1:
            rank = 1
        src = {b["source"] for b in use}
        c.execute("INSERT INTO readings(stream_id,period,volume,corrected_volume,quality,source,coverage) VALUES(?,?,?,?,?,?,?)",
                  (stream_id, period, vol, corr, ("valid", "estimated", "suspect")[rank],
                   next(iter(src)) if len(src) == 1 else "mixed", min(b["coverage"] for b in use)))


def _sync_site_tables(c, site_id: int):
    def get(var):
        return {b["period"]: b for b in c.execute(
            "SELECT * FROM ts_buckets WHERE site_id=? AND variable=? AND stream_id=0 AND meter_id=0", (site_id, var))
            if b["quality"] not in BAD}
    prod, temp, hrs, sto = get("production"), get("ambient_temp"), get("operating_hours"), get("storage_delta")
    c.execute("DELETE FROM production WHERE site_id=?", (site_id,))
    for period, b in sorted(prod.items()):
        c.execute("INSERT INTO production(site_id,period,product_qty,ambient_temp,operating_hours) VALUES(?,?,?,?,?)",
                  (site_id, period, b["value"], temp[period]["value"] if period in temp else None,
                   hrs[period]["value"] if period in hrs else None))
    c.execute("DELETE FROM storage_changes WHERE site_id=?", (site_id,))
    for period, b in sorted(sto.items()):
        c.execute("INSERT INTO storage_changes(site_id,period,delta_volume,uncertainty) VALUES(?,?,?,?)",
                  (site_id, period, b["value"], b["uncertainty"] or 0.0))


def rebuild(c, site_id: int, keys: set[tuple] | None = None):
    """بازسازی سطل‌ها و جداول ماهانه برای مجموعه‌ای از سری‌ها (variable, stream_id, meter_id)؛ None = همه."""
    cal = site_calendar(c, site_id)
    variables = _variables(c)
    if keys is None:
        keys = {(r["variable"], r["stream_id"], r["meter_id"]) for r in c.execute(
            "SELECT DISTINCT variable, stream_id, meter_id FROM ts_entries WHERE site_id=?", (site_id,))}
        keys |= {(r["variable"], r["stream_id"], r["meter_id"]) for r in c.execute(
            "SELECT DISTINCT variable, stream_id, meter_id FROM ts_buckets WHERE site_id=?", (site_id,))}
        streams_all = [r["id"] for r in c.execute("SELECT id FROM streams WHERE site_id=?", (site_id,))]
    else:
        streams_all = []
    streams, site_level = set(streams_all), False
    for var, st, mt in keys:
        agg = (variables.get(var) or {"agg": "sum"})["agg"]
        _rebuild_series(c, site_id, var, st, mt, cal, agg)
        if var == "stream_volume" and st:
            streams.add(st)
        if st == 0:
            site_level = True
    for st in streams:
        if one(c.execute("SELECT 1 x FROM streams WHERE id=?", (st,))):
            _sync_readings(c, site_id, st)
    if site_level or keys is None:
        _sync_site_tables(c, site_id)


def set_site_calendar(site_id: int, calendar: str):
    cal = tu.need_calendar(calendar)
    with db() as c:
        c.execute("UPDATE sites SET calendar=? WHERE id=?", (cal, site_id))
        rebuild(c, site_id, None)


# ---------------------------------------------------------------- مهاجرت داده‌های ماهانه قدیمی
def backfill_site(c, site_id: int):
    """تبدیل ردیف‌های ماهانه قدیمی (readings/production/storage_changes) به ts_entries؛ فقط یک‌بار و فقط اگر ts_entries خالی است."""
    mk = f"ts_backfill:{site_id}"
    if one(c.execute("SELECT 1 x FROM meta WHERE key=?", (mk,))) or \
            one(c.execute("SELECT 1 x FROM ts_entries WHERE site_id=? LIMIT 1", (site_id,))):
        c.execute("INSERT OR IGNORE INTO meta(key,value) VALUES(?,'1')", (mk,))
        return 0
    cal = site_calendar(c, site_id)
    n = 0

    def add(var, stream_id, period, value, **kw):
        nonlocal n
        try:
            s, e = tu.month_bounds(period, cal)
        except Exception:
            return
        c.execute("""INSERT OR IGNORE INTO ts_entries(site_id,variable,stream_id,meter_id,start_date,end_date,granularity,value,
                     corrected_value,uncertainty,quality,source) VALUES(?,?,?,0,?,?,'month',?,?,?,?,?)""",
                  (site_id, var, stream_id, tu.iso(s), tu.iso(e), value, kw.get("corr"), kw.get("unc", 0.0),
                   kw.get("quality", "valid"), kw.get("source", "legacy")))
        n += 1

    for r in rows(c.execute("SELECT r.* FROM readings r JOIN streams s ON s.id=r.stream_id WHERE s.site_id=?", (site_id,))):
        src = r["source"] if r["source"] in SOURCES else "legacy"
        add("stream_volume", r["stream_id"], r["period"], r["volume"], corr=r["corrected_volume"], quality=r["quality"], source=src)
    for p in rows(c.execute("SELECT * FROM production WHERE site_id=?", (site_id,))):
        add("production", 0, p["period"], p["product_qty"])
        if p["ambient_temp"] is not None:
            add("ambient_temp", 0, p["period"], p["ambient_temp"])
        if p["operating_hours"] is not None:
            add("operating_hours", 0, p["period"], p["operating_hours"])
    for s in rows(c.execute("SELECT * FROM storage_changes WHERE site_id=?", (site_id,))):
        add("storage_delta", 0, s["period"], s["delta_volume"], unc=s["uncertainty"] or 0.0)
    c.execute("INSERT OR IGNORE INTO meta(key,value) VALUES(?,'1')", (mk,))
    if n:
        rebuild(c, site_id, None)
    return n


def backfill_all():
    with db() as c:
        total = 0
        for s in rows(c.execute("SELECT id FROM sites")):
            total += backfill_site(c, s["id"])
    return total
