"""خط مبنا (رگرسیون خطی چندمتغیره)، صرفه‌جویی تعدیل‌شده و تشخیص ناهنجاری."""
from __future__ import annotations

import numpy as np


def fit_baseline(X: list[list[float]], y: list[float]) -> dict:
    """W = b0 + b1*P + b2*T + ... با کمترین مربعات؛ R² و RMSE برمی‌گرداند."""
    Xa = np.asarray(X, float)
    ya = np.asarray(y, float)
    if Xa.ndim != 2 or len(ya) != len(Xa):
        raise ValueError("ابعاد X و y سازگار نیست")
    n, k = Xa.shape
    if n < k + 2:
        raise ValueError("تعداد داده برای برازش کافی نیست")
    A = np.hstack([np.ones((n, 1)), Xa])
    coef, *_ = np.linalg.lstsq(A, ya, rcond=None)
    pred = A @ coef
    ss_res = float(np.sum((ya - pred) ** 2))
    ss_tot = float(np.sum((ya - ya.mean()) ** 2))
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 1.0
    rmse = float(np.sqrt(ss_res / n))
    dof = max(n - k - 1, 1)
    return {"coef": coef.tolist(), "r2": r2, "rmse": rmse,
            "cv_rmse_pct": rmse / ya.mean() * 100 if ya.mean() else None,
            "se": float(np.sqrt(ss_res / dof)), "n": n}


def predict(model: dict, X: list[list[float]]) -> list[float]:
    c = np.asarray(model["coef"], float)
    Xa = np.asarray(X, float)
    return (c[0] + Xa @ c[1:]).tolist()


def adjusted_saving(model: dict, X_post: list[list[float]], actual: list[float]) -> dict:
    """Saving = Baseline_adjusted − Actual، با عدم قطعیت تقریبی (۹۵٪) بر پایه خطای مدل."""
    base = np.asarray(predict(model, X_post))
    act = np.asarray(actual, float)
    n = len(act)
    saving = float(np.sum(base - act))
    u = float(1.96 * model["se"] * np.sqrt(n))
    return {"baseline_total": float(base.sum()), "actual_total": float(act.sum()),
            "saving": saving, "saving_pct": saving / float(base.sum()) * 100 if base.sum() else None,
            "uncertainty_95": u, "significant": abs(saving) > u}


def zscore_anomalies(values: list[float], threshold: float = 3.0, window: int | None = None) -> list[dict]:
    """Z=(x−μ)/σ؛ با window، میانگین/انحراف متحرک (بدون شامل‌کردن نقطه جاری)."""
    v = np.asarray(values, float)
    out = []
    for i, x in enumerate(v):
        ref = v[max(0, i - window):i] if window else np.delete(v, i)
        if len(ref) < 5:
            continue
        sd = ref.std(ddof=1)
        if sd == 0:
            continue
        z = (x - ref.mean()) / sd
        if abs(z) > threshold:
            out.append({"index": i, "value": float(x), "z": float(z)})
    return out


def night_base_flow(hourly: list[float], night_hours: tuple[int, int] = (1, 5)) -> dict:
    """جریان پایه شب/توقف تولید: شاخص مفید نشتی (باید با بازدید میدانی تأیید شود)."""
    h = np.asarray(hourly, float)
    if len(h) % 24 != 0 or len(h) == 0:
        raise ValueError("داده ساعتی باید مضرب ۲۴ باشد")
    d = h.reshape(-1, 24)
    night = d[:, night_hours[0]:night_hours[1]].mean(axis=1)
    return {"night_mean": float(night.mean()), "night_min": float(night.min()),
            "share_of_daily_pct": float(night.mean() * 24 / d.sum(axis=1).mean() * 100)}
