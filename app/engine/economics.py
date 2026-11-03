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
    lo, hi = -0.99, 10.0
    if f(lo) * f(hi) > 0:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2
        if f(lo) * f(mid) <= 0:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


def measure_economics(saving_m3_year: float, marginal_cost_m3: float, capex: float,
                      opex_year: float = 0.0, life_years: int = 10,
                      discount_rate: float = 0.15, escalation: float = 0.0) -> dict:
    gross = saving_m3_year * marginal_cost_m3
    net = gross - opex_year
    flows = [net * (1 + escalation) ** t for t in range(life_years)]
    return {"annual_gross_saving": gross, "annual_net_saving": net,
            "payback_years": payback(capex, net),
            "npv": npv(discount_rate, capex, flows),
            "irr": irr(capex, flows),
            "lcc_per_m3": ((capex + opex_year * life_years) / (saving_m3_year * life_years)
                           if saving_m3_year > 0 else None)}


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
