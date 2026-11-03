import math
import pytest
from app.engine.balance import Flow, water_balance
from app.engine import equipment as eq
from app.engine.kpi import safe_eval, FormulaError, evaluate_level
from app.engine.economics import payback, npv, irr, measure_economics, rank_measures
from app.engine.baseline import fit_baseline, predict, adjusted_saving, zscore_anomalies, night_base_flow


# ---------- موازنه ----------
def test_balance_simple():
    r = water_balance([Flow("in", 1000, 2)], [Flow("a", 600, 2), Flow("b", 380, 2)], storage_change=0)
    assert r.residual == pytest.approx(20)
    assert r.error_percent == pytest.approx(2.0)
    # u = sqrt(20^2 + 12^2 + 7.6^2)
    assert r.u_residual == pytest.approx(math.sqrt(20**2 + 12**2 + 7.6**2))
    assert not r.significant


def test_balance_storage_and_screening():
    r = water_balance([Flow("in", 1000, 0.5)], [Flow("a", 700, 0.5)], storage_change=50)
    assert r.residual == pytest.approx(250)
    assert "بررسی" in r.status


def test_balance_skips_invalid_and_warns():
    r = water_balance([Flow("in", 100)], [Flow("x", 90), Flow("bad", 999, quality="invalid")])
    assert r.total_out == 90
    assert any("bad" in w for w in r.warnings)


def test_balance_zero_input():
    r = water_balance([], [Flow("a", 5)])
    assert r.error_percent is None


# ---------- برج خنک‌کن ----------
def test_cooling_tower_theory():
    r = eq.cooling_tower(1000, 5, 4, drift_pct=0.0)
    E = 0.00085 * 1.8 * 5 * 1000
    assert r["evaporation_m3h"] == pytest.approx(E)
    assert r["blowdown_m3h"] == pytest.approx(E / 3)
    assert r["makeup_theory_m3h"] == pytest.approx(E + E / 3)


def test_cooling_tower_coc_from_ec_and_loss():
    r = eq.cooling_tower(1000, 5, 1.5, ec_makeup=500, ec_blowdown=2000, makeup_m3h=20, drift_pct=0)
    assert r["coc"] == pytest.approx(4)
    assert r["unexplained_loss_m3h"] > 0
    assert any("نشتی" in w for w in r["warnings"])


def test_cooling_tower_invalid_coc():
    with pytest.raises(ValueError):
        eq.cooling_tower(1000, 5, 1.0)


def test_coc_saving_3_to_6_about_20pct():
    r = eq.cooling_tower_coc_saving(1000, 5, 3, 6, hours=8000)
    assert 15 < r["saving_pct_of_makeup"] < 25   # هم‌خوان با مرجع DOE (~۲۰٪)


# ---------- بویلر / RO / پمپ / تصفیه ----------
def test_boiler():
    r = eq.boiler(100, 70, 35)
    assert r["condensate_return_pct"] == pytest.approx(70)
    assert r["makeup_ratio"] == pytest.approx(0.35)


def test_ro():
    r = eq.reverse_osmosis(100, 75, 1000, 20, p_feed_bar=15, p_conc_bar=13, power_kw=60)
    assert r["recovery_pct"] == pytest.approx(75)
    assert r["salt_rejection_pct"] == pytest.approx(98)
    assert r["sec_kwh_m3"] == pytest.approx(0.8)
    r2 = eq.reverse_osmosis(100, 90, 1000, 20)
    assert any("Recovery" in w for w in r2["warnings"])


def test_pump():
    # 100 m3/h, 30 m, 12 kW => 998*9.80665*(100/3600)*30/1000 = 8.157 kW
    r = eq.pump(100, 30, 12)
    assert r["hydraulic_kw"] == pytest.approx(8.157, abs=0.01)
    assert r["efficiency_pct"] == pytest.approx(67.98, abs=0.1)


def test_wwtp():
    r = eq.wastewater_plant(1000, 900, 800, 80, reuse_m3d=450)
    assert r["cod_removal_pct"] == pytest.approx(90)
    assert r["reuse_share_pct"] == pytest.approx(50)


# ---------- KPI ----------
def test_kpi_formula_and_safety():
    assert safe_eval("consumed / production", {"consumed": 1_000_000, "production": 200_000}) == 5
    assert safe_eval("reused / total_use * 100", {"reused": 300_000, "total_use": 1_300_000}) == pytest.approx(23.077, abs=0.001)
    for bad in ["__import__('os').system('ls')", "a.__class__", "open('x')", "[1,2]"]:
        with pytest.raises(FormulaError):
            safe_eval(bad, {"a": 1})
    with pytest.raises(FormulaError):
        safe_eval("a/0", {"a": 1})
    with pytest.raises(FormulaError):
        safe_eval("zz+1", {})


def test_levels():
    assert evaluate_level(12, 10, 20) == "هشدار"
    assert evaluate_level(25, 10, 20) == "بحرانی"
    assert evaluate_level(3, 10, 20) == "عادی"


# ---------- اقتصاد ----------
def test_economics():
    assert payback(100, 25) == 4
    assert payback(100, 0) is None
    assert npv(0.1, 100, [60, 60]) == pytest.approx(-100 + 60/1.1 + 60/1.21)
    r = irr(100, [60, 60])
    assert r == pytest.approx(0.1307, abs=1e-3)
    e = measure_economics(10_000, 2.0, 50_000, opex_year=2_000, life_years=10, discount_rate=0.1)
    assert e["annual_net_saving"] == pytest.approx(18_000)
    assert e["payback_years"] == pytest.approx(50_000 / 18_000)


def test_ranking_prefers_better():
    ms = [
        {"id": 1, "name": "A", "saving_m3_year": 100, "payback_years": 9, "ease": 1, "supply_risk": 1, "environment": 1, "tech_risk": 5},
        {"id": 2, "name": "B", "saving_m3_year": 90, "payback_years": 1, "ease": 5, "supply_risk": 4, "environment": 4, "tech_risk": 1},
    ]
    r = rank_measures(ms)
    assert r[0]["id"] == 2 and r[0]["rank"] == 1


# ---------- خط مبنا ----------
def test_baseline_recovers_coefficients():
    import numpy as np
    rng = np.random.default_rng(0)
    P = rng.uniform(100, 200, 60); T = rng.uniform(10, 40, 60)
    W = 50 + 2.0 * P + 3.0 * T + rng.normal(0, 0.5, 60)
    m = fit_baseline(np.c_[P, T].tolist(), W.tolist())
    assert m["coef"][1] == pytest.approx(2.0, abs=0.05)
    assert m["coef"][2] == pytest.approx(3.0, abs=0.1)
    assert m["r2"] > 0.99
    # صرفه‌جویی ۱۰ واحد در روز
    Xp = np.c_[P[:30], T[:30]].tolist()
    act = (np.array(predict(m, Xp)) - 10).tolist()
    s = adjusted_saving(m, Xp, act)
    assert s["saving"] == pytest.approx(300, abs=1e-6)
    assert s["significant"]


def test_baseline_errors():
    with pytest.raises(ValueError):
        fit_baseline([[1], [2]], [1, 2])


def test_anomaly_and_night_flow():
    vals = [10, 10.2, 9.8, 10.1, 9.9, 10.0, 10.3, 9.7, 30.0, 10.1]
    a = zscore_anomalies(vals, threshold=3)
    assert [x["index"] for x in a] == [8]
    hourly = ([5.0] * 24) * 3
    n = night_base_flow(hourly)
    assert n["night_mean"] == 5.0 and n["share_of_daily_pct"] == pytest.approx(100)
