"""گزارش ممیزی HTML (قابل چاپ/ذخیره PDF از مرورگر)."""
from __future__ import annotations

from html import escape as e
from datetime import date

from . import services
from .db import db, one


def fmt(v, d=1):
    if v is None:
        return "—"
    return f"{v:,.{d}f}"


def build_report(site_id: int, period: str | None) -> str:
    with db() as c:
        site = one(c.execute("SELECT * FROM sites WHERE id=?", (site_id,)))
    bal = services.site_balance(site_id, period)
    kp = services.site_kpis(site_id, period)
    projects = services.site_projects(site_id)
    rank = services.ranking(site_id)
    mc = services.marginal_cost(site)
    sysacc = services.by_system(site_id, period)
    with db() as c:
        ind = one(c.execute("SELECT name, subgroups FROM industries WHERE code=?", (site["industry_code"],)))
    import json as _json
    sub_name = ""
    if ind and site.get("subgroup_code"):
        sub_name = next((n for code, n in _json.loads(ind["subgroups"]) if code == site["subgroup_code"]), "")
    ind_label = (f"{site['industry_code']} – {ind['name']}" if ind else site["industry_code"]) + (f" / {sub_name}" if sub_name else "")

    def tr(*cells):
        return "<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>"

    stream_rows = "".join(tr(e(s["name"]), "ورودی" if s["direction"] == "in" else "خروجی", e(s["kind"]),
                             fmt(s["volume"], 0), s["readings"], s["bad"] + s["suspect"]) for s in bal["streams"])
    kpi_rows = "".join(tr(e(k["name"]), fmt(k["value"], 2) if k["value"] is not None else (e(k["error"] or "—")),
                          e(k["unit"]), f'<span class="lv {e(k["level"])}">{e(k["level"])}</span>') for k in kp)
    proj_rows = "".join(tr(e(p["title"]), fmt(p["saving_m3_year"], 0), fmt(p["capex"], 0), fmt(p["opex_year"], 0),
                           fmt(p["economics"]["payback_years"], 1), fmt(p["economics"]["npv"], 0),
                           fmt(p["economics"]["irr"] * 100 if p["economics"]["irr"] is not None else None, 1),
                           e(p["status"])) for p in projects)
    sys_rows = "".join(tr(e(s["code"] if s["code"] != "UNASSIGNED" else "—"), e(s["name"]), fmt(s["in_total"], 0), fmt(s["out_total"], 0),
                          fmt(s["difference"], 0), fmt(s["share_of_site_input_pct"], 1) if s["share_of_site_input_pct"] is not None else "—")
                       for s in sysacc["systems"])
    missing = "".join(f"<li>{e(m['code'])} – {e(m['name'])}</li>" for m in sysacc["missing_typical_systems"])
    rank_rows = "".join(tr(r["rank"], e(r["name"]), r["score"], fmt(r["saving_m3_year"], 0)) for r in rank)
    warn = "".join(f"<li>{e(w)}</li>" for w in bal["warnings"])
    total_saving = sum(p["saving_m3_year"] for p in projects if p["status"] != "rejected")
    z = f'{bal["z_score"]:.2f}' if bal["z_score"] is not None else "—"
    ep = f'{bal["error_percent"]:.2f}٪' if bal["error_percent"] is not None else "—"

    return f"""<!doctype html><html lang="fa" dir="rtl"><head><meta charset="utf-8">
<title>گزارش ممیزی آب – {e(site['name'])}</title>
<style>
body{{font-family:Vazirmatn,Tahoma,sans-serif;margin:2rem auto;max-width:900px;color:#12202b;line-height:1.8}}
h1{{font-size:1.6rem}} h2{{border-bottom:2px solid #1d6fa5;padding-bottom:.2rem;margin-top:2rem;font-size:1.15rem}}
table{{border-collapse:collapse;width:100%;font-size:.88rem}} td,th{{border:1px solid #cbd5df;padding:.35rem .5rem;text-align:right}}
th{{background:#eaf2f8}} .note{{background:#fff7e0;border:1px solid #f0d58a;padding:.6rem 1rem;border-radius:6px}}
.lv.بحرانی{{color:#b3261e;font-weight:bold}} .lv.هشدار{{color:#a15c00;font-weight:bold}} .lv.عادی{{color:#1b6e3c}}
.grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:.6rem}} .card{{border:1px solid #cbd5df;border-radius:8px;padding:.6rem}}
.card b{{display:block;font-size:1.2rem}} @media print{{body{{margin:0}}}}
</style></head><body>
<h1>گزارش ممیزی و موازنه آب</h1>
<p><b>مجتمع:</b> {e(site['name'])} &nbsp; <b>صنعت:</b> {e(ind_label)} &nbsp; <b>دوره:</b> {e(bal['period'])} &nbsp; <b>تاریخ تهیه:</b> {date.today().isoformat()}</p>

<h2>۱. موازنه آب سایت</h2>
<div class="grid">
<div class="card">مجموع ورودی<b>{fmt(bal['total_in'],0)} m³</b></div>
<div class="card">مجموع خروجی ثبت‌شده<b>{fmt(bal['total_out'],0)} m³</b></div>
<div class="card">باقیمانده (R)<b>{fmt(bal['residual'],0)} m³</b></div>
<div class="card">درصد اختلاف<b>{ep}</b></div>
<div class="card">عدم قطعیت (u<sub>R</sub>)<b>±{fmt(bal['u_residual'],0)} m³</b></div>
<div class="card">Z = R/u<sub>R</sub><b>{z}</b></div></div>
<p><b>وضعیت:</b> {e(bal['status'])}</p>
<p class="note">باقیمانده موازنه به‌طور خودکار نشتی فرض نشده است؛ می‌تواند ناشی از خطای اندازه‌گیری، تخمین، اختلاف زمانی ثبت و
تغییر موجودی نیز باشد. آستانه {site['screening_pct']:.0f}٪ فقط آستانه غربالگری است.</p>
{f'<h3>هشدارهای کیفیت داده</h3><ul>{warn}</ul>' if warn else ''}
<table><tr><th>جریان</th><th>جهت</th><th>نوع</th><th>حجم (m³)</th><th>تعداد قرائت</th><th>داده مشکوک/نامعتبر</th></tr>{stream_rows}</table>

<h2>۲. مصرف به تفکیک سیستم‌های مصرف‌کننده</h2>
<table><tr><th>کد</th><th>سیستم</th><th>ورودی (m³)</th><th>خروجی (m³)</th><th>اختلاف</th><th>سهم از ورودی مجتمع٪</th></tr>{sys_rows or '<tr><td colspan=6>جریانی به سیستم‌ها اختصاص داده نشده است</td></tr>'}</table>
<p class="note">ورودی/خروجی هر سیستم شامل جریان‌های داخلی هم هست؛ مجموع ستون‌ها بین سیستم‌ها قابل جمع نیست (خروجی یک سیستم می‌تواند ورودی سیستم دیگر باشد). «سهم از ورودی مجتمع» فقط برای جریان‌های ورودی از مرز مجتمع است.</p>
{f'<h3>سیستم‌های نوعی این صنعت که هنوز تعریف نشده‌اند (شکاف داده)</h3><ul>{missing}</ul>' if missing else ''}

<h2>۳. شاخص‌های عملکرد (KPI)</h2>
<table><tr><th>شاخص</th><th>مقدار</th><th>واحد</th><th>وضعیت</th></tr>{kpi_rows}</table>

<h2>۴. پروژه‌های صرفه‌جویی</h2>
<p>هزینه نهایی هر مترمکعب آب (تأمین+پمپاژ+تصفیه+توزیع+دفع): <b>{fmt(mc,0)}</b> واحد پول. مجموع صرفه‌جویی پروژه‌های فعال: <b>{fmt(total_saving,0)} m³/سال</b>.</p>
<table><tr><th>پروژه</th><th>صرفه‌جویی (m³/سال)</th><th>CAPEX</th><th>OPEX/سال</th><th>بازگشت (سال)</th><th>NPV</th><th>IRR٪</th><th>وضعیت</th></tr>{proj_rows or '<tr><td colspan=8>پروژه‌ای ثبت نشده است</td></tr>'}</table>
<p class="note">پروژه‌ها ممکن است هم‌پوشانی داشته باشند؛ جمع صرفه‌جویی‌ها فقط در صورت استقلال فنی پروژه‌ها معتبر است.</p>

<h2>۵. اولویت‌بندی</h2>
<table><tr><th>رتبه</th><th>پروژه</th><th>امتیاز</th><th>صرفه‌جویی (m³/سال)</th></tr>{rank_rows or '<tr><td colspan=4>—</td></tr>'}</table>
<p style="font-size:.8rem;color:#555">تولید شده توسط AquaAudit. نتایج مبتنی بر داده‌های واردشده‌اند و باید توسط مهندس مسئول تأیید شوند.</p>
</body></html>"""
