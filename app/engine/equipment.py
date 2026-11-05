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


# ---------------- واحد DM / تبادل یونی ----------------
def dm_plant(feed_m3: float, product_m3: float, regen_waste_m3: float | None = None,
             ec_feed: float | None = None, ec_product: float | None = None) -> dict:
    """بازیابی واحد و نسبت پساب احیا به آب تولیدی (سند ۶.۱ برای SYS-07)."""
    if feed_m3 <= 0 or product_m3 < 0:
        raise ValueError("حجم خوراک باید مثبت و حجم محصول نامنفی باشد")
    w: list[str] = []
    rec = product_m3 / feed_m3 * 100
    waste = regen_waste_m3 if regen_waste_m3 is not None else max(feed_m3 - product_m3, 0.0)
    out = {"recovery_pct": rec, "regen_waste_m3": waste,
           "waste_ratio": waste / product_m3 if product_m3 else None, "warnings": w}
    _chk(product_m3 > feed_m3, "حجم محصول بیشتر از خوراک است؛ داده‌ها را بررسی کنید.", w)
    _chk(rec < 85, "بازیابی DM پایین (<۸۵٪)؛ بررسی دفعات احیا و آبکشی.", w)
    if ec_feed and ec_product is not None:
        out["conductivity_rejection_pct"] = (1 - ec_product / ec_feed) * 100
    return out


# ---------------- شبکه توزیع و آتش‌نشانی ----------------
def distribution(v_input_m3: float, v_delivered_m3: float, q_night_m3h: float | None = None,
                 q_avg_m3h: float | None = None, test_volume_m3: float = 0.0) -> dict:
    """درصد تلفات شبکه و سهم جریان پایه شبانه؛ حجم آزمون‌های آتش‌نشانی به‌عنوان مصرف مجاز کسر می‌شود."""
    if v_input_m3 <= 0:
        raise ValueError("حجم ورودی شبکه باید مثبت باشد")
    w: list[str] = []
    loss = v_input_m3 - v_delivered_m3 - test_volume_m3
    out = {"loss_m3": loss, "loss_pct": loss / v_input_m3 * 100, "warnings": w}
    _chk(loss < 0, "تحویل بیشتر از ورودی است؛ خطای اندازه‌گیری/کنتور.", w)
    _chk(out["loss_pct"] > 15, "تلفات شبکه بالای ۱۵٪؛ نشت‌یابی و ممیزی کنتورها.", w)
    if q_night_m3h is not None and q_avg_m3h:
        out["night_flow_pct"] = q_night_m3h / q_avg_m3h * 100
        _chk(out["night_flow_pct"] > 30, "جریان پایه شب بالاست؛ احتمال نشتی.", w)
    return out


# ---------------- مصارف بهداشتی ----------------
def sanitary(volume_m3: float, persons: float, days: float, benchmark_l_person_day: float = 60.0) -> dict:
    if persons <= 0 or days <= 0:
        raise ValueError("تعداد افراد و روز باید مثبت باشد")
    q = volume_m3 * 1000.0 / (persons * days)
    w: list[str] = []
    _chk(q > benchmark_l_person_day * 1.3, "مصرف سرانه بیش از ۱۳۰٪ مبنا؛ بررسی شیرآلات، فلاش‌تانک و نشتی.", w)
    return {"per_capita_l_person_day": q, "benchmark_l_person_day": benchmark_l_person_day,
            "excess_m3": max(volume_m3 - benchmark_l_person_day * persons * days / 1000.0, 0.0), "warnings": w}


# ---------------- آبیاری فضای سبز ----------------
def irrigation(area_m2: float, et0_mm_day: float, kc: float, days: float, actual_m3: float,
               effective_rain_mm: float = 0.0, efficiency: float = 0.75) -> dict:
    """V_req = A·(ET0·Kc·days − P_eff)/1000/η ؛ مازاد = مصرف واقعی − نیاز."""
    if area_m2 <= 0 or not (0 < efficiency <= 1):
        raise ValueError("مساحت باید مثبت و راندمان بین 0 و 1 باشد")
    need_mm = max(et0_mm_day * kc * days - effective_rain_mm, 0.0)
    req = area_m2 * need_mm / 1000.0 / efficiency
    w: list[str] = []
    out = {"required_m3": req, "excess_m3": actual_m3 - req,
           "excess_pct": (actual_m3 - req) / req * 100 if req else None, "warnings": w}
    _chk(req and actual_m3 > req * 1.25, "مصرف واقعی بیش از ۱۲۵٪ نیاز؛ زمان‌بندی/حسگر رطوبت و پساب تصفیه‌شده را بررسی کنید.", w)
    return out
