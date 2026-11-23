"""Local workbook splitting with original sheet names, row numbers and cell values."""
from collections import Counter
from datetime import date
import hashlib
import io
import posixpath
import re
from pathlib import PurePosixPath
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZIP_DEFLATED, ZipFile
import zlib

from business_agent.activity_intake_108.xlsx_preprocess import compact_worksheets, WorksheetLimit

CONTRACT = "shenjia-batch/1.0"
MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG = "http://schemas.openxmlformats.org/package/2006/relationships"
CONTENT = "http://schemas.openxmlformats.org/package/2006/content-types"
NS = {"m": MAIN, "r": REL, "p": PKG}
SHEET = "{" + MAIN + "}"
XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"
MAX_FILE = 5 * 1024 * 1024
MAX_EXPANDED = 20 * 1024 * 1024
MAX_ENTRY = 5 * 1024 * 1024
MAX_PRODUCT_ROWS = 10_000
MAX_GROUPS = 100
SINGLE_ANALYSIS_ROWS = 999
MAX_OUTPUT = 32 * 1024 * 1024
REQUIRED = ("神价ID", "神价名称", "活动周期", "子活动ID", "神价商品", "神价UPC")
ID = re.compile(r"[0-9]{1,24}")
UPC = re.compile(r"[0-9]{8,14}")
COORD = re.compile(r"([A-Z]{1,3})([1-9][0-9]{0,6})")
PERIOD = re.compile(r"\s*(\d{4})-(\d{1,2})-(\d{1,2})\s*(?:至|~|～|—|–|-)\s*(\d{4})-(\d{1,2})-(\d{1,2})\s*")
ET.register_namespace("", MAIN)
ET.register_namespace("r", REL)


class BatchError(ValueError):
    def __init__(self, code, message, **details):
        super().__init__(message)
        self.code, self.details = code, details


def _xml(value):
    if b"\x00" in value or re.search(br"<!\s*(DOCTYPE|ENTITY)", value, re.I):
        raise BatchError("INVALID_WORKBOOK", "工作簿包含不支持的 XML 声明。")
    return ET.fromstring(value)


def _archive(data):
    with ZipFile(io.BytesIO(data)) as archive:
        entries = archive.infolist()
        if len(entries) > 200 or len(entries) != len({i.filename for i in entries}):
            raise BatchError("WORKBOOK_LIMIT", "工作簿展开超出可处理范围。")
        try:
            compact = compact_worksheets(archive, MAX_ENTRY, MAX_EXPANDED)
        except WorksheetLimit as exc:
            raise BatchError("WORKBOOK_LIMIT", "工作簿展开超出可处理范围。") from exc
        parts = {}
        for entry in entries:
            name = entry.filename
            if ((name not in compact and entry.file_size > MAX_ENTRY) or entry.flag_bits & 1 or "\\" in name
                    or PurePosixPath(name).is_absolute() or ".." in PurePosixPath(name).parts
                    or (entry.external_attr >> 16) & 0o170000 == 0o120000
                    or re.search(r"vbaProject|embeddings/|activeX/", name, re.I)):
                raise BatchError("INVALID_WORKBOOK", "工作簿包含超限或不受支持的文件。")
            if entry.is_dir():
                continue
            value = compact[name] if name in compact else archive.read(entry)
            if name.endswith(".rels") and any(
                node.get("TargetMode", "").lower() == "external"
                or re.match(r"^[a-z]+:", node.get("Target", ""), re.I) for node in _xml(value)
            ):
                raise BatchError("INVALID_WORKBOOK", "工作簿包含外部引用。")
            parts[name] = value
    return parts


def _sheet(parts):
    workbook = _xml(parts["xl/workbook.xml"])
    sheets = workbook.findall("m:sheets/m:sheet", NS)
    if len(sheets) != 1:
        raise BatchError("FORMAT_NOT_SUPPORTED", "此入口支持单工作表拼接的神价表。")
    relation_id = sheets[0].get("{" + REL + "}id")
    rels = _xml(parts["xl/_rels/workbook.xml.rels"])
    relations = [r for r in rels if r.get("Id") == relation_id and r.get("Type") == REL + "/worksheet"]
    if len(relations) != 1:
        raise ValueError("invalid worksheet relationship")
    target = relations[0].get("Target", "")
    path = target.lstrip("/") if target.startswith("/") else posixpath.normpath("xl/" + target)
    if not path.startswith("xl/worksheets/"):
        raise ValueError("invalid worksheet path")
    shared = []
    if "xl/sharedStrings.xml" in parts:
        shared = [_text_nodes(si) for si in _xml(parts["xl/sharedStrings.xml"]).findall("m:si", NS)]
    return sheets[0].attrib["name"], workbook.find("m:workbookPr", NS), _xml(parts[path]), shared


def _text_nodes(node):
    return "".join((item.text or "") for item in
                   node.findall("m:t", NS) + node.findall("m:r/m:t", NS))


def _cells(row, shared, row_number):
    values = {}
    for cell in row.findall("m:c", NS):
        match = COORD.fullmatch(cell.attrib["r"])
        if not match or int(match[2]) != row_number or match[1] in values:
            raise ValueError("invalid cell coordinates")
        kind = cell.get("t", "n")
        raw = cell.findtext("m:v", "", NS)
        if kind == "s":
            index = int(raw)
            if index < 0:
                raise ValueError("invalid shared-string index")
            value = shared[index]
        elif kind == "inlineStr":
            node = cell.find("m:is", NS)
            value = _text_nodes(node) if node is not None else ""
        else:
            value = raw
        if cell.find("m:f", NS) is not None:
            kind = "formula"
        values[match[1]] = (kind, value.strip())
    return values


def _header(values):
    labels = {label: [column for column, (kind, value) in values.items()
                      if value == label and kind in ("s", "str", "inlineStr")] for label in REQUIRED}
    if not all(labels.values()):
        return None
    if any(len(labels[label]) != 1 for label in REQUIRED if label != "子活动ID"):
        raise BatchError("HEADER_CONFLICT", "神价表主字段表头重复，请核对表头。")
    child_columns = labels["子活动ID"]
    # The helper block on the right may repeat 子活动ID. Use the first export column.
    rank = lambda col: (len(col), col)
    candidates = [col for col in child_columns
                  if rank(labels["活动周期"][0]) < rank(col) < rank(labels["神价商品"][0])]
    if len(candidates) != 1:
        raise BatchError("HEADER_CONFLICT", "无法唯一确定主表的子活动 ID 列。")
    return {label: candidates[0] if label == "子活动ID" else labels[label][0] for label in REQUIRED}


def _period(raw):
    match = PERIOD.fullmatch(raw)
    if not match:
        return None
    try:
        values = tuple(map(int, match.groups()))
        start, end = date(*values[:3]), date(*values[3:])
        return (start.isoformat(), end.isoformat()) if start <= end else None
    except ValueError:
        return None


def _groups(root, shared, sheet_name):
    data = root.find("m:sheetData", NS)
    if data is None:
        raise ValueError("missing sheetData")
    groups, active_columns, header_rows = {}, None, []
    prefix = []
    last = total = 0
    for row in data.findall("m:row", NS):
        number = int(row.attrib["r"])
        if not last < number <= 1_048_576:
            raise ValueError("invalid row coordinates")
        last = number
        values = _cells(row, shared, number)
        if not any(value or kind == "formula" for kind, value in values.values()):
            continue
        columns = _header(values)
        if columns:
            active_columns = columns
            header_rows.append(row)
            continue
        if active_columns is None:
            if len(prefix) >= 12:
                raise BatchError("FORMAT_NOT_SUPPORTED", "未找到神价合并表的主字段表头。")
            prefix.append(row)
            continue
        main_id, name, period, child, product, upc = (
            values.get(active_columns[label], ("", "")) for label in REQUIRED
        )
        source_id = main_id[1]
        location = {"sheet": sheet_name, "row": number}
        if not ID.fullmatch(source_id) or main_id[0] not in ("n", "s", "str", "inlineStr"):
            raise BatchError("MAIN_ID_UNRESOLVED", "商品行的主神价 ID 缺失或格式异常。", **location)
        dates = _period(period[1]) if period[0] in ("s", "str", "inlineStr") else None
        if not dates or not name[1] or name[0] not in ("s", "str", "inlineStr"):
            raise BatchError("ACTIVITY_FIELDS_UNRESOLVED", "神价名称或活动周期待核对。", **location)
        if (not ID.fullmatch(child[1]) or child[0] == "formula" or upc[0] == "formula"
                or not (product[1] and product[0] != "formula" or UPC.fullmatch(upc[1]))):
            raise BatchError("PRODUCT_ROW_UNRESOLVED", "子活动 ID 或商品识别信息待核对。", **location)
        group = groups.setdefault(source_id, {"name": name[1], "dates": dates, "rows": []})
        if (group["name"], group["dates"]) != (name[1], dates):
            raise BatchError("ACTIVITY_FIELDS_CONFLICT", "同一神价 ID 的名称或周期不一致。", **location,
                             sourceActivityId=source_id, firstRow=int(group["rows"][0].attrib["r"]))
        group["rows"].append(row)
        total += 1
        if len(groups) > MAX_GROUPS or total > MAX_PRODUCT_ROWS:
            raise BatchError("WORKBOOK_LIMIT", "神价活动或商品明细超过本地批量处理范围。")
    if not header_rows or len(groups) < 2:
        raise BatchError("FORMAT_NOT_SUPPORTED", "此文件不包含至少两个明确的神价活动。")
    return groups, prefix, header_rows, total


def _relationship_xml(items):
    root = ET.Element("{" + PKG + "}Relationships")
    for index, (kind, target) in enumerate(items, 1):
        ET.SubElement(root, "{" + PKG + "}Relationship", {"Id": f"rId{index}", "Type": REL + "/" + kind, "Target": target})
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _make_workbook(name, workbook_props, root, rows, shared, parts):
    sheet = ET.Element(SHEET + "worksheet")
    for tag in ("sheetFormatPr", "cols"):
        if original := root.find("m:" + tag, NS):
            sheet.append(original)
    data = ET.SubElement(sheet, SHEET + "sheetData")
    for original in sorted(rows, key=lambda row: int(row.attrib["r"])):
        row = ET.SubElement(data, SHEET + "row", original.attrib)
        for source in original.findall("m:c", NS):
            if source.find("m:f", NS) is not None:
                raise BatchError("FORMULA_REVIEW_REQUIRED", "分场文件包含公式，请先核对公式依赖后再拆分。",
                                 sheet=name, cell=source.attrib["r"])
            cell = ET.fromstring(ET.tostring(source))
            if cell.get("t") == "s":
                shared_value = shared[int(cell.findtext("m:v", "", NS))]
                cell.clear()
                cell.attrib.update(source.attrib, t="inlineStr")
                node = ET.SubElement(ET.SubElement(cell, SHEET + "is"), SHEET + "t")
                node.set(XML_SPACE, "preserve")
                node.text = shared_value
            row.append(cell)
    workbook = ET.Element(SHEET + "workbook")
    if workbook_props is not None and "date1904" in workbook_props.attrib:
        ET.SubElement(workbook, SHEET + "workbookPr", {"date1904": workbook_props.attrib["date1904"]})
    sheets = ET.SubElement(workbook, SHEET + "sheets")
    ET.SubElement(sheets, SHEET + "sheet", {"name": name, "sheetId": "1", "{" + REL + "}id": "rId1"})
    relations = [("worksheet", "worksheets/sheet1.xml")]
    payload = {"xl/workbook.xml": ("spreadsheetml.sheet.main", workbook),
               "xl/worksheets/sheet1.xml": ("spreadsheetml.worksheet", sheet)}
    if "xl/styles.xml" in parts:
        relations.append(("styles", "styles.xml"))
        payload["xl/styles.xml"] = ("spreadsheetml.styles", _xml(parts["xl/styles.xml"]))
    ct = ET.Element("{" + CONTENT + "}Types")
    ET.SubElement(ct, "{" + CONTENT + "}Default", Extension="rels",
                  ContentType="application/vnd.openxmlformats-package.relationships+xml")
    ET.SubElement(ct, "{" + CONTENT + "}Default", Extension="xml", ContentType="application/xml")
    for path, (kind, _) in payload.items():
        ET.SubElement(ct, "{" + CONTENT + "}Override", PartName="/" + path,
                      ContentType="application/vnd.openxmlformats-officedocument." + kind + "+xml")
    result = io.BytesIO()
    with ZipFile(result, "w", ZIP_DEFLATED) as output:
        for path, value in (("[Content_Types].xml", ct), *((key, value) for key, (_, value) in payload.items())):
            output.writestr(path, ET.tostring(value, encoding="utf-8", xml_declaration=True))
        output.writestr("_rels/.rels", _relationship_xml([("officeDocument", "xl/workbook.xml")]))
        output.writestr("xl/_rels/workbook.xml.rels", _relationship_xml(relations))
    return result.getvalue()


def split_workbook(data, *, include_workbooks=False):
    if not isinstance(data, bytes) or not 0 < len(data) <= MAX_FILE:
        raise BatchError("WORKBOOK_LIMIT", "原表大小超出可处理范围。")
    try:
        parts = _archive(data)
        name, workbook_props, root, shared = _sheet(parts)
        groups, prefix, header_rows, total = _groups(root, shared, name)
    except (BadZipFile, KeyError, IndexError, TypeError, OverflowError, ET.ParseError, RuntimeError, OSError, zlib.error) as exc:
        raise BatchError("INVALID_WORKBOOK", "无法读取此 XLSX 文件。") from exc
    except ValueError as exc:
        if isinstance(exc, BatchError):
            raise
        raise BatchError("INVALID_WORKBOOK", "无法读取此 XLSX 文件。") from exc
    activities = []
    exported_bytes = 0
    for source_id, group in groups.items():
        rows = group["rows"]
        start, end = group["dates"]
        item = {"sourceActivityId": source_id, "activityName": group["name"], "startDate": start,
                "endDate": end, "productRowCount": len(rows), "sourceSheet": name,
                "sourceRows": [int(row.attrib["r"]) for row in rows], "filename": f"神价-{source_id}.xlsx"}
        if include_workbooks:
            selected = prefix + header_rows + rows
            if len(selected) > SINGLE_ANALYSIS_ROWS:
                raise BatchError("ACTIVITY_ROW_LIMIT", "单场神价商品超出当前识别行数。", sourceActivityId=source_id)
            content = _make_workbook(name, workbook_props, root, selected, shared, parts)
            exported_bytes += len(content)
            if len(content) > MAX_FILE or exported_bytes > MAX_OUTPUT * 3 // 4:
                raise BatchError("WORKBOOK_LIMIT", "分场文件超出可返回的大小。")
            item.update(sha256=hashlib.sha256(content).hexdigest(), content=content)
        activities.append(item)
    return {"sourceSha256": hashlib.sha256(data).hexdigest(), "activityCount": len(activities),
            "productRowCount": total, "activities": activities}
