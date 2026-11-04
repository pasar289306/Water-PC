"""لایه پایگاه داده (sqlite3). ساختار جداول با PostgreSQL سازگار نگه داشته شده است."""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

DATA_DIR = Path(os.environ.get("AQUA_DATA_DIR", Path(__file__).resolve().parent.parent / "data"))
DB_PATH = Path(os.environ.get("AQUA_DB", DATA_DIR / "aquaaudit.db"))

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT UNIQUE NOT NULL,
  full_name TEXT DEFAULT '',
  password_hash TEXT NOT NULL,
  role TEXT NOT NULL CHECK(role IN ('admin','engineer','viewer')),
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sites(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  industry_code TEXT DEFAULT 'IND-15',
  location TEXT DEFAULT '',
  product_unit TEXT DEFAULT 'تن',
  cost_supply REAL DEFAULT 0, cost_pumping REAL DEFAULT 0, cost_treatment REAL DEFAULT 0,
  cost_distribution REAL DEFAULT 0, cost_disposal REAL DEFAULT 0,
  screening_pct REAL DEFAULT 10,
  created_by INTEGER REFERENCES users(id),
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS units(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  site_id INTEGER NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  system_code TEXT DEFAULT 'SYS-04'
);

CREATE TABLE IF NOT EXISTS equipment(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  site_id INTEGER NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
  unit_id INTEGER REFERENCES units(id) ON DELETE SET NULL,
  name TEXT NOT NULL,
  kind TEXT NOT NULL,           -- cooling_tower | boiler | ro | pump | wwtp | other
  params TEXT DEFAULT '{}'      -- JSON
);

-- scope: external = مرز مجتمع (در موازنه سایت)، internal = جریان بازچرخانی داخلی (شمارش دوباره نمی‌شود)
-- kind: raw|potable|fresh|rain|reclaimed_in | wastewater|blowdown|evaporation|product|leakage|other_out |
--       reclaimed|recycled|condensate (internal)
CREATE TABLE IF NOT EXISTS streams(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  site_id INTEGER NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
  unit_id INTEGER REFERENCES units(id) ON DELETE SET NULL,
  name TEXT NOT NULL,
  direction TEXT NOT NULL CHECK(direction IN ('in','out')),
  kind TEXT NOT NULL,
  scope TEXT NOT NULL DEFAULT 'external' CHECK(scope IN ('external','internal')),
  meter_id TEXT DEFAULT '',
  uncertainty_pct REAL DEFAULT 2.0,
  UNIQUE(site_id, name)
);

CREATE TABLE IF NOT EXISTS readings(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  stream_id INTEGER NOT NULL REFERENCES streams(id) ON DELETE CASCADE,
  period TEXT NOT NULL,                 -- YYYY-MM
  volume REAL NOT NULL,                 -- m3 (مقدار خام)
  corrected_volume REAL,                -- مقدار اصلاح‌شده (جدا از خام)
  quality TEXT NOT NULL DEFAULT 'valid' CHECK(quality IN ('valid','estimated','suspect','invalid','missing')),
  source TEXT DEFAULT 'manual',
  entered_by INTEGER REFERENCES users(id),
  entered_at TEXT DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(stream_id, period)
);

CREATE TABLE IF NOT EXISTS production(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  site_id INTEGER NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
  period TEXT NOT NULL,
  product_qty REAL NOT NULL,
  ambient_temp REAL, operating_hours REAL,
  UNIQUE(site_id, period)
);

CREATE TABLE IF NOT EXISTS storage_changes(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  site_id INTEGER NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
  period TEXT NOT NULL,
  delta_volume REAL NOT NULL DEFAULT 0,
  uncertainty REAL DEFAULT 0,
  UNIQUE(site_id, period)
);

CREATE TABLE IF NOT EXISTS kpi_defs(
  code TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  formula TEXT NOT NULL,
  unit TEXT DEFAULT '',
  grp TEXT DEFAULT '',
  warn REAL, critical REAL,
  reference TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS measures(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  code TEXT UNIQUE NOT NULL,
  title TEXT NOT NULL,
  grp TEXT NOT NULL,                    -- A..E
  hierarchy TEXT NOT NULL,              -- reduce|replace|reuse
  system_code TEXT DEFAULT '',
  industries TEXT DEFAULT 'ALL',
  problem TEXT DEFAULT '', mechanism TEXT DEFAULT '',
  required_data TEXT DEFAULT '', calc_method TEXT DEFAULT '',
  saving_min_pct REAL, saving_max_pct REAL, saving_basis TEXT DEFAULT '',
  capex_note TEXT DEFAULT '', payback_note TEXT DEFAULT '',
  tech_risk INTEGER DEFAULT 3, ease INTEGER DEFAULT 3,
  quality_constraints TEXT DEFAULT '', verification TEXT DEFAULT '',
  source TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS projects(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  site_id INTEGER NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
  measure_id INTEGER REFERENCES measures(id) ON DELETE SET NULL,
  title TEXT NOT NULL,
  saving_m3_year REAL NOT NULL,
  capex REAL NOT NULL DEFAULT 0,
  opex_year REAL NOT NULL DEFAULT 0,
  life_years INTEGER NOT NULL DEFAULT 10,
  discount_rate REAL NOT NULL DEFAULT 0.15,
  ease INTEGER DEFAULT 3, supply_risk INTEGER DEFAULT 3,
  environment INTEGER DEFAULT 3, tech_risk INTEGER DEFAULT 3,
  status TEXT NOT NULL DEFAULT 'proposed' CHECK(status IN ('proposed','approved','in_progress','done','rejected')),
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS audit_log(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER, username TEXT,
  action TEXT NOT NULL, entity TEXT, entity_id TEXT, detail TEXT,
  at TEXT DEFAULT CURRENT_TIMESTAMP
);
-- کاتالوگ مرجع (داده، نه کد): از طریق API قابل افزودن/ویرایش است
CREATE TABLE IF NOT EXISTS industries(
  code TEXT PRIMARY KEY, name TEXT NOT NULL, examples TEXT DEFAULT '',
  subgroups TEXT DEFAULT '[]',          -- JSON: [[code,name],...]
  typical_systems TEXT DEFAULT '[]',    -- JSON: [SYS-xx,...]
  processes TEXT DEFAULT '[]',          -- JSON: فرآیندهای نوعی مصرف‌کننده آب
  highlights TEXT DEFAULT '',           -- راهکارهای شاخص صنعت (سند)
  measure_codes TEXT DEFAULT '[]',      -- JSON: کد راهکارهای مرتبط
  source TEXT DEFAULT 'guide'
);
CREATE TABLE IF NOT EXISTS systems(
  code TEXT PRIMARY KEY, name_fa TEXT NOT NULL, name_en TEXT DEFAULT '', category TEXT DEFAULT '',
  equipment TEXT DEFAULT '[]', typical_in TEXT DEFAULT '[]', typical_out TEXT DEFAULT '[]',
  measurements TEXT DEFAULT '[]', calc_module TEXT, kpis TEXT DEFAULT '[]', notes TEXT DEFAULT '',
  source TEXT DEFAULT 'guide'
);
CREATE TABLE IF NOT EXISTS stream_types(
  code TEXT PRIMARY KEY, name_en TEXT DEFAULT '', name_fa TEXT NOT NULL,
  direction TEXT NOT NULL, scope TEXT NOT NULL, kind TEXT NOT NULL,
  definition TEXT DEFAULT '', quality_class TEXT DEFAULT '', allowed_use TEXT DEFAULT '',
  origin TEXT DEFAULT '', destination TEXT DEFAULT '', recycle_status TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS source_categories(
  code TEXT PRIMARY KEY, name TEXT NOT NULL, examples TEXT DEFAULT '', source TEXT DEFAULT 'doc'
);
CREATE TABLE IF NOT EXISTS equipment_types(
  code TEXT PRIMARY KEY, name_fa TEXT NOT NULL, name_en TEXT DEFAULT '', system_code TEXT DEFAULT '',
  source TEXT DEFAULT 'guide'           -- doc | guide | user
);
CREATE TABLE IF NOT EXISTS parameters(
  code TEXT PRIMARY KEY, name TEXT NOT NULL, symbol TEXT DEFAULT '', unit TEXT DEFAULT '',
  grp TEXT DEFAULT '', note TEXT DEFAULT '', source TEXT DEFAULT 'doc'
);
CREATE TABLE IF NOT EXISTS instruments(
  code TEXT PRIMARY KEY, name_fa TEXT NOT NULL, name_en TEXT DEFAULT '', usage TEXT DEFAULT '', source TEXT DEFAULT 'doc'
);
CREATE TABLE IF NOT EXISTS system_formulas(
  id INTEGER PRIMARY KEY AUTOINCREMENT, system_code TEXT NOT NULL, name TEXT NOT NULL, formula TEXT NOT NULL,
  source TEXT DEFAULT 'doc', UNIQUE(system_code, name)
);
CREATE INDEX IF NOT EXISTS ix_readings_period ON readings(period);
CREATE INDEX IF NOT EXISTS ix_streams_site ON streams(site_id);
"""


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    return c


@contextmanager
def db():
    c = connect()
    try:
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


# ستون‌های افزوده‌شده در نسخه‌های بعدی؛ برای دیتابیس‌های قدیمی به‌صورت خودکار و بدون از دست رفتن داده اضافه می‌شوند
MIGRATIONS = [
    ("sites", "subgroup_code", "TEXT DEFAULT ''"),
    ("streams", "stream_type", "TEXT DEFAULT ''"),        # کد نوع جریان (stream_types)
    ("streams", "system_code", "TEXT DEFAULT ''"),        # سیستم مصرف‌کننده (SYS-xx)
    ("streams", "source_category", "TEXT DEFAULT ''"),    # دسته منبع (SRC-xx) برای جریان‌های ورودی
    ("streams", "origin", "TEXT DEFAULT ''"),
    ("streams", "destination", "TEXT DEFAULT ''"),
    ("streams", "quality_class", "TEXT DEFAULT ''"),
    ("streams", "allowed_use", "TEXT DEFAULT ''"),
    ("streams", "recycle_status", "TEXT DEFAULT ''"),
]


def migrate(c: sqlite3.Connection):
    for table, col, decl in MIGRATIONS:
        cols = {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}
        if col not in cols:
            c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")


def init_db():
    with db() as c:
        c.executescript(SCHEMA)
        migrate(c)


def rows(cur) -> list[dict]:
    return [dict(r) for r in cur.fetchall()]


def one(cur) -> dict | None:
    r = cur.fetchone()
    return dict(r) if r else None
