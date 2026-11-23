"""Read the price mechanism for an already identified child-activity group."""

from decimal import Decimal
import re

from .grouping import _child_rows

VERSION = "child-workbook-rules/1.1"
HEADERS = ("子活动ID", "UPC", "报名价", "平台补贴", "活动价")
STORE_PRICE_EXPORT = re.compile(r"活动_[^/\\\n]*(神价|爆好价)[^/\\\n]*[（(][0-9]+[)）]_子活动清单(?:\s*\([0-9]+\))?\.xlsx", re.I)
METHOD_LABELS = {"shen_jia": "神价", "bao_hao_jia": "爆好价"}
AMOUNT = re.compile(r"[0-9]{1,12}(?:\.[0-9]{1,4})?")


def child_workbook_rule(documents, candidate, evidence, *, method=None, material_names=None):
    """Use source cells only when the recognized rows cover one export in full."""
    if len(documents) != 1 or not candidate["productRows"]:
        return None
    document, = documents
    shape = _child_rows(document)
    if shape is None:
        return None
    sheet, expected = shape
    if not expected:
        return None
    by_id = {item["evidenceId"]: item for item in evidence}
    rows = {row["locator"]["row"]: row for row in document["tableRows"]}
    header = rows.get(min(expected) - 1)
    if not header:
        return None
    cells = {}
    for key in HEADERS:
        found = [cell for cell in header["cells"] if cell["text"].strip() == key]
        if len(found) != 1:
            return None
        cells[key] = found[0]

    remaining = set(expected)
    observations = []
    for product in candidate["productRows"]:
        if product["matchKind"] == "conflict" or not product["rawUpc"]:
            return None
        refs = [by_id[eid] for eid in product["evidenceIds"]]
        upc_column = cells["UPC"]["column"]
        anchors = {ref["locator"]["row"] for ref in refs
                   if ref["materialId"] == document["materialId"] and ref["sourceSha256"] == document["sha256"]
                   and ref["locator"].get("sheet") == sheet
                   and ref["locator"].get("cell", "").rstrip("0123456789") == upc_column
                   and ref["quote"] == product["rawUpc"]}
        if len(anchors) != 1:
            return None
        position, = anchors
        if position not in remaining:
            return None
        remaining.remove(position)
        data = {cell["column"]: cell for cell in rows[position]["cells"]}
        price_cells = [data.get(cells[key]["column"]) for key in HEADERS[2:]]
        if not all(cell and cell["sourceType"] in ("string", "number")
                   and AMOUNT.fullmatch(cell["text"].strip()) for cell in price_cells):
            return None
        signup, subsidy, price = (Decimal(cell["text"].strip()) for cell in price_cells)
        observations.append((signup, subsidy, price, price_cells))

    if remaining:
        return None
    filename = (material_names or {}).get(document["materialId"], "")
    export = STORE_PRICE_EXPORT.fullmatch(filename)
    recognized_method = (method or {}).get("value") if (method or {}).get("status") == "explicit" else None
    label = METHOD_LABELS.get(recognized_method)
    if export and recognized_method is None and (method or {}).get("status") != "conflict":
        label = export[1]
    if export and label and export[1] != label:
        label = None

    discrepancies = [row for row in observations if row[0] - row[1] != row[2]]
    subsidized = [row for row in observations if row[1] > 0]
    references = [cells[key] for key in HEADERS[2:]]
    if discrepancies:
        summary = "按商品设置活动价。原表报名价、平台补贴和活动价有金额差异，待提报人核对。"
        references.extend(cell for row in discrepancies[:2] for cell in row[3])
    elif not subsidized:
        summary = ("按商品设置活动价，原表平台补贴均为 0 元，报名价与活动价一致"
                   + ("，活动让利由门店自行承担。" if label == "神价" else "。"))
        references.extend(row[3][1] for row in observations[:4])
    elif label:
        summary = f"{label}原表列有平台补贴，实际承担方待提报人核对。"
        references.extend(subsidized[0][3])
    else:
        summary = "按商品设置活动价，活动价为报名价扣除平台补贴。"
        references.extend(subsidized[0][3])
    return {"version": VERSION, "summary": summary, "status": "conflict" if discrepancies or (label and subsidized) else "explicit",
            "evidence": [{"materialId": document["materialId"], "sourceSha256": document["sha256"],
                          "locator": cell["locator"], "quote": cell["text"]} for cell in references]}
