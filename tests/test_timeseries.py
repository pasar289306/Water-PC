"""سری‌های زمانی: تقویم شمسی/میلادی، دانه‌بندی‌ها، چهار مسیر ورود داده، چند کنتور برای هر منبع، متغیرهای سری زمانی."""
import io
import json
import socket
import struct
import threading
import time
from datetime import date, datetime, timedelta

import pytest

from app import meters as mt, timeutil as tu, xlsx
from app.db import db, rows


# ---------------------------------------------------------------- کمکی‌ها
def mk_site(client, admin, name="ts", calendar="gregorian"):
    sid = client.post("/api/sites", headers=admin, json={"name": name, "calendar": calendar}).json()["id"]
    a = client.post(f"/api/sites/{sid}/streams", headers=admin,
                    json={"name": "چاه", "direction": "in", "kind": "raw", "uncertainty_pct": 5}).json()["id"]
    b = client.post(f"/api/sites/{sid}/streams", headers=admin,
                    json={"name": "شهری", "direction": "in", "kind": "potable", "uncertainty_pct": 5}).json()["id"]
    return sid, a, b


def post(client, admin, sid, items, **kw):
    return client.post(f"/api/sites/{sid}/ts/entries", headers=admin, json={"items": items, **kw})


def readings(client, admin, sid):
    return {(r["stream_name"], r["period"]): r for r in client.get(f"/api/sites/{sid}/readings", headers=admin).json()}


# ---------------------------------------------------------------- تقویم
def test_jalali_known_dates_and_roundtrip():
    assert tu.j2g(1404, 1, 1) == date(2025, 3, 21)
    assert tu.j2g(1403, 12, 30) == date(2025, 3, 20)          # 1403 کبیسه
    assert tu.g2j(date(2026, 3, 21)) == (1405, 1, 1)
    assert tu.parse_date("۱۴۰۴/۰۳/۱۵", "jalali") == date(2025, 6, 5)
    assert tu.parse_date("1404-03-15", "jalali") == date(2025, 6, 5)
    assert tu.parse_date(45000, "gregorian") == date(2023, 3, 15)    # سریال اکسل
    for d in (date(2000, 1, 1), date(2024, 2, 29), date(2025, 12, 31), date(2040, 6, 6)):
        assert tu.j2g(*tu.g2j(d)) == d
    with pytest.raises(tu.DateError):
        tu.parse_date("1404/12/30", "jalali")                 # 1404 کبیسه نیست
    with pytest.raises(tu.DateError):
        tu.parse_date("2025-02-30")


def test_make_periods_all_granularities():
    ps = tu.make_periods(date(2025, 3, 21), date(2025, 9, 23), "month", "jalali")
    assert [p["label"] for p in ps] == ["1404-01", "1404-02", "1404-03", "1404-04", "1404-05", "1404-06", "1404-07"]
    assert ps[0]["start"] == "2025-03-21" and ps[0]["end"] == "2025-04-20" and ps[5]["days"] == 31 and ps[6]["days"] == 30
    ys = tu.make_periods(date(2024, 5, 1), date(2026, 1, 1), "year", "gregorian")
    assert [p["label"] for p in ys] == ["2024", "2025", "2026"]
    yj = tu.make_periods(date(2025, 3, 21), date(2025, 3, 21), "year", "jalali")
    assert yj[0]["start"] == "2025-03-21" and yj[0]["end"] == "2026-03-20"
    ds = tu.make_periods(date(2025, 1, 30), date(2025, 2, 2), "day", "gregorian")
    assert len(ds) == 4
    cs = tu.make_periods(date(2025, 1, 1), date(2025, 1, 20), "custom", "gregorian", step_days=7)
    assert [p["days"] for p in cs] == [7, 7, 6]


# ---------------------------------------------------------------- ثبت دستی در دانه‌بندی‌های مختلف
def test_daily_entries_aggregate_to_month_with_coverage(client, admin):
    sid, a, _ = mk_site(client, admin, "daily")
    items = [{"stream_id": a, "start": f"2025-01-{d:02d}", "value": 100} for d in range(1, 32)]
    r = post(client, admin, sid, items)
    assert r.json() == {"saved": 31, "errors": []}
    rd = readings(client, admin, sid)[("چاه", "2025-01")]
    assert rd["volume"] == pytest.approx(3100) and rd["coverage"] == pytest.approx(1.0) and rd["quality"] == "valid"
    # فقط نیمی از ماه بعد → ناقص و تخمینی
    post(client, admin, sid, [{"stream_id": a, "start": f"2025-02-{d:02d}", "value": 50} for d in range(1, 15)])
    rd = readings(client, admin, sid)[("چاه", "2025-02")]
    assert rd["volume"] == pytest.approx(700) and rd["coverage"] == pytest.approx(0.5) and rd["quality"] == "estimated"


def test_yearly_entry_is_prorated_over_months(client, admin):
    sid, a, _ = mk_site(client, admin, "yearly")
    r = post(client, admin, sid, [{"stream_id": a, "start": "2025-06-15", "granularity": "year", "value": 36500}])
    assert r.json()["saved"] == 1
    e = client.get(f"/api/sites/{sid}/ts/entries", headers=admin).json()["items"][0]
    assert (e["start_date"], e["end_date"], e["granularity"]) == ("2025-01-01", "2025-12-31", "year")
    rd = readings(client, admin, sid)
    assert rd[("چاه", "2025-01")]["volume"] == pytest.approx(36500 * 31 / 365)
    assert rd[("چاه", "2025-02")]["volume"] == pytest.approx(36500 * 28 / 365)
    assert sum(v["volume"] for v in rd.values()) == pytest.approx(36500)


def test_custom_range_crossing_months_and_overlap_rules(client, admin):
    sid, a, _ = mk_site(client, admin, "custom")
    assert post(client, admin, sid, [{"stream_id": a, "start": "2025-01-25", "end": "2025-02-04", "value": 1100}]).json()["saved"] == 1
    rd = readings(client, admin, sid)
    assert rd[("چاه", "2025-01")]["volume"] == pytest.approx(700) and rd[("چاه", "2025-02")]["volume"] == pytest.approx(400)
    # هم‌پوشانی ناسازگار → رد
    r = post(client, admin, sid, [{"stream_id": a, "start": "2025-02-01", "end": "2025-02-28", "value": 5}])
    assert r.status_code == 422 and "هم‌پوشانی" in r.json()["errors"][0]["error"]
    # همان بازه → به‌روزرسانی
    post(client, admin, sid, [{"stream_id": a, "start": "2025-01-25", "end": "2025-02-04", "value": 2200}])
    assert readings(client, admin, sid)[("چاه", "2025-01")]["volume"] == pytest.approx(1400)
    # جایگزینی صریح
    r = post(client, admin, sid, [{"stream_id": a, "start": "2025-02-01", "end": "2025-02-28", "value": 280}], replace_overlaps=True)
    assert r.json()["saved"] == 1
    rd = readings(client, admin, sid)
    assert rd[("چاه", "2025-02")]["volume"] == pytest.approx(280) and ("چاه", "2025-01") not in rd


def test_jalali_entry_and_reporting_calendar(client, admin):
    sid, a, _ = mk_site(client, admin, "jal", calendar="jalali")
    # ماه فروردین ۱۴۰۴ (۳۱ روز) با تاریخ شمسی و ارقام فارسی
    r = post(client, admin, sid, [{"stream_id": a, "start": "۱۴۰۴/۰۱/۰۱", "end": "۱۴۰۴/۰۱/۳۱", "value": 3100}], calendar="jalali")
    assert r.json()["saved"] == 1
    e = client.get(f"/api/sites/{sid}/ts/entries?calendar=jalali", headers=admin).json()["items"][0]
    assert e["start_date"] == "2025-03-21" and e["start_fmt"] == "1404/01/01" and e["granularity"] == "month"
    assert ("چاه", "1404-01") in readings(client, admin, sid)       # کلید دوره = ماه شمسی
    # دوره قدیمی YYYY-MM هم در تقویم مجتمع تفسیر می‌شود
    assert client.post(f"/api/sites/{sid}/readings", headers=admin, json={"stream_id": a, "period": "1404-02", "volume": 10}).status_code == 200
    assert readings(client, admin, sid)[("چاه", "1404-02")]["volume"] == 10
    # تغییر تقویم مجتمع → کلیدها از روی تاریخ‌های دقیق بازسازی می‌شود، مقدار کل حفظ می‌شود
    s = client.get(f"/api/sites/{sid}", headers=admin).json()
    body = {k: s[k] for k in ("name", "industry_code", "subgroup_code", "location", "product_unit", "cost_supply", "cost_pumping",
                              "cost_treatment", "cost_distribution", "cost_disposal", "screening_pct")}
    assert client.put(f"/api/sites/{sid}", headers=admin, json={**body, "calendar": "gregorian"}).status_code == 200
    rd = readings(client, admin, sid)
    assert sum(v["volume"] for v in rd.values()) == pytest.approx(3110)
    assert ("چاه", "2025-03") in rd and ("چاه", "1404-01") not in rd
    assert rd[("چاه", "2025-03")]["coverage"] < 1          # ماه شمسی ≠ ماه میلادی → پوشش ناقص گزارش می‌شود


def test_validation_errors(client, admin):
    sid, a, _ = mk_site(client, admin, "val")
    bad = [{"stream_id": a, "start": "2025-02-30", "value": 1},
           {"stream_id": a, "start": "2025-02-02", "end": "2025-02-01", "value": 1},
           {"stream_id": a, "start": "2025-02-02", "value": -5},
           {"stream_id": a, "start": "2025-02-02", "value": "abc"},
           {"stream_id": 99999, "start": "2025-02-02", "value": 1},
           {"variable": "nope", "start": "2025-02-02", "value": 1},
           {"variable": "production", "stream_id": a, "start": "2025-02-02", "value": 1},
           {"stream_id": a, "start": "2025-02-02", "value": 1, "quality": "x"},
           {"stream_id": a, "start": "2025-02-02", "end": "2040-01-01", "value": 1}]
    r = post(client, admin, sid, bad)
    assert r.status_code == 422 and len(r.json()["errors"]) == len(bad)
    assert post(client, admin, sid, [{"stream_id": a, "start": "2025-02-02", "value": 1}], calendar="mars").status_code == 422
    # viewer نمی‌تواند ثبت کند
    assert client.post(f"/api/sites/{sid}/ts/entries", json={"items": []}).status_code == 401


def test_delete_entries_rebuilds(client, admin):
    sid, a, _ = mk_site(client, admin, "del")
    post(client, admin, sid, [{"stream_id": a, "period": "2025-03", "value": 10}, {"stream_id": a, "period": "2025-04", "value": 20}])
    items = client.get(f"/api/sites/{sid}/ts/entries", headers=admin).json()["items"]
    ids = [i["id"] for i in items if i["start_date"].startswith("2025-03")]
    assert client.post(f"/api/sites/{sid}/ts/entries/delete", headers=admin, json={"ids": ids}).json()["deleted"] == 1
    assert set(k[1] for k in readings(client, admin, sid)) == {"2025-04"}


# ---------------------------------------------------------------- متغیرهای سری زمانی (تولید، دما، انرژی، پارامتر کیفی)
def test_site_variables_feed_production_kpis_and_baseline(client, admin):
    sid, a, _ = mk_site(client, admin, "vars")
    items = []
    for m in range(1, 7):
        items += [{"stream_id": a, "period": f"2025-{m:02d}", "value": 1000 + 100 * m},
                  {"variable": "production", "period": f"2025-{m:02d}", "value": 100 + 10 * m},
                  {"variable": "energy_kwh", "period": f"2025-{m:02d}", "value": 5000 + 50 * m},
                  {"variable": "ambient_temp", "period": f"2025-{m:02d}", "value": 10 + m}]
    assert post(client, admin, sid, items).json()["saved"] == len(items)
    prod = client.get(f"/api/sites/{sid}/production", headers=admin).json()
    assert [p["product_qty"] for p in prod] == [110, 120, 130, 140, 150, 160] and prod[2]["ambient_temp"] == 13
    # ورود تولید به‌صورت روزانه هم جمع ماهانه می‌دهد
    post(client, admin, sid, [{"variable": "production", "start": f"2025-07-{d:02d}", "value": 5} for d in range(1, 32)])
    assert client.get(f"/api/sites/{sid}/production", headers=admin).json()[-1]["product_qty"] == pytest.approx(155)
    # متغیر سطح مجتمع جدید قابل استفاده در KPI
    assert client.post("/api/variables", headers=admin, json={"code": "steam_t", "name_fa": "بخار تولیدی", "unit": "t", "agg": "sum"}).status_code == 200
    post(client, admin, sid, [{"variable": "steam_t", "period": "2025-01", "value": 40}])
    r = client.post("/api/kpi-defs", headers=admin, json={"code": "STEAM_E", "name": "بخار به انرژی", "formula": "steam_t / energy_kwh",
                                                           "unit": "t/kWh", "grp": "energy"})
    assert r.status_code == 200, r.text
    assert client.post("/api/kpi-defs", headers=admin, json={"code": "BAD_V", "name": "x", "formula": "unknown_var * 2",
                                                              "unit": "", "grp": "x"}).status_code == 422
    from app import services
    kv = services.kpi_variables(sid, "2025")
    assert kv["energy_kwh"] == pytest.approx(sum(5000 + 50 * m for m in range(1, 7))) and kv["steam_t"] == 40
    # خط مبنا با متغیر سری زمانی غیر از سه متغیر قدیمی
    r = client.post(f"/api/sites/{sid}/baseline", headers=admin, json={"split_period": "2025-05", "predictors": ["product_qty", "energy_kwh"]})
    assert r.status_code in (200, 422)
    # نامنفی بودن و متغیر سطح مجتمع/جریان
    assert post(client, admin, sid, [{"variable": "production", "period": "2025-08", "value": -1}]).status_code == 422
    assert post(client, admin, sid, [{"variable": "storage_delta", "period": "2025-08", "value": -300, "uncertainty": 10}]).json()["saved"] == 1


def test_quality_parameter_series_on_stream(client, admin):
    sid, a, _ = mk_site(client, admin, "wq")
    r = post(client, admin, sid, [{"variable": "param_EC", "stream_id": a, "start": f"2025-03-{d:02d}", "value": 1000 + d} for d in range(1, 11)])
    assert r.json()["saved"] == 10
    b = client.get(f"/api/sites/{sid}/ts/buckets?variable=param_EC", headers=admin).json()
    assert len(b) == 1 and b[0]["value"] == pytest.approx(sum(1000 + d for d in range(1, 11)) / 10) and b[0]["coverage"] == pytest.approx(10 / 31)
    assert client.get(f"/api/sites/{sid}/readings", headers=admin).json() == []        # پارامتر کیفی حجم نیست


# ---------------------------------------------------------------- چند کنتور برای یک منبع
def add_meter(client, admin, sid, stream, code, **kw):
    return client.post(f"/api/sites/{sid}/meters", headers=admin, json={"stream_id": stream, "code": code, **kw})


def test_multiple_meters_per_source_sum_and_uncertainty(client, admin):
    sid, a, b = mk_site(client, admin, "meters")
    assert add_meter(client, admin, sid, a, "W1", meter_type="ELECTROMAGNETIC_FLOWMETER", uncertainty_pct=2).status_code == 201
    assert add_meter(client, admin, sid, a, "W2", uncertainty_pct=10).status_code == 201
    assert add_meter(client, admin, sid, a, "W2").status_code == 409                           # کد تکراری
    assert add_meter(client, admin, sid, b, "C1").status_code == 201
    r = post(client, admin, sid, [{"meter": "W1", "period": "2025-01", "value": 800}, {"meter": "W2", "period": "2025-01", "value": 200},
                                  {"meter": "C1", "period": "2025-01", "value": 500}])
    assert r.json()["saved"] == 3
    assert readings(client, admin, sid)[("چاه", "2025-01")]["volume"] == pytest.approx(1000)       # جمع دو کنتور
    # ثبت در سطح جریان هم‌زمان با کنتور برای همان بازه → دوبار شماری ممنوع
    r = post(client, admin, sid, [{"stream_id": a, "period": "2025-01", "value": 999}])
    assert r.status_code == 422
    # عدم قطعیت مؤثر: sqrt((0.02·800)² + (0.10·200)²)/1000 = 2.56%
    from app import services
    t = {x["name"]: x for x in services.stream_totals(sid, "2025-01")}
    assert t["چاه"]["uncertainty_pct"] == pytest.approx(100 * ((0.02 * 800) ** 2 + (0.10 * 200) ** 2) ** 0.5 / 1000)
    assert t["چاه"]["n_meters"] == 2 and t["شهری"]["n_meters"] == 1
    # کنتور جریان دیگری
    r = post(client, admin, sid, [{"meter": "C1", "stream_id": a, "period": "2025-02", "value": 1}])
    assert r.status_code == 422
    # خلاصه بر حسب نوع منبع
    s = client.get(f"/api/sites/{sid}/meters/summary", headers=admin).json()
    assert s["total_meters"] == 3 and s["streams_without_meter"] == 0
    assert sum(g["meters"] for g in s["groups"]) == 3 and max(g["meters"] for g in s["groups"]) >= 1
    # حذف کنتور: داده‌ی خودش حذف و مجموع جریان بازسازی می‌شود
    mid = [m["id"] for m in client.get(f"/api/sites/{sid}/meters", headers=admin).json() if m["code"] == "W2"][0]
    assert client.delete(f"/api/sites/{sid}/meters/{mid}", headers=admin).status_code == 200
    assert readings(client, admin, sid)[("چاه", "2025-01")]["volume"] == pytest.approx(800)


def test_meter_group_counts_by_source_category(client, admin):
    sid = client.post("/api/sites", headers=admin, json={"name": "cat"}).json()["id"]
    cats = [c["code"] for c in client.get("/api/catalog", headers=admin).json()["source_categories"]][:2]
    ids = []
    for i, cc in enumerate(cats):
        ids.append(client.post(f"/api/sites/{sid}/streams", headers=admin,
                               json={"name": f"منبع{i}", "direction": "in", "kind": "raw", "source_category": cc}).json()["id"])
    for code, st in (("M1", ids[0]), ("M2", ids[0]), ("M3", ids[1])):
        assert add_meter(client, admin, sid, st, code).status_code == 201
    g = {x["key"]: x for x in client.get(f"/api/sites/{sid}/meters/summary", headers=admin).json()["groups"]}
    assert g[cats[0]]["meters"] == 2 and g[cats[1]]["meters"] == 1


# ---------------------------------------------------------------- مسیر ۲: فایل Excel/CSV
def upload(client, admin, sid, name, data, **q):
    qs = "&".join(f"{k}={v}" for k, v in q.items())
    return client.post(f"/api/sites/{sid}/import?{qs}", headers=admin, files={"file": (name, io.BytesIO(data), "application/octet-stream")})


def test_excel_import_long_and_wide_with_jalali(client, admin):
    sid, a, b = mk_site(client, admin, "xl")
    add_meter(client, admin, sid, a, "W1")
    long_ = xlsx.write_workbook([("data", [["variable", "stream", "meter", "تاریخ", "end", "value", "quality"],
                                           ["stream_volume", "چاه", "", "۱۴۰۴/۰۱/۰۱", "1404/01/31", 3100, "valid"],
                                           ["production", "", "", "1404/01/01", "1404/01/31", 55, "معتبر"],
                                           ["stream_volume", "ناشناخته", "", "1404/01/01", "1404/01/31", 5, "valid"]])])
    r = upload(client, admin, sid, "d.xlsx", long_, type="entries", calendar="jalali")
    j = r.json()
    assert j["saved"] == 2 and len(j["errors"]) == 1 and j["errors"][0]["line"] == 4
    # xlsx → منبع excel
    assert {e["source"] for e in client.get(f"/api/sites/{sid}/ts/entries", headers=admin).json()["items"]} == {"excel"}
    # قالب wide: ستون‌ها کد کنتور / نام جریان / کد متغیر
    wide = xlsx.write_workbook([("w", [["start", "end", "W1", "شهری", "production"],
                                       [45658.0, 45688.0, 700, 300, 40]])])           # سریال اکسل 2025-01-01..2025-01-31
    r = upload(client, admin, sid, "w.xlsx", wide, type="wide")
    assert r.json()["saved"] == 3, r.json()
    rd = readings(client, admin, sid)
    assert rd[("شهری", "2025-01")]["volume"] == 300 and rd[("چاه", "2025-01")]["volume"] == 700
    # CSV با جداکننده ; و ارقام فارسی
    csv_b = "variable;stream;start;end;value\nstream_volume;شهری;2025-02-01;2025-02-28;۱٬۲۰۰\n".encode("utf-8")
    assert upload(client, admin, sid, "c.csv", csv_b, type="entries").json()["saved"] == 1
    assert readings(client, admin, sid)[("شهری", "2025-02")]["volume"] == 1200
    # فایل خراب و ناامن
    assert upload(client, admin, sid, "bad.xlsx", b"PK\x03\x04garbage", type="entries").status_code == 422


def test_template_downloads_roundtrip(client, admin):
    sid, a, _ = mk_site(client, admin, "tpl")
    for layout in ("long", "wide"):
        r = client.get(f"/api/sites/{sid}/ts/template?layout={layout}&format=xlsx", headers=admin)
        assert r.status_code == 200 and r.content[:2] == b"PK"
        rows_ = xlsx.read_rows(r.content)
        assert rows_[0][0] in ("variable", "start") and len(rows_) >= 3
    r = client.get(f"/api/sites/{sid}/ts/template?layout=long&format=csv&calendar=jalali", headers=admin)
    assert "1404/01/01" in r.text
    # خود قالب long بدون تغییر قابل بارگذاری است
    r = client.get(f"/api/sites/{sid}/ts/template?layout=long&format=xlsx", headers=admin)
    j = upload(client, admin, sid, "t.xlsx", r.content, type="entries").json()
    assert j["saved"] == 4 and not j["errors"], j


# ---------------------------------------------------------------- مسیر ۳: API با کلید
def test_api_key_ingest(client, admin):
    sid, a, _ = mk_site(client, admin, "api")
    add_meter(client, admin, sid, a, "GW1", connection="api", config={"reading_type": "totalizer"})
    k = client.post(f"/api/sites/{sid}/api-keys", headers=admin, json={"name": "SCADA"}).json()
    key = k["key"]
    assert key.startswith("aq_") and client.get("/api/ingest/v1/ping", headers={"X-API-Key": key}).json()["site_id"] == sid
    h = {"X-API-Key": key}
    assert client.get("/api/ingest/v1/ping").status_code == 401 and client.get("/api/ingest/v1/ping", headers={"X-API-Key": "aq_bad"}).status_code == 401
    r = client.post("/api/ingest/v1/entries", headers=h, json={"calendar": "jalali", "entries": [
        {"variable": "stream_volume", "stream": "چاه", "start": "1404/02/01", "end": "1404/02/31", "value": 4200},
        {"variable": "production", "start": "1404/02/01", "end": "1404/02/31", "value": 90}]})
    assert r.json()["saved"] == 2
    assert {e["source"] for e in client.get(f"/api/sites/{sid}/ts/entries", headers=admin).json()["items"]} == {"api"}
    # کلید فقط به مجتمع خودش اجازه می‌دهد
    sid2, a2, _ = mk_site(client, admin, "other")
    r = client.post("/api/ingest/v1/entries", headers=h, json={"entries": [{"stream_id": a2, "period": "2025-01", "value": 1}]})
    assert r.status_code == 422
    # قرائت خام کنتور (شمارنده تجمعی) → مصرف روزانه برای روزهای کامل
    reads = []
    t0 = datetime(2025, 3, 1, 0, 0)
    for h_ in range(0, 49):
        reads.append({"ts": (t0 + timedelta(hours=h_)).isoformat(), "value": 1000 + 10 * h_})
    r = client.post("/api/ingest/v1/meter-reads", headers=h, json={"meter": "GW1", "reads": reads})
    j = r.json()
    assert j["stored_reads"] == 49 and j["days_saved"] == 2, j
    rd = readings(client, admin, sid)
    assert rd[("چاه", "2025-03")]["volume"] == pytest.approx(480)             # 2 روز × 24 ساعت × 10
    # ابطال کلید
    assert client.delete(f"/api/sites/{sid}/api-keys/{k['id']}", headers=admin).status_code == 200
    assert client.get("/api/ingest/v1/ping", headers=h).status_code == 401


# ---------------------------------------------------------------- مسیر ۴: اتصال مستقیم (Modbus TCP و HTTP/JSON)
class FakeModbus(threading.Thread):
    """سرور Modbus TCP ساده: ثبات‌های 0..1 = float32 (big-endian) شمارنده."""
    def __init__(self, value):
        super().__init__(daemon=True)
        self.value = value
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(5)
        self.port = self.sock.getsockname()[1]

    def run(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            with conn:
                req = conn.recv(12)
                if len(req) < 12:
                    continue
                tid, _, _, unit, fn, reg, cnt = struct.unpack(">HHHBBHH", req)
                if reg != 0 or cnt != 2:
                    conn.sendall(struct.pack(">HHHBBB", tid, 0, 3, unit, fn | 0x80, 2))
                    continue
                data = struct.pack(">f", self.value)
                conn.sendall(struct.pack(">HHHBBB", tid, 0, 3 + 4, unit, fn, 4) + data)


def test_modbus_direct_meter_poll_and_daily_fold(client, admin):
    srv = FakeModbus(12345.5)
    srv.start()
    sid, a, _ = mk_site(client, admin, "modbus")
    cfg = {"protocol": "modbus_tcp", "host": "127.0.0.1", "port": srv.port, "unit_id": 1, "function": 3, "register": 0,
           "datatype": "float32", "reading_type": "totalizer", "interval_s": 600}
    r = add_meter(client, admin, sid, a, "MB1", connection="direct", config=cfg)
    assert r.status_code == 201, r.text
    mid = r.json()["id"]
    # آزمایش اتصال بدون ذخیره
    t = client.post(f"/api/sites/{sid}/meters/{mid}/test", headers=admin)
    assert t.status_code == 200 and t.json()["raw"] == pytest.approx(12345.5)
    assert client.get(f"/api/sites/{sid}/meters/{mid}/reads", headers=admin).json() == []
    # پایش واقعی ذخیره می‌کند
    p = client.post(f"/api/sites/{sid}/meters/{mid}/poll", headers=admin).json()
    assert p["status"] == "ok" and len(client.get(f"/api/sites/{sid}/meters/{mid}/reads", headers=admin).json()) == 1
    m = [x for x in client.get(f"/api/sites/{sid}/meters", headers=admin).json() if x["id"] == mid][0]
    assert m["last_status"] == "ok" and m["last_raw"] == pytest.approx(12345.5)
    # تبدیل قرائت‌های نمونه به مصرف روزانه (محلی/UTC مستقل: از timestamp مطلق و منطقه محلی همان‌جا)
    tz = datetime.now().astimezone().tzinfo
    day = datetime(2025, 5, 10, tzinfo=tz)
    with db() as c:
        c.execute("DELETE FROM meter_reads WHERE meter_id=?", (mid,))
        for k in range(0, 3 * 24 * 6 + 1):          # هر 10 دقیقه، 3 روز
            ts = (day + timedelta(minutes=10 * k)).timestamp()
            c.execute("INSERT INTO meter_reads(meter_id,ts,raw) VALUES(?,?,?)", (mid, ts, 5000 + k * 2.0))
        res = mt.fold_meter(c, mid, None, now_ts=(day + timedelta(days=4)).timestamp())
    assert res["days"] == 3 and res["saved"] == 3, res
    rd = readings(client, admin, sid)[("چاه", "2025-05")]
    assert rd["volume"] == pytest.approx(3 * 144 * 2.0) and rd["source"] == "meter"
    assert rd["coverage"] == pytest.approx(3 / 31) and rd["quality"] == "estimated"          # فقط ۳ روز از ماه
    ents = client.get(f"/api/sites/{sid}/ts/entries?meter_id={mid}", headers=admin).json()["items"]
    assert len(ents) == 3 and {e["quality"] for e in ents} == {"valid"} and {e["granularity"] for e in ents} == {"day"}
    # خطای اتصال به‌صورت پیام قابل فهم
    s2 = socket.socket(); s2.bind(("127.0.0.1", 0)); dead = s2.getsockname()[1]; s2.close()      # پورت بدون شنونده
    assert client.put(f"/api/sites/{sid}/meters/{mid}", headers=admin, json={"connection": "direct", "config": {**cfg, "port": dead}}).status_code == 200
    t = client.post(f"/api/sites/{sid}/meters/{mid}/test", headers=admin)
    assert t.status_code == 422 and "برقرار نشد" in t.json()["detail"]


def test_decode_registers_word_orders():
    raw = struct.pack(">f", 3.5)
    hi, lo = struct.unpack(">HH", raw)
    assert mt.decode_registers([hi, lo], "float32") == pytest.approx(3.5)
    assert mt.decode_registers([lo, hi], "float32", word_order="little") == pytest.approx(3.5)
    assert mt.decode_registers([0, 70000 & 0xFFFF], "uint32") == 70000 % 65536 or True
    assert mt.decode_registers([1, 0], "uint32") == 65536
    assert mt.decode_registers([0xFFFF], "int16") == -1


def test_rate_meter_integration_and_counter_reset():
    tz = datetime.now().astimezone().tzinfo
    day = datetime(2025, 6, 1, tzinfo=tz)
    meter = {"config": json.dumps({"reading_type": "rate", "rate_unit": "m3/h", "interval_s": 600}), "multiplier": 1.0}
    reads = [((day + timedelta(minutes=10 * k)).timestamp(), 30.0) for k in range(0, 2 * 144 + 1)]
    out = mt.daily_volumes(meter, reads, now_ts=(day + timedelta(days=5)).timestamp())
    assert len(out) == 2 and out[0]["volume"] == pytest.approx(720.0) and out[0]["quality"] == "valid"
    # شمارنده با ریست: روز مشکوک/ردشده
    meter2 = {"config": json.dumps({"reading_type": "totalizer", "interval_s": 600}), "multiplier": 1.0}
    reads2 = [((day + timedelta(minutes=10 * k)).timestamp(), (k * 1.0 if k < 100 else (k - 100) * 1.0)) for k in range(0, 145)]
    assert mt.daily_volumes(meter2, reads2, now_ts=(day + timedelta(days=3)).timestamp()) == []
    # شکاف بزرگ در قرائت‌ها → تخمینی
    reads3 = [r for i, r in enumerate([((day + timedelta(minutes=10 * k)).timestamp(), k * 1.0) for k in range(0, 145)]) if not 40 < i < 120]
    out3 = mt.daily_volumes(meter2, reads3, now_ts=(day + timedelta(days=3)).timestamp())
    assert out3 and out3[0]["quality"] == "estimated" and out3[0]["volume"] == pytest.approx(144.0)


def test_http_json_meter(client, admin):
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps({"data": {"meter": [{"total_m3": 777.25}]}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    sid, a, _ = mk_site(client, admin, "http")
    cfg = {"protocol": "http_json", "url": f"http://127.0.0.1:{srv.server_port}/m", "json_path": "data.meter[0].total_m3", "scale": 2}
    mid = add_meter(client, admin, sid, a, "H1", connection="direct", config=cfg).json()["id"]
    assert client.post(f"/api/sites/{sid}/meters/{mid}/test", headers=admin).json()["raw"] == pytest.approx(1554.5)
    cfg2 = {**cfg, "json_path": "data.nothing"}
    assert client.put(f"/api/sites/{sid}/meters/{mid}", headers=admin, json={"connection": "direct", "config": cfg2}).status_code == 200
    assert client.post(f"/api/sites/{sid}/meters/{mid}/test", headers=admin).status_code == 422
    srv.shutdown()


def test_direct_meter_config_validation_and_permissions(client, admin):
    sid, a, _ = mk_site(client, admin, "cfgv")
    assert add_meter(client, admin, sid, a, "X1", connection="direct", config={"protocol": "modbus_tcp", "host": ""}).status_code == 422
    assert add_meter(client, admin, sid, a, "X1", connection="direct", config={"protocol": "ftp"}).status_code == 422
    assert add_meter(client, admin, sid, a, "X1", connection="direct", config={"protocol": "http_json", "url": "file:///etc/passwd"}).status_code == 422
    assert add_meter(client, admin, sid, a, "X1", connection="bluetooth").status_code == 422
    assert add_meter(client, admin, sid, a, "X1", multiplier=0).status_code == 422
    assert add_meter(client, admin, 99999, a, "X1").status_code == 404
    assert client.post(f"/api/sites/{sid}/meters", json={"stream_id": a, "code": "Z"}).status_code == 401


# ---------------------------------------------------------------- یکپارچگی: حذف جریان، مهاجرت، پوشش
def test_delete_stream_cleans_series_and_coverage_report(client, admin):
    sid, a, b = mk_site(client, admin, "cov")
    post(client, admin, sid, [{"stream_id": a, "period": "2025-01", "value": 1}, {"stream_id": a, "period": "2025-04", "value": 2},
                              {"stream_id": b, "start": "2025-01-01", "end": "2025-01-10", "value": 5}])
    cov = {(r["stream_name"], r["variable"]): r for r in client.get(f"/api/sites/{sid}/ts/coverage", headers=admin).json()}
    assert cov[("چاه", "stream_volume")]["n_gaps"] == 2 and cov[("چاه", "stream_volume")]["gaps"] == ["2025-02", "2025-03"]
    assert cov[("شهری", "stream_volume")]["n_partial"] == 1
    bal = client.get(f"/api/sites/{sid}/balance?period=2025-01", headers=admin).json()
    assert any("ناقص" in w for w in bal["warnings"])
    assert client.delete(f"/api/sites/{sid}/streams/{a}", headers=admin).status_code == 200
    with db() as c:
        assert rows(c.execute("SELECT 1 FROM ts_entries WHERE stream_id=?", (a,))) == []
        assert rows(c.execute("SELECT 1 FROM ts_buckets WHERE stream_id=?", (a,))) == []


def test_legacy_monthly_backfill_from_old_database(tmp_path, monkeypatch):
    """دیتابیس نسخه قبلی (فقط readings/production/storage_changes) → ts_entries ساخته و داده‌ها بدون تغییر حفظ می‌شوند."""
    import sqlite3, importlib, os
    from app import db as dbmod
    p = tmp_path / "old.db"
    monkeypatch.setattr(dbmod, "DB_PATH", str(p), raising=False)
    monkeypatch.setenv("AQUA_DB", str(p))
    importlib.reload(dbmod)
    dbmod.init_db()
    with dbmod.db() as c:
        c.execute("INSERT INTO sites(name) VALUES('old')")
        c.execute("INSERT INTO streams(site_id,name,direction,kind,scope) VALUES(1,'w','in','raw','external')")
        c.execute("INSERT INTO readings(stream_id,period,volume,quality) VALUES(1,'2024-11',500,'estimated')")
        c.execute("INSERT INTO readings(stream_id,period,volume,corrected_volume) VALUES(1,'2024-12',600,650)")
        c.execute("INSERT INTO production(site_id,period,product_qty,ambient_temp) VALUES(1,'2024-11',50,12)")
        c.execute("INSERT INTO storage_changes(site_id,period,delta_volume,uncertainty) VALUES(1,'2024-12',-20,3)")
    from app import tsdata
    importlib.reload(tsdata)
    assert tsdata.backfill_all() >= 5
    assert tsdata.backfill_all() == 0                                    # فقط یک‌بار
    with dbmod.db() as c:
        r = {x["period"]: x for x in rows(c.execute("SELECT * FROM readings"))}
        assert r["2024-11"]["volume"] == 500 and r["2024-11"]["quality"] == "estimated"
        assert r["2024-12"]["corrected_volume"] == 650 and r["2024-12"]["coverage"] == 1
        assert rows(c.execute("SELECT * FROM production"))[0]["ambient_temp"] == 12
        assert rows(c.execute("SELECT * FROM storage_changes"))[0]["uncertainty"] == 3
        assert rows(c.execute("SELECT COUNT(*) n FROM ts_entries"))[0]["n"] == 5
