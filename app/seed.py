"""داده‌های اولیه: KPIها، بانک راهکارها، کاربر مدیر و (اختیاری) مجتمع نمونه.

توجه: درصدهای صرفه‌جویی بانک راهکارها «بازه‌های راهنمای مهندسی» هستند، نه تضمین.
هر مقدار باید با مبنای محاسبه‌اش و در سایت واقعی راستی‌آزمایی شود."""
from __future__ import annotations

import os
import random
import secrets

from . import auth
from .db import DATA_DIR, db, one
from .engine.kpi import DEFAULT_KPIS

GEN = "مبنای عمومی مهندسی؛ نیازمند راستی‌آزمایی در سایت"
DOE = "US DOE FEMP – Cooling Tower Water Management"

# code, title, grp, hierarchy, system, industries, problem, mechanism, required_data, calc_method,
# min%, max%, basis, capex_note, payback_note, tech_risk, ease, quality_constraints, verification, source
M = [
    ("A-01", "شناسایی و رفع نشتی شبکه توزیع", "A", "reduce", "SYS-08", "ALL", "نشتی خطوط، فلنج‌ها و اتصالات",
     "کاهش اتلاف فیزیکی", "کنتور اصلی/فرعی، جریان پایه شب", "S = Q_leak × t × 8760", 2, 15, "کل آب ورودی مجتمع",
     "کم تا متوسط", "معمولاً کمتر از ۲ سال", 1, 4, "—", "مقایسه جریان پایه شب قبل و بعد", GEN),
    ("A-02", "کنترل فشار شبکه (PRV)", "A", "reduce", "SYS-08", "ALL", "فشار اضافی و افزایش نشتی/مصرف",
     "کاهش دبی نشتی و مصرف وابسته به فشار", "فشار نقاط مختلف، دبی", "مقایسه دبی در فشار قبل/بعد", 1, 8,
     "آب توزیع‌شده در ناحیه", "متوسط", "۱ تا ۴ سال", 2, 3, "حفظ حداقل فشار فرآیند", "ثبت فشار و دبی", GEN),
    ("A-03", "نصب نازل‌های کم‌مصرف و کنترل دبی شست‌وشو", "A", "reduce", "SYS-05", "ALL", "مصرف زیاد آب شست‌وشو",
     "کاهش دبی با حفظ کارایی", "دبی و زمان شست‌وشو", "S = Q_before·t_before − Q_after·t_after", 10, 40,
     "آب مصرفی شست‌وشوی همان فرآیند", "کم", "کمتر از ۱ سال", 1, 5, "بازرسی کیفیت نظافت", "کنتور فرعی شست‌وشو", GEN),
    ("A-04", "حذف شست‌وشوی غیرضروری و بهینه‌سازی زمان‌بندی", "A", "reduce", "SYS-05", "ALL", "شست‌وشو بدون نیاز",
     "کاهش تعداد/مدت چرخه‌ها", "برنامه و ثبت شست‌وشو", "S = S_cycle × ΔN", 5, 25, "آب شست‌وشو",
     "ناچیز", "فوری", 1, 5, "الزامات بهداشتی/ایمنی", "ثبت چرخه‌ها", GEN),
    ("A-05", "کنترل سرریز مخازن (سنسور سطح و اینترلاک)", "A", "reduce", "SYS-08", "ALL", "سرریز مخازن و حوضچه‌ها",
     "جلوگیری از اتلاف", "سطح مخزن، ساعات سرریز", "حجم سرریز مشاهده‌شده", 0.5, 5, "کل آب ورودی",
     "کم", "کمتر از ۱ سال", 1, 4, "—", "ثبت سطح", GEN),
    ("A-06", "اصلاح آبیاری فضای سبز (زمان‌بندی/قطره‌ای)", "A", "reduce", "SYS-11", "ALL", "آبیاری بیش از نیاز",
     "کاهش مصرف", "سطح و برنامه آبیاری", "S = A × Δ(mm)/1000", 20, 50, "آب آبیاری",
     "کم", "۱ تا ۲ سال", 1, 4, "—", "کنتور فرعی", GEN),
    ("A-07", "نصب کنتور فرعی و پایش متمرکز", "A", "reduce", "SYS-08", "ALL", "فقدان داده تفکیکی مصرف",
     "امکان شناسایی انحراف و نشتی", "نقشه شبکه", "منفعت غیرمستقیم (شناسایی)", 1, 10, "کل آب مجتمع",
     "متوسط", "۱ تا ۳ سال", 1, 3, "کالیبراسیون کنتور", "مقایسه کنتور اصلی و مجموع فرعی", "EPA Water Efficiency"),
    ("B-01", "افزایش کنترل‌شده سیکل تغلیظ برج خنک‌کن", "B", "reduce", "SYS-01", "IND-01,IND-02,IND-03,IND-09",
     "COC پایین و بلودان زیاد", "کاهش بلودان و آب جبرانی", "EC آب جبرانی/چرخشی، دبی بلودان",
     "M = E·COC/(COC−1)", 5, 25, "آب جبرانی برج (نه کل کارخانه)", "کم تا متوسط", "کمتر از ۲ سال", 3, 3,
     "رسوب، خوردگی، سیلیس، سختی", "ثبت COC و مصرف جبرانی", DOE),
    ("B-02", "کنترل خودکار بلودان بر اساس هدایت", "B", "reduce", "SYS-01", "IND-01,IND-02,IND-03,IND-09",
     "بلودان دستی/ثابت", "تنظیم دقیق بلودان", "EC، دبی", "مقایسه بلودان قبل/بعد", 5, 20, "آب جبرانی برج",
     "کم", "کمتر از ۱ سال", 2, 4, "کالیبراسیون پروب EC", "ثبت EC و بلودان", DOE),
    ("B-03", "اصلاح حذف‌کننده قطرات (Drift Eliminator)", "B", "reduce", "SYS-01", "IND-01,IND-02,IND-03,IND-09",
     "دریفت بالا", "کاهش تلفات قطره", "درصد دریفت", "D = Q·drift%", 0.1, 1, "آب جبرانی برج",
     "متوسط", "۲ تا ۵ سال", 2, 3, "—", "اندازه‌گیری دریفت", GEN),
    ("B-04", "کنترل نشتی و سرریز حوضچه برج", "B", "reduce", "SYS-01", "ALL", "آب جبرانی واقعی بیش از نظری",
     "شناسایی تلفات غیرتبخیری", "آب جبرانی واقعی و نظری", "L = M_actual − M_theory", 1, 8, "آب جبرانی برج",
     "کم", "کمتر از ۱ سال", 1, 4, "—", "مقایسه موازنه برج", GEN),
    ("B-05", "بازیافت کندانس بیشتر", "B", "reuse", "SYS-02", "IND-01,IND-02,IND-03,IND-06,IND-09",
     "نرخ بازیافت کندانس پایین", "کاهش آب جبرانی و انرژی", "بخار، کندانس برگشتی",
     "S = ΔCR × V_steam", 10, 40, "آب جبرانی بویلر", "متوسط", "۱ تا ۳ سال", 2, 3,
     "آلودگی کندانس (هدایت/روغن)", "کنتور کندانس", GEN),
    ("B-06", "تعمیر تله‌های بخار و نشتی شبکه بخار", "B", "reduce", "SYS-02", "ALL", "نشتی بخار و کندانس",
     "کاهش تلفات", "بررسی تله‌ها", "S = n × m_leak × t", 2, 10, "آب جبرانی بویلر",
     "کم", "کمتر از ۱ سال", 1, 4, "—", "بازرسی دوره‌ای", GEN),
    ("B-07", "بهینه‌سازی بلودان پیوسته بویلر", "B", "reduce", "SYS-02", "ALL", "بلودان بیش از نیاز",
     "تنظیم بر اساس TDS", "TDS آب بویلر/تغذیه", "B% = 100/COC", 10, 30, "بلودان بویلر",
     "کم", "کمتر از ۲ سال", 2, 4, "حد TDS و سیلیس بویلر", "ثبت TDS", GEN),
    ("B-08", "بهینه‌سازی فشار و پیش‌تصفیه RO", "B", "reduce", "SYS-03", "ALL", "گرفتگی غشا و بازیابی پایین",
     "افزایش پایداری Recovery", "فشار ورودی/خروجی، EC", "Recovery = Qp/Qf", 2, 10, "آب خام ورودی RO",
     "متوسط", "۲ تا ۴ سال", 3, 3, "رسوب‌گذاری با افزایش Recovery", "ثبت Recovery و ΔP", GEN),
    ("B-09", "استفاده مجدد از کنسانتره RO در کاربرد سازگار", "B", "reuse", "SYS-03", "ALL", "تخلیه کنسانتره",
     "جایگزینی آب تازه", "کیفیت کنسانتره و نیاز مصرف‌کننده", "S = Q_conc × سهم قابل استفاده", 5, 30,
     "کنسانتره RO", "متوسط", "۱ تا ۳ سال", 3, 3, "شوری/سختی مصرف‌کننده", "آنالیز آزمایشگاه", GEN),
    ("B-10", "شست‌وشوی معکوس هوشمند فیلترها", "B", "reduce", "SYS-03", "ALL", "بک‌واش زمانی",
     "بک‌واش بر اساس ΔP", "ΔP فیلتر", "S = ΔN × V_backwash", 10, 50, "آب بک‌واش",
     "کم", "کمتر از ۲ سال", 2, 4, "—", "ثبت چرخه", GEN),
    ("B-11", "اصلاح عملکرد پمپ‌ها (VSD/تعویض)", "B", "reduce", "SYS-08", "ALL", "راندمان پمپ پایین",
     "کاهش انرژی انتقال و گاهی دبی اضافی", "دبی، هد، توان", "η = ρgQH/P", 0, 5, "آب انتقالی (عمدتاً انرژی)",
     "متوسط", "۱ تا ۴ سال", 2, 3, "—", "اندازه‌گیری توان", GEN),
    ("C-01", "بازیافت پساب تصفیه‌شده برای برج خنک‌کن", "C", "reuse", "SYS-06", "IND-01,IND-02,IND-03,IND-09",
     "برداشت زیاد آب تازه", "جایگزینی آب جبرانی", "کیفیت پساب، ظرفیت ETP", "S = V_reuse",
     10, 60, "آب جبرانی برج", "بالا", "۲ تا ۶ سال", 4, 2, "آمونیاک، فسفات، TDS، میکروبی", "آنالیز و کنتور", GEN),
    ("C-02", "بازچرخانی آب خنک‌کاری یک‌بارگذر", "C", "reuse", "SYS-01", "ALL", "تخلیه آب خنک‌کن تمیز",
     "گردش در مدار بسته", "دما و دبی", "S = Q × t", 30, 90, "آب خنک‌کاری یک‌بارگذر",
     "متوسط", "۱ تا ۳ سال", 2, 3, "دمای مجاز فرآیند", "کنتور", GEN),
    ("C-03", "بازیافت آب شست‌وشو (فیلتر/ته‌نشینی)", "C", "reuse", "SYS-05", "ALL", "تخلیه آب شست‌وشو",
     "استفاده مجدد در شست‌وشوی اولیه", "کیفیت پساب شست‌وشو", "S = V × سهم استفاده مجدد", 20, 60,
     "آب شست‌وشو", "متوسط", "۱ تا ۳ سال", 3, 3, "الزامات بهداشتی", "آنالیز", GEN),
    ("C-04", "بازیافت آب بک‌واش فیلترها", "C", "reuse", "SYS-03", "ALL", "تخلیه آب بک‌واش",
     "برگشت به ابتدای تصفیه", "کدورت/TSS", "S = V_backwash × سهم برگشتی", 50, 90, "آب بک‌واش",
     "کم", "کمتر از ۲ سال", 2, 4, "کنترل کدورت برگشتی", "کنتور", GEN),
    ("C-05", "جمع‌آوری و استفاده از آب باران", "C", "replace", "", "ALL", "وابستگی به آب تازه",
     "جایگزینی در مصارف مناسب", "سطح آبگیر، بارش", "S = A × R × C", 1, 8, "کل آب تازه",
     "متوسط تا بالا", "۴ تا ۱۰ سال", 2, 3, "کیفیت و ذخیره‌سازی", "کنتور", GEN),
    ("C-06", "استفاده از پساب شهری تصفیه‌شده", "C", "replace", "", "ALL", "کمبود آب تازه",
     "جایگزینی آب خام", "دسترسی و کیفیت پساب شهری", "S = V_sub", 10, 70, "مصارف جایگزین‌شده",
     "بالا", "متغیر", 4, 2, "مجوز محیط‌زیست و کیفیت", "کنتور", GEN),
    ("C-07", "طراحی شبکه آب چندکیفیته", "C", "reuse", "SYS-04", "IND-02,IND-03,IND-08,IND-09,IND-07",
     "استفاده از آب با کیفیت بالا برای مصارف کم‌حساس", "آبشاری‌کردن مصرف", "تحلیل Water Pinch",
     "حداقل آب تازه (Pinch)", 10, 40, "آب فرآیندی", "بالا", "۲ تا ۵ سال", 4, 2,
     "قیود چند آلاینده", "موازنه جرم آلاینده", "Water Pinch Analysis"),
    ("C-08", "بازیافت بلودان برج/بویلر در مصارف سازگار", "C", "reuse", "SYS-01", "ALL", "تخلیه بلودان",
     "جایگزینی آب جبرانی/شست‌وشو", "EC و ترکیب بلودان", "S = V_blowdown × سهم", 5, 40, "بلودان",
     "متوسط", "۱ تا ۴ سال", 3, 3, "املاح و مواد شیمیایی تصفیه", "آنالیز", GEN),
    ("D-01", "تغییر فناوری خنک‌کاری (هوا/هیبرید)", "D", "reduce", "SYS-01", "IND-01,IND-02",
     "مصرف زیاد آب تبخیری", "کاهش تبخیر", "بار حرارتی", "S = E_before − E_after", 30, 90,
     "آب جبرانی برج", "بسیار بالا", "بیش از ۵ سال", 4, 1, "افزایش مصرف انرژی", "اندازه‌گیری", GEN),
    ("D-02", "تغییر فناوری شست‌وشو (CIP بهینه/خشک)", "D", "reduce", "SYS-05", "IND-06,IND-10",
     "مصرف بالای آب CIP", "کاهش حجم و چرخه", "حجم CIP", "S = ΔV × N", 15, 50, "آب CIP",
     "متوسط", "۱ تا ۳ سال", 3, 3, "الزامات بهداشتی", "کنتور فرعی", GEN),
    ("D-03", "بازچرخانی پساب رنگرزی/شست‌وشوی نساجی", "D", "reuse", "SYS-04", "IND-07",
     "پساب رنگی زیاد", "تصفیه و استفاده مجدد", "کیفیت پساب", "S = V_reuse", 20, 60, "آب فرآیندی",
     "بالا", "۲ تا ۵ سال", 4, 2, "رنگ و املاح", "آنالیز", GEN),
    ("D-04", "بازیافت آب باطله و آبگیری لجن (معدن)", "D", "reuse", "SYS-04", "IND-04",
     "تلفات آب در باطله", "بازگرداندن آب به فرآیند", "رطوبت باطله", "S = V_recovered", 20, 70, "آب فرآوری",
     "بالا", "۲ تا ۵ سال", 3, 2, "املاح و pH", "کنتور", GEN),
    ("D-05", "کنترل گردوغبار با آب بازیافتی (سیمان/معدن)", "D", "replace", "SYS-04", "IND-04,IND-05",
     "استفاده از آب تازه برای گردوغبار", "جایگزینی با پساب", "حجم مصرف", "S = V", 20, 80, "آب گردوغبار",
     "کم", "کمتر از ۲ سال", 1, 4, "—", "کنتور", GEN),
    ("E-01", "پایش هوشمند و هشدار انحراف مصرف", "E", "reduce", "", "ALL", "نبود پایش مستمر",
     "تشخیص زودهنگام انحراف", "داده کنتورها", "منفعت غیرمستقیم", 2, 10, "کل مصرف",
     "متوسط", "۱ تا ۳ سال", 1, 3, "—", "مقایسه با خط مبنا", "ISO 46001"),
    ("E-02", "مدیریت مصرف در شیفت‌ها و آموزش کارکنان", "E", "reduce", "", "ALL", "رفتار مصرف",
     "کاهش اتلاف عملیاتی", "ثبت مصرف شیفتی", "تحلیل شیفتی", 1, 5, "کل مصرف",
     "ناچیز", "فوری", 1, 5, "—", "کنتور فرعی", "ISO 46001"),
    ("E-03", "برنامه اضطراری کم‌آبی", "E", "reduce", "", "ALL", "ریسک تأمین",
     "کاهش ریسک نه لزوماً مصرف", "اولویت مصارف", "—", None, None, "—",
     "ناچیز", "—", 1, 4, "—", "تمرین دوره‌ای", "ISO 46001"),
    ("E-04", "تدوین خط مبنا و صحه‌گذاری صرفه‌جویی (M&V)", "E", "reduce", "", "ALL", "ادعای صرفه‌جویی بدون مبنا",
     "اندازه‌گیری مبتنی بر مدل", "تولید، دما، ساعات", "Saving = Baseline_adj − Actual", None, None, "—",
     "ناچیز", "—", 1, 4, "—", "R² و CV(RMSE)", "ISO 46001"),
    ("A-08", "اصلاح فرآیند خنک‌کاری تجهیزات (جلوگیری از تخلیه مستقیم)", "A", "reduce", "SYS-01", "ALL",
     "تخلیه آب خنک‌کن به فاضلاب", "بازگشت به مدار", "نقشه خطوط", "S = Q × t", 20, 80, "آب خنک‌کن تجهیز",
     "کم تا متوسط", "کمتر از ۲ سال", 1, 4, "دمای مجاز", "کنتور", GEN),
    ("B-12", "بهینه‌سازی بازیافت حرارت و آب بلودان بویلر", "B", "reuse", "SYS-02", "ALL",
     "تلفات حرارت/آب بلودان", "فلش‌تانک و مبدل", "بلودان", "S = V_flash", 2, 10, "آب جبرانی بویلر",
     "متوسط", "۲ تا ۴ سال", 2, 3, "—", "اندازه‌گیری", GEN),
    ("C-09", "بازیافت آب فیلتر پرس/لجن‌گیری ETP", "C", "reuse", "SYS-06", "ALL", "تخلیه فیلتریت",
     "برگشت به ETP", "حجم فیلتریت", "S = V", 1, 5, "آب ورودی ETP", "کم", "کمتر از ۲ سال", 2, 4,
     "بار آلودگی برگشتی", "کنتور", GEN),
]

MEASURE_COLS = ("code,title,grp,hierarchy,system_code,industries,problem,mechanism,required_data,calc_method,"
                "saving_min_pct,saving_max_pct,saving_basis,capex_note,payback_note,tech_risk,ease,"
                "quality_constraints,verification,source").split(",")


def seed_kpis():
    with db() as c:
        for code, name, formula, unit, grp, warn, crit in DEFAULT_KPIS:
            c.execute("INSERT OR IGNORE INTO kpi_defs(code,name,formula,unit,grp,warn,critical,reference) VALUES(?,?,?,?,?,?,?,?)",
                      (code, name, formula, unit, grp, warn, crit, "ISO 46001 / EPA / DOE"))


def seed_measures():
    with db() as c:
        for m in M:
            assert len(m) == len(MEASURE_COLS), m[0]
            c.execute(f"INSERT OR IGNORE INTO measures({','.join(MEASURE_COLS)}) VALUES({','.join('?' * len(m))})", m)


def seed_admin():
    with db() as c:
        if one(c.execute("SELECT id FROM users LIMIT 1")):
            return
    pw = os.environ.get("AQUA_ADMIN_PASSWORD")
    generated = False
    if not pw:
        pw, generated = secrets.token_urlsafe(9), True
    with db() as c:
        c.execute("INSERT INTO users(username,full_name,password_hash,role) VALUES('admin','مدیر سیستم',?,'admin')",
                  (auth.hash_password(pw),))
    if generated:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        f = DATA_DIR / "INITIAL_ADMIN_PASSWORD.txt"
        f.write_text(f"username: admin\npassword: {pw}\n(پس از ورود، گذرواژه را تغییر دهید و این فایل را حذف کنید)\n", encoding="utf-8")
        try:
            f.chmod(0o600)
        except OSError:
            pass
        try:  # کنسول ویندوز/برنامه بدون کنسول ممکن است فارسی را نتواند چاپ کند؛ این نباید startup را بیندازد
            print(f"\n[AquaAudit] Admin user created. Initial password saved in: {f}\n")
        except Exception:
            pass


def seed_demo():
    """مجتمع نمونه فرضی با ۱۲ ماه داده (فقط برای آشنایی؛ کاملاً مصنوعی)."""
    with db() as c:
        if one(c.execute("SELECT id FROM sites LIMIT 1")):
            return
        cur = c.execute("""INSERT INTO sites(name,industry_code,location,product_unit,cost_supply,cost_pumping,
            cost_treatment,cost_distribution,cost_disposal,screening_pct) VALUES(?,?,?,?,?,?,?,?,?,10)""",
                        ("مجتمع نمونه (داده فرضی)", "IND-03", "—", "تن", 20000, 3000, 8000, 2000, 6000))
        sid = cur.lastrowid
        u1 = c.execute("INSERT INTO units(site_id,name,system_code) VALUES(?,?,?)", (sid, "سیستم خنک‌کاری", "SYS-01")).lastrowid
        c.execute("INSERT INTO units(site_id,name,system_code) VALUES(?,?,?)", (sid, "بخار و بویلر", "SYS-02"))
        c.execute("INSERT INTO units(site_id,name,system_code) VALUES(?,?,?)", (sid, "تصفیه فاضلاب", "SYS-06"))
        import json
        c.execute("INSERT INTO equipment(site_id,unit_id,name,kind,params) VALUES(?,?,?,?,?)",
                  (sid, u1, "برج خنک‌کن شماره ۱", "cooling_tower",
                   json.dumps({"circulation_m3h": 1500, "delta_t_c": 6, "coc": 2.5})))
        c.execute("INSERT INTO equipment(site_id,name,kind,params) VALUES(?,?,?,?)",
                  (sid, "بویلر اصلی", "boiler", json.dumps({"steam_t_h": 80, "condensate_return_t_h": 45})))

        def mk(name, d, kind, scope="external", unc=2.0):
            return c.execute("INSERT INTO streams(site_id,name,direction,kind,scope,uncertainty_pct) VALUES(?,?,?,?,?,?)",
                             (sid, name, d, kind, scope, unc)).lastrowid
        well = mk("چاه‌های تأمین", "in", "raw", unc=2)
        city = mk("آب شهری", "in", "potable", unc=1.5)
        rain = mk("آب باران", "in", "rain", unc=10)
        ww = mk("پساب خروجی", "out", "wastewater", unc=3)
        bd = mk("بلودان برج", "out", "blowdown", unc=5)
        ev = mk("تبخیر برج", "out", "evaporation", unc=10)
        pr = mk("آب همراه محصول", "out", "product", unc=5)
        lk = mk("نشتی تخمینی", "out", "leakage", unc=30)
        rc = mk("پساب بازچرخانی داخلی", "in", "reclaimed", scope="internal", unc=3)

        rnd = random.Random(42)
        for m in range(1, 13):
            period = f"2025-{m:02d}"
            temp = 8 + 20 * (1 - abs(m - 7) / 6)
            prod = 16000 + rnd.uniform(-1500, 1500)
            c.execute("INSERT INTO production(site_id,period,product_qty,ambient_temp,operating_hours) VALUES(?,?,?,?,?)",
                      (sid, period, prod, temp, 720 - rnd.randint(0, 40)))
            base = prod * 4.4 + temp * 900
            wellv = base * 0.62
            cityv = base * 0.30
            rainv = base * 0.01
            tot = wellv + cityv + rainv
            c.execute("INSERT INTO readings(stream_id,period,volume) VALUES(?,?,?)", (well, period, wellv))
            c.execute("INSERT INTO readings(stream_id,period,volume) VALUES(?,?,?)", (city, period, cityv))
            c.execute("INSERT INTO readings(stream_id,period,volume,quality) VALUES(?,?,?,'estimated')", (rain, period, rainv))
            ev_v = tot * (0.30 + 0.004 * temp)
            bd_v = tot * 0.12
            pr_v = tot * 0.06
            lk_v = tot * 0.04
            ww_v = tot * 0.28
            sumout = ev_v + bd_v + pr_v + lk_v + ww_v
            ww_v += (tot - sumout) + rnd.uniform(-0.02, 0.02) * tot  # اختلاف اندازه‌گیری
            for sid_, v, q in ((ww, ww_v, "valid"), (bd, bd_v, "valid"), (ev, ev_v, "estimated"),
                               (pr, pr_v, "estimated"), (lk, lk_v, "estimated")):
                c.execute("INSERT INTO readings(stream_id,period,volume,quality) VALUES(?,?,?,?)", (sid_, period, v, q))
            c.execute("INSERT INTO readings(stream_id,period,volume) VALUES(?,?,?)", (rc, period, tot * 0.23))
        c.execute("INSERT INTO storage_changes(site_id,period,delta_volume,uncertainty) VALUES(?,?,?,?)",
                  (sid, "2025-12", 0, 500))


def seed_all():
    seed_kpis()
    seed_measures()
    seed_admin()
    if os.environ.get("AQUA_DEMO", "1") == "1":
        seed_demo()
