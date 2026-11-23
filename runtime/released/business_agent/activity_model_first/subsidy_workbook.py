"""Reconcile one explicitly named single-item subsidy sheet after model recognition."""
from copy import deepcopy
import re

from .submission_checks import AMOUNT, AMOUNT_FIELDS, HEADERS, header_label

INTERNAL_CODE = re.compile(r"[0-9]+-[0-9]+-[0-9]+")
UPC = re.compile(r"[0-9]{8,14}")
IDENTITY_FIELDS = {"activityName", "type", "platformCode", "sourceActivityId", "startAt", "endAt"}


def _sheet(documents):
    if len(documents) != 1 or documents[0]["kind"] != "xlsx":
        return None
    doc = documents[0]
    rows = [row for row in doc["tableRows"] if any(c["text"].strip() for c in row["cells"])]
    if doc["status"] != "parsed" or doc["textBlocks"] or len(rows) < 2:
        return None
    if len({row["locator"].get("sheet") for row in rows}) != 1:
        return None
    header = rows[0]
    labels = [header_label(cell["text"]) for cell in header["cells"]]
    required = {*HEADERS, "序号", "品牌", "内部商品码", "UPC候选"}
    if not required <= set(labels) or any(labels.count(label) != 1 for label in required):
        return None
    if set(labels) - required - {""}:
        return None
    columns = dict(zip(labels, (cell["column"] for cell in header["cells"])))
    price_columns = {value: columns[key] for key, value in HEADERS.items()}
    main_rows, tail_rows = [], []
    for row in rows[1:]:
        cells = {cell["column"]: cell for cell in row["cells"]}
        values = {column: cell["text"].strip() for column, cell in cells.items()}

        def read(label):
            return values.get(columns[label], "")

        if not read("商品名称") or any(
                not AMOUNT.fullmatch(values.get(price_columns[key], ""))
                for key in AMOUNT_FIELDS[:2]):
            return None
        net = cells.get(price_columns["net"], {})
        if not AMOUNT.fullmatch(net.get("text", "")) and net.get("sourceType") != "formula":
            return None
        code, upc = read("内部商品码"), read("UPC候选")
        if read("序号").isdigit() and INTERNAL_CODE.fullmatch(code) and upc and not tail_rows:
            main_rows.append((row, cells, columns["内部商品码"], columns["UPC候选"]))
        elif not read("序号") and not read("品牌") and not upc and UPC.fullmatch(code):
            tail_rows.append((row, cells, None, columns["内部商品码"]))
        else:
            return None
    net_col = price_columns["net"]
    formula = re.compile(rf"={price_columns['price']}(\d+)-{price_columns['subsidy']}\1")
    if not main_rows or not any(formula.fullmatch(cells[net_col]["text"]) for _, cells, _, _ in main_rows):
        return None
    return doc, header, columns, main_rows + tail_rows


def reconcile_subsidy(payload, result, metadata, lookup):
    """Keep model row coverage and use the sheet's explicit layout for grouping and codes."""
    manual = payload.get("manualFields") or {}
    form = manual.get("fields", {})
    name = form.get("activityName") or ""
    if (form.get("type") != "platform" or "补贴" not in name or form.get("manualProductRows")):
        return result, metadata
    scoped = _sheet(payload["documents"])
    if scoped is None:
        return result, metadata
    doc, header, columns, source_rows = scoped
    candidates = result["candidates"]
    named = [item for item in candidates if item["fields"]["activityName"]["value"] == name
             and item["fields"]["activityName"]["status"] == "explicit"]
    if len(named) != 1 or any(conflict["field"] in IDENTITY_FIELDS for conflict in result["conflicts"]):
        return result, metadata
    primary = named[0]
    # Product brands and repeated descriptions of the shared headers can span batches.
    header_labels = {"系统活动价", "拟申请补贴", "补贴后单价"}
    def describes_sheet(value):
        if not value:
            return True
        lines = [line.strip() for line in value.splitlines() if line.strip()]
        return header_label(value) in header_labels or all(
            header_label(line) in header_labels or line in {"元/销售件", "条件测算，元"}
            for line in lines)
    for item in candidates:
        if item is primary:
            continue
        for key, field in item["fields"].items():
            if field["status"] == "missing":
                continue
            if field["status"] == "explicit" and (
                key == "rulesText" and describes_sheet(field["value"])
                or key in IDENTITY_FIELDS and field["value"] == primary["fields"][key]["value"]
            ):
                continue
            return result, metadata
    if any(item["activityMethod"]["status"] != "missing" for item in metadata["candidateMetadata"]
           if item["candidateId"] != primary["candidateId"]):
        return result, metadata

    ids = {entry["locator"]["row"]: entry["rowId"] for entry in lookup.values()
           if entry["materialId"] == doc["materialId"] and "row" in entry["locator"]}
    products = [row for candidate in candidates for row in candidate["productRows"]]
    if any(len(row["sourceRowIds"]) != 1 for row in products):
        return result, metadata
    by_row = {row["sourceRowIds"][0]: row for row in products}
    expected = {ids.get(row["locator"]["row"]) for row, _, _, _ in source_rows}
    if None in expected or len(expected) != len(products) or set(by_row) != expected:
        return result, metadata

    if len(result["evidence"]) + 3 * len(products) + 3 > 6000:
        return result, metadata
    result, metadata = deepcopy(result), deepcopy(metadata)
    primary_id = primary["candidateId"]
    primary = next(item for item in result["candidates"] if item["candidateId"] == primary_id)
    evidence = result["evidence"]
    by_cell = {(item["materialId"], item["locator"].get("sheet"), item["locator"].get("cell"), item["quote"]):
               item["evidenceId"] for item in evidence}
    used_ids = {item["evidenceId"] for item in evidence}

    def cite(cell):
        locator, quote = cell["locator"], cell["text"]
        key = doc["materialId"], locator.get("sheet"), locator["cell"], quote
        if key not in by_cell:
            index = len(evidence) + 1
            while f"ev-{index}" in used_ids:
                index += 1
            eid = f"ev-{index}"
            by_cell[key] = eid
            used_ids.add(eid)
            evidence.append({"materialId": doc["materialId"], "sourceSha256": doc["sha256"],
                             "locator": locator, "quote": quote, "evidenceId": eid})
        return by_cell[key]

    merged = []
    for row, cells, code_col, upc_col in source_rows:
        original = by_row[ids[row["locator"]["row"]]]
        name_cell = cells[columns["商品名称"]]
        code = cells[code_col] if code_col else None
        upc = cells[upc_col]
        raw_upc = upc["text"] if UPC.fullmatch(upc["text"]) else None
        refs = [cite(cell) for cell in (name_cell, code, upc) if cell]
        if raw_upc is None:
            result["warnings"].append({
                "code": "SUBSIDY_UPC_FORMAT_REVIEW",
                "message": (f"第 {row['locator']['row']} 行 UPC 候选不是8至14位数字条码，"
                            "已保留原文及内部码，请提报人核对；未将其改写为 UPC。"),
                "locator": upc["locator"],
            })
        merged.append(original | {
            "rawName": name_cell["text"],
            "rawInternalCode": code["text"] if code else None,
            "rawUpc": raw_upc,
            "evidenceIds": list(dict.fromkeys(original["evidenceIds"] + refs)),
        })
    primary["productRows"] = merged
    rules = primary["fields"]["rulesText"]
    if not form.get("rulesText") and (rules["status"] == "missing" or
                                     rules["status"] == "explicit" and describes_sheet(rules["value"])):
        header_cells = {cell["column"]: cell for cell in header["cells"]}
        refs = [cite(header_cells[columns[label]]) for label in ("系统活动价", "拟申请补贴", "补贴后单价")]
        remark = form.get("remark") or ""
        if form.get("platformCode") == "taobao_instant" and "淘宝闪购承担" in remark:
            used_ids = {item["evidenceId"] for item in evidence}
            index = len(used_ids) + 1
            while f"ev-{index}" in used_ids:
                index += 1
            refs.append(f"ev-{index}")
            evidence.append({"evidenceId": refs[-1], "materialId": manual["materialId"],
                             "sourceSha256": manual["sha256"],
                             "locator": {"kind": "form", "fieldPath": "remark"}, "quote": "淘宝闪购承担"})
        primary["fields"]["rulesText"] = {
            "value": ("淘宝闪购单品补贴，平台按商品销售件补贴，金额以原表为准。"
                      if form.get("platformCode") == "taobao_instant" and "淘宝闪购承担" in (form.get("remark") or "")
                      else "单品补贴，按商品每销售件申请，补贴金额以原表为准。"),
            "status": "explicit",
            "evidenceIds": list(dict.fromkeys(rules["evidenceIds"] + refs)),
        }
    result["candidates"] = [primary]
    result["missingFields"] = [{"field": key, "reasonCode": "NOT_PROVIDED"}
                               for key, field in sorted(primary["fields"].items()) if field["status"] == "missing"]
    labels = [item["brandNames"] for item in metadata["candidateMetadata"] if item["brandNames"]["status"] == "explicit"]
    metadata["candidateMetadata"] = [item for item in metadata["candidateMetadata"]
                                     if item["candidateId"] == primary["candidateId"]]
    if labels:
        metadata["candidateMetadata"][0]["brandNames"] = {
            "value": list(dict.fromkeys(brand for label in labels for brand in label["value"])),
            "status": "explicit", "evidenceIds": list(dict.fromkeys(ref for label in labels for ref in label["evidenceIds"])),
        }
    return result, metadata
