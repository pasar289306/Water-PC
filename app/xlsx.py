"""خواندن/نوشتن حداقلی فایل Excel (.xlsx) بدون وابستگی خارجی (فقط zipfile و xml).

خواندن: اولین کاربرگ؛ مقادیر عددی به float، متن به str. نوشتن: چند کاربرگ با سلول‌های متنی/عددی (برای قالب نمونه).
"""
from __future__ import annotations

import io
import re
import zipfile
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
      "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}
MAX_UNCOMPRESSED = 60 * 1024 * 1024
MAX_ROWS = 100000


class XlsxError(ValueError):
    pass


def _xml(z: zipfile.ZipFile, name: str):
    raw = z.read(name)
    if b"<!DOCTYPE" in raw[:2000].upper() or b"<!ENTITY" in raw.upper():
        raise XlsxError("ساختار فایل Excel نامعتبر/ناامن است")
    return ET.fromstring(raw)


def _col_index(ref: str) -> int:
    letters = re.match(r"[A-Z]+", ref).group(0)
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def read_rows(raw: bytes) -> list[list]:
    try:
        z = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile:
        raise XlsxError("فایل، Excel (.xlsx) معتبر نیست (فرمت .xls قدیمی پشتیبانی نمی‌شود؛ آن را به .xlsx ذخیره کنید)")
    if sum(i.file_size for i in z.infolist()) > MAX_UNCOMPRESSED:
        raise XlsxError("حجم فایل پس از باز کردن بیش از حد مجاز است")
    names = set(z.namelist())
    if "xl/workbook.xml" not in names:
        raise XlsxError("فایل، Excel (.xlsx) معتبر نیست")
    shared: list[str] = []
    if "xl/sharedStrings.xml" in names:
        for si in _xml(z, "xl/sharedStrings.xml").findall("m:si", NS):
            shared.append("".join(t.text or "" for t in si.iter(f"{{{NS['m']}}}t")))
    wb = _xml(z, "xl/workbook.xml")
    first = wb.find("m:sheets/m:sheet", NS)
    if first is None:
        raise XlsxError("فایل Excel کاربرگی ندارد")
    rid = first.get(f"{{{NS['r']}}}id")
    target = "worksheets/sheet1.xml"
    if "xl/_rels/workbook.xml.rels" in names:
        for rel in _xml(z, "xl/_rels/workbook.xml.rels"):
            if rel.get("Id") == rid:
                target = rel.get("Target")
                break
    path = target.lstrip("/") if target.startswith("/") else "xl/" + target
    if path not in names:
        raise XlsxError("کاربرگ اول فایل پیدا نشد")
    out: list[list] = []
    for row in _xml(z, path).iter(f"{{{NS['m']}}}row"):
        cells: dict[int, object] = {}
        for c in row.findall("m:c", NS):
            idx = _col_index(c.get("r") or "A1")
            t = c.get("t")
            v = c.find("m:v", NS)
            val: object = None
            if t == "s" and v is not None and v.text is not None:
                i = int(v.text)
                val = shared[i] if i < len(shared) else ""
            elif t == "inlineStr":
                val = "".join(x.text or "" for x in c.iter(f"{{{NS['m']}}}t"))
            elif t in ("str", "e"):
                val = (v.text if v is not None else "") or ""
            elif t == "b":
                val = bool(int(v.text)) if v is not None and v.text else None
            elif v is not None and v.text not in (None, ""):
                try:
                    val = float(v.text)
                except ValueError:
                    val = v.text
            if val is not None and val != "":
                cells[idx] = val
        if cells:
            width = max(cells) + 1
            out.append([cells.get(i) for i in range(width)])
        else:
            out.append([])
        if len(out) > MAX_ROWS:
            raise XlsxError(f"تعداد ردیف‌ها بیش از {MAX_ROWS} است")
    return out


def _col_name(i: int) -> str:
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def write_workbook(sheets: list[tuple[str, list[list]]]) -> bytes:
    """sheets: [(نام کاربرگ, ردیف‌ها)]؛ هر سلول str یا عدد."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="xml" ContentType="application/xml"/>'
                   '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                   + "".join(f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                             for i in range(1, len(sheets) + 1)) + "</Types>")
        z.writestr("_rels/.rels", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        z.writestr("xl/workbook.xml", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   f'<workbook xmlns="{NS["m"]}" xmlns:r="{NS["r"]}"><sheets>'
                   + "".join(f'<sheet name="{escape(n)[:31]}" sheetId="{i}" r:id="rId{i}"/>' for i, (n, _) in enumerate(sheets, 1))
                   + "</sheets></workbook>")
        z.writestr("xl/_rels/workbook.xml.rels", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   + "".join(f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>'
                             for i in range(1, len(sheets) + 1)) + "</Relationships>")
        for i, (_, rws) in enumerate(sheets, 1):
            body = []
            for r, row in enumerate(rws, 1):
                cs = []
                for ci, v in enumerate(row):
                    if v is None or v == "":
                        continue
                    ref = f"{_col_name(ci)}{r}"
                    if isinstance(v, (int, float)) and not isinstance(v, bool):
                        cs.append(f'<c r="{ref}"><v>{v}</v></c>')
                    else:
                        cs.append(f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{escape(str(v))}</t></is></c>')
                body.append(f'<row r="{r}">{"".join(cs)}</row>')
            z.writestr(f"xl/worksheets/sheet{i}.xml", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                       f'<worksheet xmlns="{NS["m"]}"><sheetData>{"".join(body)}</sheetData></worksheet>')
    return buf.getvalue()
