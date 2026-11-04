import sqlite3
import pytest

DOC_STREAM_TYPES = {"RAW", "FRESH", "POTABLE", "PROCESS", "COOLING", "BFW", "CONDENSATE", "RO_PERMEATE", "RO_CONC",
                    "RECLAIMED", "RECYCLED", "WASTEWATER", "BLOWDOWN", "RAIN", "EVAPORATION"}


def new_site(client, admin, **kw):
    body = {"name": "کاتالوگ", "industry_code": "IND-03", "cost_supply": 10, **kw}
    r = client.post("/api/sites", headers=admin, json=body)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_catalog_matches_document(client, admin):
    cat = client.get("/api/catalog", headers=admin).json()
    assert [i["code"] for i in cat["industries"][:15]] == [f"IND-{n:02d}" for n in range(1, 16)]
    assert [s["code"] for s in cat["systems"][:12]] == [f"SYS-{n:02d}" for n in range(1, 13)]
    assert DOC_STREAM_TYPES <= {t["code"] for t in cat["stream_types"]}
    assert len(cat["source_categories"]) == 5
    names = {i["code"]: i["name"] for i in cat["industries"]}
    assert names["IND-03"] == "فلزات" and names["IND-10"] == "دارویی"
    sy = {s["code"]: s for s in cat["systems"]}
    assert "برج خنک‌کن (Cooling Tower)" in sy["SYS-01"]["equipment"]
    assert {"RO", "UF", "MF", "NF"} == set(sy["SYS-03"]["equipment"])
    assert sy["SYS-01"]["calc_module"] == "cooling-tower"
    # هر سیستم، هر صنعت و هر ارجاع کاتالوگ به دادهٔ موجود اشاره می‌کند
    sys_codes, st_codes = set(sy), {t["code"] for t in cat["stream_types"]}
    for i in cat["industries"]:
        assert set(i["typical_systems"]) <= sys_codes and i["subgroups"] and i["processes"], i["code"]
    for s in cat["systems"]:
        assert set(s["typical_in"] + s["typical_out"]) <= st_codes, s["code"]
        assert s["measure_count"] >= 1, f"{s['code']} راهکاری ندارد"


def test_industry_validation_on_site(client, admin):
    assert client.post("/api/sites", headers=admin, json={"name": "x", "industry_code": "IND-99"}).status_code == 422
    assert client.post("/api/sites", headers=admin, json={"name": "x", "industry_code": "IND-03", "subgroup_code": "IND-05-A"}).status_code == 422
    sid = new_site(client, admin, subgroup_code="IND-03-A")
    assert client.get(f"/api/sites/{sid}", headers=admin).json()["subgroup_code"] == "IND-03-A"


def test_apply_template_creates_units_idempotently(client, admin):
    sid = new_site(client, admin)
    r1 = client.post(f"/api/sites/{sid}/apply-template", headers=admin).json()
    codes = {u["code"] for u in r1["created"]}
    cat = client.get("/api/catalog", headers=admin).json()
    assert codes == set(next(i for i in cat["industries"] if i["code"] == "IND-03")["typical_systems"])
    r2 = client.post(f"/api/sites/{sid}/apply-template", headers=admin).json()
    assert r2["created"] == [] and set(r2["skipped"]) == codes
    assert len(client.get(f"/api/sites/{sid}", headers=admin).json()["units"]) == len(codes)


def test_stream_type_defaults_and_validation(client, admin):
    sid = new_site(client, admin)
    r = client.post(f"/api/sites/{sid}/streams", headers=admin, json={"name": "بلودان", "stream_type": "BLOWDOWN", "system_code": "SYS-01"})
    assert r.status_code == 201
    s = next(x for x in client.get(f"/api/sites/{sid}/streams", headers=admin).json() if x["name"] == "بلودان")
    assert (s["direction"], s["kind"], s["scope"], s["system_code"]) == ("out", "blowdown", "external", "SYS-01")
    assert s["origin"] and s["recycle_status"]            # از تعریف نوع جریان پر شده
    assert client.post(f"/api/sites/{sid}/streams", headers=admin, json={"name": "a", "stream_type": "NOPE"}).status_code == 422
    assert client.post(f"/api/sites/{sid}/streams", headers=admin, json={"name": "b", "stream_type": "RAW", "system_code": "SYS-99"}).status_code == 422
    assert client.post(f"/api/sites/{sid}/streams", headers=admin, json={"name": "c"}).status_code == 422
    assert client.post(f"/api/sites/{sid}/streams", headers=admin, json={"name": "d", "stream_type": "RAW", "source_category": "SRC-X"}).status_code == 422
    # سازگاری عقب‌رو: بدون stream_type مثل قبل
    assert client.post(f"/api/sites/{sid}/streams", headers=admin, json={"name": "e", "direction": "in", "kind": "raw"}).status_code == 201


def test_internal_process_flows_do_not_inflate_reuse_or_balance(client, admin):
    sid = new_site(client, admin)
    mk = lambda **b: client.post(f"/api/sites/{sid}/streams", headers=admin, json=b).json()["id"]
    raw = mk(name="raw", stream_type="RAW", system_code="SYS-08")
    ww = mk(name="ww", stream_type="WASTEWATER", system_code="SYS-06")
    rec = mk(name="rec", stream_type="RECYCLED", system_code="SYS-01")
    cool = mk(name="cool", stream_type="COOLING", system_code="SYS-01")      # گردشی: نباید بازچرخانی حساب شود
    proc = mk(name="proc", stream_type="PROCESS", system_code="SYS-04")
    client.post(f"/api/sites/{sid}/readings", headers=admin, json={"readings": [
        {"stream_id": raw, "period": "2025-01", "volume": 1000}, {"stream_id": ww, "period": "2025-01", "volume": 600},
        {"stream_id": rec, "period": "2025-01", "volume": 250}, {"stream_id": cool, "period": "2025-01", "volume": 50000},
        {"stream_id": proc, "period": "2025-01", "volume": 800}]})
    k = {x["code"]: x["value"] for x in client.get(f"/api/sites/{sid}/kpis", headers=admin).json()}
    assert k["REUSE"] == pytest.approx(250 / 1250 * 100)       # آب خنک‌کاری/فرآیندی داخلی لحاظ نمی‌شود
    b = client.get(f"/api/sites/{sid}/balance", headers=admin).json()
    assert b["total_in"] == 1000 and b["total_out"] == 600       # جریان‌های داخلی در موازنه مرز مجتمع نیستند


def test_by_system_accounting_exact(client, admin):
    sid = new_site(client, admin)
    u = client.post(f"/api/sites/{sid}/units", headers=admin, json={"name": "خنک", "system_code": "SYS-01"}).json()["id"]
    a = client.post(f"/api/sites/{sid}/streams", headers=admin, json={"name": "جبرانی", "stream_type": "FRESH", "unit_id": u}).json()["id"]
    b = client.post(f"/api/sites/{sid}/streams", headers=admin, json={"name": "تبخیر", "stream_type": "EVAPORATION", "system_code": "SYS-01"}).json()["id"]
    c_ = client.post(f"/api/sites/{sid}/streams", headers=admin, json={"name": "بدون سیستم", "stream_type": "RAW"}).json()["id"]
    client.post(f"/api/sites/{sid}/readings", headers=admin, json={"readings": [
        {"stream_id": a, "period": "2025-01", "volume": 900}, {"stream_id": b, "period": "2025-01", "volume": 700},
        {"stream_id": c_, "period": "2025-01", "volume": 100}]})
    r = client.get(f"/api/sites/{sid}/by-system", headers=admin).json()
    g = {s["code"]: s for s in r["systems"]}
    assert g["SYS-01"]["in_total"] == 900 and g["SYS-01"]["out_total"] == 700 and g["SYS-01"]["difference"] == 200
    assert g["SYS-01"]["share_of_site_input_pct"] == pytest.approx(90.0)        # سیستم از طریق واحد حل شد
    assert g["UNASSIGNED"]["in_total"] == 100
    assert "SYS-02" in {m["code"] for m in r["missing_typical_systems"]}         # IND-03 سیستم بخار دارد، تعریف نشده
    assert "SYS-01" not in {m["code"] for m in r["missing_typical_systems"]}
    assert client.get(f"/api/sites/{sid}/by-system?period=2030", headers=admin).json()["site_input"] == 0


def test_recommendations_use_industry_flagships(client, admin):
    sid = new_site(client, admin, industry_code="IND-06")
    rec = client.get(f"/api/sites/{sid}/recommendations", headers=admin).json()
    top = [m["code"] for m in rec if m["flagship"]]
    assert {"D-06", "C-10", "D-02"} <= set(top)
    assert rec[0]["flagship"] and any("شاخص صنعت" in r for r in rec[0]["reasons"])


def test_catalog_extension_permissions_and_validation(client, admin, viewer):
    ind = {"code": "IND-X1", "name": "صنعت آزمایشی", "typical_systems": ["SYS-01"], "measure_codes": ["B-01"],
           "subgroups": [["IND-X1-A", "زیرگروه"]], "processes": ["فرآیند"]}
    assert client.post("/api/catalog/industries", headers=viewer, json=ind).status_code == 403
    assert client.post("/api/catalog/industries", headers=admin, json={**ind, "typical_systems": ["SYS-77"]}).status_code == 422
    assert client.post("/api/catalog/industries", headers=admin, json={**ind, "measure_codes": ["ZZ-1"]}).status_code == 422
    assert client.post("/api/catalog/industries", headers=admin, json=ind).status_code == 200
    assert client.post("/api/sites", headers=admin, json={"name": "s", "industry_code": "IND-X1", "subgroup_code": "IND-X1-A"}).status_code == 201
    assert client.post("/api/catalog/systems", headers=admin, json={"code": "SYS-X1", "name_fa": "سیستم ویژه", "typical_in": ["RAW"]}).status_code == 200
    assert client.post("/api/catalog/systems", headers=admin, json={"code": "SYS-X2", "name_fa": "بد", "typical_in": ["NOPE"]}).status_code == 422
    st = {"code": "X_TYPE", "name_fa": "نوع ویژه", "direction": "in", "scope": "external", "kind": "raw"}
    assert client.post("/api/catalog/stream-types", headers=admin, json=st).status_code == 200
    assert client.post("/api/catalog/stream-types", headers=admin, json={**st, "kind": "evaporation"}).status_code == 422


def test_migration_upgrades_old_database_without_losing_data(tmp_path):
    from app.db import migrate
    p = tmp_path / "old.db"
    c = sqlite3.connect(p)
    c.row_factory = sqlite3.Row
    c.executescript("""CREATE TABLE sites(id INTEGER PRIMARY KEY, name TEXT, industry_code TEXT);
                       CREATE TABLE streams(id INTEGER PRIMARY KEY, site_id INT, name TEXT, direction TEXT, kind TEXT, scope TEXT);
                       INSERT INTO sites VALUES(1,'قدیمی','IND-03'); INSERT INTO streams VALUES(1,1,'چاه','in','raw','external');""")
    migrate(c)
    migrate(c)  # دوباره اجرا بی‌خطر است
    cols = {r["name"] for r in c.execute("PRAGMA table_info(streams)")}
    assert {"stream_type", "system_code", "source_category", "origin", "destination", "quality_class",
            "allowed_use", "recycle_status"} <= cols
    assert "subgroup_code" in {r["name"] for r in c.execute("PRAGMA table_info(sites)")}
    assert c.execute("SELECT name FROM streams WHERE id=1").fetchone()["name"] == "چاه"


# ------------------------------------------------------------- بانک تجهیزات/پارامترها/فرمول‌ها
def test_equipment_bank_parameters_instruments_formulas(client, admin):
    cat = client.get("/api/catalog", headers=admin).json()
    et = cat["equipment_types"]
    assert len(et) >= 150 and len({e["code"] for e in et}) == len(et)
    sys_codes = {s["code"] for s in cat["systems"]}
    assert all(e["system_code"] in sys_codes | {""} for e in et)
    assert all(any(e["system_code"] == s for e in et) for s in [f"SYS-{n:02d}" for n in range(1, 13)]), "هر سیستم باید تجهیز داشته باشد"
    # هر ۱۲ سیستم در سند، ردیف‌های تجهیز سند را دارند
    assert {"cooling_tower", "boiler", "ro", "uf", "dm_plant", "mbr", "hvac_system", "firewater_network"} <= {e["code"] for e in et}
    assert len(cat["parameters"]) == 23
    assert {p["grp"] for p in cat["parameters"]} == {"hydraulic", "quality"}
    assert len(cat["instruments"]) == 7 and cat["parameters_note"] and cat["instrument_note"]
    for s in cat["systems"][:12]:
        assert s["formulas"], s["code"]
    assert any("COC" in f["formula"] for f in next(s for s in cat["systems"] if s["code"] == "SYS-01")["formulas"])


def test_equipment_kind_validated_against_catalog(client, admin):
    sid = new_site(client, admin)
    ok = client.post(f"/api/sites/{sid}/equipment", headers=admin, json={"name": "ED", "kind": "deaerator"})
    assert ok.status_code == 201
    bad = client.post(f"/api/sites/{sid}/equipment", headers=admin, json={"name": "x", "kind": "nonexistent_kind"})
    assert bad.status_code == 422
    # سازگاری با نسخه قبلی
    for k in ("cooling_tower", "boiler", "ro", "pump", "wwtp", "other"):
        assert client.post(f"/api/sites/{sid}/equipment", headers=admin, json={"name": k, "kind": k}).status_code == 201
    # پیشنهاد راهکار از نگاشت تجهیز→سیستم کاتالوگ استفاده می‌کند
    rec = client.get(f"/api/sites/{sid}/recommendations", headers=admin).json()
    assert any("SYS-02" in " ".join(m["reasons"]) for m in rec)


def test_equipment_type_extension_and_seed_does_not_clobber_user_rows(client, admin, viewer):
    body = {"code": "my_special_unit", "name_fa": "واحد ویژه", "system_code": "SYS-04"}
    assert client.post("/api/catalog/equipment-types", headers=viewer, json=body).status_code == 403
    assert client.post("/api/catalog/equipment-types", headers=admin, json={**body, "system_code": "SYS-99"}).status_code == 422
    assert client.post("/api/catalog/equipment-types", headers=admin, json=body).status_code == 200
    # ویرایش ردیف سند توسط کاربر، با بذر مجدد (بروزرسانی نرم‌افزار) از بین نمی‌رود
    assert client.post("/api/catalog/systems", headers=admin, json={
        "code": "SYS-10", "name_fa": "بهداشتی (اصلاح‌شده)", "typical_in": [], "typical_out": []}).status_code == 200
    from app import seed
    seed.seed_catalog()
    cat = client.get("/api/catalog", headers=admin).json()
    assert any(e["code"] == "my_special_unit" for e in cat["equipment_types"])
    s10 = next(s for s in cat["systems"] if s["code"] == "SYS-10")
    assert s10["name_fa"] == "بهداشتی (اصلاح‌شده)" and s10["source"] == "user"


def test_new_calculators(client, admin):
    r = client.post("/api/calc/dm", headers=admin, json={"feed_m3": 1000, "product_m3": 900, "regen_waste_m3": 100}).json()
    assert round(r["recovery_pct"], 6) == 90 and round(r["waste_ratio"], 6) == round(100 / 900, 6)
    r = client.post("/api/calc/distribution", headers=admin, json={"v_input_m3": 1000, "v_delivered_m3": 800, "test_volume_m3": 50}).json()
    assert r["loss_m3"] == 150 and r["loss_pct"] == 15
    r = client.post("/api/calc/sanitary", headers=admin, json={"volume_m3": 600, "persons": 100, "days": 30}).json()
    assert r["per_capita_l_person_day"] == 200 and r["excess_m3"] == 420
    r = client.post("/api/calc/irrigation", headers=admin, json={"area_m2": 10000, "et0_mm_day": 5, "kc": 0.8, "days": 30,
                                                                    "actual_m3": 1000, "efficiency": 0.8}).json()
    assert round(r["required_m3"], 6) == 1500 and r["excess_m3"] == -500
    assert client.post("/api/calc/dm", headers=admin, json={"feed_m3": 0, "product_m3": 1}).status_code == 422
    assert client.post("/api/calc/irrigation", headers=admin, json={"area_m2": 1, "et0_mm_day": 1, "kc": 1, "days": 1,
                                                                       "actual_m3": 1, "efficiency": 0}).status_code == 422
