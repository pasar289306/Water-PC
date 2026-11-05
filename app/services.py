"""سرویس‌های تجمیع: موازنه سایت، KPIها، خط مبنا و رتبه‌بندی بر پایه داده‌های ثبت‌شده."""
from __future__ import annotations

import json

from .db import db, rows, one
from .engine.balance import Flow, water_balance
from .engine.kpi import compute_kpi, evaluate_level, FormulaError
from .engine.economics import measure_economics, rank_measures, portfolio_economics
from .engine.baseline import fit_baseline, adjusted_saving

KPI_VAR_NAMES = ["input", "freshwater", "accounted", "wastewater", "evaporation", "product_water", "leakage",
                 "reused", "total_use", "consumed", "beneficial", "production", "supply", "pumping",
                 "treatment", "distribution", "disposal"]
FRESH_KINDS = {"raw", "potable", "fresh"}
# فقط این جریان‌های داخلی «بازچرخانی/استفاده مجدد» محسوب می‌شوند؛ سایر جریان‌های داخلی (آب فرآیندی، خنک‌کاری، …)
# صرفاً حسابداری سطح سیستم‌اند و نباید نرخ بازچرخانی را باد کنند (سند ۴.۴).
REUSE_KINDS = {"reclaimed", "recycled", "condensate"}
WASTE_KINDS = {"wastewater", "blowdown"}


def _period_filter(period: str | None):
    """period: None=همه، 'YYYY' = سال، 'YYYY-MM' = ماه."""
    if not period:
        return "", ()
    return "AND r.period LIKE ?", (period + "%",)


def stream_totals(site_id: int, period: str | None = None) -> list[dict]:
    clause, args = _period_filter(period)
    with db() as c:
        q = f"""
        SELECT s.*, u.system_code AS unit_system,
               COALESCE(SUM(COALESCE(r.corrected_volume, r.volume)),0) AS volume,
               COUNT(r.id) AS n,
               SUM(CASE WHEN r.quality IN ('invalid','missing') THEN 1 ELSE 0 END) AS n_bad,
               SUM(CASE WHEN r.quality='suspect' THEN 1 ELSE 0 END) AS n_suspect,
               SUM(CASE WHEN r.quality='estimated' THEN 1 ELSE 0 END) AS n_est
        FROM streams s LEFT JOIN readings r
          ON r.stream_id=s.id AND r.quality NOT IN ('invalid','missing') {clause}
        LEFT JOIN units u ON u.id=s.unit_id
        WHERE s.site_id=? GROUP BY s.id ORDER BY s.id"""
        return rows(c.execute(q, (*args, site_id)))


def site_balance(site_id: int, period: str | None = None) -> dict:
    ts = [t for t in stream_totals(site_id, period) if t["scope"] == "external"]
    with db() as c:
        site = one(c.execute("SELECT * FROM sites WHERE id=?", (site_id,)))
        if period:
            st = one(c.execute("SELECT COALESCE(SUM(delta_volume),0) d, COALESCE(SUM(uncertainty*uncertainty),0) u2 "
                               "FROM storage_changes WHERE site_id=? AND period LIKE ?", (site_id, period + "%")))
        else:
            st = one(c.execute("SELECT COALESCE(SUM(delta_volume),0) d, COALESCE(SUM(uncertainty*uncertainty),0) u2 "
                               "FROM storage_changes WHERE site_id=?", (site_id,)))
    ins = [Flow(t["name"], t["volume"], t["uncertainty_pct"], "estimated" if t["n_est"] else "valid")
           for t in ts if t["direction"] == "in"]
    outs = [Flow(t["name"], t["volume"], t["uncertainty_pct"], "estimated" if t["n_est"] else "valid")
            for t in ts if t["direction"] == "out"]
    res = water_balance(ins, outs, st["d"], st["u2"] ** 0.5, site["screening_pct"] if site else 10.0).to_dict()
    res["streams"] = [{"name": t["name"], "direction": t["direction"], "kind": t["kind"],
                       "volume": t["volume"], "readings": t["n"], "bad": t["n_bad"] or 0,
                       "suspect": t["n_suspect"] or 0} for t in ts]
    res["period"] = period or "all"
    return res


def kpi_variables(site_id: int, period: str | None = None) -> dict:
    all_t = stream_totals(site_id, period)
    ext = [t for t in all_t if t["scope"] == "external"]
    internal = [t for t in all_t if t["scope"] == "internal"]
    vol = lambda xs: float(sum(x["volume"] for x in xs))
    ins = [t for t in ext if t["direction"] == "in"]
    outs = [t for t in ext if t["direction"] == "out"]
    wastewater = vol([t for t in outs if t["kind"] in WASTE_KINDS])
    evaporation = vol([t for t in outs if t["kind"] == "evaporation"])
    product = vol([t for t in outs if t["kind"] == "product"])
    leakage = vol([t for t in outs if t["kind"] == "leakage"])
    inp = vol(ins)
    accounted = vol(outs)
    with db() as c:
        site = one(c.execute("SELECT * FROM sites WHERE id=?", (site_id,)))
        if period:
            p = one(c.execute("SELECT COALESCE(SUM(product_qty),0) q FROM production WHERE site_id=? AND period LIKE ?",
                              (site_id, period + "%")))
        else:
            p = one(c.execute("SELECT COALESCE(SUM(product_qty),0) q FROM production WHERE site_id=?", (site_id,)))
    reused = vol([t for t in internal if t["kind"] in REUSE_KINDS])
    residual = inp - accounted
    return {
        "input": inp, "freshwater": vol([t for t in ins if t["kind"] in FRESH_KINDS]),
        "accounted": accounted, "wastewater": wastewater, "evaporation": evaporation,
        "product_water": product, "leakage": leakage, "reused": reused,
        "total_use": inp + reused, "consumed": inp - wastewater,
        "beneficial": max(inp - leakage - max(residual, 0), 0),
        "production": float(p["q"]),
        "supply": site["cost_supply"], "pumping": site["cost_pumping"], "treatment": site["cost_treatment"],
        "distribution": site["cost_distribution"], "disposal": site["cost_disposal"],
    }


def site_kpis(site_id: int, period: str | None = None) -> list[dict]:
    v = kpi_variables(site_id, period)
    with db() as c:
        defs = rows(c.execute("SELECT * FROM kpi_defs ORDER BY grp, code"))
    out = []
    for d in defs:
        try:
            val = compute_kpi(d["formula"], v)
            err = None
        except FormulaError as e:
            val, err = None, str(e)
        out.append({"code": d["code"], "name": d["name"], "unit": d["unit"], "group": d["grp"],
                    "value": val, "level": evaluate_level(val, d["warn"], d["critical"]),
                    "error": err, "formula": d["formula"]})
    return out


def stream_system(t: dict) -> str:
    """سیستم مصرف‌کننده یک جریان: مقدار صریح، وگرنه سیستم واحدِ مربوط."""
    return (t.get("system_code") or t.get("unit_system") or "").strip()


def by_system(site_id: int, period: str | None = None) -> dict:
    """حسابداری سطح سیستم (Process Water Accounting): ورودی/خروجی هر سیستم مصرف‌کننده و سهم آن از ورودی مجتمع.

    جریان‌های داخلی و خارجی هر دو در سطح سیستم شمرده می‌شوند (برخلاف موازنه مرز مجتمع). مجموع بین سیستم‌ها
    نباید با هم جمع شود زیرا خروجی یک سیستم می‌تواند ورودی سیستم دیگر باشد؛ سهم از ورودی مجتمع فقط برای جریان‌های
    ورودی خارجی محاسبه می‌شود."""
    ts = stream_totals(site_id, period)
    with db() as c:
        site = one(c.execute("SELECT * FROM sites WHERE id=?", (site_id,)))
        names = {r["code"]: r for r in rows(c.execute("SELECT code,name_fa,name_en,category FROM systems"))}
        ind = one(c.execute("SELECT * FROM industries WHERE code=?", (site["industry_code"],)))
        units = rows(c.execute("SELECT system_code FROM units WHERE site_id=?", (site_id,)))
    site_in = sum(t["volume"] for t in ts if t["scope"] == "external" and t["direction"] == "in")
    groups: dict[str, dict] = {}
    for t in ts:
        code = stream_system(t) or "UNASSIGNED"
        g = groups.setdefault(code, {"code": code, "in_total": 0.0, "out_total": 0.0, "external_in": 0.0,
                                     "external_out": 0.0, "streams": []})
        v = t["volume"]
        if t["direction"] == "in":
            g["in_total"] += v
            if t["scope"] == "external":
                g["external_in"] += v
        else:
            g["out_total"] += v
            if t["scope"] == "external":
                g["external_out"] += v
        g["streams"].append({"id": t["id"], "name": t["name"], "direction": t["direction"], "scope": t["scope"],
                             "kind": t["kind"], "stream_type": t.get("stream_type") or "", "volume": v})
    out = []
    for code, g in groups.items():
        nm = names.get(code)
        g["name"] = nm["name_fa"] if nm else ("بدون سیستم" if code == "UNASSIGNED" else code)
        g["category"] = nm["category"] if nm else ""
        g["difference"] = g["in_total"] - g["out_total"]
        g["share_of_site_input_pct"] = (g["external_in"] / site_in * 100) if site_in else None
        out.append(g)
    out.sort(key=lambda x: (x["code"] == "UNASSIGNED", -x["in_total"]))
    typical = json.loads(ind["typical_systems"]) if ind else []
    defined = {s["system_code"] for s in units} | {c for c in groups if c != "UNASSIGNED"}
    return {"period": period or "all", "site_input": site_in, "systems": out,
            "industry": ({"code": ind["code"], "name": ind["name"]} if ind else None),
            "missing_typical_systems": [{"code": c, "name": names[c]["name_fa"] if c in names else c}
                                        for c in typical if c not in defined]}


def project_economics(p: dict, marginal_cost: float) -> dict:
    return measure_economics(p["saving_m3_year"], marginal_cost, p["capex"], p["opex_year"],
                             p["life_years"], p["discount_rate"], p.get("escalation") or 0.0,
                             p.get("opex_escalation") or 0.0, p.get("salvage_value") or 0.0)


def marginal_cost(site: dict) -> float:
    return sum(site[k] or 0 for k in ("cost_supply", "cost_pumping", "cost_treatment",
                                      "cost_distribution", "cost_disposal"))


def site_projects(site_id: int, with_flows: bool = False) -> list[dict]:
    with db() as c:
        site = one(c.execute("SELECT * FROM sites WHERE id=?", (site_id,)))
        ps = rows(c.execute("""SELECT p.*, m.code AS measure_code FROM projects p
                               LEFT JOIN measures m ON m.id=p.measure_id WHERE p.site_id=? ORDER BY p.id""", (site_id,)))
    mc = marginal_cost(site)
    for p in ps:
        p["economics"] = project_economics(p, mc)
        p["selected"] = bool(p["selected"])
        if not with_flows:
            p["economics"].pop("cash_flows", None)
    return ps


def annual_site_input(site_id: int) -> float | None:
    """ورودی سالانه مجتمع (m³) از میانگین ماهانه ورودی‌های مرزی × ۱۲؛ None اگر داده‌ای نباشد."""
    with db() as c:
        r = one(c.execute("""SELECT SUM(COALESCE(r.corrected_volume, r.volume)) v, COUNT(DISTINCT r.period) n
            FROM readings r JOIN streams s ON s.id=r.stream_id
            WHERE s.site_id=? AND s.direction='in' AND s.scope='external' AND r.quality NOT IN ('invalid','missing')""",
                        (site_id,)))
    return r["v"] / r["n"] * 12 if r and r["n"] else None


def portfolio(site_id: int, scope: str = "selected") -> dict:
    """ارزیابی اقتصادی تجمیعی: scope=selected (راهکارهای منتخب) یا all (همه غیررد‌شده‌ها)."""
    ps = [p for p in site_projects(site_id, with_flows=True) if p["status"] != "rejected"]
    if scope == "selected":
        ps = [p for p in ps if p["selected"]]
    res = portfolio_economics(ps)
    ai = annual_site_input(site_id)
    res["share_of_site_input_pct"] = res["saving_m3_year"] / ai * 100 if ai else None
    res["scope"] = scope
    res["projects"] = [{"id": p["id"], "title": p["title"], "status": p["status"], "selected": p["selected"],
                        "saving_m3_year": p["saving_m3_year"], "capex": p["capex"],
                        **{k: p["economics"][k] for k in ("annual_net_saving", "simple_payback_years",
                           "discounted_payback_years", "npv", "irr", "bcr")}} for p in ps]
    return res


def ranking(site_id: int, weights: dict | None = None) -> list[dict]:
    ps = [p for p in site_projects(site_id) if p["status"] != "rejected"]
    ms = [{"id": p["id"], "name": p["title"], "saving_m3_year": p["saving_m3_year"],
           "payback_years": p["economics"]["payback_years"], "ease": p["ease"],
           "supply_risk": p["supply_risk"], "environment": p["environment"], "tech_risk": p["tech_risk"],
           "capex": p["capex"], "npv": p["economics"]["npv"]} for p in ps]
    return rank_measures(ms, weights)


def baseline_analysis(site_id: int, split_period: str, predictors: list[str]) -> dict:
    """خط مبنا از داده‌های پیش از split_period؛ صرفه‌جویی دوره پس از آن (تعدیل‌شده)."""
    allowed = {"product_qty", "ambient_temp", "operating_hours"}
    if not predictors or any(p not in allowed for p in predictors):
        raise ValueError(f"پیش‌بینی‌کننده‌ها باید از {sorted(allowed)} باشند")
    with db() as c:
        prod = rows(c.execute("SELECT * FROM production WHERE site_id=? ORDER BY period", (site_id,)))
        water = {r["period"]: r["w"] for r in c.execute("""
            SELECT r.period, SUM(COALESCE(r.corrected_volume, r.volume)) w FROM readings r
            JOIN streams s ON s.id=r.stream_id
            WHERE s.site_id=? AND s.direction='in' AND s.scope='external'
              AND r.quality NOT IN ('invalid','missing') GROUP BY r.period""", (site_id,))}
    pre = [p for p in prod if p["period"] < split_period and p["period"] in water]
    post = [p for p in prod if p["period"] >= split_period and p["period"] in water]
    for p in pre + post:
        if any(p[k] is None for k in predictors):
            raise ValueError(f"مقدار متغیر برای دوره {p['period']} ثبت نشده است")
    if not post:
        raise ValueError("برای دوره پس از اجرا داده‌ای وجود ندارد")
    model = fit_baseline([[p[k] for k in predictors] for p in pre], [water[p["period"]] for p in pre])
    s = adjusted_saving(model, [[p[k] for k in predictors] for p in post], [water[p["period"]] for p in post])
    return {"predictors": predictors, "model": model, "n_pre": len(pre), "n_post": len(post),
            "result": s,
            "reliability": "قابل‌اتکا" if model["r2"] >= 0.75 and (model["cv_rmse_pct"] or 100) <= 25
                           else "ضعیف؛ نتیجه با احتیاط تفسیر شود (R² یا CV(RMSE) نامناسب)"}


def integrity(site_id: int | None = None, calc_modules: set[str] | None = None) -> dict:
    """بررسی یکپارچگی ارجاع‌ها بین بخش‌ها: کاتالوگ (صنعت/سیستم/جریان/تجهیز/راهکار/شاخص/ماشین‌حساب) و در صورت
    دادن site_id، داده‌های مجتمع (واحد، جریان، تجهیز، پروژه). هر مورد: level = error | warn."""
    issues: list[dict] = []
    add = lambda level, area, msg: issues.append({"level": level, "area": area, "message": msg})
    with db() as c:
        codes = lambda t, col="code": {r[0] for r in c.execute(f"SELECT {col} FROM {t}")}
        sys_c, st_c, ms_c = codes("systems"), codes("stream_types"), codes("measures")
        ind_c, kpi_c, eq_c, src_c = codes("industries"), codes("kpi_defs"), codes("equipment_types"), codes("source_categories")
        for r in rows(c.execute("SELECT code, typical_systems, measure_codes FROM industries")):
            for x in json.loads(r["typical_systems"] or "[]"):
                if x not in sys_c:
                    add("error", "catalog", f"صنعت {r['code']}: سیستم نوعی «{x}» تعریف نشده است")
            for x in json.loads(r["measure_codes"] or "[]"):
                if x not in ms_c:
                    add("error", "catalog", f"صنعت {r['code']}: راهکار «{x}» در بانک راهکارها نیست")
        for r in rows(c.execute("SELECT code, typical_in, typical_out, calc_module, kpis FROM systems")):
            for x in json.loads(r["typical_in"] or "[]") + json.loads(r["typical_out"] or "[]"):
                if x not in st_c:
                    add("error", "catalog", f"سیستم {r['code']}: نوع جریان «{x}» تعریف نشده است")
            for x in json.loads(r["kpis"] or "[]"):
                if x not in kpi_c:
                    add("warn", "catalog", f"سیستم {r['code']}: شاخص «{x}» در تعریف شاخص‌ها نیست")
            if calc_modules is not None:
                for x in [m.strip() for m in (r["calc_module"] or "").split(",") if m.strip()]:
                    if x not in calc_modules:
                        add("error", "catalog", f"سیستم {r['code']}: ماژول محاسباتی «{x}» وجود ندارد")
        for r in rows(c.execute("SELECT code, system_code FROM equipment_types")):
            if r["system_code"] and r["system_code"] not in sys_c:
                add("error", "catalog", f"نوع تجهیز {r['code']}: سیستم «{r['system_code']}» تعریف نشده است")
        for r in rows(c.execute("SELECT code, system_code, industries FROM measures")):
            if r["system_code"] and r["system_code"] not in sys_c:
                add("error", "catalog", f"راهکار {r['code']}: سیستم «{r['system_code']}» تعریف نشده است")
            for x in [i.strip() for i in (r["industries"] or "").split(",") if i.strip() and i.strip() != "ALL"]:
                if x not in ind_c:
                    add("warn", "catalog", f"راهکار {r['code']}: صنعت «{x}» تعریف نشده است")
        for r in rows(c.execute("SELECT DISTINCT system_code FROM system_formulas")):
            if r["system_code"] not in sys_c:
                add("error", "catalog", f"فرمول برای سیستم نامعتبر «{r['system_code']}»")
        if site_id is not None:
            site = one(c.execute("SELECT * FROM sites WHERE id=?", (site_id,)))
            if site["industry_code"] not in ind_c:
                add("error", "site", f"گروه صنعتی «{site['industry_code']}» در کاتالوگ نیست")
            elif site["subgroup_code"]:
                subs = {s[0] for s in json.loads(one(c.execute("SELECT subgroups FROM industries WHERE code=?",
                                                              (site["industry_code"],)))["subgroups"] or "[]")}
                if site["subgroup_code"] not in subs:
                    add("error", "site", f"زیرگروه «{site['subgroup_code']}» متعلق به این صنعت نیست")
            units = rows(c.execute("SELECT id, name, system_code FROM units WHERE site_id=?", (site_id,)))
            for u in units:
                if u["system_code"] not in sys_c:
                    add("error", "structure", f"واحد «{u['name']}»: سیستم «{u['system_code']}» نامعتبر است")
            unit_ids = {u["id"] for u in units}
            streams = rows(c.execute("SELECT * FROM streams WHERE site_id=?", (site_id,)))
            for s in streams:
                nm = s["name"]
                if s["stream_type"] and s["stream_type"] not in st_c:
                    add("error", "structure", f"جریان «{nm}»: نوع جریان «{s['stream_type']}» نامعتبر است")
                if s["system_code"] and s["system_code"] not in sys_c:
                    add("error", "structure", f"جریان «{nm}»: سیستم «{s['system_code']}» نامعتبر است")
                if s["source_category"] and s["source_category"] not in src_c:
                    add("error", "structure", f"جریان «{nm}»: دسته منبع «{s['source_category']}» نامعتبر است")
                if s["unit_id"] and s["unit_id"] not in unit_ids:
                    add("error", "structure", f"جریان «{nm}»: به واحدی از مجتمع دیگر وصل است")
                if s["direction"] == "in" and s["scope"] == "external" and not s["meter_id"]:
                    add("warn", "data", f"جریان ورودی «{nm}» شماره کنتور ندارد (ردیابی‌پذیری اندازه‌گیری)")
            if not streams:
                add("warn", "structure", "هنوز جریانی برای مجتمع تعریف نشده است")
            elif not any(s["direction"] == "in" and s["scope"] == "external" for s in streams):
                add("error", "structure", "هیچ جریان ورودی مرزی تعریف نشده است؛ موازنه محاسبه‌پذیر نیست")
            for e in rows(c.execute("SELECT name, kind, unit_id FROM equipment WHERE site_id=?", (site_id,))):
                if e["kind"] not in eq_c:
                    add("error", "structure", f"تجهیز «{e['name']}»: نوع «{e['kind']}» در بانک تجهیزات نیست")
                if e["unit_id"] and e["unit_id"] not in unit_ids:
                    add("error", "structure", f"تجهیز «{e['name']}»: به واحدی از مجتمع دیگر وصل است")
            ai = annual_site_input(site_id)
            for p in rows(c.execute("SELECT id, title, measure_id, saving_m3_year, capex, status, selected FROM projects WHERE site_id=?", (site_id,))):
                if p["measure_id"] and p["measure_id"] not in {r[0] for r in c.execute("SELECT id FROM measures")}:
                    add("error", "projects", f"پروژه «{p['title']}»: راهکار مرجع حذف شده است")
                if ai and p["saving_m3_year"] > ai:
                    add("warn", "projects", f"پروژه «{p['title']}»: صرفه‌جویی ({p['saving_m3_year']:.0f}) از ورودی سالانه مجتمع بیشتر است")
                if p["selected"] and p["status"] == "rejected":
                    add("warn", "projects", f"پروژه «{p['title']}» ردشده است ولی در منتخب‌ها علامت دارد (در ارزیابی لحاظ نمی‌شود)")
                if p["capex"] == 0 and p["saving_m3_year"] > 0:
                    add("warn", "projects", f"پروژه «{p['title']}»: CAPEX صفر است؛ IRR/BCR معنادار نیست")
            if site["cost_supply"] + site["cost_pumping"] + site["cost_treatment"] + site["cost_distribution"] + site["cost_disposal"] <= 0:
                add("warn", "site", "هزینه آب مجتمع صفر است؛ تحلیل اقتصادی راهکارها منفعتی نشان نمی‌دهد")
    return {"ok": not any(i["level"] == "error" for i in issues), "errors": sum(i["level"] == "error" for i in issues),
            "warnings": sum(i["level"] == "warn" for i in issues), "issues": issues}
