"""
A tiny Excel (.xlsx) writer and reader — no extra packages needed.

An .xlsx file is a zip of a few XML files. This writes just what my_predictions.xlsx needs: several tabs, bold
shaded header rows that stay on screen when you scroll, set column widths, a filter button on the header, and
green YES / red NO. Opens in Numbers, Excel and Google Sheets.
"""
import re
import zipfile
from xml.sax.saxutils import escape

# Cell styles (index into cellXfs below)
NORMAL, HEADER, TITLE, YES, NO, BOLD, MUTED, CENTER, CENTER_BOLD, HEADER_CENTER = range(10)

STYLES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<fonts count="6">
<font><sz val="12"/><name val="Helvetica Neue"/></font>
<font><b/><sz val="12"/><name val="Helvetica Neue"/></font>
<font><b/><sz val="16"/><name val="Helvetica Neue"/></font>
<font><b/><sz val="12"/><color rgb="FF1E7B34"/><name val="Helvetica Neue"/></font>
<font><b/><sz val="12"/><color rgb="FFC0392B"/><name val="Helvetica Neue"/></font>
<font><sz val="12"/><color rgb="FF8A8A8A"/><name val="Helvetica Neue"/></font>
</fonts>
<fills count="3">
<fill><patternFill patternType="none"/></fill>
<fill><patternFill patternType="gray125"/></fill>
<fill><patternFill patternType="solid"><fgColor rgb="FFDDE5F0"/><bgColor indexed="64"/></patternFill></fill>
</fills>
<borders count="2">
<border><left/><right/><top/><bottom/><diagonal/></border>
<border><left/><right/><top/><bottom style="thin"><color rgb="FF9AA5B1"/></bottom><diagonal/></border>
</borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="10">
<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
<xf numFmtId="0" fontId="1" fillId="2" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1"><alignment wrapText="1" vertical="center"/></xf>
<xf numFmtId="0" fontId="2" fillId="0" borderId="0" xfId="0" applyFont="1"/>
<xf numFmtId="0" fontId="3" fillId="0" borderId="0" xfId="0" applyFont="1"><alignment horizontal="center"/></xf>
<xf numFmtId="0" fontId="4" fillId="0" borderId="0" xfId="0" applyFont="1"><alignment horizontal="center"/></xf>
<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/>
<xf numFmtId="0" fontId="5" fillId="0" borderId="0" xfId="0" applyFont="1"><alignment horizontal="center"/></xf>
<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"><alignment horizontal="center"/></xf>
<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"><alignment horizontal="center"/></xf>
<xf numFmtId="0" fontId="1" fillId="2" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1"><alignment horizontal="center" vertical="center"/></xf>
</cellXfs>
</styleSheet>"""


def col_letter(i: int) -> str:
    """0 -> A, 25 -> Z, 26 -> AA"""
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def _cell(ref: str, value, style: int) -> str:
    s = f' s="{style}"' if style else ""
    if value is None or value == "":
        return f'<c r="{ref}"{s}/>' if style else ""
    if isinstance(value, bool):
        value = "YES" if value else "NO"
    if isinstance(value, (int, float)):
        return f'<c r="{ref}"{s}><v>{value}</v></c>'
    return f'<c r="{ref}"{s} t="inlineStr"><is><t xml:space="preserve">{escape(str(value))}</t></is></c>'


def _sheet_xml(sheet: dict) -> str:
    rows, styles = sheet["rows"], sheet.get("styles", {})
    widths, header_row, left_cols = sheet.get("widths", []), sheet.get("freeze_row"), sheet.get("freeze_col")
    if header_row and left_cols:          # header row AND the first columns stay on screen
        view = (f'<sheetViews><sheetView workbookViewId="0"><pane xSplit="{left_cols}" ySplit="{header_row}" '
                f'topLeftCell="{col_letter(left_cols)}{header_row + 1}" activePane="bottomRight" state="frozen"/>'
                '</sheetView></sheetViews>')
    elif header_row:
        view = (f'<sheetViews><sheetView workbookViewId="0"><pane ySplit="{header_row}" topLeftCell="A{header_row + 1}" '
                f'activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>')
    else:
        view = '<sheetViews><sheetView workbookViewId="0"/></sheetViews>'
    cols = "".join(f'<col min="{i + 1}" max="{i + 1}" width="{w}" customWidth="1"/>' for i, w in enumerate(widths))
    body = []
    for r, row in enumerate(rows, 1):
        cells = "".join(_cell(f"{col_letter(c)}{r}", v, styles.get((r - 1, c), 0)) for c, v in enumerate(row))
        height = sheet.get("heights", {}).get(r - 1)
        ht = f' ht="{height}" customHeight="1"' if height else ""
        body.append(f'<row r="{r}"{ht}>{cells}</row>')
    autofilter = ""
    if sheet.get("filter_row") and rows:
        fr = sheet["filter_row"]
        autofilter = f'<autoFilter ref="A{fr}:{col_letter(len(rows[fr - 1]) - 1)}{len(rows)}"/>'
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            f'{view}{"<cols>" + cols + "</cols>" if cols else ""}<sheetData>{"".join(body)}</sheetData>{autofilter}'
            '</worksheet>')


def _filter_names(sheets: list) -> str:
    """Excel needs a hidden name for each tab's filter range."""
    names = [f'<definedName name="_xlnm._FilterDatabase" localSheetId="{i}" hidden="1">'
             f"'{escape(s['name'])}'!$A${s['filter_row']}:${col_letter(len(s['rows'][s['filter_row'] - 1]) - 1)}"
             f"${len(s['rows'])}</definedName>"
             for i, s in enumerate(sheets) if s.get("filter_row") and s["rows"]]
    return f"<definedNames>{''.join(names)}</definedNames>" if names else ""


def write_xlsx(path: str, sheets: list) -> None:
    """sheets: [{"name", "rows": [[...]], "styles": {(row, col): style}, "widths": [...], "freeze_row": n,
    "filter_row": n, "heights": {row: points}}] — rows and cols counted from 0 in `styles`/`heights`."""
    n = len(sheets)
    content_types = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                     '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                     '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                     '<Default Extension="xml" ContentType="application/xml"/>'
                     '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                     '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
                     + "".join(f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                               for i in range(1, n + 1)) + '</Types>')
    root_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                 '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                 '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
                 '</Relationships>')
    workbook = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'
                + "".join(f'<sheet name="{escape(s["name"], {chr(34): "&quot;"})}" sheetId="{i}" r:id="rId{i}"/>'
                          for i, s in enumerate(sheets, 1))
                + '</sheets>' + _filter_names(sheets) + '</workbook>')
    wb_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
               '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
               + "".join(f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>'
                         for i in range(1, n + 1))
               + f'<Relationship Id="rId{n + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
               '</Relationships>')
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", root_rels)
        z.writestr("xl/workbook.xml", workbook)
        z.writestr("xl/_rels/workbook.xml.rels", wb_rels)
        z.writestr("xl/styles.xml", STYLES)
        for i, s in enumerate(sheets, 1):
            z.writestr(f"xl/worksheets/sheet{i}.xml", _sheet_xml(s))


def read_xlsx(path: str) -> dict:
    """{tab name: [[cell values]]} for files written by write_xlsx (used by the tests and for quick checks)."""
    from xml.sax.saxutils import unescape
    out = {}
    with zipfile.ZipFile(path) as z:
        names = [unescape(n, {"&quot;": '"'}) for n in re.findall(r'<sheet name="([^"]*)"', z.read("xl/workbook.xml").decode())]
        for i, name in enumerate(names, 1):
            xml = z.read(f"xl/worksheets/sheet{i}.xml").decode()
            rows = []
            for row in re.findall(r"<row [^>]*>(.*?)</row>", xml):
                cells = {}
                for ref, attrs, inner in re.findall(r'<c r="([A-Z]+)\d+"([^>]*?)(?:/>|>(.*?)</c>)', row):
                    col = 0
                    for ch in ref:
                        col = col * 26 + ord(ch) - 64
                    t = re.search(r"<t[^>]*>(.*?)</t>", inner or "")
                    v = re.search(r"<v>(.*?)</v>", inner or "")
                    cells[col - 1] = (unescape(t.group(1)) if t else
                                      (float(v.group(1)) if "." in v.group(1) else int(v.group(1))) if v else "")
                rows.append([cells.get(c, "") for c in range(max(cells) + 1)] if cells else [])
            out[name] = rows
    return out
