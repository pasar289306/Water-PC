"""مدل‌های تجهیزات: برج خنک‌کن، بویلر، RO، پمپ، تصفیه‌خانه.
هر تابع محدوده اعتبار و هشدار برمی‌گرداند."""
from __future__ import annotations

G = 9.80665


def _chk(cond: bool, msg: str, warnings: list[str]):
    if cond:
        warnings.append(msg)


# ---------------- برج خنک‌کن ----------------
def cooling_tower(circulation_m3h: float, delta_t_c: float, coc: float,
                  makeup_m3h: float | None = None, drift_pct: float = 0.005,
                  ec_makeup: float | None = None, ec_blowdown: float | None = None,
                  coc_max: float | None = None) -> dict:
    """تبخیر ≈ 0.00085 × 1.8 × ΔT × Q (قاعده سرانگشتی؛ ΔT بر حسب °C).
    B = E/(COC−1) − D ؛ M = E + B + D (بدون نشتی)."""
    w: list[str] = []
    if ec_makeup and ec_blowdown:
        coc = ec_blowdown / ec_makeup
    _chk(coc <= 1, "سیکل تغلیظ باید بیشتر از ۱ باشد.", w)
    if coc <= 1:
        raise ValueError("COC باید بزرگ‌تر از ۱ باشد")
    _chk(coc > 10, "COC بسیار بالا؛ خطر رسوب/خوردگی.", w)
    E = 0.00085 * 1.8 * delta_t_c * circulation_m3h
    D = circulation_m3h * drift_pct / 100.0
    B = max(E / (coc - 1) - D, 0.0)
    M_theory = E + B + D
    out = {"evaporation_m3h": E, "drift_m3h": D, "blowdown_m3h": B,
           "makeup_theory_m3h": M_theory, "coc": coc, "warnings": w}
    if makeup_m3h is not None:
        L = makeup_m3h - M_theory
        out["makeup_actual_m3h"] = makeup_m3h
        out["unexplained_loss_m3h"] = L
        out["unexplained_loss_pct"] = L / makeup_m3h * 100 if makeup_m3h else None
        if makeup_m3h and L / makeup_m3h > 0.05:
            w.append("آب جبرانی واقعی بیش از مقدار نظری است؛ احتمال نشتی/سرریز.")
    return out


def cooling_tower_coc_saving(circulation_m3h: float, delta_t_c: float,
                             coc_from: float, coc_to: float, hours: float = 8000) -> dict:
    a = cooling_tower(circulation_m3h, delta_t_c, coc_from)
    b = cooling_tower(circulation_m3h, delta_t_c, coc_to)
    s = (a["makeup_theory_m3h"] - b["makeup_theory_m3h"]) * hours
    return {"saving_m3_year": s,
            "saving_pct_of_makeup": s / (a["makeup_theory_m3h"] * hours) * 100,
            "makeup_before_m3h": a["makeup_theory_m3h"], "makeup_after_m3h": b["makeup_theory_m3h"],
            "note": "فقط با تأیید محدودیت‌های رسوب/خوردگی قابل اجراست."}


# ---------------- بویلر ----------------
def boiler(steam_t_h: float, condensate_return_t_h: float, makeup_m3_h: float,
           blowdown_pct: float | None = None, feed_tds: float | None = None,
           boiler_tds_max: float | None = None) -> dict:
    w: list[str] = []
    cr = condensate_return_t_h / steam_t_h * 100 if steam_t_h else None
    mwr = makeup_m3_h / steam_t_h if steam_t_h else None
    if feed_tds and boiler_tds_max:
        coc = boiler_tds_max / feed_tds
        blowdown_pct_calc = 100.0 / coc
        blowdown_pct = blowdown_pct if blowdown_pct is not None else blowdown_pct_calc
    else:
        coc = None
    _chk(cr is not None and cr < 60, "نرخ بازیافت کندانس پایین است (<۶۰٪).", w)
    _chk(mwr is not None and mwr > 0.4, "نسبت آب جبرانی به بخار بالا است؛ بررسی نشتی/تله‌های بخار.", w)
    return {"condensate_return_pct": cr, "makeup_ratio": mwr, "boiler_coc": coc,
            "blowdown_pct": blowdown_pct, "warnings": w}


# ---------------- RO ----------------
def reverse_osmosis(feed_m3h: float, permeate_m3h: float, ec_feed: float, ec_perm: float,
                    p_feed_bar: float | None = None, p_conc_bar: float | None = None,
                    power_kw: float | None = None, max_recovery: float = 85.0) -> dict:
    if feed_m3h <= 0:
        raise ValueError("دبی خوراک باید مثبت باشد")
    w: list[str] = []
    rec = permeate_m3h / feed_m3h * 100
    rej = (1 - ec_perm / ec_feed) * 100 if ec_feed else None
    conc = feed_m3h - permeate_m3h
    dp = (p_feed_bar - p_conc_bar) if p_feed_bar is not None and p_conc_bar is not None else None
    sec = power_kw / permeate_m3h if power_kw is not None and permeate_m3h else None
    _chk(rec > max_recovery, f"Recovery بالاتر از {max_recovery}٪؛ خطر رسوب غشا.", w)
    _chk(rej is not None and rej < 95, "دفع نمک پایین‌تر از ۹۵٪؛ بررسی تخریب/نشتی غشا.", w)
    _chk(dp is not None and dp > 3.5, "افت فشار غشا بالا؛ احتمال گرفتگی.", w)
    return {"recovery_pct": rec, "salt_rejection_pct": rej, "concentrate_m3h": conc,
            "delta_p_bar": dp, "sec_kwh_m3": sec, "warnings": w}


# ---------------- پمپ ----------------
def pump(q_m3h: float, head_m: float, p_electric_kw: float, rho: float = 998.0) -> dict:
    if p_electric_kw <= 0:
        raise ValueError("توان الکتریکی باید مثبت باشد")
    q = q_m3h / 3600.0
    p_hyd = rho * G * q * head_m / 1000.0  # kW
    eta = p_hyd / p_electric_kw * 100
    w: list[str] = []
    _chk(eta > 95, "راندمان غیرواقعی؛ داده‌ها را بررسی کنید.", w)
    _chk(eta < 50, "راندمان پایین (<۵۰٪)؛ بررسی ساییدگی/نقطه کار.", w)
    return {"hydraulic_kw": p_hyd, "efficiency_pct": eta,
            "specific_energy_kwh_m3": p_electric_kw / q_m3h if q_m3h else None, "warnings": w}


# ---------------- تصفیه‌خانه ----------------
def removal(c_in: float, c_out: float) -> float:
    if c_in <= 0:
        raise ValueError("غلظت ورودی باید مثبت باشد")
    return (c_in - c_out) / c_in * 100


def wastewater_plant(q_in_m3d: float, q_out_m3d: float, cod_in: float, cod_out: float,
                     tss_in: float | None = None, tss_out: float | None = None,
                     reuse_m3d: float = 0.0, energy_kwh_d: float | None = None) -> dict:
    out = {"cod_removal_pct": removal(cod_in, cod_out),
           "water_recovery_pct": q_out_m3d / q_in_m3d * 100 if q_in_m3d else None,
           "reuse_share_pct": reuse_m3d / q_out_m3d * 100 if q_out_m3d else None,
           "sludge_water_loss_m3d": q_in_m3d - q_out_m3d,
           "warnings": ["برای استفاده مجدد، کیفیت متناسب با مصرف‌کننده (نه فقط COD) کنترل شود."]}
    if tss_in and tss_out is not None:
        out["tss_removal_pct"] = removal(tss_in, tss_out)
    if energy_kwh_d is not None and q_out_m3d:
        out["energy_kwh_m3"] = energy_kwh_d / q_out_m3d
    return out
