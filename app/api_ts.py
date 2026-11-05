"""مسیرهای API سری‌های زمانی، ورود داده (دستی/فایل/API/کنتور مستقیم)، کنتورها و کلیدهای API."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import secrets

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from . import auth, meters as mt, timeutil as tu, tsdata, xlsx
from .db import db, rows, one

S = "/api/sites/{site_id:int}"
MAX_UPLOAD = 5 * 1024 * 1024
QUAL_ALIASES = {"معتبر": "valid", "تخمینی": "estimated", "مشکوک": "suspect", "نامعتبر": "invalid", "ناموجود": "missing"}
HEADER_ALIASES = {"جریان": "stream", "کنتور": "meter", "متغیر": "variable", "شروع": "start", "تاریخ": "start", "پایان": "end",
                  "مقدار": "value", "وضعیت": "quality", "دوره": "period", "حجم": "volume", "تقویم": "calendar",
                  "مقدار اصلاح شده": "corrected_value", "توضیح": "note", "date": "start"}


def _m():
    from . import main as m  # وارد کردن تنبل برای جلوگیری از وابستگی حلقوی
    return m


def _err(status: int, msg: str):
    return JSONResponse({"detail": msg}, status_code=status)


def _calendar(request: Request, d: dict | None = None) -> str | None:
    cal = (d or {}).get("calendar") or request.query_params.get("calendar")
    if cal:
        try:
            return tu.need_calendar(cal)
        except tu.DateError as e:
            raise _m().HTTPException(422, str(e))
    return None


# ---------------------------------------------------------------- متغیرها
def list_variables(request: Request):
    auth.require(request)
    with db() as c:
        return JSONResponse(rows(c.execute("SELECT * FROM variables ORDER BY source='builtin' DESC, grp, code")))


async def create_variable(request: Request):
    m = _m()
    u = auth.require(request, "engineer")
    d = await m.body(request)
    code = str(d.get("code", "")).strip()
    if not tsdata._VAR_RE.match(code) or code.startswith("param_") or code in {v[0] for v in tsdata.BUILTIN_VARIABLES}:
        raise m.HTTPException(422, "کد متغیر باید با حرف انگلیسی کوچک شروع شود (a-z، رقم، _) و با متغیرهای داخلی تکراری نباشد")
    if not str(d.get("name_fa", "")).strip():
        raise m.HTTPException(422, "نام فارسی لازم است")
    agg = d.get("agg", "sum")
    ent = d.get("entity", "site")
    if agg not in ("sum", "mean") or ent not in ("site", "stream"):
        raise m.HTTPException(422, "agg باید sum یا mean و entity باید site یا stream باشد")
    with db() as c:
        c.execute("""INSERT INTO variables(code,name_fa,unit,agg,entity,nonneg,grp,note,source) VALUES(?,?,?,?,?,?,?,?,'user')
                     ON CONFLICT(code) DO UPDATE SET name_fa=excluded.name_fa, unit=excluded.unit, agg=excluded.agg,
                     entity=excluded.entity, nonneg=excluded.nonneg, grp=excluded.grp, note=excluded.note
                     WHERE variables.source='user'""",
                  (code, d["name_fa"].strip(), str(d.get("unit", "")), agg, ent, 1 if d.get("nonneg", True) else 0,
                   str(d.get("grp", "custom")), str(d.get("note", ""))))
    auth.log(u, "upsert", "variable", code)
    return JSONResponse({"ok": True})


# ---------------------------------------------------------------- ثبت و فهرست
def _tserr(e: Exception):
    return _m().HTTPException(422, str(e))


async def post_entries(request: Request):
    m = _m()
    u = auth.require(request, "engineer")
    site_id = m.sid(request)
    m.get_site(site_id)
    d = await m.body(request)
    items = d.get("items", d.get("entries"))
    if not isinstance(items, list) or not items:
        raise m.HTTPException(422, "فهرست items خالی است")
    try:
        res = tsdata.save_entries(site_id, items, "manual", u["id"], _calendar(request, d), bool(d.get("replace_overlaps")))
    except (tsdata.TsError, tu.DateError) as e:
        raise _tserr(e)
    auth.log(u, "upsert", "ts_entries", site_id, f"{res['saved']} rows")
    return JSONResponse(res, status_code=200 if res["saved"] or not res["errors"] else 422)


async def delete_entries(request: Request):
    m = _m()
    u = auth.require(request, "engineer")
    site_id = m.sid(request)
    m.get_site(site_id)
    d = await m.body(request)
    ids = d.get("ids")
    if not isinstance(ids, list) or not all(isinstance(i, int) for i in ids) or not ids:
        raise m.HTTPException(422, "ids باید فهرست شناسه‌های عددی باشد")
    n = tsdata.delete_entries(site_id, ids)
    auth.log(u, "delete", "ts_entries", site_id, f"{n} rows")
    return JSONResponse({"deleted": n})


def list_entries(request: Request):
    m = _m()
    auth.require(request)
    site_id = m.sid(request)
    m.get_site(site_id)
    q = request.query_params
    sql = """SELECT e.*, s.name AS stream_name, mt.code AS meter_code, v.name_fa AS variable_name, v.unit AS unit
             FROM ts_entries e LEFT JOIN streams s ON s.id=e.stream_id LEFT JOIN meters mt ON mt.id=e.meter_id
             LEFT JOIN variables v ON v.code=e.variable WHERE e.site_id=?"""
    args: list = [site_id]
    for key, col in (("variable", "e.variable"), ("stream_id", "e.stream_id"), ("meter_id", "e.meter_id"), ("source", "e.source")):
        if q.get(key):
            sql += f" AND {col}=?"
            args.append(q[key])
    with db() as c0:
        cal = _calendar(request) or tsdata.site_calendar(c0, site_id)
    try:
        if q.get("from"):
            sql += " AND e.end_date>=?"
            args.append(tu.iso(tu.parse_date(q["from"], cal)))
        if q.get("to"):
            sql += " AND e.start_date<=?"
            args.append(tu.iso(tu.parse_date(q["to"], cal)))
    except tu.DateError as e:
        raise _tserr(e)
    limit = min(int(q.get("limit", 1000)), 5000)
    offset = max(int(q.get("offset", 0)), 0)
    with db() as c:
        total = one(c.execute("SELECT COUNT(*) n FROM (" + sql + ")", args))["n"]
        data = rows(c.execute(sql + " ORDER BY e.start_date DESC, e.variable, e.id DESC LIMIT ? OFFSET ?", (*args, limit, offset)))
    for r in data:
        r["start_fmt"] = tu.fmt_date(tu.from_iso(r["start_date"]), cal)
        r["end_fmt"] = tu.fmt_date(tu.from_iso(r["end_date"]), cal)
    return JSONResponse({"total": total, "calendar": cal, "items": data})


async def make_periods(request: Request):
    m = _m()
    auth.require(request)
    d = await m.body(request)
    try:
        cal = tu.need_calendar(d.get("calendar"))
        s, e = tu.parse_date(d.get("start"), cal), tu.parse_date(d.get("end"), cal)
        ps = tu.make_periods(s, e, d.get("granularity", "month"), cal, d.get("step_days"))
    except tu.DateError as ex:
        raise _tserr(ex)
    return JSONResponse({"calendar": cal, "periods": ps})


async def convert_date(request: Request):
    """تبدیل تاریخ بین تقویم‌ها (برای رابط کاربری و کلاینت‌های API)."""
    m = _m()
    auth.require(request)
    d = await m.body(request)
    try:
        src = tu.need_calendar(d.get("from_calendar"))
        dst = tu.need_calendar(d.get("to_calendar"))
        g = tu.parse_date(d.get("date"), src)
        return JSONResponse({"iso": tu.iso(g), "date": tu.fmt_date(g, dst), "calendar": dst})
    except tu.DateError as ex:
        raise _tserr(ex)


def list_buckets(request: Request):
    m = _m()
    auth.require(request)
    site_id = m.sid(request)
    m.get_site(site_id)
    q = request.query_params
    sql = """SELECT b.*, s.name AS stream_name FROM ts_buckets b LEFT JOIN streams s ON s.id=b.stream_id WHERE b.site_id=?"""
    args: list = [site_id]
    if q.get("variable"):
        sql += " AND b.variable=?"
        args.append(q["variable"])
    if q.get("stream_id"):
        sql += " AND b.stream_id=?"
        args.append(q["stream_id"])
    with db() as c:
        return JSONResponse(rows(c.execute(sql + " ORDER BY b.period, b.variable, b.stream_id, b.meter_id LIMIT 20000", args)))


def coverage(request: Request):
    """خلاصه کامل‌بودن داده هر متغیر/جریان: اولین و آخرین دوره، تعداد دوره‌ها و تعداد دوره‌های ناقص؛ و شکاف‌ها."""
    m = _m()
    auth.require(request)
    site_id = m.sid(request)
    m.get_site(site_id)
    with db() as c:
        data = rows(c.execute("""SELECT b.variable, b.stream_id, s.name AS stream_name, v.name_fa AS variable_name,
              MIN(b.period) first_period, MAX(b.period) last_period, COUNT(DISTINCT b.period) n_periods,
              SUM(CASE WHEN b.coverage<0.999 THEN 1 ELSE 0 END) n_partial
              FROM ts_buckets b LEFT JOIN streams s ON s.id=b.stream_id LEFT JOIN variables v ON v.code=b.variable
              WHERE b.site_id=? GROUP BY b.variable, b.stream_id ORDER BY b.variable, b.stream_id""", (site_id,)))
        for r in data:
            pers = {x["period"] for x in c.execute("SELECT period FROM ts_buckets WHERE site_id=? AND variable=? AND stream_id=?",
                                                    (site_id, r["variable"], r["stream_id"]))}
            gaps, k = [], r["first_period"]
            while k < r["last_period"]:
                if k not in pers:
                    gaps.append(k)
                k = tu.next_month_key(k)
            r["gaps"] = gaps[:24]
            r["n_gaps"] = len(gaps)
    return JSONResponse(data)


# ---------------------------------------------------------------- ورود از فایل (CSV / Excel)
def _decode(raw: bytes) -> str:
    for enc in ("utf-8-sig", "cp1256"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    raise _m().HTTPException(400, "کدگذاری فایل پشتیبانی نمی‌شود (UTF-8 یا Windows-1256)")


def read_table(filename: str, raw: bytes) -> list[list]:
    if raw[:2] == b"PK" or (filename or "").lower().endswith((".xlsx", ".xlsm")):
        try:
            return xlsx.read_rows(raw)
        except xlsx.XlsxError as e:
            raise _m().HTTPException(422, str(e))
    text = _decode(raw)
    first = text.splitlines()[0] if text.strip() else ""
    delim = ";" if first.count(";") > first.count(",") else ("\t" if first.count("\t") > first.count(",") else ",")
    return [r for r in csv.reader(io.StringIO(text), delimiter=delim)]


def _canon(h) -> str:
    s = str(h or "").strip().lower().replace("‌", " ")
    return HEADER_ALIASES.get(s, s)


def table_to_items(table: list[list], kind: str, c, site_id: int) -> tuple[list[dict], list[dict], list[int]]:
    """تبدیل جدول به ردیف‌های ts. kind: readings | production | entries | wide. خروجی: (items, خطاهای ساختاری)."""
    table = [r for r in table if any(x not in (None, "") for x in r)]
    if not table:
        return [], [{"line": 1, "error": "فایل خالی است"}], []
    header = [str(x or "").strip() for x in table[0]]
    items: list[dict] = []
    errs: list[dict] = []
    line_of: list[int] = []
    if kind == "wide":
        canon = [_canon(h) for h in header]
        if "start" not in canon and "period" not in canon:
            return [], [{"line": 1, "error": "ستون اول باید date/start یا period باشد"}], []
        meters_ = {r["code"]: r for r in c.execute("SELECT code FROM meters WHERE site_id=?", (site_id,))}
        streams_ = {r["name"] for r in c.execute("SELECT name FROM streams WHERE site_id=?", (site_id,))}
        vars_ = {r["code"] for r in c.execute("SELECT code FROM variables")}
        cols = []
        for i, h in enumerate(header):
            if canon[i] in ("start", "period", "end", "granularity", "calendar", "quality"):
                continue
            if not h:
                continue
            if h in meters_:
                cols.append((i, {"meter": h}))
            elif h in streams_:
                cols.append((i, {"stream": h}))
            elif h in vars_:
                cols.append((i, {"variable": h}))
            else:
                errs.append({"line": 1, "error": f"ستون «{h}» نه کنتور است نه جریان و نه متغیر؛ نادیده گرفته شد"})
        for ln, row in enumerate(table[1:], start=2):
            base = {}
            for i, name in enumerate(canon):
                if name in ("start", "period", "end", "granularity", "calendar", "quality") and i < len(row) and row[i] not in (None, ""):
                    base[name] = row[i]
            if base.get("quality"):
                base["quality"] = QUAL_ALIASES.get(str(base["quality"]).strip(), str(base["quality"]).strip())
            for i, tgt in cols:
                if i < len(row) and row[i] not in (None, ""):
                    items.append({**base, **tgt, "value": row[i]})
                    line_of.append(ln)
        return items, errs, line_of
    canon = [_canon(h) for h in header]
    for ln, row in enumerate(table[1:], start=2):
        d = {canon[i]: row[i] for i in range(min(len(row), len(canon))) if row[i] not in (None, "")}
        if not d:
            continue
        if d.get("quality"):
            d["quality"] = QUAL_ALIASES.get(str(d["quality"]).strip(), str(d["quality"]).strip())
        if kind == "readings":
            if "volume" in d and "value" not in d:
                d["value"] = d.pop("volume")
            d.setdefault("variable", "stream_volume")
            items.append(d)
            line_of.append(ln)
        elif kind == "production":
            for col, var in (("product_qty", "production"), ("ambient_temp", "ambient_temp"), ("operating_hours", "operating_hours")):
                if col in d:
                    items.append({"variable": var, "value": d[col], **{k: d[k] for k in ("period", "start", "end", "calendar", "granularity") if k in d}})
                    line_of.append(ln)
            if "product_qty" not in d:
                errs.append({"line": ln, "error": "ستون product_qty موجود نیست"})
        else:
            items.append(d)
            line_of.append(ln)
    return items, errs, line_of


async def import_file(request: Request):
    """ورود از CSV/Excel. type=readings|production|entries|wide ؛ calendar=gregorian|jalali ؛ replace=1 برای جایگزینی هم‌پوشانی‌ها."""
    m = _m()
    u = auth.require(request, "engineer")
    site_id = m.sid(request)
    m.get_site(site_id)
    kind = request.query_params.get("type", "readings")
    if kind not in ("readings", "production", "entries", "wide"):
        raise m.HTTPException(422, "type باید readings، production، entries یا wide باشد")
    form = await request.form()
    f = form.get("file")
    if f is None or not hasattr(f, "read"):
        raise m.HTTPException(422, "فایل ارسال نشده است")
    raw = await f.read()
    if len(raw) > MAX_UPLOAD:
        raise m.HTTPException(413, "حجم فایل بیش از ۵ مگابایت است")
    table = read_table(getattr(f, "filename", "") or "", raw)
    cal = _calendar(request)
    with db() as c:
        out = table_to_items(table, kind, c, site_id)
        items, struct_errs, line_of = out
        res = tsdata.save_entries(site_id, items, "excel" if (raw[:2] == b"PK") else "csv", u["id"], cal,
                                  request.query_params.get("replace") == "1", c=c) if items else {"saved": 0, "errors": []}
    for e in res["errors"]:  # شماره ردیف ذخیره → شماره خط فایل
        if 1 <= e["line"] <= len(line_of):
            e["line"] = line_of[e["line"] - 1]
    errors = struct_errs + res["errors"]
    saved = res["saved"]
    if kind == "production":  # هر ردیف فایل = یک ماه/بازه (سه متغیر)؛ شمارش به‌صورت ردیف
        saved = len(set(line_of) - {e["line"] for e in errors})
    auth.log(u, "import_file", kind, site_id, f"saved={saved} errors={len(errors)}")
    return JSONResponse({"saved": saved, "entries_saved": res["saved"], "errors": errors},
                        status_code=200 if saved or not errors else 422)


def template(request: Request):
    """قالب نمونه ورود داده (xlsx یا csv) بر پایه جریان‌ها/کنتورهای همین مجتمع."""
    m = _m()
    auth.require(request)
    site_id = m.sid(request)
    m.get_site(site_id)
    layout = request.query_params.get("layout", "long")
    fmt = request.query_params.get("format", "xlsx")
    cal = _calendar(request) or "gregorian"
    jal = cal == "jalali"
    with db() as c:
        streams = rows(c.execute("SELECT name FROM streams WHERE site_id=? ORDER BY id LIMIT 4", (site_id,)))
        meters_ = rows(c.execute("SELECT code FROM meters WHERE site_id=? ORDER BY id LIMIT 2", (site_id,)))
    s1 = streams[0]["name"] if streams else "نام جریان"
    d1, d2, d3, e1 = (("1404/01/01", "1404/01/02", "1404/01/03", "1404/01/31") if jal
                      else ("2025-03-21", "2025-03-22", "2025-03-23", "2025-04-20"))
    if layout == "wide":
        names = [x["code"] for x in meters_] + [x["name"] for x in streams]
        names = names[:4] or ["نام جریان"]
        sheet = [["start", "end", *names], [d1, e1, *[1000 + 10 * i for i in range(len(names))]],
                 [e1, "", *[900 for _ in names]]]
        csv_rows = sheet
    else:
        sheet = [["variable", "stream", "meter", "start", "end", "value", "quality", "corrected_value", "note"],
                 ["stream_volume", s1, "", d1, d2, 1250.5, "valid", "", "قرائت دو روز"],
                 ["stream_volume", s1, "", d3, d3, 1190, "estimated", "", ""],
                 ["production", "", "", d1, e1, 5400, "valid", "", "تولید ماه"],
                 ["ambient_temp", "", "", d1, e1, 21.5, "valid", "", ""]]
        csv_rows = sheet
    if fmt == "csv":
        buf = io.StringIO()
        csv.writer(buf).writerows(csv_rows)
        return Response("﻿" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="aquaaudit-template-{layout}.csv"'})
    help_rows = [["راهنما"], ["تاریخ‌ها در تقویم انتخابی (" + ("شمسی" if jal else "میلادی") + ") نوشته شوند؛ ارقام فارسی هم مجاز است."],
                 ["start/end: ابتدا و انتهای بازه (هر دو شامل). برای مقدار ماهانه، start=اول ماه و end=آخر ماه، یا ستون period (YYYY-MM)."],
                 ["variable: کد متغیر (برای حجم جریان: stream_volume). stream: نام جریان؛ meter: کد کنتور (اختیاری)."],
                 ["quality: valid | estimated | suspect | invalid | missing (یا معتبر، تخمینی، ...)."],
                 ["هم‌پوشانی بازه‌ها برای یک جریان/کنتور مجاز نیست؛ بازه یکسان، مقدار قبلی را به‌روزرسانی می‌کند."]]
    data = xlsx.write_workbook([("data", sheet), ("help", help_rows)])
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="aquaaudit-template-{layout}.xlsx"'})


# ---------------------------------------------------------------- کلیدهای API و ورود داده با API
def _hash(k: str) -> str:
    return hashlib.sha256(k.encode()).hexdigest()


def list_keys(request: Request):
    m = _m()
    auth.require(request, "engineer")
    site_id = m.sid(request)
    m.get_site(site_id)
    with db() as c:
        return JSONResponse(rows(c.execute("""SELECT id,name,prefix,created_at,last_used_at,revoked FROM api_keys
                                              WHERE site_id=? ORDER BY id DESC""", (site_id,))))


async def create_key(request: Request):
    m = _m()
    u = auth.require(request, "engineer")
    site_id = m.sid(request)
    m.get_site(site_id)
    d = await m.body(request)
    name = str(d.get("name", "")).strip()
    if not name:
        raise m.HTTPException(422, "نام کلید لازم است")
    key = "aq_" + secrets.token_urlsafe(30)
    with db() as c:
        cur = c.execute("INSERT INTO api_keys(site_id,name,prefix,key_hash,created_by) VALUES(?,?,?,?,?)",
                        (site_id, name[:80], key[:9], _hash(key), u["id"]))
    auth.log(u, "create", "api_key", cur.lastrowid, name)
    return JSONResponse({"id": cur.lastrowid, "key": key, "note": "این کلید فقط همین یک‌بار نمایش داده می‌شود؛ آن را ذخیره کنید."}, 201)


def revoke_key(request: Request):
    m = _m()
    u = auth.require(request, "engineer")
    site_id, kid = m.sid(request), int(request.path_params["key_id"])
    with db() as c:
        cur = c.execute("UPDATE api_keys SET revoked=1 WHERE id=? AND site_id=?", (kid, site_id))
    if cur.rowcount == 0:
        raise m.HTTPException(404, "کلید یافت نشد")
    auth.log(u, "revoke", "api_key", kid)
    return JSONResponse({"ok": True})


def _key_site(request: Request) -> int:
    k = request.headers.get("x-api-key") or ""
    if not k:
        ah = request.headers.get("authorization", "")
        if ah.lower().startswith("apikey "):
            k = ah[7:].strip()
    if not k:
        raise _m().HTTPException(401, "کلید API ارسال نشده است (هدر X-API-Key)")
    with db() as c:
        r = one(c.execute("SELECT id, site_id FROM api_keys WHERE key_hash=? AND revoked=0", (_hash(k),)))
        if not r:
            raise _m().HTTPException(401, "کلید API نامعتبر یا ابطال‌شده است")
        c.execute("UPDATE api_keys SET last_used_at=CURRENT_TIMESTAMP WHERE id=?", (r["id"],))
    return r["site_id"]


def ingest_ping(request: Request):
    site_id = _key_site(request)
    with db() as c:
        return JSONResponse({"ok": True, "site_id": site_id, "calendar": tsdata.site_calendar(c, site_id)})


async def ingest_entries(request: Request):
    m = _m()
    site_id = _key_site(request)
    d = await m.body(request)
    items = d.get("entries", d.get("items"))
    if not isinstance(items, list) or not items:
        raise m.HTTPException(422, "فهرست entries خالی است")
    try:
        res = tsdata.save_entries(site_id, items, "api", None, _calendar(request, d), bool(d.get("replace_overlaps")))
    except (tsdata.TsError, tu.DateError) as e:
        raise _tserr(e)
    return JSONResponse(res, status_code=200 if res["saved"] or not res["errors"] else 422)


async def ingest_meter_reads(request: Request):
    """قرائت‌های خام کنتور: {"meter": "کد", "reads": [{"ts": "2025-06-01T08:00:00+03:30" یا epoch, "value": 123.4}, ...]}."""
    from datetime import datetime
    m = _m()
    site_id = _key_site(request)
    d = await m.body(request)
    reads_in = d.get("reads")
    if not isinstance(reads_in, list) or not reads_in or len(reads_in) > 50000:
        raise m.HTTPException(422, "reads باید فهرست غیرخالی (حداکثر ۵۰٬۰۰۰ مورد) باشد")
    errors, reads = [], []
    for i, r in enumerate(reads_in, 1):
        try:
            ts = r["ts"]
            if isinstance(ts, (int, float)):
                t = float(ts)
            else:
                dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
                t = dt.timestamp()  # بدون منطقه زمانی = ساعت محلی سرور
            reads.append((t, tsdata.to_float(r["value"], "مقدار")))
        except (KeyError, ValueError, tsdata.TsError) as e:
            errors.append({"line": i, "error": f"قرائت نامعتبر: {e}"})
    with db() as c:
        try:
            meter = tsdata._lookup_meter(c, site_id, d.get("meter"))
        except tsdata.TsError as e:
            raise _tserr(e)
        n = mt.store_reads(c, meter["id"], reads)
        c.execute("UPDATE meters SET last_poll_at=?, last_status='ok (api)', last_raw=? WHERE id=?",
                  (__import__("datetime").datetime.now().isoformat(timespec="seconds"), reads[-1][1] if reads else None, meter["id"]))
        res = mt.fold_meter(c, meter["id"], None)
    return JSONResponse({"stored_reads": n, "days_saved": res["saved"], "errors": errors + res["errors"]})


# ---------------------------------------------------------------- کنتورها
def _meter_row(r: dict) -> dict:
    try:
        r["config"] = json.loads(r["config"] or "{}")
    except ValueError:
        r["config"] = {}
    r["config"].pop("auth_header", None) if r["config"].get("auth_header") else None
    return r


def list_meters(request: Request):
    m = _m()
    auth.require(request)
    site_id = m.sid(request)
    m.get_site(site_id)
    with db() as c:
        ms = rows(c.execute("""SELECT mt.*, s.name AS stream_name, s.kind AS stream_kind, s.direction AS direction,
                  s.source_category AS source_category, s.stream_type AS stream_type,
                  (SELECT COUNT(*) FROM meter_reads r WHERE r.meter_id=mt.id) AS n_reads,
                  (SELECT COUNT(*) FROM ts_entries e WHERE e.meter_id=mt.id) AS n_entries
                  FROM meters mt JOIN streams s ON s.id=mt.stream_id WHERE mt.site_id=? ORDER BY s.id, mt.id""", (site_id,)))
    return JSONResponse([_meter_row(x) for x in ms])


def meters_summary(request: Request):
    """تعداد کنتورها بر حسب نوع منبع (دسته منبع برای ورودی‌ها؛ نوع جریان برای خروجی‌ها)."""
    m = _m()
    auth.require(request)
    site_id = m.sid(request)
    m.get_site(site_id)
    with db() as c:
        streams = rows(c.execute("""SELECT s.id, s.name, s.kind, s.direction, s.scope, s.source_category,
                  (SELECT COUNT(*) FROM meters m WHERE m.stream_id=s.id) AS n_meters
                  FROM streams s WHERE s.site_id=? ORDER BY s.id""", (site_id,)))
        ms = rows(c.execute("SELECT stream_id, connection, enabled FROM meters WHERE site_id=?", (site_id,)))
        cats = {r["code"]: r["name"] for r in c.execute("SELECT code,name FROM source_categories")}
    groups: dict[str, dict] = {}
    by_stream = {}
    for x in ms:
        by_stream.setdefault(x["stream_id"], []).append(x)
    for s in streams:
        if s["direction"] == "in" and s["source_category"]:
            key, label = s["source_category"], cats.get(s["source_category"], s["source_category"])
        else:
            key, label = f"{s['direction']}:{s['kind']}", None
        g = groups.setdefault(key, {"key": key, "label": label, "direction": s["direction"], "kind": s["kind"], "streams": 0,
                                    "meters": 0, "manual": 0, "api": 0, "direct": 0, "streams_without_meter": 0, "stream_names": []})
        g["streams"] += 1
        g["stream_names"].append(s["name"])
        mm = by_stream.get(s["id"], [])
        g["meters"] += len(mm)
        for x in mm:
            g[x["connection"]] += 1
        if not mm:
            g["streams_without_meter"] += 1
    return JSONResponse({"groups": list(groups.values()), "total_meters": len(ms),
                         "streams_without_meter": sum(1 for s in streams if not s["n_meters"])})


def _meter_payload(c, site_id: int, d: dict, existing: dict | None = None) -> dict:
    m = _m()
    code = str(d.get("code", existing["code"] if existing else "")).strip()
    if not code or len(code) > 60:
        raise m.HTTPException(422, "کد/برچسب کنتور لازم است")
    st = one(c.execute("SELECT id FROM streams WHERE id=? AND site_id=?", (d.get("stream_id", existing["stream_id"] if existing else None), site_id)))
    if not st:
        raise m.HTTPException(422, "جریان (منبع) انتخاب‌شده در این مجتمع نیست")
    conn = d.get("connection", existing["connection"] if existing else "manual")
    if conn not in ("manual", "api", "direct"):
        raise m.HTTPException(422, "روش اتصال باید manual، api یا direct باشد")
    cfg = d.get("config", json.loads(existing["config"]) if existing else {})
    try:
        cfg = mt.clean_config(cfg) if conn == "direct" else {k: cfg[k] for k in ("reading_type", "rate_unit", "counter_max", "interval_s") if k in cfg}
        mult = tsdata.to_float(d.get("multiplier", existing["multiplier"] if existing else 1), "ضریب تبدیل")
        default_unc = existing["uncertainty_pct"] if existing else (one(c.execute(
            "SELECT uncertainty_pct u FROM streams WHERE id=?", (st["id"],))) or {"u": 5.0})["u"]
        unc = tsdata.to_float(d.get("uncertainty_pct", default_unc), "عدم قطعیت")
    except (mt.MeterError, tsdata.TsError) as e:
        raise _tserr(e)
    if mult <= 0 or not 0 <= unc <= 100:
        raise m.HTTPException(422, "ضریب باید مثبت و عدم قطعیت بین ۰ تا ۱۰۰ باشد")
    return {"stream_id": st["id"], "code": code, "name": str(d.get("name", existing["name"] if existing else ""))[:120],
            "meter_type": str(d.get("meter_type", existing["meter_type"] if existing else "other"))[:60], "connection": conn,
            "multiplier": mult, "uncertainty_pct": unc, "serial": str(d.get("serial", existing["serial"] if existing else ""))[:80],
            "location": str(d.get("location", existing["location"] if existing else ""))[:120],
            "installed_at": str(d.get("installed_at", existing["installed_at"] if existing else ""))[:20],
            "notes": str(d.get("notes", existing["notes"] if existing else ""))[:300],
            "config": json.dumps(cfg, ensure_ascii=False), "enabled": 1 if d.get("enabled", existing["enabled"] if existing else True) else 0}


async def create_meter(request: Request):
    m = _m()
    site_id = m.sid(request)
    m.get_site(site_id)
    d = await m.body(request)
    u = auth.require(request, "admin" if d.get("connection") == "direct" else "engineer")
    with db() as c:
        p = _meter_payload(c, site_id, d)
        try:
            cur = c.execute("""INSERT INTO meters(site_id,stream_id,code,name,meter_type,connection,multiplier,uncertainty_pct,serial,
                               location,installed_at,notes,config,enabled) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                            (site_id, p["stream_id"], p["code"], p["name"], p["meter_type"], p["connection"], p["multiplier"],
                             p["uncertainty_pct"], p["serial"], p["location"], p["installed_at"], p["notes"], p["config"], p["enabled"]))
        except Exception as e:
            if "UNIQUE" in str(e):
                raise m.HTTPException(409, f"کد کنتور «{p['code']}» در این مجتمع تکراری است")
            raise
    auth.log(u, "create", "meter", cur.lastrowid, p["code"])
    return JSONResponse({"id": cur.lastrowid}, 201)


async def update_meter(request: Request):
    m = _m()
    site_id, mid = m.sid(request), int(request.path_params["meter_id"])
    d = await m.body(request)
    with db() as c:
        ex = one(c.execute("SELECT * FROM meters WHERE id=? AND site_id=?", (mid, site_id)))
        if not ex:
            raise m.HTTPException(404, "کنتور یافت نشد")
        u = auth.require(request, "admin" if (d.get("connection", ex["connection"]) == "direct" or ex["connection"] == "direct") else "engineer")
        p = _meter_payload(c, site_id, d, ex)
        if p["stream_id"] != ex["stream_id"] and one(c.execute("SELECT 1 x FROM ts_entries WHERE meter_id=? LIMIT 1", (mid,))):
            raise m.HTTPException(422, "کنتوری که داده ثبت‌شده دارد به جریان دیگری منتقل نمی‌شود")
        old_unc = ex["uncertainty_pct"]
        try:
            c.execute("""UPDATE meters SET stream_id=?,code=?,name=?,meter_type=?,connection=?,multiplier=?,uncertainty_pct=?,serial=?,
                         location=?,installed_at=?,notes=?,config=?,enabled=? WHERE id=?""",
                      (p["stream_id"], p["code"], p["name"], p["meter_type"], p["connection"], p["multiplier"], p["uncertainty_pct"],
                       p["serial"], p["location"], p["installed_at"], p["notes"], p["config"], p["enabled"], mid))
        except Exception as e:
            if "UNIQUE" in str(e):
                raise m.HTTPException(409, f"کد کنتور «{p['code']}» تکراری است")
            raise
    auth.log(u, "update", "meter", mid)
    return JSONResponse({"ok": True})


def delete_meter(request: Request):
    m = _m()
    u = auth.require(request, "engineer")
    site_id, mid = m.sid(request), int(request.path_params["meter_id"])
    with db() as c:
        ex = one(c.execute("SELECT * FROM meters WHERE id=? AND site_id=?", (mid, site_id)))
        if not ex:
            raise m.HTTPException(404, "کنتور یافت نشد")
        c.execute("DELETE FROM ts_entries WHERE meter_id=?", (mid,))
        c.execute("DELETE FROM ts_buckets WHERE meter_id=?", (mid,))
        c.execute("DELETE FROM meters WHERE id=?", (mid,))
        tsdata.rebuild(c, site_id, {("stream_volume", ex["stream_id"], 0)})
    auth.log(u, "delete", "meter", mid, ex["code"])
    return JSONResponse({"ok": True})


def _own_meter(request: Request, need="engineer") -> dict:
    m = _m()
    auth.require(request, need)
    site_id, mid = m.sid(request), int(request.path_params["meter_id"])
    with db() as c:
        ex = one(c.execute("SELECT * FROM meters WHERE id=? AND site_id=?", (mid, site_id)))
    if not ex:
        raise m.HTTPException(404, "کنتور یافت نشد")
    return ex


def test_meter(request: Request):
    ex = _own_meter(request, "admin")
    try:
        return JSONResponse(mt.poll_meter(ex["id"], store=False))
    except mt.MeterError as e:
        raise _tserr(e)


def poll_meter_now(request: Request):
    ex = _own_meter(request, "admin")
    try:
        return JSONResponse(mt.poll_meter(ex["id"], auth.require(request, "admin")["id"]))
    except mt.MeterError as e:
        raise _tserr(e)


def fold_meter_now(request: Request):
    ex = _own_meter(request)
    with db() as c:
        return JSONResponse(mt.fold_meter(c, ex["id"], auth.require(request, "engineer")["id"]))


def meter_reads(request: Request):
    ex = _own_meter(request, "viewer")
    with db() as c:
        return JSONResponse(rows(c.execute("SELECT ts, raw FROM meter_reads WHERE meter_id=? ORDER BY ts DESC LIMIT 500", (ex["id"],))))


TS_ROUTES = [
    Route("/api/variables", list_variables),
    Route("/api/variables", create_variable, methods=["POST"]),
    Route("/api/calendar/convert", convert_date, methods=["POST"]),
    Route(S + "/ts/entries", list_entries),
    Route(S + "/ts/entries", post_entries, methods=["POST"]),
    Route(S + "/ts/entries/delete", delete_entries, methods=["POST"]),
    Route(S + "/ts/periods", make_periods, methods=["POST"]),
    Route(S + "/ts/buckets", list_buckets),
    Route(S + "/ts/coverage", coverage),
    Route(S + "/ts/template", template),
    Route(S + "/import", import_file, methods=["POST"]),
    Route(S + "/api-keys", list_keys),
    Route(S + "/api-keys", create_key, methods=["POST"]),
    Route(S + "/api-keys/{key_id:int}", revoke_key, methods=["DELETE"]),
    Route("/api/ingest/v1/ping", ingest_ping),
    Route("/api/ingest/v1/entries", ingest_entries, methods=["POST"]),
    Route("/api/ingest/v1/meter-reads", ingest_meter_reads, methods=["POST"]),
    Route(S + "/meters", list_meters),
    Route(S + "/meters", create_meter, methods=["POST"]),
    Route(S + "/meters/summary", meters_summary),
    Route(S + "/meters/{meter_id:int}", update_meter, methods=["PUT"]),
    Route(S + "/meters/{meter_id:int}", delete_meter, methods=["DELETE"]),
    Route(S + "/meters/{meter_id:int}/test", test_meter, methods=["POST"]),
    Route(S + "/meters/{meter_id:int}/poll", poll_meter_now, methods=["POST"]),
    Route(S + "/meters/{meter_id:int}/fold", fold_meter_now, methods=["POST"]),
    Route(S + "/meters/{meter_id:int}/reads", meter_reads),
]
