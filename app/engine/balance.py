"""موتور موازنه آب (Water Balance Engine) با محاسبه عدم قطعیت.

مبنا: ΣQin − ΣQout − ΔS = R
باقیمانده R به‌طور خودکار نشتی فرض نمی‌شود؛ معناداری آن با Z = R / u_R سنجیده می‌شود.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict
from typing import Iterable


@dataclass
class Flow:
    name: str
    volume: float                 # m3 در بازه گزارش
    uncertainty_pct: float = 2.0  # عدم قطعیت نسبی (٪) - ۹۵٪ اطمینان
    quality: str = "valid"        # valid | estimated | suspect | invalid | missing

    @property
    def u_abs(self) -> float:
        return abs(self.volume) * self.uncertainty_pct / 100.0


@dataclass
class BalanceResult:
    total_in: float
    total_out: float
    storage_change: float
    residual: float
    error_percent: float | None
    u_residual: float
    z_score: float | None
    significant: bool
    status: str
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _usable(flows: Iterable[Flow]):
    for f in flows:
        if f.quality in ("invalid", "missing"):
            continue
        yield f


def water_balance(inputs: list[Flow], outputs: list[Flow], storage_change: float = 0.0,
                  storage_uncertainty: float = 0.0, screening_pct: float = 10.0,
                  z_limit: float = 2.0) -> BalanceResult:
    """موازنه جرمی یک گره/واحد/سایت.

    - جریان‌های invalid/missing از محاسبه کنار گذاشته می‌شوند و هشدار می‌گیرند.
    - عدم قطعیت مستقل: u_R = sqrt(Σ u_i²)
    - آستانه ۱۰٪ فقط آستانه غربالگری است (نه حد مجاز عمومی).
    """
    warnings: list[str] = []
    for f in list(inputs) + list(outputs):
        if f.quality in ("invalid", "missing"):
            warnings.append(f"جریان «{f.name}» با وضعیت {f.quality} در محاسبه لحاظ نشد.")
        elif f.quality == "suspect":
            warnings.append(f"داده جریان «{f.name}» مشکوک است.")

    ins = list(_usable(inputs))
    outs = list(_usable(outputs))
    total_in = sum(f.volume for f in ins)
    total_out = sum(f.volume for f in outs)
    residual = total_in - total_out - storage_change
    u_r = math.sqrt(sum(f.u_abs ** 2 for f in ins + outs) + storage_uncertainty ** 2)

    error_pct = None if total_in == 0 else abs(residual) / total_in * 100.0
    z = None if u_r == 0 else residual / u_r
    significant = z is not None and abs(z) > z_limit

    if total_in == 0:
        status = "بدون ورودی"
    elif error_pct is not None and error_pct > screening_pct:
        status = "نیازمند بررسی (بالاتر از آستانه غربالگری)"
    elif significant:
        status = "اختلاف معنادار نسبت به عدم قطعیت"
    else:
        status = "قابل قبول (در محدوده عدم قطعیت)"

    return BalanceResult(total_in, total_out, storage_change, residual, error_pct,
                         u_r, z, significant, status, warnings)
