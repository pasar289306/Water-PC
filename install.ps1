# نصب AquaAudit روی ویندوز (PowerShell)
# استفاده:  powershell -ExecutionPolicy Bypass -File install.ps1 [-Run] [-NoDemo]
param([switch]$Run, [switch]$NoDemo)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

# پیدا کردن Python 3.11+
$py = $null
foreach ($c in @("py -3.13", "py -3.12", "py -3.11", "python3", "python")) {
    $parts = $c.Split(" ")
    $exe = $parts[0]; $args0 = @($parts | Select-Object -Skip 1)
    if (Get-Command $exe -ErrorAction SilentlyContinue) {
        & $exe @args0 -c "import sys; sys.exit(0 if sys.version_info>=(3,11) else 1)" 2>$null
        if ($LASTEXITCODE -eq 0) { $py = @{ exe = $exe; args = $args0 }; break }
    }
}
if (-not $py) { Write-Error "Python 3.11 یا بالاتر لازم است. از python.org نصب کنید (گزینه Add to PATH را بزنید)."; exit 1 }
Write-Host "[1/4] Python پیدا شد"

Write-Host "[2/4] ساخت محیط مجازی (.venv)"
& $py.exe @($py.args) -m venv .venv
$vpy = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"

Write-Host "[3/4] نصب وابستگی‌ها"
& $vpy -m pip install --quiet --upgrade pip
& $vpy -m pip install --quiet -r requirements.txt
if ($LASTEXITCODE -ne 0) { Write-Error "نصب وابستگی‌ها ناموفق بود"; exit 1 }

Write-Host "[4/4] ساخت فایل تنظیمات و بررسی نصب"
New-Item -ItemType Directory -Force -Path data | Out-Null
if (-not (Test-Path .env)) {
    $secret = & $vpy -c "import secrets;print(secrets.token_hex(32))"
    $pw = & $vpy -c "import secrets;print(secrets.token_urlsafe(12))"
    $demo = if ($NoDemo) { 0 } else { 1 }
    @("AQUA_SECRET=$secret", "AQUA_ADMIN_PASSWORD=$pw", "AQUA_DEMO=$demo", "HOST=127.0.0.1", "PORT=8000") |
        Set-Content -Encoding ASCII .env
    Write-Host "فایل .env با کلید و گذرواژه تصادفی ساخته شد."
}
& $vpy -c "import starlette, uvicorn, pydantic, numpy, jwt, multipart; print('وابستگی‌ها سالم هستند')"
if ($LASTEXITCODE -ne 0) { Write-Error "بررسی نصب ناموفق بود"; exit 1 }

$start = @'
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
Get-Content .env | ForEach-Object { if ($_ -match '^\s*([^#=]+)=(.*)$') { Set-Item -Path ("Env:" + $Matches[1].Trim()) -Value $Matches[2].Trim() } }
$h = if ($env:HOST) { $env:HOST } else { "127.0.0.1" }
$p = if ($env:PORT) { $env:PORT } else { "8000" }
& "$PSScriptRoot\.venv\Scripts\python.exe" -m uvicorn app.main:app --host $h --port $p
'@
Set-Content -Encoding UTF8 start.ps1 $start

Write-Host ""
Write-Host "نصب کامل شد. اجرا:  powershell -ExecutionPolicy Bypass -File start.ps1   سپس http://127.0.0.1:8000"
Write-Host "ورود: کاربر admin و گذرواژه AQUA_ADMIN_PASSWORD از فایل .env"
if ($Run) { & "$PSScriptRoot\start.ps1" }
