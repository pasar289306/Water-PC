"""کنتورها و اتصال مستقیم به دستگاه‌های اندازه‌گیری.

- هر کنتور به یک جریان (منبع/مصرف) وصل است؛ یک جریان می‌تواند چند کنتور داشته باشد (مجموع آن‌ها حجم جریان است).
- اتصال direct: Modbus TCP (ثبات‌های Holding/Input) یا HTTP/JSON؛ مقدار خام (شمارنده تجمعی totalizer یا دبی لحظه‌ای rate)
  در meter_reads ثبت و سپس به مصرف «روزانه» تبدیل و از مسیر استاندارد ts (منبع meter) ذخیره می‌شود.
- اتصال api: دستگاه/گیت‌وی قرائت‌ها را با کلید API به /api/ingest/v1/meter-reads می‌فرستد (همان تبدیل روزانه).
"""
from __future__ import annotations

import json
import socket
import struct
import threading
import time
import urllib.request
from datetime import date, datetime, timedelta

from . import timeutil as tu
from . import tsdata
from .db import db, rows, one

DTYPES = {"uint16": ("H", 1), "int16": ("h", 1), "uint32": ("I", 2), "int32": ("i", 2), "float32": ("f", 2), "float64": ("d", 4)}
RATE_TO_M3S = {"m3/h": 1 / 3600.0, "l/s": 0.001, "m3/s": 1.0, "m3/d": 1 / 86400.0, "l/min": 0.001 / 60.0}
PROTOCOLS = ("modbus_tcp", "http_json")
READING_TYPES = ("totalizer", "rate")
_lock = threading.Lock()


class MeterError(ValueError):
    pass


DEFAULT_CONFIG = {"protocol": "modbus_tcp", "reading_type": "totalizer", "interval_s": 300, "timeout_s": 5,
                  "host": "", "port": 502, "unit_id": 1, "function": 3, "register": 0, "datatype": "float32",
                  "word_order": "big", "byte_order": "big", "scale": 1.0, "offset": 0.0,
                  "rate_unit": "m3/h", "counter_max": None, "url": "", "json_path": "", "auth_header": ""}


def clean_config(cfg: dict | None) -> dict:
    """اعتبارسنجی و تکمیل تنظیمات اتصال مستقیم."""
    c = {**DEFAULT_CONFIG, **(cfg or {})}
    if c["protocol"] not in PROTOCOLS:
        raise MeterError(f"پروتکل باید یکی از {list(PROTOCOLS)} باشد")
    if c["reading_type"] not in READING_TYPES:
        raise MeterError("نوع قرائت باید totalizer (شمارنده تجمعی) یا rate (دبی لحظه‌ای) باشد")
    if c["reading_type"] == "rate" and c["rate_unit"] not in RATE_TO_M3S:
        raise MeterError(f"واحد دبی باید یکی از {list(RATE_TO_M3S)} باشد")
    for k, lo, hi in (("interval_s", 5, 86400), ("timeout_s", 1, 60), ("port", 1, 65535), ("unit_id", 0, 255),
                      ("function", 3, 4), ("register", 0, 65535)):
        try:
            c[k] = int(c[k])
        except (TypeError, ValueError):
            raise MeterError(f"{k} باید عدد صحیح باشد")
        if not lo <= c[k] <= hi:
            raise MeterError(f"{k} باید بین {lo} و {hi} باشد")
    for k in ("scale", "offset"):
        try:
            c[k] = float(c[k])
        except (TypeError, ValueError):
            raise MeterError(f"{k} باید عدد باشد")
    if c["counter_max"] not in (None, ""):
        c["counter_max"] = float(c["counter_max"])
    else:
        c["counter_max"] = None
    if c["datatype"] not in DTYPES:
        raise MeterError(f"نوع داده باید یکی از {list(DTYPES)} باشد")
    if c["word_order"] not in ("big", "little") or c["byte_order"] not in ("big", "little"):
        raise MeterError("word_order و byte_order باید big یا little باشند")
    if c["protocol"] == "modbus_tcp" and not str(c["host"]).strip():
        raise MeterError("آدرس دستگاه (host) لازم است")
    if c["protocol"] == "http_json":
        if not str(c["url"]).startswith(("http://", "https://")):
            raise MeterError("نشانی (url) باید با http:// یا https:// شروع شود")
    return c


# ---------------------------------------------------------------- Modbus TCP
def _recv_exact(s: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = s.recv(n - len(buf))
        if not chunk:
            raise MeterError("اتصال Modbus قبل از دریافت کامل پاسخ بسته شد")
        buf += chunk
    return buf


def decode_registers(regs: list[int], datatype: str, word_order: str = "big", byte_order: str = "big") -> float:
    fmt, n = DTYPES[datatype]
    if len(regs) != n:
        raise MeterError("تعداد ثبات‌ها با نوع داده نمی‌خواند")
    words = list(regs) if word_order == "big" else list(reversed(regs))
    raw = b"".join(struct.pack(">H", w) if byte_order == "big" else struct.pack("<H", w) for w in words)
    return float(struct.unpack(">" + fmt, raw)[0])


def modbus_read(cfg: dict) -> float:
    fmt, count = DTYPES[cfg["datatype"]]
    tid = int(time.time() * 1000) & 0xFFFF
    req = struct.pack(">HHHBBHH", tid, 0, 6, cfg["unit_id"], cfg["function"], cfg["register"], count)
    try:
        with socket.create_connection((cfg["host"], cfg["port"]), timeout=cfg["timeout_s"]) as s:
            s.settimeout(cfg["timeout_s"])
            s.sendall(req)
            head = _recv_exact(s, 7)
            _, proto, length, _unit = struct.unpack(">HHHB", head)
            if proto != 0 or not 2 <= length <= 260:
                raise MeterError("پاسخ Modbus نامعتبر است")
            pdu = _recv_exact(s, length - 1)
    except OSError as e:
        raise MeterError(f"اتصال به {cfg['host']}:{cfg['port']} برقرار نشد: {e}")
    if pdu[0] & 0x80:
        raise MeterError(f"دستگاه خطای Modbus برگرداند (کد {pdu[1] if len(pdu) > 1 else '?'})")
    if pdu[0] != cfg["function"] or pdu[1] != count * 2 or len(pdu) < 2 + count * 2:
        raise MeterError("طول/تابع پاسخ Modbus با درخواست نمی‌خواند")
    regs = list(struct.unpack(f">{count}H", pdu[2:2 + count * 2]))
    return decode_registers(regs, cfg["datatype"], cfg["word_order"], cfg["byte_order"])


# ---------------------------------------------------------------- HTTP/JSON
def _json_path(obj, path: str):
    cur = obj
    for part in [p for p in path.replace("[", ".").replace("]", "").split(".") if p != ""]:
        if isinstance(cur, list):
            cur = cur[int(part)]
        elif isinstance(cur, dict):
            cur = cur[part]
        else:
            raise MeterError(f"مسیر JSON «{path}» در پاسخ یافت نشد")
    return cur


def http_read(cfg: dict) -> float:
    req = urllib.request.Request(cfg["url"], headers={"Accept": "application/json", "User-Agent": "AquaAudit"})
    if cfg.get("auth_header"):
        k, _, v = cfg["auth_header"].partition(":")
        if v:
            req.add_header(k.strip(), v.strip())
    try:
        with urllib.request.urlopen(req, timeout=cfg["timeout_s"]) as r:
            data = r.read(1_000_000)
    except OSError as e:
        raise MeterError(f"دریافت از {cfg['url']} ناموفق بود: {e}")
    try:
        obj = json.loads(data.decode("utf-8"))
        v = _json_path(obj, cfg.get("json_path") or "") if cfg.get("json_path") else obj
        return float(v)
    except (ValueError, KeyError, IndexError, TypeError) as e:
        raise MeterError(f"مقدار عددی از پاسخ JSON استخراج نشد: {e}")


def read_raw(cfg: dict) -> float:
    v = modbus_read(cfg) if cfg["protocol"] == "modbus_tcp" else http_read(cfg)
    return v * cfg["scale"] + cfg["offset"]


# ---------------------------------------------------------------- ثبت قرائت و تبدیل به مصرف روزانه
def _local_tz():
    return datetime.now().astimezone().tzinfo


def store_reads(c, meter_id: int, reads: list[tuple[float, float]]) -> int:
    n = 0
    for ts, raw in reads:
        cur = c.execute("INSERT OR IGNORE INTO meter_reads(meter_id,ts,raw) VALUES(?,?,?)", (meter_id, float(ts), float(raw)))
        n += cur.rowcount
    return n


def fold_cfg(meter: dict) -> dict:
    """پارامترهای تبدیل به مصرف روزانه (برای کنتورهای api هم؛ بدون اعتبارسنجی فیلدهای اتصال)."""
    try:
        raw = json.loads(meter.get("config") or "{}")
    except ValueError:
        raw = {}
    c = {**DEFAULT_CONFIG, **raw}
    if c["reading_type"] not in READING_TYPES:
        c["reading_type"] = "totalizer"
    if c["rate_unit"] not in RATE_TO_M3S:
        c["rate_unit"] = "m3/h"
    try:
        c["interval_s"] = max(5, int(c["interval_s"]))
    except (TypeError, ValueError):
        c["interval_s"] = 300
    c["counter_max"] = float(c["counter_max"]) if c.get("counter_max") not in (None, "") else None
    return c


def daily_volumes(meter: dict, reads: list[tuple[float, float]], now_ts: float | None = None, tz=None) -> list[dict]:
    """از قرائت‌ها (ts, raw) مصرف روزهای «کامل» را به m³ می‌دهد.
    totalizer: اختلاف شمارنده در مرز روز (درون‌یابی خطی بین دو قرائت مجاور)؛ rate: انتگرال ذوزنقه‌ای دبی.
    quality: estimated اگر فاصله بین قرائت‌ها > ۴ برابر دوره پایش یا > ۶ ساعت باشد؛ suspect اگر شمارنده کاهش یافته."""
    cfg = fold_cfg(meter)
    mult = meter["multiplier"] or 1.0
    tz = tz or _local_tz()
    now_ts = now_ts or time.time()
    rs = sorted(reads)
    if len(rs) < 2:
        return []
    max_gap = max(4 * cfg["interval_s"], 6 * 3600) if cfg["interval_s"] < 21600 else 4 * cfg["interval_s"]
    first_day = datetime.fromtimestamp(rs[0][0], tz).date()
    last_day = datetime.fromtimestamp(min(rs[-1][0], now_ts), tz).date()
    out = []
    d = first_day
    while d <= last_day:
        s_ts = datetime(d.year, d.month, d.day, tzinfo=tz).timestamp()
        e_ts = datetime.combine(d + timedelta(days=1), datetime.min.time(), tzinfo=tz).timestamp()
        if rs[0][0] <= s_ts and rs[-1][0] >= e_ts and e_ts <= now_ts:
            seg = [r for r in rs if s_ts <= r[0] <= e_ts]
            lo = max([r for r in rs if r[0] <= s_ts], key=lambda r: r[0])
            hi = min([r for r in rs if r[0] >= e_ts], key=lambda r: r[0])
            pts = [lo] + [r for r in seg if r[0] > lo[0] and r[0] < hi[0]] + [hi]
            q, vol = "valid", 0.0
            ok_day = True
            if cfg["reading_type"] == "totalizer":
                for (t0, v0), (t1, v1) in zip(pts, pts[1:]):
                    if t1 - t0 > max_gap:
                        q = "estimated"
                    if v1 < v0:  # کاهش شمارنده: ریست یا سرریز
                        if cfg["counter_max"]:
                            vol += (v1 + cfg["counter_max"] - v0) * ((min(t1, e_ts) - max(t0, s_ts)) / (t1 - t0))
                            q = "suspect"
                        else:
                            ok_day = False
                        continue
                    vol += (v1 - v0) * ((min(t1, e_ts) - max(t0, s_ts)) / (t1 - t0)) if t1 > t0 else 0
            else:
                f = RATE_TO_M3S[cfg["rate_unit"]]
                for (t0, v0), (t1, v1) in zip(pts, pts[1:]):
                    if t1 - t0 > max_gap:
                        q = "estimated"
                    a, b = max(t0, s_ts), min(t1, e_ts)
                    if b > a:
                        ra = v0 + (v1 - v0) * (a - t0) / (t1 - t0)
                        rb = v0 + (v1 - v0) * (b - t0) / (t1 - t0)
                        vol += (ra + rb) / 2 * f * (b - a)
            if ok_day:
                out.append({"date": d, "volume": vol * mult, "quality": q})
        d += timedelta(days=1)
    return out


def fold_meter(c, meter_id: int, user_id: int | None = None, now_ts: float | None = None) -> dict:
    """قرائت‌های ذخیره‌شده را به مصرف روزانه تبدیل و در ts_entries (منبع meter) ذخیره می‌کند."""
    m = one(c.execute("SELECT * FROM meters WHERE id=?", (meter_id,)))
    if not m:
        raise MeterError("کنتور یافت نشد")
    reads = [(r["ts"], r["raw"]) for r in c.execute("SELECT ts,raw FROM meter_reads WHERE meter_id=? ORDER BY ts", (meter_id,))]
    days = daily_volumes(m, reads, now_ts)
    items = [{"variable": "stream_volume", "meter_id": meter_id, "start": tu.iso(d["date"]), "end": tu.iso(d["date"]),
              "value": d["volume"], "quality": d["quality"], "granularity": "day"} for d in days]
    res = tsdata.save_entries(m["site_id"], items, "meter", user_id, calendar="gregorian", c=c) if items else {"saved": 0, "errors": []}
    return {"days": len(days), **res}


def poll_meter(meter_id: int, user_id: int | None = None, store: bool = True) -> dict:
    """یک قرائت مستقیم از دستگاه؛ در حالت store=False فقط آزمایش اتصال (بدون ذخیره)."""
    with db() as c:
        m = one(c.execute("SELECT * FROM meters WHERE id=?", (meter_id,)))
    if not m:
        raise MeterError("کنتور یافت نشد")
    if m["connection"] != "direct":
        raise MeterError("این کنتور از نوع «اتصال مستقیم» نیست")
    cfg = clean_config(json.loads(m["config"] or "{}"))
    now = time.time()
    try:
        raw = read_raw(cfg)
        status = "ok"
    except MeterError as e:
        raw, status = None, f"خطا: {e}"
    if not store:
        if raw is None:
            raise MeterError(status[5:])
        return {"raw": raw, "status": "ok"}
    with db() as c:
        c.execute("UPDATE meters SET last_poll_at=?, last_status=?, last_raw=COALESCE(?, last_raw) WHERE id=?",
                  (datetime.fromtimestamp(now).isoformat(timespec="seconds"), status, raw, meter_id))
        res = {}
        if raw is not None:
            store_reads(c, meter_id, [(now, raw)])
            res = fold_meter(c, meter_id, user_id, now)
    return {"raw": raw, "status": status, **res}


# ---------------------------------------------------------------- پایش دوره‌ای در پس‌زمینه
class Poller(threading.Thread):
    daemon = True

    def __init__(self):
        super().__init__(name="aqua-poller")
        self.stop_ev = threading.Event()

    def run(self):
        while not self.stop_ev.wait(10):
            try:
                self.tick()
            except Exception:  # پایش نباید برنامه را بیندازد
                pass

    def tick(self):
        with db() as c:
            ms = rows(c.execute("SELECT id, config, last_poll_at FROM meters WHERE connection='direct' AND enabled=1"))
        for m in ms:
            try:
                iv = int(json.loads(m["config"] or "{}").get("interval_s", 300))
            except (ValueError, TypeError):
                iv = 300
            last = datetime.fromisoformat(m["last_poll_at"]).timestamp() if m["last_poll_at"] else 0
            if time.time() - last >= iv:
                try:
                    poll_meter(m["id"])
                except MeterError:
                    pass

    def stop(self):
        self.stop_ev.set()
