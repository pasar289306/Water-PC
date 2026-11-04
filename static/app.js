"use strict";
/* AquaAudit – رابط کاربری (بدون وابستگی خارجی؛ آفلاین) */

// ---------- ابزارها ----------
const esc = s => String(s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const toStr = v => v == null ? "" : Array.isArray(v) ? v.map(toStr).join("") : (typeof v === "object" && "__raw" in v) ? v.__raw : esc(v);
const html = (s, ...v) => ({ __raw: s.reduce((a, x, i) => a + x + (i < v.length ? toStr(v[i]) : ""), "") });
const $ = (sel, root = document) => root.querySelector(sel);
const fmt = (v, d = 1) => v == null || isNaN(v) ? "—" : Number(v).toLocaleString("en-US", { maximumFractionDigits: d, minimumFractionDigits: 0 });

const KIND_FA = { raw: "آب خام", potable: "آب شرب", fresh: "آب تازه", rain: "آب باران", reclaimed_in: "بازیافتی ورودی",
  wastewater: "پساب", blowdown: "بلودان", evaporation: "تبخیر", product: "آب همراه محصول", leakage: "نشتی", other_out: "سایر خروجی",
  reclaimed: "بازیافتی داخلی", recycled: "بازچرخانی", condensate: "کندانس",
  process: "آب فرآیندی", cooling: "آب خنک‌کاری", bfw: "آب تغذیه بویلر", permeate: "آب تولیدی RO", concentrate: "کنسانتره RO" };
const QUAL_FA = { valid: "معتبر", estimated: "تخمینی", suspect: "مشکوک", invalid: "نامعتبر", missing: "ناموجود" };
const SYS_FA = { "SYS-01": "خنک‌کاری", "SYS-02": "بخار/بویلر", "SYS-03": "RO/غشایی", "SYS-04": "فرآیند", "SYS-05": "شست‌وشو", "SYS-06": "فاضلاب",
  "SYS-07": "DM", "SYS-08": "توزیع", "SYS-09": "خدمات", "SYS-10": "بهداشتی", "SYS-11": "آبیاری", "SYS-12": "آتش‌نشانی" };
const IND_FA = { "IND-01": "انرژی", "IND-02": "نفت و گاز", "IND-03": "فلزات", "IND-04": "معدن", "IND-05": "مصالح ساختمانی", "IND-06": "غذایی",
  "IND-07": "نساجی", "IND-08": "کاغذ و چوب", "IND-09": "شیمیایی", "IND-10": "دارویی", "IND-11": "خودرو", "IND-12": "الکترونیک",
  "IND-13": "کشاورزی صنعتی", "IND-14": "لجستیک", "IND-15": "سایر" };
const ROLE_FA = { admin: "مدیر", engineer: "مهندس", viewer: "مشاهده‌گر" };
const PSTATUS = { proposed: "پیشنهادی", approved: "تأییدشده", in_progress: "در حال اجرا", done: "انجام‌شده", rejected: "ردشده" };
const HIER_FA = { reduce: "کاهش", replace: "جایگزینی", reuse: "استفاده مجدد" };

const state = { token: localStorage.getItem("aq_token"), user: null, sites: [], siteId: null, page: "dashboard", catalog: null };
const cat = () => state.catalog || { industries: [], systems: [], stream_types: [], source_categories: [], system_categories: {}, source_attributes: [] };
const stName = code => (cat().stream_types.find(t => t.code === code) || {}).name_fa || code || "";
const eqName = code => (cat().equipment_types.find(t => t.code === code) || {}).name_fa || code || "";
const sysName = code => code ? `${code} ${SYS_FA[code] || ""}`.trim() : "—";
async function loadCatalog() {
  state.catalog = await api("/api/catalog");
  for (const s of state.catalog.systems) SYS_FA[s.code] = s.name_fa;
  for (const i of state.catalog.industries) IND_FA[i.code] = i.name;
}

async function api(path, opt = {}) {
  const headers = { ...(opt.headers || {}) };
  if (state.token) headers.Authorization = "Bearer " + state.token;
  let body = opt.body;
  if (body && !(body instanceof FormData)) { headers["Content-Type"] = "application/json"; body = JSON.stringify(body); }
  const r = await fetch(path, { method: opt.method || "GET", headers, body });
  if (r.status === 401 && !path.includes("/auth/login")) { logout(); throw new Error("نشست منقضی شد؛ دوباره وارد شوید"); }
  if (opt.raw) { if (!r.ok) throw new Error("خطا در دریافت"); return r; }
  let data = null; try { data = await r.json(); } catch { /* بدنه خالی */ }
  if (!r.ok) throw new Error((data && data.detail) || "خطای ناشناخته (" + r.status + ")");
  return data;
}
function toast(msg, bad = false) {
  const t = $("#toast"); t.textContent = msg; t.className = "toast" + (bad ? " bad" : "");
  clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.add("hidden"), bad ? 6000 : 2800);
}
const guard = fn => async (...a) => { try { await fn(...a); } catch (e) { toast(e.message, true); } };
const num = v => v === "" || v == null ? null : Number(v);
const canEdit = () => state.user && ["admin", "engineer"].includes(state.user.role);
const period = () => ($("#periodInp").value || "").trim();
const q = () => period() ? "?period=" + encodeURIComponent(period()) : "";
const site = () => state.sites.find(s => s.id === state.siteId);
function formData(form) { const o = {}; new FormData(form).forEach((v, k) => o[k] = v); return o; }

// ---------- ورود/خروج ----------
function logout() { localStorage.removeItem("aq_token"); state.token = null; state.user = null; showLogin(); }
function showLogin() { $("#app").classList.add("hidden"); $("#login").classList.remove("hidden"); }
$("#loginForm").addEventListener("submit", async e => {
  e.preventDefault(); $("#loginErr").textContent = "";
  try {
    const r = await api("/api/auth/login", { method: "POST", body: formData(e.target) });
    state.token = r.token; state.user = r.user; localStorage.setItem("aq_token", r.token); e.target.reset(); await boot();
  } catch (err) { $("#loginErr").textContent = err.message; }
});
$("#logoutBtn").onclick = logout;

// ---------- ناوبری ----------
const PAGES = [
  ["dashboard", "داشبورد"], ["structure", "ساختار و جریان‌ها"], ["catalog", "طبقه‌بندی صنایع و سیستم‌ها"],
  ["data", "ورود داده"], ["balance", "موازنه و تحلیل"],
  ["calc", "محاسبه تجهیزات"], ["measures", "بانک راهکارها"], ["projects", "پروژه‌ها و اولویت‌بندی"],
  ["kpis", "تعریف شاخص‌ها"], ["report", "گزارش ممیزی"], ["admin", "مدیریت"],
];
function renderNav() {
  $("#nav").innerHTML = PAGES.filter(p => p[0] !== "admin" || state.user.role === "admin")
    .map(([k, t]) => `<a data-page="${k}" class="${k === state.page ? "active" : ""}">${esc(t)}</a>`).join("");
}
$("#nav").addEventListener("click", e => { const a = e.target.closest("a[data-page]"); if (a) { state.page = a.dataset.page; render(); } });
$("#refreshBtn").onclick = () => render();
$("#siteSel").onchange = e => { state.siteId = Number(e.target.value); render(); };
$("#periodInp").addEventListener("change", () => render());

async function boot() {
  try { state.user = await api("/api/auth/me"); } catch { return showLogin(); }
  $("#login").classList.add("hidden"); $("#app").classList.remove("hidden");
  $("#whoami").textContent = `${state.user.full_name || state.user.username} (${ROLE_FA[state.user.role]})`;
  try { await loadCatalog(); } catch (e) { toast("بارگذاری کاتالوگ ناموفق بود: " + e.message, true); }
  await loadSites(); render();
}
async function loadSites(select) {
  state.sites = await api("/api/sites");
  if (select) state.siteId = select;
  if (!state.sites.find(s => s.id === state.siteId)) state.siteId = state.sites[0]?.id ?? null;
  $("#siteSel").innerHTML = state.sites.map(s => `<option value="${s.id}" ${s.id === state.siteId ? "selected" : ""}>${esc(s.name)}</option>`).join("");
}
const render = guard(async () => {
  renderNav(); const v = $("#view");
  if (!["admin", "measures", "calc", "catalog", "structure"].includes(state.page) && !state.siteId) {
    v.innerHTML = html`<div class="card">ابتدا یک مجتمع صنعتی تعریف کنید (بخش «ساختار و جریان‌ها»).</div>`.__raw; return;
  }
  v.innerHTML = '<div class="card muted">در حال بارگذاری…</div>';
  await VIEWS[state.page](v);
});

// ---------- نمودارها (SVG) ----------
function monthlyChart(series) {
  if (!series.length) return html`<p class="muted">داده ماهانه‌ای ثبت نشده است.</p>`;
  const W = 720, H = 240, P = { l: 56, r: 12, t: 14, b: 34 };
  const max = Math.max(...series.flatMap(s => [s.input, s.output]), 1);
  const bw = (W - P.l - P.r) / series.length;
  const y = v => H - P.b - (v / max) * (H - P.t - P.b);
  let g = "";
  for (let i = 0; i <= 4; i++) {
    const v = max * i / 4; g += `<line x1="${P.l}" x2="${W - P.r}" y1="${y(v)}" y2="${y(v)}" stroke="var(--line)"/><text x="${P.l - 6}" y="${y(v) + 4}" font-size="10" text-anchor="end" fill="var(--muted)">${esc(fmt(v / 1000, 0))}k</text>`;
  }
  const bars = series.map((s, i) => {
    const x = W - P.r - (i + 1) * bw; // راست‌به‌چپ
    return `<rect x="${x + bw * .12}" y="${y(s.input)}" width="${bw * .36}" height="${H - P.b - y(s.input)}" fill="var(--pri)" rx="2"><title>${esc(s.period)} ورودی: ${esc(fmt(s.input, 0))}</title></rect>
<rect x="${x + bw * .52}" y="${y(s.output)}" width="${bw * .36}" height="${H - P.b - y(s.output)}" fill="#d9822b" rx="2"><title>${esc(s.period)} خروجی: ${esc(fmt(s.output, 0))}</title></rect>
<text x="${x + bw / 2}" y="${H - 14}" font-size="10" text-anchor="middle" fill="var(--muted)">${esc(s.period.slice(2))}</text>`;
  }).join("");
  return html`<svg viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="نمودار ماهانه ورودی و خروجی آب">${{ __raw: g + bars }}</svg>
  <div class="legend"><span><i style="background:var(--pri)"></i>ورودی (m³)</span><span><i style="background:#d9822b"></i>خروجی ثبت‌شده (m³)</span></div>`;
}
function flowBars(sk) {
  const all = [...sk.sources, ...sk.sinks]; const max = Math.max(...all.map(a => a.value), 1);
  const row = (a, cls) => html`<div class="bar-row"><span>${a.name}</span><div><div class="bar ${cls}" style="width:${(a.value / max * 100).toFixed(1)}%"></div></div><span class="num">${fmt(a.value, 0)}</span></div>`;
  return html`<h3>منابع</h3>${sk.sources.map(a => row(a, ""))}<h3>مصارف و تلفات</h3>${sk.sinks.map(a => row(a, "out"))}`;
}
const levelChip = l => ({ "عادی": "ok", "هشدار": "warn", "بحرانی": "bad" }[l] || "");

// ---------- صفحات ----------
const VIEWS = {};

VIEWS.dashboard = async v => {
  const id = state.siteId;
  const [bal, kp, series, sk, bs] = await Promise.all([api(`/api/sites/${id}/balance${q()}`), api(`/api/sites/${id}/kpis${q()}`),
    api(`/api/sites/${id}/series`), api(`/api/sites/${id}/sankey${q()}`), api(`/api/sites/${id}/by-system${q()}`)]);
  const ep = bal.error_percent == null ? "—" : bal.error_percent.toFixed(2) + "٪";
  v.innerHTML = html`
  <h2>داشبورد – ${site().name}</h2>
  <div class="grid g4">
    <div class="kpi"><div class="n">ورودی کل (m³)</div><div class="v">${fmt(bal.total_in, 0)}</div></div>
    <div class="kpi"><div class="n">خروجی ثبت‌شده (m³)</div><div class="v">${fmt(bal.total_out, 0)}</div></div>
    <div class="kpi ${bal.significant ? "warn" : ""}"><div class="n">باقیمانده موازنه</div><div class="v">${fmt(bal.residual, 0)} <span class="small">(${ep})</span></div></div>
    <div class="kpi"><div class="n">Z = R/u<sub>R</sub></div><div class="v">${bal.z_score == null ? "—" : bal.z_score.toFixed(2)}</div></div>
  </div>
  <p class="note">${bal.status}${bal.warnings.length ? " – " + bal.warnings.length + " هشدار کیفیت داده" : ""}</p>
  <div class="card"><h3>روند ماهانه</h3>${monthlyChart(series)}</div>
  <div class="card"><h3>شاخص‌های عملکرد</h3><div class="grid g3">${kp.map(k => html`
    <div class="kpi ${k.level === "هشدار" ? "warn" : k.level === "بحرانی" ? "crit" : ""}"><div class="n">${k.name}</div>
    <div class="v">${k.value == null ? "—" : fmt(k.value, 2)} <span class="small muted">${k.unit}</span></div>
    <span class="chip ${levelChip(k.level)}">${k.level}</span>${k.error ? html` <span class="small err">${k.error}</span>` : ""}</div>`)}</div></div>
  <div class="card"><h3>نمودار جریان آب (m³)</h3>${flowBars(sk)}</div>
  <div class="card"><h3>مصرف به تفکیک سیستم‌های مصرف‌کننده</h3>${systemBars(bs)}</div>`.__raw;
};

function systemBars(bs) {
  if (!bs.systems.length) return html`<p class="muted">جریانی ثبت نشده است.</p>`;
  const max = Math.max(...bs.systems.map(s => Math.max(s.in_total, s.out_total)), 1);
  const rows = bs.systems.map(s => html`<div class="bar-row wide"><span>${s.code === "UNASSIGNED" ? "بدون سیستم" : sysName(s.code)}</span>
    <div><div class="bar" style="width:${(s.in_total / max * 100).toFixed(1)}%" title="ورودی"></div><div class="bar out" style="width:${(s.out_total / max * 100).toFixed(1)}%;margin-top:3px" title="خروجی"></div></div>
    <span class="small" style="line-height:1.4">ورودی: <b dir="ltr">${fmt(s.in_total, 0)}</b><br>خروجی: <b dir="ltr">${fmt(s.out_total, 0)}</b>${s.share_of_site_input_pct != null && s.external_in ? html`<br><span class="muted">${fmt(s.share_of_site_input_pct, 1)}٪ از ورودی مجتمع</span>` : ""}</span></div>`);
  const miss = bs.missing_typical_systems;
  return html`<div class="legend"><span><i style="background:var(--pri)"></i>ورودی سیستم</span><span><i style="background:#d9822b"></i>خروجی سیستم</span></div>${rows}
   <p class="note small">ورودی/خروجی شامل جریان‌های داخلی هم می‌شود؛ مجموع سیستم‌ها قابل جمع نیست (خروجی یک سیستم ممکن است ورودی سیستم دیگر باشد).</p>
   ${miss.length ? html`<p class="note small"><b>شکاف داده:</b> سیستم‌های نوعی صنعت «${bs.industry ? bs.industry.name : ""}» که هنوز تعریف نشده‌اند: ${miss.map(m => m.name).join("، ")}. از «ساختار و جریان‌ها ← اعمال الگوی صنعت» استفاده کنید.</p>` : ""}`;
}

VIEWS.structure = async v => {
  const id = state.siteId;
  const s = id ? await api(`/api/sites/${id}`) : null;
  const streams = id ? await api(`/api/sites/${id}/streams`) : [];
  const C = cat();
  const curInd = C.industries.find(i => i.code === (s ? s.industry_code : "IND-15")) || C.industries[0] || { subgroups: [], typical_systems: [] };
  const ind = C.industries.map(i => html`<option value="${i.code}" ${i.code === curInd.code ? "selected" : ""}>${i.code} – ${i.name}</option>`);
  const subs = html`<option value="">— بدون زیرگروه —</option>${curInd.subgroups.map(([c, n]) => html`<option value="${c}" ${s && s.subgroup_code === c ? "selected" : ""}>${n}</option>`)}`;
  const sysOpts = (sel = "", empty = "— بدون سیستم —") => html`<option value="">${empty}</option>${C.systems.map(x => html`<option value="${x.code}" ${x.code === sel ? "selected" : ""}>${x.code} ${x.name_fa}</option>`)}`;
  const dis = canEdit() ? "" : "disabled";
  const typical = curInd.typical_systems.map(c => SYS_FA[c] || c).join("، ");
  v.innerHTML = html`
  <h2>ساختار مجتمع و جریان‌های آب</h2>
  <div class="card"><h3>${s ? "مشخصات مجتمع" : "تعریف مجتمع جدید"}</h3>
  <form data-form="site" class="form">
    <label>نام<input name="name" required value="${s?.name || ""}" ${dis}></label>
    <label>گروه صنعتی<select name="industry_code" ${dis}>${ind}</select></label>
    <label>زیرگروه<select name="subgroup_code" ${dis}>${subs}</select></label>
    <label>محل<input name="location" value="${s?.location || ""}" ${dis}></label>
    <label>واحد محصول<input name="product_unit" value="${s?.product_unit || "تن"}" ${dis}></label>
    <label>هزینه تأمین (واحد پول/m³)<input name="cost_supply" type="number" min="0" step="any" value="${s?.cost_supply ?? 0}" ${dis}></label>
    <label>هزینه پمپاژ<input name="cost_pumping" type="number" min="0" step="any" value="${s?.cost_pumping ?? 0}" ${dis}></label>
    <label>هزینه تصفیه<input name="cost_treatment" type="number" min="0" step="any" value="${s?.cost_treatment ?? 0}" ${dis}></label>
    <label>هزینه توزیع<input name="cost_distribution" type="number" min="0" step="any" value="${s?.cost_distribution ?? 0}" ${dis}></label>
    <label>هزینه دفع<input name="cost_disposal" type="number" min="0" step="any" value="${s?.cost_disposal ?? 0}" ${dis}></label>
    <label>آستانه غربالگری موازنه (٪)<input name="screening_pct" type="number" min="0.1" max="100" step="any" value="${s?.screening_pct ?? 10}" ${dis}></label>
    ${canEdit() ? html`<div><button class="btn primary" name="act" value="${s ? "update" : "create"}">${s ? "ذخیره تغییرات" : "ایجاد مجتمع"}</button>
      ${s ? html` <button type="button" class="btn" data-act="newsite">مجتمع جدید</button>` : ""}
      ${s && state.user.role === "admin" ? html` <button type="button" class="btn danger" data-act="delsite">حذف</button>` : ""}</div>` : ""}
  </form>${s ? html`<p class="muted small">هزینه نهایی هر m³ = <b>${fmt(s.marginal_cost, 0)}</b></p>` : ""}
  <p class="note small" id="indHint">سیستم‌های نوعی این صنعت: ${typical || "—"}</p></div>
  ${s ? html`
  <div class="grid g2">
   <div class="card"><h3>واحدها (سیستم‌های مصرف‌کننده)</h3>
    <table><tr><th>نام</th><th>سیستم</th></tr>${s.units.map(u => html`<tr><td>${u.name}</td><td>${sysName(u.system_code)}</td></tr>`)}</table>
    ${canEdit() ? html`<p><button class="btn small" data-act="template">اعمال الگوی صنعت (ساخت واحد برای سیستم‌های نوعی)</button></p>
     <form data-form="unit" class="form"><label>نام<input name="name" required></label>
     <label>سیستم<select name="system_code">${C.systems.map(x => html`<option value="${x.code}">${x.code} ${x.name_fa}</option>`)}</select></label>
     <button class="btn">افزودن واحد</button></form>` : ""}</div>
   <div class="card"><h3>تجهیزات</h3>
    <table><tr><th>نام</th><th>نوع</th><th>پارامترها</th></tr>${s.equipment.map(e => html`<tr><td>${e.name}</td><td>${eqName(e.kind)}</td><td class="num">${JSON.stringify(e.params)}</td></tr>`)}</table>
    ${canEdit() ? html`<form data-form="equipment" class="form"><label>نام<input name="name" required></label>
     <label>نوع<select name="kind">${[...cat().systems.map(sy => html`<optgroup label="${sy.code} ${sy.name_fa}">${cat().equipment_types.filter(e => e.system_code === sy.code).map(e => html`<option value="${e.code}">${e.name_fa}</option>`)}</optgroup>`), html`<option value="other">سایر</option>`]}</select></label>
     <label>پارامترها (JSON)<input name="params" value="{}" dir="ltr"></label><button class="btn">افزودن تجهیز</button></form>` : ""}</div>
  </div>
  <div class="card"><h3>جریان‌های آب</h3>
   <div class="tablewrap"><table><tr><th>نام</th><th>نوع جریان</th><th>سیستم</th><th>جهت</th><th>محدوده</th><th>عدم قطعیت٪</th><th>شماره کنتور</th><th>کاربرد مجاز</th><th></th></tr>
   ${streams.map(t => html`<tr><td>${t.name}</td><td>${t.stream_type ? stName(t.stream_type) : (KIND_FA[t.kind] || t.kind)}</td><td>${sysName(t.system_code || t.unit_system)}</td>
     <td>${t.direction === "in" ? "ورودی" : "خروجی"}</td>
     <td>${t.scope === "internal" ? "داخلی" : "مرز مجتمع"}</td><td class="num">${t.uncertainty_pct}</td><td>${t.meter_id}</td><td>${t.allowed_use || ""}</td>
     <td>${canEdit() ? html`<button class="btn small danger" data-act="delstream" data-id="${t.id}">حذف</button>` : ""}</td></tr>`)}</table></div>
   ${canEdit() ? html`<form data-form="stream" class="form">
     <label>نام<input name="name" required></label>
     <label>نوع جریان (کاتالوگ)<select name="stream_type"><option value="">— دستی —</option>${C.stream_types.map(t => html`<option value="${t.code}">${t.name_fa} (${t.name_en})</option>`)}</select></label>
     <label>سیستم مصرف‌کننده<select name="system_code">${sysOpts()}</select></label>
     <label>جهت<select name="direction"><option value="in">ورودی</option><option value="out">خروجی</option></select></label>
     <label>نوع محاسباتی<select name="kind">${Object.entries(KIND_FA).map(([k, t]) => html`<option value="${k}">${t}</option>`)}</select></label>
     <label>محدوده<select name="scope"><option value="external">مرز مجتمع</option><option value="internal">داخلی (بازچرخانی/سیستم)</option></select></label>
     <label>دسته منبع (ورودی)<select name="source_category"><option value="">—</option>${C.source_categories.map(x => html`<option value="${x.code}">${x.name}</option>`)}</select></label>
     <label>مبدأ<input name="origin"></label><label>مقصد<input name="destination"></label>
     <label>کلاس کیفی<input name="quality_class"></label><label>کاربرد مجاز<input name="allowed_use"></label>
     <label>وضعیت بازیافت<input name="recycle_status"></label>
     <label>عدم قطعیت (٪)<input name="uncertainty_pct" type="number" min="0" max="100" step="any" value="2"></label>
     <label>شماره کنتور<input name="meter_id"></label><button class="btn primary">افزودن جریان</button></form>
     <p class="note small" id="stHint">با انتخاب «نوع جریان»، جهت، محدوده، مبدأ/مقصد، کیفیت و کاربرد مجاز از کاتالوگ پر می‌شود و قابل ویرایش است.
     جریان «داخلی» در موازنه مرز مجتمع شمرده نمی‌شود؛ فقط «بازیافتی/بازچرخانی/کندانس» در نرخ بازچرخانی لحاظ می‌شوند.</p>` : ""}
  </div>` : ""}`.__raw;
};

// نمای کاتالوگ: صنایع، سیستم‌ها، انواع جریان، منابع
VIEWS.catalog = async v => {
  const C = cat(); const tab = VIEWS.catalog.tab || "industries";
  const measures = await api("/api/measures"); const mt = Object.fromEntries(measures.map(m => [m.code, m.title]));
  const tabs = [["industries", `صنایع (${C.industries.length})`], ["systems", `سیستم‌های مصرف‌کننده (${C.systems.length})`],
    ["streams", `انواع جریان (${C.stream_types.length})`], ["sources", `منابع تأمین (${C.source_categories.length})`],
    ["equipment", `بانک تجهیزات (${C.equipment_types.length})`], ["parameters", `پارامترها و ابزار (${C.parameters.length + C.instruments.length})`]];
  const chips = a => a.map(x => html`<span class="chip">${x}</span> `);
  let body = "";
  if (tab === "industries") body = html`<p class="note small">کد، نام، نمونه صنایع و «راهکارهای شاخص» از سند Methodology آمده است. زیرگروه‌ها و فرآیندهای نوعی راهنمای مهندسی‌اند و باید توسط متخصص هر صنعت بازبینی شوند.</p>
    ${C.industries.map(i => html`<details class="card"><summary><b>${i.code}</b> – ${i.name} <span class="muted small">(${i.examples})</span></summary>
     <div class="kv" style="margin-top:.6rem"><b>زیرگروه‌ها</b><span>${chips(i.subgroups.map(x => x[1]))}</span>
      <b>سیستم‌های نوعی</b><span>${chips(i.typical_systems.map(c => sysName(c)))}</span>
      <b>فرآیندهای مصرف‌کننده آب</b><span>${i.processes.join("، ")}</span>
      <b>راهکارهای شاخص (سند)</b><span>${i.highlights}</span>
      <b>راهکارهای مرتبط</b><span>${i.measure_codes.map(c => html`<span class="chip" title="${mt[c] || ""}">${c}</span> `)}</span></div></details>`)}`;
  else if (tab === "systems") body = html`<p class="note small">طبقه‌بندی SYS-01 تا SYS-12 و تجهیزات از سند آمده است. هر سیستم پارامترها، جریان‌های نوعی، ماژول محاسباتی و راهکارهای مخصوص خود را دارد.</p>
    ${C.systems.map(x => html`<details class="card"><summary><b>${x.code}</b> – ${x.name_fa} <span class="muted small">(${x.name_en}) · ${C.system_categories[x.category] || x.category}</span></summary>
     <div class="kv" style="margin-top:.6rem"><b>تجهیزات</b><span>${chips(x.equipment)}</span>
      <b>جریان‌های ورودی نوعی</b><span>${chips(x.typical_in.map(stName))}</span><b>جریان‌های خروجی نوعی</b><span>${chips(x.typical_out.map(stName))}</span>
      <b>اندازه‌گیری‌ها</b><span>${x.measurements.join("، ")}</span><b>شاخص‌های مرتبط</b><span>${chips(x.kpis)}</span>
      <b>نکته مهندسی</b><span>${x.notes}</span>
      <b>فرمول‌ها</b><span>${(x.formulas || []).map(f => html`<div>${f.name}: <code dir="ltr">${f.formula}</code></div>`)}</span>
      <b>راهکارها</b><span>${x.measure_count} راهکار <button class="btn small" data-act="sysmeasures" data-id="${x.code}">نمایش</button>
       ${(x.calc_module || "").split(",").filter(Boolean).map(m => html` <button class="btn small" data-act="syscalc" data-id="${m}">${CALC[m] ? CALC[m][0] : m}</button>`)}</span></div></details>`)}`;
  else if (tab === "streams") body = html`<div class="tablewrap"><table><tr><th>کد</th><th>نام</th><th>جهت</th><th>محدوده</th><th>تعریف</th><th>کیفیت</th><th>کاربرد مجاز</th><th>مبدأ</th><th>مقصد</th><th>وضعیت بازیافت</th></tr>
    ${C.stream_types.map(t => html`<tr><td>${t.code}</td><td>${t.name_fa}<br><span class="muted small">${t.name_en}</span></td><td>${t.direction === "in" ? "ورودی" : "خروجی"}</td>
     <td>${t.scope === "internal" ? "داخلی" : "مرز"}</td><td style="white-space:normal;min-width:220px">${t.definition}</td><td>${t.quality_class}</td><td style="white-space:normal">${t.allowed_use}</td><td>${t.origin}</td><td>${t.destination}</td><td>${t.recycle_status}</td></tr>`)}</table></div>
    <p class="note small">هر جریان باید علاوه بر نام و نوع، مشخصات کیفی، کاربرد مجاز، منشأ، مقصد و وضعیت بازیافت داشته باشد (سند، ۲.۳). دو نوع PRODUCT و LEAKAGE را نرم‌افزار برای کامل‌شدن موازنه افزوده است.</p>`;
  else if (tab === "equipment") {
    const g = VIEWS.catalog.sys || "";
    body = html`<p class="note small">ردیف‌های «سند» عیناً از جدول سند آمده‌اند؛ «راهنما» فهرست تکمیلی مهندسی است و باید بازبینی شود. مهندس می‌تواند نوع جدید بیفزاید.</p>
     <div style="margin-bottom:.6rem"><select data-act="eqsys"><option value="">همه سیستم‌ها</option>${C.systems.map(x => html`<option value="${x.code}" ${x.code === g ? "selected" : ""}>${x.code} ${x.name_fa}</option>`)}</select></div>
     <div class="tablewrap"><table><tr><th>کد</th><th>نام</th><th>سیستم</th><th>منبع</th></tr>
     ${C.equipment_types.filter(e => !g || e.system_code === g).map(e => html`<tr><td dir="ltr">${e.code}</td><td>${e.name_fa}<br><span class="muted small">${e.name_en}</span></td><td>${sysName(e.system_code)}</td><td>${e.source === "doc" ? "سند" : e.source === "user" ? "کاربر" : "راهنما"}</td></tr>`)}</table></div>
     ${canEdit() ? html`<form data-form="eqtype" class="form"><label>کد (لاتین)<input name="code" required dir="ltr" pattern="[A-Za-z0-9_]{2,40}"></label><label>نام فارسی<input name="name_fa" required></label>
      <label>نام انگلیسی<input name="name_en" dir="ltr"></label><label>سیستم<select name="system_code"><option value="">—</option>${C.systems.map(x => html`<option value="${x.code}">${x.code} ${x.name_fa}</option>`)}</select></label>
      <button class="btn">افزودن نوع تجهیز</button></form>` : ""}`;
  } else if (tab === "parameters") body = html`<h3>پارامترهای اندازه‌گیری</h3>
     <div class="tablewrap"><table><tr><th>پارامتر</th><th>نماد</th><th>واحد</th><th>گروه</th><th>محل/اهمیت</th></tr>
     ${C.parameters.map(p => html`<tr><td>${p.name}</td><td dir="ltr">${p.symbol}</td><td dir="ltr">${p.unit}</td><td>${p.grp === "hydraulic" ? "هیدرولیکی" : "کیفی"}</td><td>${p.note}</td></tr>`)}</table></div>
     <p class="note small">${C.parameters_note}</p><h3>تجهیزات اندازه‌گیری</h3>
     <table><tr><th>تجهیز</th><th>کاربرد</th></tr>${C.instruments.map(i => html`<tr><td>${i.name_fa}<br><span class="muted small">${i.name_en}</span></td><td>${i.usage}</td></tr>`)}</table>
     <p class="note small">${C.instrument_note}</p>`;
  else body = html`<table><tr><th>کد</th><th>دسته منبع</th><th>نمونه‌ها</th></tr>${C.source_categories.map(x => html`<tr><td>${x.code}</td><td>${x.name}</td><td>${x.examples}</td></tr>`)}</table>
    <p class="note small">برای هر منبع باید ثبت شود: ${C.source_attributes.join("، ")}. (در جریان ورودی: دسته منبع، شماره کنتور، عدم قطعیت و کیفیت.)</p>`;
  v.innerHTML = html`<h2>طبقه‌بندی صنایع و سیستم‌های مصرف‌کننده آب</h2>
   <div style="display:flex;gap:.4rem;flex-wrap:wrap;margin-bottom:.8rem">${tabs.map(([k, t]) => html`<button class="btn small ${k === tab ? "primary" : ""}" data-act="cattab" data-id="${k}">${t}</button>`)}</div>${body}`.__raw;
};

VIEWS.data = async v => {
  const id = state.siteId;
  const [streams, readings, prod] = await Promise.all([api(`/api/sites/${id}/streams`), api(`/api/sites/${id}/readings`), api(`/api/sites/${id}/production`)]);
  const ro = !canEdit();
  v.innerHTML = html`
  <h2>ورود داده</h2>
  ${ro ? html`<p class="note">نقش شما فقط مشاهده است.</p>` : html`
  <div class="grid g2">
   <div class="card"><h3>ثبت قرائت ماهانه</h3>
    <form data-form="reading" class="form">
     <label>جریان<select name="stream_id">${streams.map(s => html`<option value="${s.id}">${s.name} (${s.direction === "in" ? "ورودی" : "خروجی"})</option>`)}</select></label>
     <label>دوره (YYYY-MM)<input name="period" required pattern="\\d{4}-(0[1-9]|1[0-2])" placeholder="2025-03" dir="ltr"></label>
     <label>حجم (m³)<input name="volume" type="number" step="any" min="0" required></label>
     <label>وضعیت داده<select name="quality">${Object.entries(QUAL_FA).map(([k, t]) => html`<option value="${k}">${t}</option>`)}</select></label>
     <label>حجم اصلاح‌شده (اختیاری)<input name="corrected_volume" type="number" step="any" min="0"></label>
     <button class="btn primary">ثبت</button></form></div>
   <div class="card"><h3>داده تولید ماهانه</h3>
    <form data-form="production" class="form">
     <label>دوره<input name="period" required pattern="\\d{4}-(0[1-9]|1[0-2])" placeholder="2025-03" dir="ltr"></label>
     <label>مقدار تولید<input name="product_qty" type="number" step="any" min="0" required></label>
     <label>دمای محیط (°C)<input name="ambient_temp" type="number" step="any"></label>
     <label>ساعات کارکرد<input name="operating_hours" type="number" step="any" min="0"></label>
     <button class="btn primary">ثبت</button></form>
    <h3 style="margin-top:1rem">تغییر موجودی مخازن</h3>
    <form data-form="storage" class="form">
     <label>دوره<input name="period" required placeholder="2025-03" dir="ltr"></label>
     <label>ΔV (m³، مثبت = افزایش)<input name="delta_volume" type="number" step="any" required></label>
     <label>عدم قطعیت (m³)<input name="uncertainty" type="number" step="any" min="0" value="0"></label>
     <button class="btn">ثبت</button></form></div>
  </div>
  <div class="card"><h3>ورود گروهی از فایل CSV</h3>
   <form data-form="csv" class="form">
    <label>نوع<select name="type"><option value="readings">قرائت‌ها: stream,period,volume,quality</option><option value="production">تولید: period,product_qty,ambient_temp,operating_hours</option></select></label>
    <label>فایل CSV (حداکثر ۵MB)<input type="file" name="file" accept=".csv,text/csv" required></label>
    <button class="btn primary">بارگذاری</button></form>
   <div id="csvResult"></div></div>`}
  <div class="grid g2">
  <div class="card"><h3>آخرین قرائت‌ها</h3><div class="tablewrap" style="max-height:340px"><table><tr><th>دوره</th><th>جریان</th><th>حجم</th><th>وضعیت</th><th>منبع</th></tr>
   ${readings.slice(0, 300).map(r => html`<tr><td>${r.period}</td><td>${r.stream_name}</td><td class="num">${fmt(r.corrected_volume ?? r.volume, 1)}</td><td>${QUAL_FA[r.quality]}</td><td>${r.source}</td></tr>`)}</table></div></div>
  <div class="card"><h3>تولید</h3><div class="tablewrap" style="max-height:340px"><table><tr><th>دوره</th><th>تولید</th><th>دما</th><th>ساعات</th></tr>
   ${prod.map(p => html`<tr><td>${p.period}</td><td class="num">${fmt(p.product_qty, 1)}</td><td class="num">${fmt(p.ambient_temp)}</td><td class="num">${fmt(p.operating_hours)}</td></tr>`)}</table></div></div></div>`.__raw;
};

VIEWS.balance = async v => {
  const id = state.siteId;
  const [bal, an] = await Promise.all([api(`/api/sites/${id}/balance${q()}`), api(`/api/sites/${id}/anomalies`)]);
  v.innerHTML = html`
  <h2>موازنه آب و تحلیل</h2>
  <div class="card"><h3>موازنه سایت (${bal.period})</h3>
   <div class="kv"><b>مجموع ورودی</b><span>${fmt(bal.total_in, 0)} m³</span><b>مجموع خروجی</b><span>${fmt(bal.total_out, 0)} m³</span>
   <b>تغییر موجودی</b><span>${fmt(bal.storage_change, 0)} m³</span><b>باقیمانده R</b><span>${fmt(bal.residual, 0)} m³</span>
   <b>درصد اختلاف</b><span>${bal.error_percent == null ? "—" : bal.error_percent.toFixed(2) + "٪"}</span>
   <b>عدم قطعیت u<sub>R</sub> (۹۵٪)</b><span>±${fmt(bal.u_residual, 0)} m³</span><b>Z</b><span>${bal.z_score == null ? "—" : bal.z_score.toFixed(2)}</span>
   <b>وضعیت</b><span><b style="color:var(--ink)">${bal.status}</b></span></div>
   ${bal.warnings.length ? html`<ul>${bal.warnings.map(w => html`<li>${w}</li>`)}</ul>` : ""}
   <p class="note small">باقیمانده به‌طور خودکار «نشتی» فرض نمی‌شود؛ می‌تواند از خطای اندازه‌گیری، تخمین، اختلاف زمانی یا تغییر موجودی باشد.</p>
   <table><tr><th>جریان</th><th>جهت</th><th>نوع</th><th>حجم (m³)</th><th>قرائت</th><th>مشکوک/نامعتبر</th></tr>
   ${bal.streams.map(s => html`<tr><td>${s.name}</td><td>${s.direction === "in" ? "ورودی" : "خروجی"}</td><td>${KIND_FA[s.kind] || s.kind}</td><td class="num">${fmt(s.volume, 0)}</td><td>${s.readings}</td><td>${s.suspect + s.bad}</td></tr>`)}</table></div>
  <div class="card"><h3>تشخیص ناهنجاری آماری (مصرف ماهانه)</h3><p>${an.note}</p>
   ${an.anomalies.length ? html`<table><tr><th>دوره</th><th>مقدار</th><th>Z</th></tr>${an.anomalies.map(a => html`<tr><td>${a.period}</td><td class="num">${fmt(a.value, 0)}</td><td class="num">${a.z.toFixed(2)}</td></tr>`)}</table>` : ""}</div>
  <div class="card"><h3>خط مبنای مصرف و صحه‌گذاری صرفه‌جویی (رگرسیون)</h3>
   <form data-form="baseline" class="form">
    <label>آغاز دوره پس از اجرای اصلاحات (YYYY-MM)<input name="split_period" required placeholder="2025-10" dir="ltr"></label>
    <label>متغیرهای مدل (ctrl برای چندتایی)<select name="predictors" multiple size="3"><option value="product_qty" selected>تولید</option><option value="ambient_temp">دمای محیط</option><option value="operating_hours">ساعات کارکرد</option></select></label>
    <button class="btn primary">محاسبه</button></form><div id="baselineOut"></div></div>`.__raw;
};

const CALC = {
  "cooling-tower": ["برج خنک‌کن", [["circulation_m3h", "دبی گردشی (m³/h)", 1500], ["delta_t_c", "ΔT آب (°C)", 6], ["coc", "سیکل تغلیظ (COC)", 3], ["makeup_m3h", "آب جبرانی واقعی (m³/h، اختیاری)", ""], ["drift_pct", "دریفت (٪)", 0.005], ["ec_makeup", "EC آب جبرانی (اختیاری)", ""], ["ec_blowdown", "EC بلودان (اختیاری؛ COC را بازنویسی می‌کند)", ""]]],
  "coc-saving": ["صرفه‌جویی افزایش COC", [["circulation_m3h", "دبی گردشی (m³/h)", 1500], ["delta_t_c", "ΔT (°C)", 6], ["coc_from", "COC فعلی", 3], ["coc_to", "COC هدف", 6], ["hours", "ساعات کارکرد سالانه", 8000]]],
  "boiler": ["بویلر و بخار", [["steam_t_h", "بخار تولیدی (t/h)", 80], ["condensate_return_t_h", "کندانس برگشتی (t/h)", 50], ["makeup_m3_h", "آب جبرانی (m³/h)", 35], ["feed_tds", "TDS آب تغذیه (اختیاری)", ""], ["boiler_tds_max", "حداکثر TDS بویلر (اختیاری)", ""]]],
  "ro": ["اسمز معکوس (RO)", [["feed_m3h", "دبی خوراک (m³/h)", 100], ["permeate_m3h", "دبی پرمیت (m³/h)", 75], ["ec_feed", "EC خوراک", 1000], ["ec_perm", "EC پرمیت", 20], ["p_feed_bar", "فشار ورودی (bar)", ""], ["p_conc_bar", "فشار کنسانتره (bar)", ""], ["power_kw", "توان (kW)", ""]]],
  "pump": ["پمپ", [["q_m3h", "دبی (m³/h)", 100], ["head_m", "هد (m)", 30], ["p_electric_kw", "توان الکتریکی (kW)", 12]]],
  "dm": ["واحد DM / تبادل یونی", [["feed_m3", "حجم خوراک (m³)", 1000], ["product_m3", "حجم آب تولیدی (m³)", 900], ["regen_waste_m3", "پساب احیا و آبکشی (m³، اختیاری)", ""], ["ec_feed", "EC خوراک (اختیاری)", ""], ["ec_product", "EC محصول (اختیاری)", ""]]],
  "distribution": ["شبکه توزیع / آتش‌نشانی", [["v_input_m3", "حجم ورودی شبکه (m³)", 1000], ["v_delivered_m3", "حجم تحویلی/اندازه‌گیری‌شده (m³)", 850], ["test_volume_m3", "حجم آزمون‌های آتش‌نشانی (m³)", 0], ["q_night_m3h", "دبی شبانه (m³/h، اختیاری)", ""], ["q_avg_m3h", "دبی میانگین (m³/h، اختیاری)", ""]]],
  "sanitary": ["مصارف بهداشتی", [["volume_m3", "حجم مصرف (m³)", 600], ["persons", "تعداد افراد", 100], ["days", "تعداد روز", 30], ["benchmark_l_person_day", "مبنا (L/نفر/روز)", 60]]],
  "irrigation": ["آبیاری فضای سبز", [["area_m2", "مساحت (m²)", 10000], ["et0_mm_day", "ET₀ (mm/day)", 5], ["kc", "ضریب گیاهی Kc", 0.8], ["days", "تعداد روز", 30], ["actual_m3", "مصرف واقعی (m³)", 1500], ["effective_rain_mm", "باران مؤثر (mm)", 0], ["efficiency", "راندمان آبیاری (0–1)", 0.75]]],
  "wwtp": ["تصفیه‌خانه فاضلاب", [["q_in_m3d", "دبی ورودی (m³/d)", 1000], ["q_out_m3d", "دبی خروجی (m³/d)", 900], ["cod_in", "COD ورودی (mg/L)", 800], ["cod_out", "COD خروجی", 80], ["reuse_m3d", "آب مصرف مجدد (m³/d)", 0], ["energy_kwh_d", "انرژی (kWh/d، اختیاری)", ""]]],
};
VIEWS.calc = async v => {
  const k = VIEWS.calc.kind || "cooling-tower"; const [title, fields] = CALC[k];
  v.innerHTML = html`<h2>محاسبه تجهیزات</h2>
  <div class="card"><div style="display:flex;gap:.4rem;flex-wrap:wrap;margin-bottom:.8rem">${Object.entries(CALC).map(([key, c]) => html`<button class="btn small ${key === k ? "primary" : ""}" data-act="calckind" data-id="${key}">${c[0]}</button>`)}</div>
  <h3>${title}</h3>
  <form data-form="calc" data-kind="${k}" class="form">${fields.map(([n, l, d]) => html`<label>${l}<input name="${n}" type="number" step="any" value="${d}" dir="ltr"></label>`)}
  <button class="btn primary">محاسبه</button></form><div id="calcOut"></div></div>`.__raw;
};

VIEWS.measures = async v => {
  const fs = VIEWS.measures.system || "";
  const [ms, rec] = await Promise.all([api("/api/measures" + (fs ? "?system=" + encodeURIComponent(fs) : "")),
    state.siteId ? api(`/api/sites/${state.siteId}/recommendations`) : []]);
  VIEWS.measures.data = ms;
  const row = m => html`<tr><td>${m.code}</td><td>${m.title}${m.flagship ? html` <span class="chip ok" title="راهکار شاخص این صنعت در سند">شاخص صنعت</span>` : ""}</td><td>${HIER_FA[m.hierarchy]}</td><td>${SYS_FA[m.system_code] || "عمومی"}</td>
   <td class="num">${m.saving_min_pct == null ? "—" : m.saving_min_pct + "–" + m.saving_max_pct + "٪"}</td><td>${m.saving_basis}</td><td>${m.payback_note}</td>
   <td>${canEdit() && state.siteId ? html`<button class="btn small" data-act="mkproject" data-id="${m.id}">افزودن به پروژه‌ها</button>` : ""}</td></tr>`;
  v.innerHTML = html`<h2>بانک راهکارهای صرفه‌جویی</h2>
  <p class="note">درصدهای صرفه‌جویی «بازه‌های راهنمای مهندسی» هستند و مبنای هر درصد در ستون «مبنا» آمده است. هیچ‌کدام تضمین نیستند و باید در سایت واقعی با اندازه‌گیری راستی‌آزمایی شوند.</p>
  ${state.siteId ? html`<div class="card"><h3>پیشنهاد برای «${site().name}» (${rec.length} راهکار مرتبط)</h3>
   <div class="tablewrap"><table><tr><th>کد</th><th>راهکار</th><th>سلسله‌مراتب</th><th>سیستم</th><th>صرفه‌جویی</th><th>مبنا</th><th>بازگشت</th><th></th><th>دلیل پیشنهاد</th></tr>
   ${rec.map(m => html`${row(m)}`).map((r, i) => html`${{ __raw: r.__raw.replace(/<\/tr>$/, `<td>${esc((rec[i].reasons || []).join("؛ "))}</td></tr>`) }}`)}</table></div></div>` : ""}
  <div class="card"><h3>همه راهکارها (${ms.length})${fs ? html` – سیستم ${sysName(fs)} <button class="btn small" data-act="clearsys">نمایش همه</button>` : ""}</h3>
   <div class="tablewrap" style="max-height:520px"><table><tr><th>کد</th><th>راهکار</th><th>سلسله‌مراتب</th><th>سیستم</th><th>صرفه‌جویی</th><th>مبنا</th><th>بازگشت</th><th></th></tr>${ms.map(row)}</table></div></div>`.__raw;
};

VIEWS.projects = async v => {
  const id = state.siteId;
  const [ps, rk] = await Promise.all([api(`/api/sites/${id}/projects`), api(`/api/sites/${id}/ranking`)]);
  const pre = VIEWS.projects.prefill || {}; VIEWS.projects.prefill = null;
  v.innerHTML = html`<h2>پروژه‌ها و اولویت‌بندی</h2>
  ${canEdit() ? html`<div class="card"><h3>پروژه جدید</h3><form data-form="project" class="form">
   <input type="hidden" name="measure_id" value="${pre.measure_id || ""}">
   <label>عنوان<input name="title" required value="${pre.title || ""}"></label>
   <label>صرفه‌جویی (m³/سال)<input name="saving_m3_year" type="number" min="0" step="any" required></label>
   <label>سرمایه‌گذاری CAPEX<input name="capex" type="number" min="0" step="any" value="0"></label>
   <label>هزینه بهره‌برداری سالانه<input name="opex_year" type="number" min="0" step="any" value="0"></label>
   <label>عمر (سال)<input name="life_years" type="number" min="1" max="50" value="10"></label>
   <label>نرخ تنزیل<input name="discount_rate" type="number" min="0" max="1" step="any" value="0.15"></label>
   <label>سهولت اجرا (۱-۵)<input name="ease" type="number" min="1" max="5" value="${pre.ease || 3}"></label>
   <label>کاهش ریسک تأمین (۱-۵)<input name="supply_risk" type="number" min="1" max="5" value="3"></label>
   <label>اثر زیست‌محیطی مثبت (۱-۵)<input name="environment" type="number" min="1" max="5" value="3"></label>
   <label>ریسک فنی (۱-۵؛ بالاتر = بدتر)<input name="tech_risk" type="number" min="1" max="5" value="${pre.tech_risk || 3}"></label>
   <button class="btn primary">ثبت پروژه</button></form></div>` : ""}
  <div class="card"><h3>پروژه‌ها (هزینه هر m³ آب: ${fmt(site().cost_supply + site().cost_pumping + site().cost_treatment + site().cost_distribution + site().cost_disposal, 0)})</h3>
   <div class="tablewrap"><table><tr><th>پروژه</th><th>صرفه‌جویی m³/سال</th><th>CAPEX</th><th>OPEX</th><th>صرفه‌جویی خالص/سال</th><th>بازگشت (سال)</th><th>NPV</th><th>IRR</th><th>وضعیت</th><th></th></tr>
   ${ps.map(p => html`<tr><td>${p.title}</td><td class="num">${fmt(p.saving_m3_year, 0)}</td><td class="num">${fmt(p.capex, 0)}</td><td class="num">${fmt(p.opex_year, 0)}</td>
    <td class="num">${fmt(p.economics.annual_net_saving, 0)}</td><td class="num">${fmt(p.economics.payback_years, 2)}</td><td class="num">${fmt(p.economics.npv, 0)}</td>
    <td class="num">${p.economics.irr == null ? "—" : fmt(p.economics.irr * 100, 1) + "٪"}</td>
    <td>${canEdit() ? html`<select data-act="pstatus" data-id="${p.id}">${Object.entries(PSTATUS).map(([k, t]) => html`<option value="${k}" ${k === p.status ? "selected" : ""}>${t}</option>`)}</select>` : PSTATUS[p.status]}</td>
    <td>${canEdit() ? html`<button class="btn small danger" data-act="delproject" data-id="${p.id}">حذف</button>` : ""}</td></tr>`)}</table></div>
   <p class="note small">جمع صرفه‌جویی پروژه‌ها فقط در صورت استقلال فنی آن‌ها معتبر است (هم‌پوشانی ممکن است).</p></div>
  <div class="card"><h3>رتبه‌بندی (امتیازدهی وزنی چندمعیاره)</h3><table><tr><th>رتبه</th><th>پروژه</th><th>امتیاز (۰-۱۰۰)</th><th>صرفه‌جویی m³/سال</th></tr>
   ${rk.map(r => html`<tr><td>${r.rank}</td><td>${r.name}</td><td class="num">${r.score}</td><td class="num">${fmt(r.saving_m3_year, 0)}</td></tr>`)}</table>
   <p class="muted small">وزن‌ها: صرفه‌جویی ۲۵٪، اقتصاد ۲۰٪، سهولت ۱۵٪، ریسک تأمین ۱۵٪، محیط‌زیست ۱۵٪، ریسک فنی ۱۰٪.</p></div>`.__raw;
};

VIEWS.kpis = async v => {
  const defs = await api("/api/kpi-defs");
  v.innerHTML = html`<h2>تعریف شاخص‌ها (KPI)</h2>
  <div class="card"><table><tr><th>کد</th><th>نام</th><th>فرمول</th><th>واحد</th><th>هشدار</th><th>بحرانی</th></tr>
   ${defs.map(d => html`<tr><td>${d.code}</td><td>${d.name}</td><td dir="ltr" class="num">${d.formula}</td><td>${d.unit}</td><td>${d.warn ?? "—"}</td><td>${d.critical ?? "—"}</td></tr>`)}</table>
   <p class="note small">متغیرهای مجاز: input, freshwater, accounted, wastewater, evaporation, product_water, leakage, reused, total_use, consumed, beneficial, production, supply, pumping, treatment, distribution, disposal. عملگرهای + − * / ** و توابع max, min, abs.</p></div>
  ${canEdit() ? html`<div class="card"><h3>افزودن/ویرایش شاخص</h3><form data-form="kpidef" class="form">
   <label>کد (انگلیسی)<input name="code" required pattern="[A-Za-z0-9_]{2,30}" dir="ltr"></label><label>نام<input name="name" required></label>
   <label>فرمول<input name="formula" required dir="ltr" placeholder="freshwater / production"></label><label>واحد<input name="unit"></label>
   <label>حد هشدار<input name="warn" type="number" step="any"></label><label>حد بحرانی<input name="critical" type="number" step="any"></label>
   <button class="btn primary">ذخیره</button></form></div>` : ""}`.__raw;
};

VIEWS.report = async v => {
  v.innerHTML = html`<h2>گزارش ممیزی</h2><div class="card"><p>گزارش کامل موازنه، شاخص‌ها و پروژه‌ها برای «${site().name}» ${period() ? "(دوره " + period() + ")" : "(همه دوره‌ها)"}.</p>
  <button class="btn primary" data-act="report">باز کردن گزارش (قابل چاپ / ذخیره PDF)</button></div>`.__raw;
};

VIEWS.admin = async v => {
  const [users, log] = await Promise.all([api("/api/users"), api("/api/audit-log")]);
  v.innerHTML = html`<h2>مدیریت</h2>
  <div class="grid g2"><div class="card"><h3>کاربران</h3><table><tr><th>نام کاربری</th><th>نام</th><th>نقش</th></tr>
   ${users.map(u => html`<tr><td>${u.username}</td><td>${u.full_name}</td><td>${ROLE_FA[u.role]}</td></tr>`)}</table>
   <form data-form="user" class="form"><label>نام کاربری<input name="username" required minlength="3"></label><label>نام کامل<input name="full_name"></label>
   <label>گذرواژه (حداقل ۸)<input name="password" type="password" required minlength="8"></label>
   <label>نقش<select name="role"><option value="viewer">مشاهده‌گر</option><option value="engineer">مهندس</option><option value="admin">مدیر</option></select></label>
   <button class="btn primary">افزودن کاربر</button></form></div>
  <div class="card"><h3>تغییر گذرواژه من</h3><form data-form="password" class="form">
   <label>گذرواژه فعلی<input name="old_password" type="password" required></label><label>گذرواژه جدید (حداقل ۸)<input name="new_password" type="password" required minlength="8"></label>
   <button class="btn primary">تغییر</button></form></div></div>
  <div class="card"><h3>گزارش فعالیت (۵۰۰ مورد آخر)</h3><div class="tablewrap" style="max-height:360px"><table><tr><th>زمان (UTC)</th><th>کاربر</th><th>عملیات</th><th>موجودیت</th><th>شناسه</th><th>جزئیات</th></tr>
   ${log.map(l => html`<tr><td class="num">${l.at}</td><td>${l.username || "—"}</td><td>${l.action}</td><td>${l.entity}</td><td>${l.entity_id}</td><td>${l.detail}</td></tr>`)}</table></div></div>`.__raw;
};

// ---------- فرم‌ها ----------
const FORMS = {
  async site(f, e) {
    const d = formData(f); const act = e.submitter?.value;
    ["cost_supply", "cost_pumping", "cost_treatment", "cost_distribution", "cost_disposal", "screening_pct"].forEach(k => d[k] = Number(d[k] || 0));
    if (act === "create") { const r = await api("/api/sites", { method: "POST", body: d }); await loadSites(r.id); }
    else { await api(`/api/sites/${state.siteId}`, { method: "PUT", body: d }); await loadSites(); }
    toast("ذخیره شد"); render();
  },
  async unit(f) { await api(`/api/sites/${state.siteId}/units`, { method: "POST", body: formData(f) }); toast("واحد افزوده شد"); render(); },
  async eqtype(f) { await api("/api/catalog/equipment-types", { method: "POST", body: formData(f) }); await loadCatalog(); toast("نوع تجهیز افزوده شد"); render(); },
  async equipment(f) {
    const d = formData(f); try { d.params = JSON.parse(d.params || "{}"); } catch { throw new Error("پارامترها باید JSON معتبر باشند"); }
    await api(`/api/sites/${state.siteId}/equipment`, { method: "POST", body: d }); toast("تجهیز افزوده شد"); render();
  },
  async stream(f) { const d = formData(f); d.uncertainty_pct = Number(d.uncertainty_pct); await api(`/api/sites/${state.siteId}/streams`, { method: "POST", body: d }); toast("جریان افزوده شد"); render(); },
  async reading(f) {
    const d = formData(f); d.stream_id = Number(d.stream_id); d.volume = Number(d.volume); d.corrected_volume = num(d.corrected_volume);
    await api(`/api/sites/${state.siteId}/readings`, { method: "POST", body: d }); toast("قرائت ثبت شد"); render();
  },
  async production(f) {
    const d = formData(f); d.product_qty = Number(d.product_qty); d.ambient_temp = num(d.ambient_temp); d.operating_hours = num(d.operating_hours);
    await api(`/api/sites/${state.siteId}/production`, { method: "POST", body: d }); toast("ثبت شد"); render();
  },
  async storage(f) { const d = formData(f); d.delta_volume = Number(d.delta_volume); d.uncertainty = Number(d.uncertainty || 0); await api(`/api/sites/${state.siteId}/storage`, { method: "POST", body: d }); toast("ثبت شد"); },
  async csv(f) {
    const fd = new FormData(f); const type = fd.get("type"); fd.delete("type");
    const r = await api(`/api/sites/${state.siteId}/import?type=${type}`, { method: "POST", body: fd });
    $("#csvResult").innerHTML = html`<p class="note">${r.saved} ردیف ذخیره شد. ${r.errors.length} خطا.</p>${r.errors.length ? html`<table><tr><th>خط</th><th>خطا</th></tr>${r.errors.map(x => html`<tr><td>${x.line}</td><td>${x.error}</td></tr>`)}</table>` : ""}`.__raw;
    if (r.saved) toast("ورود داده انجام شد");
  },
  async baseline(f) {
    const preds = [...f.querySelector("[name=predictors]").selectedOptions].map(o => o.value);
    const r = await api(`/api/sites/${state.siteId}/baseline`, { method: "POST", body: { split_period: f.split_period.value, predictors: preds } });
    const s = r.result;
    $("#baselineOut").innerHTML = html`<div class="kv" style="margin-top:.8rem"><b>تعداد دوره مبنا / پس از اجرا</b><span>${r.n_pre} / ${r.n_post}</span>
      <b>R²</b><span>${r.model.r2.toFixed(3)}</span><b>CV(RMSE)</b><span>${fmt(r.model.cv_rmse_pct, 2)}٪</span>
      <b>اعتبار مدل</b><span>${r.reliability}</span><b>مصرف مبنای تعدیل‌شده</b><span>${fmt(s.baseline_total, 0)} m³</span>
      <b>مصرف واقعی</b><span>${fmt(s.actual_total, 0)} m³</span><b>صرفه‌جویی</b><span><b style="color:var(--ink)">${fmt(s.saving, 0)} m³ (${fmt(s.saving_pct, 2)}٪)</b></span>
      <b>عدم قطعیت ۹۵٪</b><span>±${fmt(s.uncertainty_95, 0)} m³</span><b>معنادار؟</b><span>${s.significant ? "بله" : "خیر؛ در محدوده عدم قطعیت"}</span></div>`.__raw;
  },
  async calc(f) {
    const d = {}; new FormData(f).forEach((v, k) => { if (v !== "") d[k] = Number(v); });
    const r = await api(`/api/calc/${f.dataset.kind}`, { method: "POST", body: d });
    const w = r.warnings || []; delete r.warnings;
    $("#calcOut").innerHTML = html`<div class="kv" style="margin-top:.8rem">${Object.entries(r).map(([k, val]) => html`<b dir="ltr">${k}</b><span class="num">${typeof val === "number" ? fmt(val, 3) : val == null ? "—" : String(val)}</span>`)}</div>
      ${w.length ? html`<ul>${w.map(x => html`<li class="err" style="min-height:0">${x}</li>`)}</ul>` : ""}`.__raw;
  },
  async project(f) {
    const d = formData(f); d.measure_id = num(d.measure_id);
    ["saving_m3_year", "capex", "opex_year", "discount_rate"].forEach(k => d[k] = Number(d[k]));
    ["life_years", "ease", "supply_risk", "environment", "tech_risk"].forEach(k => d[k] = parseInt(d[k], 10));
    await api(`/api/sites/${state.siteId}/projects`, { method: "POST", body: d }); toast("پروژه ثبت شد"); render();
  },
  async kpidef(f) { const d = formData(f); d.warn = num(d.warn); d.critical = num(d.critical); d.grp = "custom"; await api("/api/kpi-defs", { method: "POST", body: d }); toast("شاخص ذخیره شد"); render(); },
  async user(f) { await api("/api/users", { method: "POST", body: formData(f) }); toast("کاربر افزوده شد"); render(); },
  async password(f) { await api("/api/auth/password", { method: "POST", body: formData(f) }); f.reset(); toast("گذرواژه تغییر کرد"); },
};
$("#view").addEventListener("submit", e => {
  const f = e.target.closest("form[data-form]"); if (!f) return;
  e.preventDefault(); guard(() => FORMS[f.dataset.form](f, e))();
});

// ---------- عملیات دکمه‌ها ----------
const ACTS = {
  async delstream(b) { if (confirm("جریان و قرائت‌های آن حذف شود؟")) { await api(`/api/sites/${state.siteId}/streams/${b.dataset.id}`, { method: "DELETE" }); render(); } },
  async delsite() { if (confirm("کل مجتمع با تمام داده‌هایش حذف می‌شود. ادامه می‌دهید؟")) { await api(`/api/sites/${state.siteId}`, { method: "DELETE" }); await loadSites(); render(); } },
  newsite() { state.siteId = null; $("#siteSel").value = ""; VIEWS.structure.blank = true; renderBlankSite(); },
  calckind(b) { VIEWS.calc.kind = b.dataset.id; render(); },
  async template() {
    const r = await api(`/api/sites/${state.siteId}/apply-template`, { method: "POST" });
    toast(r.created.length ? `${r.created.length} واحد برای سیستم‌های نوعی صنعت ساخته شد` : "همه واحدهای نوعی از قبل وجود دارند"); render();
  },
  eqsys(b) { VIEWS.catalog.sys = b.value; render(); },
  cattab(b) { VIEWS.catalog.tab = b.dataset.id; render(); },
  sysmeasures(b) { VIEWS.measures.system = b.dataset.id; state.page = "measures"; render(); },
  syscalc(b) { if (CALC[b.dataset.id]) VIEWS.calc.kind = b.dataset.id; state.page = "calc"; render(); },
  clearsys() { VIEWS.measures.system = ""; render(); },
  mkproject(b) {
    const m = VIEWS.measures.data.find(x => x.id === Number(b.dataset.id));
    VIEWS.projects.prefill = { measure_id: m.id, title: m.title, ease: m.ease, tech_risk: m.tech_risk };
    state.page = "projects"; render();
  },
  async delproject(b) { if (confirm("پروژه حذف شود؟")) { await api(`/api/sites/${state.siteId}/projects/${b.dataset.id}`, { method: "DELETE" }); render(); } },
  async report() {
    const r = await api(`/api/sites/${state.siteId}/report${q()}`, { raw: true });
    const url = URL.createObjectURL(new Blob([await r.text()], { type: "text/html;charset=utf-8" }));
    window.open(url, "_blank");
  },
};
function renderBlankSite() {
  state.sites = state.sites.slice(); state.siteId = null;
  VIEWS.structure($("#view"));
}
$("#view").addEventListener("click", e => { const b = e.target.closest("[data-act]"); if (b && b.tagName !== "SELECT" && ACTS[b.dataset.act]) guard(() => ACTS[b.dataset.act](b))(); });
$("#view").addEventListener("change", e => {
  const es = e.target.closest("select[data-act=eqsys]"); if (es) return ACTS.eqsys(es);
  const ind = e.target.closest("form[data-form=site] select[name=industry_code]");
  if (ind) {
    const i = cat().industries.find(x => x.code === ind.value); const f = ind.form;
    f.subgroup_code.innerHTML = `<option value="">— بدون زیرگروه —</option>` + (i ? i.subgroups : []).map(([c, n]) => `<option value="${esc(c)}">${esc(n)}</option>`).join("");
    const h = $("#indHint"); if (h && i) h.textContent = "سیستم‌های نوعی این صنعت: " + i.typical_systems.map(c => SYS_FA[c] || c).join("، ");
    return;
  }
  const st = e.target.closest("form[data-form=stream] select[name=stream_type]");
  if (st) {
    const t = cat().stream_types.find(x => x.code === st.value); const f = st.form;
    if (t) {
      f.direction.value = t.direction; f.scope.value = t.scope; f.kind.value = t.kind;
      f.origin.value = t.origin; f.destination.value = t.destination; f.quality_class.value = t.quality_class;
      f.allowed_use.value = t.allowed_use; f.recycle_status.value = t.recycle_status;
      const h = $("#stHint"); if (h) h.textContent = t.definition + (t.scope === "internal" ? " — جریان داخلی: در موازنه مرز مجتمع شمرده نمی‌شود." : "");
    }
    return;
  }
  const s = e.target.closest("select[data-act=pstatus]"); if (!s) return;
  guard(async () => {
    const p = (await api(`/api/sites/${state.siteId}/projects`)).find(x => x.id === Number(s.dataset.id));
    const body = { ...p, status: s.value }; delete body.economics; delete body.id; delete body.site_id; delete body.created_at; delete body.measure_code;
    await api(`/api/sites/${state.siteId}/projects/${s.dataset.id}`, { method: "PUT", body }); toast("وضعیت بروزرسانی شد"); render();
  })();
});

boot();
