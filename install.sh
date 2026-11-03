#!/usr/bin/env bash
# نصب AquaAudit روی لینوکس/مک
# استفاده:  ./install.sh [--run] [--dir مسیر] [--no-demo]
#   --run      پس از نصب، برنامه را اجرا کن
#   --no-demo  بدون مجتمع نمونه فرضی (برای استقرار واقعی)
set -euo pipefail

RUN=0; DEMO=1
cd "$(dirname "$0")"
while [ $# -gt 0 ]; do
  case "$1" in
    --run) RUN=1 ;;
    --no-demo) DEMO=0 ;;
    -h|--help) sed -n '2,6p' "$0"; exit 0 ;;
    *) echo "گزینه ناشناخته: $1" >&2; exit 2 ;;
  esac
  shift
done

PY=""
for c in python3.13 python3.12 python3.11 python3; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info>=(3,11) else 1)' 2>/dev/null; then
    PY="$c"; break
  fi
done
if [ -z "$PY" ]; then
  echo "خطا: Python نسخه 3.11 یا بالاتر لازم است. آن را از python.org یا مدیر بسته سیستم نصب کنید." >&2
  exit 1
fi
echo "[1/4] استفاده از $($PY --version)"

echo "[2/4] ساخت محیط مجازی (.venv)"
"$PY" -m venv .venv

echo "[3/4] نصب وابستگی‌ها"
.venv/bin/python -m pip install --quiet --upgrade pip
.venv/bin/python -m pip install --quiet -r requirements.txt

echo "[4/4] ساخت فایل تنظیمات و بررسی نصب"
mkdir -p data
if [ ! -f .env ]; then
  umask 077
  {
    echo "AQUA_SECRET=$(.venv/bin/python -c 'import secrets;print(secrets.token_hex(32))')"
    echo "AQUA_ADMIN_PASSWORD=$(.venv/bin/python -c 'import secrets;print(secrets.token_urlsafe(12))')"
    echo "AQUA_DEMO=$DEMO"
    echo "HOST=127.0.0.1"
    echo "PORT=8000"
  } > .env
  echo "فایل .env با کلید و گذرواژه تصادفی ساخته شد (گذرواژه مدیر داخل همین فایل است)."
fi
.venv/bin/python -c "import starlette, uvicorn, pydantic, numpy, jwt, multipart; print('وابستگی‌ها سالم هستند')"

cat > start.sh <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
set -a; . ./.env; set +a
exec .venv/bin/python -m uvicorn app.main:app --host "${HOST:-127.0.0.1}" --port "${PORT:-8000}"
EOF
chmod +x start.sh

echo
echo "نصب کامل شد. اجرا:  ./start.sh   سپس http://127.0.0.1:8000"
echo "ورود: کاربر admin و گذرواژه AQUA_ADMIN_PASSWORD از فایل .env (پس از ورود تغییرش دهید)."
if [ "$RUN" = 1 ]; then exec ./start.sh; fi
