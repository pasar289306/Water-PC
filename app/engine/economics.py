"""موتور ارزیابی اقتصادی و رتبه‌بندی راهکارها."""
from __future__ import annotations

import numpy as np


def payback(capex: float, annual_net_saving: float) -> float | None:
    if annual_net_saving <= 0:
        return None
    return capex / annual_net_saving


def npv(rate: float, capex: float, cash_flows: list[float]) -> float:
    return -capex + sum(cf / (1 + rate) ** (t + 1) for t, cf in enumerate(cash_flows))


def irr(capex: float, cash_flows: list[float]) -> float | None:
    """IRR با روش دوبخشی (بدون وابستگی)."""
    f = lambda r: npv(r, capex, cash_flows)
    lo, hi = -0.99, 1000.0  # تا ۱۰۰٬۰۰۰٪؛ بازگشت‌های بسیار سریع هم IRR دارند
    if f(lo) * f(hi) > 0:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2
        if f(lo) * f(mid) <= 0:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


def discounted_payback(capex: float, flows: list[float], rate: float) -> float | None:
    """زمان بازگشت تنزیل‌یافته (سال، با درون‌یابی خطی در سال بازگشت)؛ اگر در عمر پروژه برنگردد None."""
    if capex <= 0:
        return 0.0
    cum = 0.0
    for t, cf in enumerate(flows):
        pv = cf / (1 + rate) ** (t + 1)
        if cum + pv >= capex and pv > 0:
            return t + (capex - cum) / pv
        cum += pv
    return None


def measure_economics(saving_m3_year: float, marginal_cost_m3: float, capex: float,
                      opex_year: float = 0.0, life_years: int = 10,
                      discount_rate: float = 0.15, escalation: float = 0.0,
                      opex_escalation: float = 0.0, salvage_value: float = 0.0) -> dict:
    """ارزیابی اقتصادی یک راهکار صرفه‌جویی.
    - منفعت سال t = صرفه‌جویی آب × هزینه نهایی هر m³ × (1+escalation)^(t-1)   (t = 1..life)
    - هزینه بهره‌برداری سال t = opex × (1+opex_escalation)^(t-1)
    - سرمایه‌گذاری CAPEX در زمان صفر؛ ارزش اسقاط (اختیاری) در پایان عمر
    خروجی: بازگشت ساده، بازگشت تنزیل‌یافته، NPV، IRR، نسبت منفعت به هزینه (BCR = PV منافع / (CAPEX + PV هزینه‌ها)) و جدول جریان نقدی."""
    if life_years < 1:
        raise ValueError("عمر پروژه باید حداقل ۱ سال باشد")
    gross = saving_m3_year * marginal_cost_m3
    net = gross - opex_year
    rows_, flows = [], []
    cum = -capex
    cum_d = -capex
    pv_ben = pv_opex = 0.0
    for t in range(1, life_years + 1):
        ben = gross * (1 + escalation) ** (t - 1)
        opx = opex_year * (1 + opex_escalation) ** (t - 1)
        extra = salvage_value if t == life_years else 0.0
        n = ben - opx + extra
        d = 1 / (1 + discount_rate) ** t
        pv_ben += (ben + extra) * d
        pv_opex += opx * d
        cum += n
        cum_d += n * d
        flows.append(n)
        rows_.append({"year": t, "benefit": ben, "opex": opx, "net": n, "discounted": n * d,
                      "cumulative": cum, "cumulative_discounted": cum_d})
    pv_costs = capex + pv_opex
    sp = payback(capex, net)
    return {"annual_gross_saving": gross, "annual_net_saving": net,
            "simple_payback_years": sp, "payback_years": sp,
            "discounted_payback_years": discounted_payback(capex, flows, discount_rate),
            "npv": npv(discount_rate, capex, flows),
            "irr": irr(capex, flows),
            "bcr": (pv_ben / pv_costs) if pv_costs > 0 else None,
            "pv_benefits": pv_ben, "pv_costs": pv_costs,
            "lcc_per_m3": ((capex + opex_year * life_years) / (saving_m3_year * life_years)
                           if saving_m3_year > 0 else None),
            "cash_flows": rows_}


def portfolio_economics(items: list[dict]) -> dict:
    """ارزیابی تجمیعی مجموعه‌ای از راهکارهای منتخب. هر آیتم: {capex, saving_m3_year, economics(با cash_flows)}.
    NPV و PV منافع/هزینه‌ها جمع‌پذیرند؛ IRR و بازگشت‌ها از جریان نقدی تجمیعی (جمع سال‌به‌سال) محاسبه می‌شوند.
    هشدار: جمع اثرات فقط برای راهکارهای مستقل از هم معتبر است."""
    if not items:
        return {"count": 0, "capex": 0.0, "saving_m3_year": 0.0, "annual_net_saving": 0.0, "npv": 0.0,
                "simple_payback_years": None, "discounted_payback_years": None, "irr": None, "bcr": None,
                "cash_flows": []}
    capex = sum(i["capex"] for i in items)
    horizon = max(len(i["economics"]["cash_flows"]) for i in items)
    cf = []
    cum = -capex
    cum_d = -capex
    for t in range(horizon):
        net = sum(i["economics"]["cash_flows"][t]["net"] for i in items if t < len(i["economics"]["cash_flows"]))
        dis = sum(i["economics"]["cash_flows"][t]["discounted"] for i in items if t < len(i["economics"]["cash_flows"]))
        cum += net
        cum_d += dis
        cf.append({"year": t + 1, "net": net, "discounted": dis, "cumulative": cum, "cumulative_discounted": cum_d})
    net_flows = [r["net"] for r in cf]
    first_net = net_flows[0] if net_flows else 0.0
    # بازگشت تنزیل‌یافته تجمیعی از روی جریان تنزیل‌شده
    dpb = None
    run = -capex
    for r in cf:
        if run + r["discounted"] >= 0 and r["discounted"] > 0:
            dpb = r["year"] - 1 + (-run) / r["discounted"] if run < 0 else float(r["year"] - 1)
            break
        run += r["discounted"]
    pv_b = sum(i["economics"]["pv_benefits"] for i in items)
    pv_c = sum(i["economics"]["pv_costs"] for i in items)
    return {"count": len(items), "capex": capex,
            "saving_m3_year": sum(i["saving_m3_year"] for i in items),
            "annual_net_saving": sum(i["economics"]["annual_net_saving"] for i in items),
            "npv": sum(i["economics"]["npv"] for i in items),
            "simple_payback_years": payback(capex, first_net) if capex > 0 else 0.0,
            "discounted_payback_years": dpb if capex > 0 else 0.0,
            "irr": irr(capex, net_flows), "bcr": (pv_b / pv_c) if pv_c > 0 else None,
            "pv_benefits": pv_b, "pv_costs": pv_c, "cash_flows": cf}


DEFAULT_WEIGHTS = {"saving": 0.25, "economics": 0.20, "ease": 0.15,
                   "supply_risk": 0.15, "environment": 0.15, "tech_risk": 0.10}


def rank_measures(measures: list[dict], weights: dict[str, float] | None = None) -> list[dict]:
    """امتیازدهی وزنی Score_i = Σ w_j r_ij ؛ معیارها در بازه ۰..۱۰۰ نرمال می‌شوند.
    هر measure: {id, name, saving_m3_year, payback_years, ease(1-5), supply_risk(1-5),
                 environment(1-5), tech_risk(1-5; بالاتر = ریسک بیشتر)}"""
    w = dict(DEFAULT_WEIGHTS)
    if weights:
        w.update(weights)
    s = sum(w.values()) or 1.0
    w = {k: v / s for k, v in w.items()}
    if not measures:
        return []
    sav = np.array([m.get("saving_m3_year", 0) or 0 for m in measures], float)
    sav_n = sav / sav.max() * 100 if sav.max() > 0 else np.zeros_like(sav)
    out = []
    for i, m in enumerate(measures):
        pb = m.get("payback_years")
        eco = 0.0 if pb is None else max(0.0, min(100.0, 100 * (1 - pb / 10)))
        ease = (m.get("ease", 3) - 1) / 4 * 100
        sr = (m.get("supply_risk", 3) - 1) / 4 * 100
        env = (m.get("environment", 3) - 1) / 4 * 100
        tr = 100 - (m.get("tech_risk", 3) - 1) / 4 * 100
        score = (w["saving"] * sav_n[i] + w["economics"] * eco + w["ease"] * ease +
                 w["supply_risk"] * sr + w["environment"] * env + w["tech_risk"] * tr)
        out.append({**m, "score": round(float(score), 2)})
    out.sort(key=lambda x: x["score"], reverse=True)
    for r, m in enumerate(out, 1):
        m["rank"] = r
    return out
