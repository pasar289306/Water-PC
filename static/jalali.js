"use strict";
/* تقویم شمسی ↔ میلادی (الگوریتم jalaali؛ همان الگوریتم سمت سرور) + ابزار تاریخ ISO */
const JMONTHS = ["فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور", "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند"];
const GMONTHS = ["ژانویه", "فوریه", "مارس", "آوریل", "مه", "ژوئن", "ژوئیه", "اوت", "سپتامبر", "اکتبر", "نوامبر", "دسامبر"];
const _BRK = [-61, 9, 38, 199, 426, 686, 756, 818, 1111, 1181, 1210, 1635, 2060, 2097, 2192, 2262, 2324, 2394, 2456, 3178];
const _div = (a, b) => Math.trunc(a / b), _mod = (a, b) => a - _div(a, b) * b;
function _jalCal(jy, noLeap) {
  const bl = _BRK.length; let gy = jy + 621, leapJ = -14, jp = _BRK[0], jm, jump = 0, leap, n;
  if (jy < jp || jy >= _BRK[bl - 1]) throw new Error("سال شمسی خارج از محدوده");
  for (let i = 1; i < bl; i++) { jm = _BRK[i]; jump = jm - jp; if (jy < jm) break; leapJ += _div(jump, 33) * 8 + _div(_mod(jump, 33), 4); jp = jm; }
  n = jy - jp; leapJ += _div(n, 33) * 8 + _div(_mod(n, 33) + 3, 4);
  if (_mod(jump, 33) === 4 && jump - n === 4) leapJ += 1;
  const leapG = _div(gy, 4) - _div((_div(gy, 100) + 1) * 3, 4) - 150, march = 20 + leapJ - leapG;
  if (noLeap) return { gy, march };
  if (jump - n < 6) n = n - jump + _div(jump + 4, 33) * 33;
  leap = _mod(_mod(n + 1, 33) - 1, 4); if (leap === -1) leap = 4;
  return { leap, gy, march };
}
function _g2d(gy, gm, gd) {
  let d = _div((gy + _div(gm - 8, 6) + 100100) * 1461, 4) + _div(153 * _mod(gm + 9, 12) + 2, 5) + gd - 34840408;
  return d - _div(_div(gy + 100100 + _div(gm - 8, 6), 100) * 3, 4) + 752;
}
function _d2g(jdn) {
  let j = 4 * jdn + 139361631; j += _div(_div(4 * jdn + 183187720, 146097) * 3, 4) * 4 - 3908;
  const i = _div(_mod(j, 1461), 4) * 5 + 308, gd = _div(_mod(i, 153), 5) + 1, gm = _mod(_div(i, 153), 12) + 1;
  return { gy: _div(j, 1461) - 100100 + _div(8 - gm, 6), gm, gd };
}
function jalaliToGregorian(jy, jm, jd) {
  const r = _jalCal(jy, true);
  return _d2g(_g2d(r.gy, 3, r.march) + (jm - 1) * 31 - _div(jm, 7) * (jm - 7) + jd - 1);
}
function gregorianToJalali(gy, gm, gd) {
  const jdn = _g2d(gy, gm, gd); let jy = gy - 621; const r = _jalCal(jy), jdn1f = _g2d(gy, 3, r.march); let k = jdn - jdn1f;
  if (k >= 0) { if (k <= 185) return { jy, jm: 1 + _div(k, 31), jd: _mod(k, 31) + 1 }; k -= 186; }
  else { jy -= 1; k += 179; if (r.leap === 1) k += 1; }
  return { jy, jm: 7 + _div(k, 30), jd: _mod(k, 30) + 1 };
}
function monthLen(cal, y, m) {
  if (cal === "jalali") { if (m <= 6) return 31; if (m <= 11) return 30; return _jalCal(y).leap === 0 ? 30 : 29; }
  return new Date(Date.UTC(y, m, 0)).getUTCDate();
}
/** {y,m,d} در تقویم cal → رشته ISO میلادی؛ null اگر نامعتبر */
function toISO(cal, y, m, d) {
  y = +y; m = +m; d = +d;
  if (!Number.isInteger(y) || !Number.isInteger(m) || !Number.isInteger(d) || m < 1 || m > 12 || d < 1) return null;
  try {
    if (cal === "jalali") { if (y < 1200 || y > 1700 || d > monthLen(cal, y, m)) return null; const g = jalaliToGregorian(y, m, d); return `${g.gy}-${String(g.gm).padStart(2, "0")}-${String(g.gd).padStart(2, "0")}`; }
    if (y < 1700 || y > 2600 || d > monthLen(cal, y, m)) return null;
    return `${y}-${String(m).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
  } catch { return null; }
}
/** ISO میلادی → {y,m,d} در تقویم cal */
function fromISO(cal, iso) {
  const [gy, gm, gd] = String(iso).slice(0, 10).split("-").map(Number);
  if (cal === "jalali") { const j = gregorianToJalali(gy, gm, gd); return { y: j.jy, m: j.jm, d: j.jd }; }
  return { y: gy, m: gm, d: gd };
}
function fmtDate(cal, iso) { if (!iso) return ""; const p = fromISO(cal, iso); return `${p.y}${cal === "jalali" ? "/" : "-"}${String(p.m).padStart(2, "0")}${cal === "jalali" ? "/" : "-"}${String(p.d).padStart(2, "0")}`; }
function todayISO() { const t = new Date(); return `${t.getFullYear()}-${String(t.getMonth() + 1).padStart(2, "0")}-${String(t.getDate()).padStart(2, "0")}`; }
if (typeof module !== "undefined") module.exports = { jalaliToGregorian, gregorianToJalali, toISO, fromISO, monthLen, fmtDate };
