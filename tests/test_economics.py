"""تحلیل اقتصادی راهکارها: بازگشت ساده/تنزیل‌یافته، NPV، IRR، BCR، سبد منتخب، و بررسی یکپارچگی."""
import sqlite3
import pytest

from app.engine.economics import measure_economics, portfolio_economics, discounted_payback


def _ann(r, n):
    return n if r == 0 else (1 - (1 + r) ** -n) / r


def test_indicators_exact_values():
    e = measure_economics(10_000, 2.0, 50_000, opex_year=2_000, life_years=10, discount_rate=0.10)
    net = 18_000
    assert e["annual_net_saving"] == net
    assert e["simple_payback_years"] == pytest.approx(50_000 / net)
    assert e["npv"] == pytest.approx(-50_000 + net * _ann(0.10, 10))
    # BCR = PV(منافع) / (CAPEX + PV(opex))
    assert e["pv_benefits"] == pytest.approx(20_000 * _ann(0.10, 10))
    assert e["bcr"] == pytest.approx(20_000 * _ann(0.10, 10) / (50_000 + 2_000 * _ann(0.10, 10)))
    # IRR: NPV در نرخ IRR صفر است
    r = e["irr"]
    assert -50_000 + net * _ann(r, 10) == pytest.approx(0, abs=1e-3)
    # بازگشت تنزیل‌یافته: سال ۴ با درون‌یابی؛ بزرگ‌تر از بازگشت ساده
    k = 50_000 / net
    cum = [_ann(0.10, t) for t in range(0, 11)]
    t0 = next(t for t in range(1, 11) if cum[t] >= k)
    expected = t0 - 1 + (k - cum[t0 - 1]) / (cum[t0] - cum[t0 - 1])
    assert e["discounted_payback_years"] == pytest.approx(expected)
    assert e["discounted_payback_years"] > e["simple_payback_years"]
    assert len(e["cash_flows"]) == 10 and e["cash_flows"][-1]["cumulative_discounted"] == pytest.approx(e["npv"])


def test_escalation_salvage_and_edge_cases():
    base = measure_economics(1000, 10, 20_000, 1000, 8, 0.12)
    esc = measure_economics(1000, 10, 20_000, 1000, 8, 0.12, escalation=0.05)
    assert esc["npv"] > base["npv"] and esc["cash_flows"][1]["benefit"] == pytest.approx(10_000 * 1.05)
    salv = measure_economics(1000, 10, 20_000, 1000, 8, 0.12, salvage_value=5000)
    assert salv["npv"] == pytest.approx(base["npv"] + 5000 / 1.12 ** 8)
    # بدون صرفه‌جویی خالص: بازگشت/IRR تعریف‌نشده، NPV منفی
    bad = measure_economics(100, 1, 10_000, 500, 5, 0.1)
    assert bad["simple_payback_years"] is None and bad["discounted_payback_years"] is None
    assert bad["irr"] is None and bad["npv"] < 0 and bad["bcr"] < 1
    # نرخ تنزیل صفر
    z = measure_economics(1000, 10, 20_000, 0, 5, 0.0)
    assert z["npv"] == pytest.approx(30_000) and z["discounted_payback_years"] == pytest.approx(2.0)
    # بدون CAPEX: بازگشت صفر
    assert measure_economics(1000, 10, 0, 0, 5, 0.1)["discounted_payback_years"] == 0.0
    with pytest.raises(ValueError):
        measure_economics(1, 1, 1, life_years=0)
    assert discounted_payback(100, [10, 10], 0.1) is None


def test_portfolio_aggregation():
    items = []
    for s, cx, ox, life, rate in [(10_000, 50_000, 2_000, 10, 0.10), (4_000, 30_000, 500, 6, 0.10)]:
        items.append({"capex": cx, "saving_m3_year": s,
                      "economics": measure_economics(s, 2.0, cx, ox, life, rate)})
    pf = portfolio_economics(items)
    assert pf["capex"] == 80_000 and pf["saving_m3_year"] == 14_000
    assert pf["npv"] == pytest.approx(sum(i["economics"]["npv"] for i in items))
    assert pf["simple_payback_years"] == pytest.approx(80_000 / sum(i["economics"]["annual_net_saving"] for i in items))
    assert pf["bcr"] == pytest.approx(sum(i["economics"]["pv_benefits"] for i in items) / sum(i["economics"]["pv_costs"] for i in items))
    # جریان تجمیعی با افق ۱۰ ساله (پروژه دوم ۶ ساله است)
    assert len(pf["cash_flows"]) == 10
    assert pf["cash_flows"][-1]["cumulative_discounted"] == pytest.approx(pf["npv"])
    # IRR تجمیعی: NPV جریان تجمیعی در نرخ IRR صفر است
    r = pf["irr"]
    assert -pf["capex"] + sum(c["net"] / (1 + r) ** c["year"] for c in pf["cash_flows"]) == pytest.approx(0, abs=1e-2)
    assert portfolio_economics([])["count"] == 0


# ---------------------------------------------------------------- API
def _site(client, admin):
    r = client.post("/api/sites", headers=admin, json={"name": "اقتصاد", "industry_code": "IND-03", "cost_supply": 1.5, "cost_disposal": 0.5})
    return r.json()["id"]


def _proj(client, admin, sid, **kw):
    body = {"title": "پروژه", "saving_m3_year": 10_000, "capex": 50_000, "opex_year": 2_000, "life_years": 10,
            "discount_rate": 0.10, **kw}
    r = client.post(f"/api/sites/{sid}/projects", headers=admin, json=body)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_project_api_economics_selection_and_portfolio(client, admin, viewer):
    sid = _site(client, admin)
    a = _proj(client, admin, sid, title="الف")
    b = _proj(client, admin, sid, title="ب", saving_m3_year=4_000, capex=30_000, opex_year=500, life_years=6, escalation=0.03)
    c = _proj(client, admin, sid, title="ج", status="rejected")
    lst = {p["id"]: p for p in client.get(f"/api/sites/{sid}/projects", headers=admin).json()}
    ea = lst[a]["economics"]
    assert ea["npv"] == pytest.approx(-50_000 + (10_000 * 2.0 - 2_000) * _ann(0.10, 10))
    assert {"simple_payback_years", "discounted_payback_years", "irr", "bcr"} <= set(ea) and "cash_flows" not in ea
    assert lst[b]["economics"]["cash_flows" if False else "annual_net_saving"] == pytest.approx(4_000 * 2.0 - 500)
    # جزئیات با جدول جریان نقدی
    det = client.get(f"/api/sites/{sid}/projects/{b}", headers=admin).json()
    assert len(det["economics"]["cash_flows"]) == 6 and det["economics"]["cash_flows"][1]["benefit"] == pytest.approx(8_000 * 1.03)
    assert client.get(f"/api/sites/{sid}/projects/99999", headers=admin).status_code == 404
    # هنوز چیزی منتخب نیست
    assert client.get(f"/api/sites/{sid}/projects/portfolio", headers=admin).json()["count"] == 0
    # انتخاب؛ viewer اجازه ندارد؛ شناسه ناموجود/نامعتبر خطا می‌دهد
    assert client.post(f"/api/sites/{sid}/projects/select", headers=viewer, json={"ids": [a]}).status_code == 403
    assert client.post(f"/api/sites/{sid}/projects/select", headers=admin, json={"ids": [a, 99999]}).status_code == 404
    assert client.post(f"/api/sites/{sid}/projects/select", headers=admin, json={"ids": "x"}).status_code == 422
    assert client.post(f"/api/sites/{sid}/projects/select", headers=admin, json={"ids": [a, b, c]}).status_code == 200
    pf = client.get(f"/api/sites/{sid}/projects/portfolio", headers=admin).json()
    assert pf["count"] == 2, "پروژه ردشده در ارزیابی منتخب‌ها لحاظ نمی‌شود"
    assert pf["capex"] == 80_000 and pf["npv"] == pytest.approx(lst[a]["economics"]["npv"] + client.get(f"/api/sites/{sid}/projects/{b}", headers=admin).json()["economics"]["npv"])
    assert {p["id"] for p in pf["projects"]} == {a, b}
    # بازنشانی: فقط الف
    client.post(f"/api/sites/{sid}/projects/select", headers=admin, json={"ids": [a]})
    assert client.get(f"/api/sites/{sid}/projects/portfolio", headers=admin).json()["count"] == 1
    assert client.get(f"/api/sites/{sid}/projects/portfolio?scope=all", headers=admin).json()["count"] == 2
    assert client.get(f"/api/sites/{sid}/projects/portfolio?scope=bad", headers=admin).status_code == 422
    rk = client.get(f"/api/sites/{sid}/ranking?selected=1", headers=admin).json()
    assert [r["id"] for r in rk] == [a] and rk[0]["rank"] == 1
    # انتخاب با ویرایش پروژه حفظ می‌شود
    body = {k: lst[a][k] for k in ("title", "saving_m3_year", "capex", "opex_year", "life_years", "discount_rate", "ease",
                                    "supply_risk", "environment", "tech_risk", "status", "escalation", "opex_escalation", "salvage_value")}
    assert client.put(f"/api/sites/{sid}/projects/{a}", headers=admin, json={**body, "selected": True, "capex": 60_000}).status_code == 200
    assert client.get(f"/api/sites/{sid}/projects/{a}", headers=admin).json()["selected"] is True


def test_standalone_economics_module(client, admin):
    r = client.post("/api/economics/evaluate", headers=admin, json={
        "saving_m3_year": 10_000, "unit_cost": 2, "capex": 50_000, "opex_year": 2_000, "life_years": 10, "discount_rate": 0.1})
    assert r.status_code == 200
    e = r.json()
    assert e["npv"] == pytest.approx(-50_000 + 18_000 * _ann(0.10, 10)) and e["bcr"] > 1 and len(e["cash_flows"]) == 10
    assert client.post("/api/economics/evaluate", headers=admin, json={"saving_m3_year": -1, "unit_cost": 2}).status_code == 422
    assert client.post("/api/economics/evaluate", json={"saving_m3_year": 1, "unit_cost": 1}).status_code == 401


# ---------------------------------------------------------------- یکپارچگی
def test_integrity_clean_catalog_and_demo_site(client, admin):
    g = client.get("/api/integrity", headers=admin).json()
    assert g["errors"] == 0, g["issues"]
    s = client.get("/api/sites/1/integrity", headers=admin).json()
    assert s["errors"] == 0, s["issues"]


def test_integrity_detects_broken_references(client, admin):
    import os
    sid = _site(client, admin)
    client.post(f"/api/sites/{sid}/units", headers=admin, json={"name": "واحد", "system_code": "SYS-01"})
    client.post(f"/api/sites/{sid}/streams", headers=admin, json={"name": "ورودی", "stream_type": "RAW"})
    _proj(client, admin, sid, title="سرمایه صفر", capex=0)
    ok = client.get(f"/api/sites/{sid}/integrity", headers=admin).json()
    assert ok["errors"] == 0 and any("CAPEX صفر" in i["message"] for i in ok["issues"])
    con = sqlite3.connect(os.environ["AQUA_DB"])
    con.execute("UPDATE units SET system_code='SYS-99' WHERE site_id=?", (sid,))
    con.execute("UPDATE streams SET stream_type='NOPE' WHERE site_id=?", (sid,))
    con.execute("UPDATE systems SET calc_module='ghost' WHERE code='SYS-04'")
    con.commit()
    try:
        bad = client.get(f"/api/sites/{sid}/integrity", headers=admin).json()
        msgs = " | ".join(i["message"] for i in bad["issues"] if i["level"] == "error")
        assert not bad["ok"] and "SYS-99" in msgs and "NOPE" in msgs and "ghost" in msgs
    finally:
        con.execute("UPDATE systems SET calc_module=NULL WHERE code='SYS-04'")
        con.commit(); con.close()
