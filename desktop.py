"""لانچر دسکتاپ AquaAudit (برای بسته‌بندی با PyInstaller).

- داده‌ها در %LOCALAPPDATA%\\AquaAudit (ویندوز) یا ~/.local/share/AquaAudit ذخیره می‌شود؛ نه کنار برنامه،
  تا حذف/بروزرسانی برنامه داده‌ها را پاک نکند.
- سرور فقط روی 127.0.0.1 گوش می‌دهد.
- گزینه‌ها:  --smoke  راه‌اندازی، بررسی سلامت و خروج (برای تست خودکار)   --no-browser
"""
from __future__ import annotations

import os
import socket
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

APP_NAME = "AquaAudit"


def data_dir() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    d = base / APP_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def free_port(preferred: int = 8000) -> int:
    for p in (preferred, 0):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", p))
                return s.getsockname()[1]
            except OSError:
                continue
    raise RuntimeError("پورت آزاد پیدا نشد")


def wait_health(port: int, timeout: float = 30) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=2) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(0.3)
    return False


def initial_password(d: Path) -> str | None:
    f = d / "INITIAL_ADMIN_PASSWORD.txt"
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.startswith("password:"):
                return line.split(":", 1)[1].strip()
    return None


def main(argv: list[str]) -> int:
    smoke = "--smoke" in argv
    d = data_dir()
    if smoke:  # تست خودکار: داده موقت و جداگانه
        import tempfile
        d = Path(tempfile.mkdtemp(prefix="aqua_smoke_"))
    os.environ.setdefault("AQUA_DATA_DIR", str(d))
    os.environ.setdefault("AQUA_DEMO", "1")  # مجتمع نمونه فرضی برای ارزیابی اولیه؛ با AQUA_DEMO=0 غیرفعال می‌شود

    import uvicorn
    from app.main import app

    port = free_port(int(os.environ.get("PORT", "8000")))
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_config=None, access_log=False)
    server = uvicorn.Server(config)
    t = threading.Thread(target=server.run, daemon=True)
    t.start()

    if not wait_health(port):
        print("سرور راه‌اندازی نشد", file=sys.stderr)
        return 1
    url = f"http://127.0.0.1:{port}/"

    if smoke:
        with urllib.request.urlopen(url, timeout=5) as r:
            ok = r.status == 200 and "AquaAudit" in r.read().decode("utf-8")
        server.should_exit = True
        t.join(timeout=10)
        print("SMOKE OK" if ok else "SMOKE FAIL")
        return 0 if ok else 1

    if "--no-browser" not in argv:
        webbrowser.open(url)
    return run_window(server, t, url, d)


def run_window(server, thread, url: str, d: Path) -> int:
    pw = initial_password(d)
    try:
        import tkinter as tk
        from tkinter import messagebox
    except Exception:  # بدون رابط گرافیکی: حالت کنسول
        print(f"AquaAudit در حال اجراست: {url}\nدرخواست قطع: Ctrl+C")
        if pw:
            print(f"ورود اولیه: admin / {pw}  (فایل: {d / 'INITIAL_ADMIN_PASSWORD.txt'})")
        try:
            while thread.is_alive():
                time.sleep(1)
        except KeyboardInterrupt:
            server.should_exit = True
        return 0

    root = tk.Tk()
    root.title("AquaAudit")
    root.geometry("420x230")
    root.resizable(False, False)
    tk.Label(root, text="AquaAudit در حال اجراست", font=("Tahoma", 13, "bold")).pack(pady=(14, 4))
    tk.Label(root, text=url, fg="#1d6fa5").pack()
    msg = (f"ورود اولیه:  admin  /  {pw}\n(پس از ورود، گذرواژه را تغییر دهید)" if pw
           else "برای ورود از کاربر admin و گذرواژه خود استفاده کنید.")
    box = tk.Text(root, height=3, width=48, borderwidth=0, background=root.cget("bg"), font=("Tahoma", 10))
    box.insert("1.0", msg)
    box.configure(state="disabled")
    box.pack(pady=8)
    fr = tk.Frame(root)
    fr.pack(pady=6)
    tk.Button(fr, text="باز کردن در مرورگر", command=lambda: webbrowser.open(url)).pack(side="left", padx=6)
    tk.Button(fr, text="پوشه داده‌ها", command=lambda: os.startfile(str(d)) if os.name == "nt" else None).pack(side="left", padx=6)

    def quit_app():
        if messagebox.askyesno("خروج", "برنامه بسته شود؟ (داده‌ها حفظ می‌شوند)"):
            server.should_exit = True
            root.destroy()

    tk.Button(fr, text="خروج", command=quit_app).pack(side="left", padx=6)
    root.protocol("WM_DELETE_WINDOW", quit_app)
    root.mainloop()
    thread.join(timeout=5)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
