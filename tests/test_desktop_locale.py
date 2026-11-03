"""رگرسیون: راه‌اندازی نباید به کدگذاری پیش‌فرض سیستم (مثلاً cp1252/ASCII در ویندوز) وابسته باشد."""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_desktop_smoke_with_ascii_locale():
    env = {k: v for k, v in os.environ.items() if not k.startswith("AQUA_")}
    env.update(PYTHONUTF8="0", PYTHONCOERCECLOCALE="0", LC_ALL="C", PYTHONIOENCODING="ascii")
    r = subprocess.run([sys.executable, str(ROOT / "desktop.py"), "--smoke"], env=env,
                       capture_output=True, text=True, timeout=90)
    assert r.returncode == 0, r.stdout + r.stderr
