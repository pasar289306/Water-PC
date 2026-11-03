"""AquaAudit – API و سرویس رابط کاربری (Starlette)."""
from __future__ import annotations

import csv
import io
import json
import os
import re
import sqlite3
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError
from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from . import auth, services
from .db import db, init_db, rows, one
from .engine import equipment as eq
from .engine.kpi import safe_eval, FormulaError
from .report import build_report

STATIC = Path(__file__).resolve().parent.parent / "static"
PERIOD_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
MAX_UPLOAD = 5 * 1024 * 1024

KINDS_IN = {"raw", "potable", "fresh", "rain", "reclaimed_in"}
KINDS_OUT = {"wastewater", "blowdown", "evaporation", "product", "leakage", "other_out"}
KINDS_INTERNAL = {"reclaimed", "recycled", "condensate"}
QUALITIES = {"valid", "estimated", "suspect", "invalid", "missing"}


# ---------------------------------------------------------------- مدل‌ها
class LoginIn(BaseModel):
    username: str
    password: str


class UserIn(BaseModel):
    username: str = Field(min_length=3, max_length=50)
    password: str = Field(min_length=8, max_length=100)
    full_name: str = ""
    role: Literal["admin", "engineer", "viewer"] = "viewer"


class SiteIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    industry_code: str = "IND-15"
    location: str = ""
    product_unit: str = "تن"
    cost_supply: float = Field(0, ge=0)
    cost_pumping: float = Field(0, ge=0)
    cost_treatment: float = Field(0, ge=0)
    cost_distribution: float = Field(0, ge=0)
    cost_disposal: float = Field(0, ge=0)
    screening_pct: float = Field(10, gt=0, le=100)


class UnitIn(BaseModel):
    name: str = Field(min_length=1)
    system_code: str = "SYS-04"


class EquipmentIn(BaseModel):
    name: str = Field(min_length=1)
    kind: Literal["cooling_tower", "boiler", "ro", "pump", "wwtp", "other"]
    unit_id: int | None = None
    params: dict = {}


class StreamIn(BaseModel):
    name: str = Field(min_length=1)
    direction: Literal["in", "out"]
    kind: str
    scope: Literal["external", "internal"] = "external"
    unit_id: int | None = None
    meter_id: str = ""
    uncertainty_pct: float = Field(2.0, ge=0, le=100)


class ReadingIn(BaseModel):
    stream_id: int
    period: str
    volume: float = Field(ge=0)
    quality: str = "valid"
    corrected_volume: float | None = Field(None, ge=0)


class ProductionIn(BaseModel):
    period: str
    product_qty: float = Field(ge=0)
    ambient_temp: float | None = None
    operating_hours: float | None = Field(None, ge=0)


class StorageIn(BaseModel):
    period: str
    delta_volume: float
    uncertainty: float = Field(0, ge=0)


class ProjectIn(BaseModel):
    measure_id: int | None = None
    title: str = Field(min_length=1)
    saving_m3_year: float = Field(ge=0)
    capex: float = Field(0, ge=0)
    opex_year: float = Field(0, ge=0)
    life_years: int = Field(10, ge=1, le=50)
    discount_rate: float = Field(0.15, ge=0, le=1)
    ease: int = Field(3, ge=1, le=5)
    supply_risk: int = Field(3, ge=1, le=5)
    environment: int = Field(3, ge=1, le=5)
    tech_risk: int = Field(3, ge=1, le=5)
    status: Literal["proposed", "approved", "in_progress", "done", "rejected"] = "proposed"


class KpiDefIn(BaseModel):
    code: str = Field(pattern=r"^[A-Za-z0-9_]{2,30}$")
    name: str
    formula: str
    unit: str = ""
    grp: str = ""
    warn: float | None = None
    critical: float | None = None
    reference: str = ""


class MeasureIn(BaseModel):
    code: str = Field(pattern=r"^[A-Za-z0-9_\-]{2,30}$")
    title: str
    grp: Literal["A", "B", "C", "D", "E"]
    hierarchy: Literal["reduce", "replace", "reuse"]
    system_code: str = ""
    industries: str = "ALL"
    problem: str = ""
    mechanism: str = ""
    required_data: str = ""
    calc_method: str = ""
    saving_min_pct: float | None = None
    saving_max_pct: float | None = None
    saving_basis: str = ""
    capex_note: str = ""
    payback_note: str = ""
    tech_risk: int = Field(3, ge=1, le=5)
    ease: int = Field(3, ge=1, le=5)
    quality_constraints: str = ""
    verification: str = ""
    source: str = ""


class BaselineIn(BaseModel):
    split_period: str
    predictors: list[str] = ["product_qty"]


def parse(model, data):
    try:
        return model(**data) if isinstance(data, dict) else model.model_validate(data)
    except ValidationError as e:
        msgs = "; ".join(f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors())
        raise HTTPException(422, msgs)


async def body(request: Request) -> dict:
    try:
        d = await request.json()
    except Exception:
        raise HTTPException(400, "بدنه درخواست JSON معتبر نیست")
    if not isinstance(d, dict):
        raise HTTPException(400, "بدنه باید یک شیء JSON باشد")
    return d


def ok(data, status=200):
    return JSONResponse(data, status_code=status)


def need_period(p: str):
    if not PERIOD_RE.match(p or ""):
        raise HTTPException(422, "قالب دوره باید YYYY-MM باشد")


def get_site(site_id: int) -> dict:
    with db() as c:
        s = one(c.execute("SELECT * FROM sites WHERE id=?", (site_id,)))
    if not s:
        raise HTTPException(404, "مجتمع یافت نشد")
    return s


def sid(request: Request) -> int:
    try:
        return int(request.path_params["site_id"])
    except (KeyError, ValueError):
        raise HTTPException(404, "شناسه نامعتبر")


def opt_period(request: Request) -> str | None:
    p = request.query_params.get("period")
    if p and not re.match(r"^\d{4}(-(0[1-9]|1[0-2]))?$", p):
        raise HTTPException(422, "قالب دوره باید YYYY یا YYYY-MM باشد")
    return p or None


# ---------------------------------------------------------------- احراز هویت
async def login(request: Request):
    d = parse(LoginIn, await body(request))
    with db() as c:
        u = one(c.execute("SELECT * FROM users WHERE username=?", (d.username,)))
    if not u or not u["active"] or not auth.verify_password(d.password, u["password_hash"]):
        auth.log(None, "login_failed", "user", d.username)
        raise HTTPException(401, "نام کاربری یا گذرواژه نادرست است")
    auth.log(u, "login", "user", u["id"])
    return ok({"token": auth.make_token(u),
               "user": {"id": u["id"], "username": u["username"], "full_name": u["full_name"], "role": u["role"]}})


def me(request: Request):
    return ok(auth.current_user(request))


def list_users(request: Request):
    auth.require(request, "admin")
    with db() as c:
        return ok(rows(c.execute("SELECT id,username,full_name,role,active,created_at FROM users ORDER BY id")))


async def create_user(request: Request):
    u = auth.require(request, "admin")
    d = parse(UserIn, await body(request))
    try:
        with db() as c:
            cur = c.execute("INSERT INTO users(username,full_name,password_hash,role) VALUES(?,?,?,?)",
                            (d.username, d.full_name, auth.hash_password(d.password), d.role))
    except sqlite3.IntegrityError:
        raise HTTPException(409, "این نام کاربری قبلاً ثبت شده است")
    auth.log(u, "create", "user", cur.lastrowid, d.username)
    return ok({"id": cur.lastrowid}, 201)


async def change_password(request: Request):
    u = auth.current_user(request)
    d = await body(request)
    old, new = d.get("old_password", ""), d.get("new_password", "")
    if len(new) < 8:
        raise HTTPException(422, "گذرواژه جدید باید حداقل ۸ نویسه باشد")
    with db() as c:
        row = one(c.execute("SELECT password_hash FROM users WHERE id=?", (u["id"],)))
        if not auth.verify_password(old, row["password_hash"]):
            raise HTTPException(403, "گذرواژه فعلی نادرست است")
        c.execute("UPDATE users SET password_hash=? WHERE id=?", (auth.hash_password(new), u["id"]))
    auth.log(u, "change_password", "user", u["id"])
    return ok({"ok": True})


# ---------------------------------------------------------------- مجتمع‌ها
def list_sites(request: Request):
    auth.require(request)
    with db() as c:
        return ok(rows(c.execute("SELECT * FROM sites ORDER BY id")))


async def create_site(request: Request):
    u = auth.require(request, "engineer")
    d = parse(SiteIn, await body(request))
    with db() as c:
        cur = c.execute("""INSERT INTO sites(name,industry_code,location,product_unit,cost_supply,cost_pumping,
            cost_treatment,cost_distribution,cost_disposal,screening_pct,created_by) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                        (d.name, d.industry_code, d.location, d.product_unit, d.cost_supply, d.cost_pumping,
                         d.cost_treatment, d.cost_distribution, d.cost_disposal, d.screening_pct, u["id"]))
    auth.log(u, "create", "site", cur.lastrowid, d.name)
    return ok({"id": cur.lastrowid}, 201)


def site_detail(request: Request):
    auth.require(request)
    s = get_site(sid(request))
    with db() as c:
        s["units"] = rows(c.execute("SELECT * FROM units WHERE site_id=?", (s["id"],)))
        eqs = rows(c.execute("SELECT * FROM equipment WHERE site_id=?", (s["id"],)))
    for e in eqs:
        e["params"] = json.loads(e["params"] or "{}")
    s["equipment"] = eqs
    s["marginal_cost"] = services.marginal_cost(s)
    return ok(s)


async def update_site(request: Request):
    u = auth.require(request, "engineer")
    site_id = sid(request)
    get_site(site_id)
    d = parse(SiteIn, await body(request))
    with db() as c:
        c.execute("""UPDATE sites SET name=?,industry_code=?,location=?,product_unit=?,cost_supply=?,cost_pumping=?,
            cost_treatment=?,cost_distribution=?,cost_disposal=?,screening_pct=? WHERE id=?""",
                  (d.name, d.industry_code, d.location, d.product_unit, d.cost_supply, d.cost_pumping,
                   d.cost_treatment, d.cost_distribution, d.cost_disposal, d.screening_pct, site_id))
    auth.log(u, "update", "site", site_id)
    return ok({"ok": True})


def delete_site(request: Request):
    u = auth.require(request, "admin")
    site_id = sid(request)
    get_site(site_id)
    with db() as c:
        c.execute("DELETE FROM sites WHERE id=?", (site_id,))
    auth.log(u, "delete", "site", site_id)
    return ok({"ok": True})


async def add_unit(request: Request):
    u = auth.require(request, "engineer")
    site_id = sid(request)
    get_site(site_id)
    d = parse(UnitIn, await body(request))
    with db() as c:
        cur = c.execute("INSERT INTO units(site_id,name,system_code) VALUES(?,?,?)", (site_id, d.name, d.system_code))
    auth.log(u, "create", "unit", cur.lastrowid, d.name)
    return ok({"id": cur.lastrowid}, 201)


async def add_equipment(request: Request):
    u = auth.require(request, "engineer")
    site_id = sid(request)
    get_site(site_id)
    d = parse(EquipmentIn, await body(request))
    with db() as c:
        cur = c.execute("INSERT INTO equipment(site_id,unit_id,name,kind,params) VALUES(?,?,?,?,?)",
                        (site_id, d.unit_id, d.name, d.kind, json.dumps(d.params, ensure_ascii=False)))
    auth.log(u, "create", "equipment", cur.lastrowid, d.name)
    return ok({"id": cur.lastrowid}, 201)


# ---------------------------------------------------------------- جریان‌ها و قرائت‌ها
def list_streams(request: Request):
    auth.require(request)
    site_id = sid(request)
    get_site(site_id)
    return ok(services.stream_totals(site_id, opt_period(request)))


async def add_stream(request: Request):
    u = auth.require(request, "engineer")
    site_id = sid(request)
    get_site(site_id)
    d = parse(StreamIn, await body(request))
    valid = KINDS_INTERNAL if d.scope == "internal" else (KINDS_IN if d.direction == "in" else KINDS_OUT)
    if d.scope == "internal" and d.kind not in KINDS_INTERNAL:
        raise HTTPException(422, f"نوع جریان داخلی باید یکی از {sorted(KINDS_INTERNAL)} باشد")
    if d.scope == "external" and d.kind not in valid:
        raise HTTPException(422, f"نوع جریان {('ورودی' if d.direction == 'in' else 'خروجی')} باید یکی از {sorted(valid)} باشد")
    try:
        with db() as c:
            cur = c.execute("""INSERT INTO streams(site_id,unit_id,name,direction,kind,scope,meter_id,uncertainty_pct)
                               VALUES(?,?,?,?,?,?,?,?)""",
                            (site_id, d.unit_id, d.name, d.direction, d.kind, d.scope, d.meter_id, d.uncertainty_pct))
    except sqlite3.IntegrityError:
        raise HTTPException(409, "جریانی با این نام در این مجتمع وجود دارد")
    auth.log(u, "create", "stream", cur.lastrowid, d.name)
    return ok({"id": cur.lastrowid}, 201)


def delete_stream(request: Request):
    u = auth.require(request, "engineer")
    site_id, stid = sid(request), int(request.path_params["stream_id"])
    with db() as c:
        cur = c.execute("DELETE FROM streams WHERE id=? AND site_id=?", (stid, site_id))
        if cur.rowcount == 0:
            raise HTTPException(404, "جریان یافت نشد")
    auth.log(u, "delete", "stream", stid)
    return ok({"ok": True})


def _upsert_reading(c, site_id: int, r: ReadingIn, user_id: int, source: str):
    need_period(r.period)
    if r.quality not in QUALITIES:
        raise HTTPException(422, f"وضعیت داده باید یکی از {sorted(QUALITIES)} باشد")
    st = one(c.execute("SELECT id FROM streams WHERE id=? AND site_id=?", (r.stream_id, site_id)))
    if not st:
        raise HTTPException(404, f"جریان {r.stream_id} در این مجتمع نیست")
    c.execute("""INSERT INTO readings(stream_id,period,volume,corrected_volume,quality,source,entered_by)
                 VALUES(?,?,?,?,?,?,?)
                 ON CONFLICT(stream_id,period) DO UPDATE SET volume=excluded.volume,
                   corrected_volume=excluded.corrected_volume, quality=excluded.quality,
                   source=excluded.source, entered_by=excluded.entered_by, entered_at=CURRENT_TIMESTAMP""",
              (r.stream_id, r.period, r.volume, r.corrected_volume, r.quality, source, user_id))


async def add_readings(request: Request):
    u = auth.require(request, "engineer")
    site_id = sid(request)
    get_site(site_id)
    d = await body(request)
    items = d.get("readings", [d] if "stream_id" in d else [])
    if not items or len(items) > 5000:
        raise HTTPException(422, "فهرست readings خالی یا بیش از ۵۰۰۰ مورد است")
    parsed = [parse(ReadingIn, i) for i in items]
    with db() as c:
        for r in parsed:
            _upsert_reading(c, site_id, r, u["id"], "manual")
    auth.log(u, "upsert", "readings", site_id, f"{len(parsed)} rows")
    return ok({"saved": len(parsed)})


def list_readings(request: Request):
    auth.require(request)
    site_id = sid(request)
    get_site(site_id)
    with db() as c:
        return ok(rows(c.execute("""SELECT r.*, s.name AS stream_name FROM readings r JOIN streams s ON s.id=r.stream_id
                                    WHERE s.site_id=? ORDER BY r.period DESC, s.name LIMIT 2000""", (site_id,))))


def _decode(raw: bytes) -> str:
    for enc in ("utf-8-sig", "cp1256"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    raise HTTPException(400, "کدگذاری فایل پشتیبانی نمی‌شود (UTF-8 یا Windows-1256)")


async def import_csv(request: Request):
    """CSV با ستون‌های: stream, period, volume, [quality]  یا  production: period, product_qty, ambient_temp, operating_hours."""
    u = auth.require(request, "engineer")
    site_id = sid(request)
    get_site(site_id)
    kind = request.query_params.get("type", "readings")
    if kind not in ("readings", "production"):
        raise HTTPException(422, "type باید readings یا production باشد")
    form = await request.form()
    f = form.get("file")
    if f is None or not hasattr(f, "read"):
        raise HTTPException(422, "فایل CSV ارسال نشده است")
    raw = await f.read()
    if len(raw) > MAX_UPLOAD:
        raise HTTPException(413, "حجم فایل بیش از ۵ مگابایت است")
    text = _decode(raw)
    reader = csv.DictReader(io.StringIO(text))
    errors, saved = [], 0
    with db() as c:
        names = {r["name"]: r["id"] for r in c.execute("SELECT id,name FROM streams WHERE site_id=?", (site_id,))}
        for i, row in enumerate(reader, start=2):
            try:
                row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
                period = row.get("period", "")
                need_period(period)
                if kind == "readings":
                    nm = row.get("stream", "")
                    if nm not in names:
                        raise ValueError(f"جریان «{nm}» تعریف نشده است")
                    vol = float(row["volume"].replace(",", ""))
                    r = ReadingIn(stream_id=names[nm], period=period, volume=vol, quality=row.get("quality") or "valid")
                    _upsert_reading(c, site_id, r, u["id"], "csv")
                else:
                    p = ProductionIn(period=period, product_qty=float(row["product_qty"].replace(",", "")),
                                     ambient_temp=float(row["ambient_temp"]) if row.get("ambient_temp") else None,
                                     operating_hours=float(row["operating_hours"]) if row.get("operating_hours") else None)
                    c.execute("""INSERT INTO production(site_id,period,product_qty,ambient_temp,operating_hours)
                                 VALUES(?,?,?,?,?) ON CONFLICT(site_id,period) DO UPDATE SET
                                 product_qty=excluded.product_qty, ambient_temp=excluded.ambient_temp,
                                 operating_hours=excluded.operating_hours""",
                              (site_id, p.period, p.product_qty, p.ambient_temp, p.operating_hours))
                saved += 1
            except HTTPException as e:
                errors.append({"line": i, "error": e.detail})
            except (ValueError, KeyError, ValidationError) as e:
                errors.append({"line": i, "error": str(e) if not isinstance(e, KeyError) else f"ستون {e} موجود نیست"})
            if len(errors) > 100:
                errors.append({"line": i, "error": "تعداد خطاها زیاد است؛ ادامه داده نشد"})
                break
    auth.log(u, "import_csv", kind, site_id, f"saved={saved} errors={len(errors)}")
    return ok({"saved": saved, "errors": errors}, 200 if saved or not errors else 422)


async def add_production(request: Request):
    u = auth.require(request, "engineer")
    site_id = sid(request)
    get_site(site_id)
    p = parse(ProductionIn, await body(request))
    need_period(p.period)
    with db() as c:
        c.execute("""INSERT INTO production(site_id,period,product_qty,ambient_temp,operating_hours) VALUES(?,?,?,?,?)
                     ON CONFLICT(site_id,period) DO UPDATE SET product_qty=excluded.product_qty,
                     ambient_temp=excluded.ambient_temp, operating_hours=excluded.operating_hours""",
                  (site_id, p.period, p.product_qty, p.ambient_temp, p.operating_hours))
    auth.log(u, "upsert", "production", site_id, p.period)
    return ok({"ok": True})


def list_production(request: Request):
    auth.require(request)
    site_id = sid(request)
    get_site(site_id)
    with db() as c:
        return ok(rows(c.execute("SELECT * FROM production WHERE site_id=? ORDER BY period", (site_id,))))


async def add_storage(request: Request):
    u = auth.require(request, "engineer")
    site_id = sid(request)
    get_site(site_id)
    s = parse(StorageIn, await body(request))
    need_period(s.period)
    with db() as c:
        c.execute("""INSERT INTO storage_changes(site_id,period,delta_volume,uncertainty) VALUES(?,?,?,?)
                     ON CONFLICT(site_id,period) DO UPDATE SET delta_volume=excluded.delta_volume,
                     uncertainty=excluded.uncertainty""", (site_id, s.period, s.delta_volume, s.uncertainty))
    auth.log(u, "upsert", "storage", site_id, s.period)
    return ok({"ok": True})


# ---------------------------------------------------------------- تحلیل
def balance(request: Request):
    auth.require(request)
    site_id = sid(request)
    get_site(site_id)
    return ok(services.site_balance(site_id, opt_period(request)))


def monthly_series(request: Request):
    """سری ماهانه ورودی/خروجی/تولید برای نمودارها."""
    auth.require(request)
    site_id = sid(request)
    get_site(site_id)
    with db() as c:
        data = rows(c.execute("""SELECT r.period, s.direction, s.kind, SUM(COALESCE(r.corrected_volume,r.volume)) v
            FROM readings r JOIN streams s ON s.id=r.stream_id
            WHERE s.site_id=? AND s.scope='external' AND r.quality NOT IN ('invalid','missing')
            GROUP BY r.period, s.direction, s.kind ORDER BY r.period""", (site_id,)))
        prod = {p["period"]: p["product_qty"] for p in
                rows(c.execute("SELECT period, product_qty FROM production WHERE site_id=?", (site_id,)))}
    by: dict = {}
    for r in data:
        m = by.setdefault(r["period"], {"period": r["period"], "input": 0.0, "output": 0.0, "wastewater": 0.0,
                                        "evaporation": 0.0})
        if r["direction"] == "in":
            m["input"] += r["v"]
        else:
            m["output"] += r["v"]
            if r["kind"] in services.WASTE_KINDS:
                m["wastewater"] += r["v"]
            if r["kind"] == "evaporation":
                m["evaporation"] += r["v"]
    out = []
    for k in sorted(by):
        m = by[k]
        m["production"] = prod.get(k)
        m["residual"] = m["input"] - m["output"]
        m["specific"] = (m["input"] / prod[k]) if prod.get(k) else None
        out.append(m)
    return ok(out)


def kpis(request: Request):
    auth.require(request)
    site_id = sid(request)
    get_site(site_id)
    return ok(services.site_kpis(site_id, opt_period(request)))


def sankey(request: Request):
    """داده نمودار جریان آب (منابع → مجتمع → مصارف)."""
    auth.require(request)
    site_id = sid(request)
    get_site(site_id)
    ts = [t for t in services.stream_totals(site_id, opt_period(request)) if t["scope"] == "external" and t["volume"] > 0]
    return ok({"sources": [{"name": t["name"], "value": t["volume"]} for t in ts if t["direction"] == "in"],
               "sinks": [{"name": t["name"], "value": t["volume"], "kind": t["kind"]} for t in ts if t["direction"] == "out"]})


def anomalies(request: Request):
    auth.require(request)
    site_id = sid(request)
    get_site(site_id)
    from .engine.baseline import zscore_anomalies
    with db() as c:
        series = rows(c.execute("""SELECT r.period, SUM(COALESCE(r.corrected_volume,r.volume)) v FROM readings r
            JOIN streams s ON s.id=r.stream_id WHERE s.site_id=? AND s.direction='in' AND s.scope='external'
            AND r.quality NOT IN ('invalid','missing') GROUP BY r.period ORDER BY r.period""", (site_id,)))
    thr = float(request.query_params.get("threshold", 3))
    found = zscore_anomalies([s["v"] for s in series], thr)
    for f in found:
        f["period"] = series[f["index"]]["period"]
    return ok({"n": len(series), "threshold": thr, "anomalies": found,
               "note": "هشدار آماری اثبات خرابی/نشتی نیست؛ با تولید و بازدید میدانی تأیید شود."
               if found else "ناهنجاری آماری یافت نشد (حداقل ۶ دوره داده لازم است)."})


async def baseline(request: Request):
    auth.require(request)
    site_id = sid(request)
    get_site(site_id)
    d = parse(BaselineIn, await body(request))
    need_period(d.split_period)
    try:
        return ok(services.baseline_analysis(site_id, d.split_period, d.predictors))
    except ValueError as e:
        raise HTTPException(422, str(e))


# ---------------------------------------------------------------- محاسبه تجهیزات (بدون ذخیره)
CALCS = {
    "cooling-tower": eq.cooling_tower, "coc-saving": eq.cooling_tower_coc_saving, "boiler": eq.boiler,
    "ro": eq.reverse_osmosis, "pump": eq.pump, "wwtp": eq.wastewater_plant,
}


async def calc(request: Request):
    auth.require(request)
    fn = CALCS.get(request.path_params["kind"])
    if not fn:
        raise HTTPException(404, "نوع محاسبه ناشناخته است")
    d = await body(request)
    try:
        return ok(fn(**d))
    except TypeError as e:
        raise HTTPException(422, f"پارامترهای ورودی نادرست: {e}")
    except (ValueError, ZeroDivisionError) as e:
        raise HTTPException(422, str(e) or "مقادیر ورودی نامعتبر")


async def test_formula(request: Request):
    auth.require(request)
    d = await body(request)
    try:
        vars_ = {k: float(v) for k, v in (d.get("variables") or {}).items()}
        return ok({"value": safe_eval(str(d.get("formula", "")), vars_)})
    except (FormulaError, ValueError) as e:
        raise HTTPException(422, str(e))


# ---------------------------------------------------------------- دانش و KPI و پروژه‌ها
def list_measures(request: Request):
    auth.require(request)
    q = request.query_params
    sql, args = "SELECT * FROM measures WHERE 1=1", []
    if q.get("grp"):
        sql += " AND grp=?"; args.append(q["grp"])
    if q.get("hierarchy"):
        sql += " AND hierarchy=?"; args.append(q["hierarchy"])
    if q.get("system"):
        sql += " AND system_code=?"; args.append(q["system"])
    if q.get("q"):
        sql += " AND (title LIKE ? OR problem LIKE ? OR mechanism LIKE ?)"; args += [f"%{q['q']}%"] * 3
    with db() as c:
        return ok(rows(c.execute(sql + " ORDER BY grp, code", args)))


async def create_measure(request: Request):
    u = auth.require(request, "engineer")
    d = parse(MeasureIn, await body(request)).model_dump()
    cols = ",".join(d)
    try:
        with db() as c:
            cur = c.execute(f"INSERT INTO measures({cols}) VALUES({','.join('?' * len(d))})", list(d.values()))
    except sqlite3.IntegrityError:
        raise HTTPException(409, "کد راهکار تکراری است")
    auth.log(u, "create", "measure", cur.lastrowid, d["code"])
    return ok({"id": cur.lastrowid}, 201)


def recommend(request: Request):
    """پیشنهاد راهکار بر پایه سیستم‌هایی که در مجتمع تجهیز دارند (قاعده‌محور، شفاف)."""
    auth.require(request)
    site_id = sid(request)
    site = get_site(site_id)
    with db() as c:
        kinds = {e["kind"] for e in rows(c.execute("SELECT kind FROM equipment WHERE site_id=?", (site_id,)))}
        units = {u["system_code"] for u in rows(c.execute("SELECT system_code FROM units WHERE site_id=?", (site_id,)))}
        ms = rows(c.execute("SELECT * FROM measures ORDER BY grp, code"))
    sys_map = {"cooling_tower": "SYS-01", "boiler": "SYS-02", "ro": "SYS-03", "wwtp": "SYS-06", "pump": "SYS-08"}
    present = {sys_map[k] for k in kinds if k in sys_map} | units
    kp = {k["code"]: k for k in services.site_kpis(site_id)}
    out = []
    for m in ms:
        reasons = []
        if m["system_code"] and m["system_code"] in present:
            reasons.append(f"مجتمع سیستم {m['system_code']} دارد")
        elif not m["system_code"]:
            reasons.append("راهکار عمومی")
        if m["industries"] not in ("ALL", "") and site["industry_code"] in m["industries"].split(","):
            reasons.append("مرتبط با صنعت مجتمع")
        if kp.get("LEAK", {}).get("level") in ("هشدار", "بحرانی") and "نشتی" in m["title"]:
            reasons.append("شاخص نشتی در سطح هشدار/بحرانی است")
        if kp.get("IMBAL", {}).get("level") in ("هشدار", "بحرانی") and m["system_code"] == "SYS-08":
            reasons.append("عدم تطابق موازنه بالاست")
        if m["system_code"] and m["system_code"] not in present and not (m["industries"] not in ("ALL", "") and site["industry_code"] in m["industries"].split(",")):
            continue
        out.append({**m, "reasons": reasons})
    return ok(out)


def list_kpi_defs(request: Request):
    auth.require(request)
    with db() as c:
        return ok(rows(c.execute("SELECT * FROM kpi_defs ORDER BY grp, code")))


async def upsert_kpi_def(request: Request):
    u = auth.require(request, "engineer")
    d = parse(KpiDefIn, await body(request))
    try:  # اعتبارسنجی فرمول: فقط متغیرهای شناخته‌شده سیستم مجازند
        safe_eval(d.formula, {n: 1.0 for n in services.KPI_VAR_NAMES})
    except FormulaError as e:
        if "تقسیم بر صفر" not in str(e):
            raise HTTPException(422, f"فرمول نامعتبر: {e}")
    with db() as c:
        c.execute("""INSERT INTO kpi_defs(code,name,formula,unit,grp,warn,critical,reference) VALUES(?,?,?,?,?,?,?,?)
                     ON CONFLICT(code) DO UPDATE SET name=excluded.name, formula=excluded.formula, unit=excluded.unit,
                     grp=excluded.grp, warn=excluded.warn, critical=excluded.critical, reference=excluded.reference""",
                  (d.code, d.name, d.formula, d.unit, d.grp, d.warn, d.critical, d.reference))
    auth.log(u, "upsert", "kpi_def", d.code)
    return ok({"ok": True})


def list_projects(request: Request):
    auth.require(request)
    site_id = sid(request)
    get_site(site_id)
    return ok(services.site_projects(site_id))


async def add_project(request: Request):
    u = auth.require(request, "engineer")
    site_id = sid(request)
    get_site(site_id)
    p = parse(ProjectIn, await body(request))
    with db() as c:
        if p.measure_id and not one(c.execute("SELECT id FROM measures WHERE id=?", (p.measure_id,))):
            raise HTTPException(404, "راهکار یافت نشد")
        cur = c.execute("""INSERT INTO projects(site_id,measure_id,title,saving_m3_year,capex,opex_year,life_years,
            discount_rate,ease,supply_risk,environment,tech_risk,status) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (site_id, p.measure_id, p.title, p.saving_m3_year, p.capex, p.opex_year, p.life_years,
                         p.discount_rate, p.ease, p.supply_risk, p.environment, p.tech_risk, p.status))
    auth.log(u, "create", "project", cur.lastrowid, p.title)
    return ok({"id": cur.lastrowid}, 201)


async def update_project(request: Request):
    u = auth.require(request, "engineer")
    site_id, pid = sid(request), int(request.path_params["project_id"])
    p = parse(ProjectIn, await body(request))
    with db() as c:
        cur = c.execute("""UPDATE projects SET measure_id=?,title=?,saving_m3_year=?,capex=?,opex_year=?,life_years=?,
            discount_rate=?,ease=?,supply_risk=?,environment=?,tech_risk=?,status=? WHERE id=? AND site_id=?""",
                        (p.measure_id, p.title, p.saving_m3_year, p.capex, p.opex_year, p.life_years, p.discount_rate,
                         p.ease, p.supply_risk, p.environment, p.tech_risk, p.status, pid, site_id))
        if cur.rowcount == 0:
            raise HTTPException(404, "پروژه یافت نشد")
    auth.log(u, "update", "project", pid)
    return ok({"ok": True})


def delete_project(request: Request):
    u = auth.require(request, "engineer")
    site_id, pid = sid(request), int(request.path_params["project_id"])
    with db() as c:
        cur = c.execute("DELETE FROM projects WHERE id=? AND site_id=?", (pid, site_id))
        if cur.rowcount == 0:
            raise HTTPException(404, "پروژه یافت نشد")
    auth.log(u, "delete", "project", pid)
    return ok({"ok": True})


def project_ranking(request: Request):
    auth.require(request)
    site_id = sid(request)
    get_site(site_id)
    return ok(services.ranking(site_id))


def audit_log_view(request: Request):
    auth.require(request, "admin")
    with db() as c:
        return ok(rows(c.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT 500")))


def report(request: Request):
    auth.require(request)
    site_id = sid(request)
    get_site(site_id)
    return HTMLResponse(build_report(site_id, opt_period(request)))


def favicon(request: Request):
    return Response(status_code=204)


def health(request: Request):
    return ok({"status": "ok"})


def index(request: Request):
    return HTMLResponse((STATIC / "index.html").read_text(encoding="utf-8"))


# ---------------------------------------------------------------- مسیرها
S = "/api/sites/{site_id:int}"
routes = [
    Route("/", index),
    Route("/api/health", health),
    Route("/favicon.ico", favicon),
    Route("/api/auth/login", login, methods=["POST"]),
    Route("/api/auth/me", me),
    Route("/api/auth/password", change_password, methods=["POST"]),
    Route("/api/users", list_users),
    Route("/api/users", create_user, methods=["POST"]),
    Route("/api/sites", list_sites),
    Route("/api/sites", create_site, methods=["POST"]),
    Route(S, site_detail),
    Route(S, update_site, methods=["PUT"]),
    Route(S, delete_site, methods=["DELETE"]),
    Route(S + "/units", add_unit, methods=["POST"]),
    Route(S + "/equipment", add_equipment, methods=["POST"]),
    Route(S + "/streams", list_streams),
    Route(S + "/streams", add_stream, methods=["POST"]),
    Route(S + "/streams/{stream_id:int}", delete_stream, methods=["DELETE"]),
    Route(S + "/readings", list_readings),
    Route(S + "/readings", add_readings, methods=["POST"]),
    Route(S + "/import", import_csv, methods=["POST"]),
    Route(S + "/production", list_production),
    Route(S + "/production", add_production, methods=["POST"]),
    Route(S + "/storage", add_storage, methods=["POST"]),
    Route(S + "/balance", balance),
    Route(S + "/series", monthly_series),
    Route(S + "/kpis", kpis),
    Route(S + "/sankey", sankey),
    Route(S + "/anomalies", anomalies),
    Route(S + "/baseline", baseline, methods=["POST"]),
    Route(S + "/recommendations", recommend),
    Route(S + "/projects", list_projects),
    Route(S + "/projects", add_project, methods=["POST"]),
    Route(S + "/projects/{project_id:int}", update_project, methods=["PUT"]),
    Route(S + "/projects/{project_id:int}", delete_project, methods=["DELETE"]),
    Route(S + "/ranking", project_ranking),
    Route(S + "/report", report),
    Route("/api/calc/{kind}", calc, methods=["POST"]),
    Route("/api/formula/test", test_formula, methods=["POST"]),
    Route("/api/measures", list_measures),
    Route("/api/measures", create_measure, methods=["POST"]),
    Route("/api/kpi-defs", list_kpi_defs),
    Route("/api/kpi-defs", upsert_kpi_def, methods=["POST"]),
    Route("/api/audit-log", audit_log_view),
    Mount("/static", StaticFiles(directory=str(STATIC)), name="static"),
]


async def http_error(request: Request, exc: HTTPException):
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)


async def server_error(request: Request, exc: Exception):
    return JSONResponse({"detail": "خطای داخلی سرور"}, status_code=500)


from contextlib import asynccontextmanager


@asynccontextmanager
async def lifespan(app):
    init_db()
    from .seed import seed_all
    seed_all()
    yield


app = Starlette(routes=routes, lifespan=lifespan,
                exception_handlers={HTTPException: http_error, 500: server_error})
