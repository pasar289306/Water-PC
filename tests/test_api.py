import io
import pytest


def test_health_and_auth_required(client):
    assert client.get("/api/health").json() == {"status": "ok"}
    assert client.get("/api/sites").status_code == 401
    assert client.get("/api/sites", headers={"Authorization": "Bearer garbage"}).status_code == 401


def test_login_wrong_password(client):
    r = client.post("/api/auth/login", json={"username": "admin", "password": "nope"})
    assert r.status_code == 401


def test_roles(client, admin, viewer):
    assert client.get("/api/sites", headers=viewer).status_code == 200
    assert client.post("/api/sites", headers=viewer, json={"name": "x"}).status_code == 403
    assert client.get("/api/users", headers=viewer).status_code == 403
    assert client.get("/api/audit-log", headers=viewer).status_code == 403
    assert client.get("/api/audit-log", headers=admin).status_code == 200


def test_user_validation_and_duplicates(client, admin):
    assert client.post("/api/users", headers=admin, json={"username": "ab", "password": "short"}).status_code == 422
    r1 = client.post("/api/users", headers=admin, json={"username": "dupuser", "password": "LongPass123"})
    r2 = client.post("/api/users", headers=admin, json={"username": "dupuser", "password": "LongPass123"})
    assert r1.status_code == 201 and r2.status_code == 409


def test_seed_demo_balance(client, admin):
    b = client.get("/api/sites/1/balance", headers=admin).json()
    assert b["total_in"] > 0 and abs(b["residual"]) < b["total_in"] * 0.1
    assert len(client.get("/api/measures", headers=admin).json()) >= 35


def test_site_stream_reading_flow_exact_numbers(client, admin):
    sid = client.post("/api/sites", headers=admin, json={
        "name": "تست", "cost_supply": 10, "cost_pumping": 2, "cost_treatment": 3,
        "cost_distribution": 1, "cost_disposal": 4}).json()["id"]
    a = client.post(f"/api/sites/{sid}/streams", headers=admin, json={"name": "in1", "direction": "in", "kind": "raw", "uncertainty_pct": 1}).json()["id"]
    b = client.post(f"/api/sites/{sid}/streams", headers=admin, json={"name": "out1", "direction": "out", "kind": "wastewater", "uncertainty_pct": 1}).json()["id"]
    c_ = client.post(f"/api/sites/{sid}/streams", headers=admin, json={"name": "ev", "direction": "out", "kind": "evaporation", "uncertainty_pct": 1}).json()["id"]
    rc = client.post(f"/api/sites/{sid}/streams", headers=admin, json={"name": "reuse", "direction": "in", "kind": "recycled", "scope": "internal"}).json()["id"]
    r = client.post(f"/api/sites/{sid}/readings", headers=admin, json={"readings": [
        {"stream_id": a, "period": "2025-01", "volume": 1000},
        {"stream_id": b, "period": "2025-01", "volume": 500},
        {"stream_id": c_, "period": "2025-01", "volume": 400},
        {"stream_id": rc, "period": "2025-01", "volume": 250}]})
    assert r.json()["saved"] == 4
    client.post(f"/api/sites/{sid}/production", headers=admin, json={"period": "2025-01", "product_qty": 200})
    bal = client.get(f"/api/sites/{sid}/balance", headers=admin).json()
    assert bal["total_in"] == 1000 and bal["total_out"] == 900 and bal["residual"] == 100
    assert bal["error_percent"] == pytest.approx(10.0)
    k = {x["code"]: x["value"] for x in client.get(f"/api/sites/{sid}/kpis", headers=admin).json()}
    assert k["SWC"] == pytest.approx((1000 - 500) / 200)            # مصرف واقعی = ورودی − پساب
    assert k["REUSE"] == pytest.approx(250 / 1250 * 100)             # بازچرخانی داخلی دوبار شمرده نمی‌شود
    assert k["COST_M3"] == 20
    # بازنویسی (upsert) قرائت
    client.post(f"/api/sites/{sid}/readings", headers=admin, json={"stream_id": a, "period": "2025-01", "volume": 2000})
    assert client.get(f"/api/sites/{sid}/balance", headers=admin).json()["total_in"] == 2000
    # داده نامعتبر در موازنه لحاظ نمی‌شود
    client.post(f"/api/sites/{sid}/readings", headers=admin, json={"stream_id": a, "period": "2025-01", "volume": 2000, "quality": "invalid"})
    assert client.get(f"/api/sites/{sid}/balance", headers=admin).json()["total_in"] == 0


def test_input_validation(client, admin):
    sid = client.post("/api/sites", headers=admin, json={"name": "val"}).json()["id"]
    s = client.post(f"/api/sites/{sid}/streams", headers=admin, json={"name": "s", "direction": "in", "kind": "raw"}).json()["id"]
    bad = [
        {"stream_id": s, "period": "2025-13", "volume": 1},
        {"stream_id": s, "period": "2025-01", "volume": -5},
        {"stream_id": s, "period": "2025-01", "volume": 1, "quality": "weird"},
        {"stream_id": 99999, "period": "2025-01", "volume": 1},
    ]
    for item in bad:
        assert client.post(f"/api/sites/{sid}/readings", headers=admin, json=item).status_code in (404, 422), item
    assert client.post(f"/api/sites/{sid}/streams", headers=admin, json={"name": "x", "direction": "in", "kind": "evaporation"}).status_code == 422
    assert client.post(f"/api/sites/{sid}/streams", headers=admin, json={"name": "s", "direction": "in", "kind": "raw"}).status_code == 409
    assert client.get("/api/sites/9999/balance", headers=admin).status_code == 404
    assert client.get(f"/api/sites/{sid}/balance?period=abc", headers=admin).status_code == 422
    r = client.post("/api/sites", headers=admin, content=b"not json", )
    assert r.status_code == 400


def test_csv_import(client, admin):
    sid = client.post("/api/sites", headers=admin, json={"name": "csv"}).json()["id"]
    client.post(f"/api/sites/{sid}/streams", headers=admin, json={"name": "چاه", "direction": "in", "kind": "raw"})
    csv_text = "stream,period,volume,quality\nچاه,2025-01,\"1,200\",valid\nچاه,2025-02,1300,estimated\nنامعلوم,2025-03,5,valid\nچاه,2025-14,5,valid\nچاه,2025-04,abc,valid\n"
    r = client.post(f"/api/sites/{sid}/import?type=readings", headers=admin,
                    files={"file": ("d.csv", io.BytesIO(csv_text.encode("utf-8")), "text/csv")})
    j = r.json()
    assert j["saved"] == 2 and len(j["errors"]) == 3
    assert j["errors"][0]["line"] == 4
    pcsv = "period,product_qty,ambient_temp,operating_hours\n2025-01,100,10,700\n2025-02,120,12,690\n"
    r = client.post(f"/api/sites/{sid}/import?type=production", headers=admin,
                    files={"file": ("p.csv", io.BytesIO(pcsv.encode()), "text/csv")})
    assert r.json()["saved"] == 2
    assert client.post(f"/api/sites/{sid}/import?type=bogus", headers=admin,
                       files={"file": ("p.csv", io.BytesIO(b"x"), "text/csv")}).status_code == 422
    assert client.post(f"/api/sites/{sid}/import", headers=admin).status_code in (400, 422)


def test_calc_endpoints(client, admin):
    r = client.post("/api/calc/cooling-tower", headers=admin, json={"circulation_m3h": 1000, "delta_t_c": 5, "coc": 4})
    assert r.status_code == 200 and r.json()["blowdown_m3h"] > 0
    assert client.post("/api/calc/cooling-tower", headers=admin, json={"circulation_m3h": 1000, "delta_t_c": 5, "coc": 1}).status_code == 422
    assert client.post("/api/calc/cooling-tower", headers=admin, json={"bogus": 1}).status_code == 422
    assert client.post("/api/calc/nothing", headers=admin, json={}).status_code == 404
    assert client.post("/api/calc/ro", headers=admin, json={"feed_m3h": 0, "permeate_m3h": 0, "ec_feed": 1, "ec_perm": 1}).status_code == 422


def test_kpi_formula_injection_blocked(client, admin):
    r = client.post("/api/formula/test", headers=admin, json={"formula": "__import__('os').system('id')", "variables": {}})
    assert r.status_code == 422
    r = client.post("/api/kpi-defs", headers=admin, json={"code": "EVIL", "name": "x", "formula": "open('/etc/passwd')"})
    assert r.status_code == 422
    r = client.post("/api/kpi-defs", headers=admin, json={"code": "UNK", "name": "x", "formula": "foo / 2"})
    assert r.status_code == 422
    r = client.post("/api/kpi-defs", headers=admin, json={"code": "MY1", "name": "نسبت", "formula": "freshwater / input * 100", "unit": "%"})
    assert r.status_code == 200
    assert any(k["code"] == "MY1" for k in client.get("/api/sites/1/kpis", headers=admin).json())


def test_projects_ranking_report_and_recommendations(client, admin):
    p = {"title": "بهینه‌سازی برج", "saving_m3_year": 40000, "capex": 500000, "opex_year": 20000,
         "ease": 4, "supply_risk": 4, "environment": 3, "tech_risk": 2}
    pid = client.post("/api/sites/1/projects", headers=admin, json=p).json()["id"]
    client.post("/api/sites/1/projects", headers=admin, json={**p, "title": "پروژه گران", "capex": 9e9, "saving_m3_year": 100})
    projs = client.get("/api/sites/1/projects", headers=admin).json()
    e = next(x for x in projs if x["id"] == pid)["economics"]
    # net = 40000*39000 - 20000
    assert e["annual_net_saving"] == pytest.approx(40000 * 39000 - 20000)
    assert e["payback_years"] == pytest.approx(500000 / e["annual_net_saving"])
    rk = client.get("/api/sites/1/ranking", headers=admin).json()
    assert rk[0]["id"] == pid and rk[0]["rank"] == 1
    assert client.put(f"/api/sites/1/projects/{pid}", headers=admin, json={**p, "status": "approved"}).status_code == 200
    assert client.put("/api/sites/1/projects/9999", headers=admin, json=p).status_code == 404
    rep = client.get("/api/sites/1/report", headers=admin)
    assert rep.status_code == 200 and "بهینه‌سازی برج" in rep.text and "dir=\"rtl\"" in rep.text
    rec = client.get("/api/sites/1/recommendations", headers=admin).json()
    assert any(m["code"] == "B-01" and m["reasons"] for m in rec)


def test_report_escapes_html(client, admin):
    sid = client.post("/api/sites", headers=admin, json={"name": "<script>alert(1)</script>"}).json()["id"]
    html = client.get(f"/api/sites/{sid}/report", headers=admin).text
    assert "<script>alert(1)</script>" not in html


def test_change_password(client, admin):
    client.post("/api/users", headers=admin, json={"username": "pwuser", "password": "OldPass1234", "role": "engineer"})
    t = client.post("/api/auth/login", json={"username": "pwuser", "password": "OldPass1234"}).json()["token"]
    h = {"Authorization": f"Bearer {t}"}
    assert client.post("/api/auth/password", headers=h, json={"old_password": "bad", "new_password": "NewPass1234"}).status_code == 403
    assert client.post("/api/auth/password", headers=h, json={"old_password": "OldPass1234", "new_password": "short"}).status_code == 422
    assert client.post("/api/auth/password", headers=h, json={"old_password": "OldPass1234", "new_password": "NewPass1234"}).status_code == 200
    assert client.post("/api/auth/login", json={"username": "pwuser", "password": "NewPass1234"}).status_code == 200


def test_baseline_and_series(client, admin):
    r = client.post("/api/sites/1/baseline", headers=admin, json={"split_period": "2025-10", "predictors": ["product_qty"]})
    assert r.status_code == 200 and "result" in r.json()
    assert client.post("/api/sites/1/baseline", headers=admin, json={"split_period": "2025-10", "predictors": ["evil"]}).status_code == 422
    assert client.post("/api/sites/1/baseline", headers=admin, json={"split_period": "2030-01", "predictors": ["product_qty"]}).status_code == 422
    s = client.get("/api/sites/1/series", headers=admin).json()
    assert len(s) == 12 and s[0]["specific"] is not None
    assert client.get("/api/sites/1/sankey", headers=admin).json()["sources"]
