"""Extract row-bound workbook prices from validated model-first analysis."""

from collections import Counter, defaultdict
from decimal import Decimal, localcontext
import re

from business_agent.activity_model_first.grouping import _child_rows

from .evidence import (
    CONTRACT, GENERIC_HEADERS, PRICE_HEADER, SourceRow, _columns, _decimal, _index,
    _key, _label, _located, _observe, _pair, _unit,
)

SIGNUP_HEADER = re.compile(PRICE_HEADER.pattern.replace("活动价", "报名价", 1))
EXCEL_CHILD_ID = re.compile(r"[0-9]{1,15}(?:\.[0-9]{1,15})?(?:E\+?[0-9]{1,2})?", re.I)


def _child_identifier(cell):
    if cell is None or cell["sourceType"] not in ("number", "string"):
        return None
    raw = cell["text"].strip()
    if re.fullmatch(r"[0-9]{1,15}", raw):
        return raw
    if cell["sourceType"] != "number" or not EXCEL_CHILD_ID.fullmatch(raw):
        return None
    value = Decimal(raw)
    if 0 < value < 10**15 and value == value.to_integral_value():
        return format(value.to_integral_value(), "f")
    return None


def _child_detail_rows(document):
    shape = _child_rows(document)
    if shape is None:
        return {}
    sheet, positions = shape
    if not positions:
        return {}
    rows = {row["locator"]["row"]: row for row in document["tableRows"]}
    header = _columns(rows.get(min(positions) - 1, {}).get("cells", []))
    if not header or header[0] != "detail":
        return {}
    headers = defaultdict(list)
    for cell in header[1]:
        label = _label(cell)
        if PRICE_HEADER.fullmatch(label):
            label = "活动价"
        elif SIGNUP_HEADER.fullmatch(label):
            label = "报名价"
        headers[label].append(cell)
    required = ("子活动ID", "UPC", "报名价", "平台补贴", "活动价")
    if any(len(headers[key]) != 1 for key in required):
        return {}
    child_header, upc_header, _, subsidy_header, price_header = (headers[key][0] for key in required)
    result = {}
    for position in positions:
        row = rows[position]
        cells = {cell["column"]: cell for cell in row["cells"]}
        child = cells.get(child_header["column"])
        child_id = _child_identifier(child)
        if child_id is None:
            continue
        grouped = headers["是否组包"]
        order_quantity = headers["每单起购件数"]
        source = SourceRow("detail", row["locator"], child, cells.get(upc_header["column"]),
                           [price_header], [cells.get(price_header["column"])],
                           cells.get(order_quantity[0]["column"]) if len(order_quantity) == 1 else None,
                           cells.get(grouped[0]["column"]) if len(grouped) == 1 else None, None)
        result[sheet, position] = (child_id, source, cells.get(subsidy_header["column"]))
    counts = Counter(child_id for child_id, _, _ in result.values())
    return {position: row for position, row in result.items() if counts[row[0]] == 1}


def _child_detail_price(price, row, refs, material, detail_rows, signup_sources):
    raw_upc = row["rawUpc"]
    if row["matchKind"] == "conflict" or not isinstance(raw_upc, str) or not re.fullmatch(r"[0-9]{8,14}", raw_upc):
        return price
    source_refs = {(ref["materialId"], ref["sourceSha256"], *_key(ref["locator"]), ref["quote"])
                   for ref in refs if ref["locator"].get("kind") == "xlsx"}
    locations = {ref[:4] for ref in source_refs}
    if len(locations) != 1:
        return price
    material_id, material_sha, sheet, position = locations.pop()
    if (material_id, material_sha) != material:
        return price
    bound = detail_rows.get((sheet, position))
    if not bound:
        return price
    child_id, source, subsidy_cell = bound

    def cited(cell):
        return cell is not None and (*material, *_key(cell["locator"]), cell["text"]) in source_refs

    if not cited(source.child) or not cited(source.upc) or source.upc["text"] != raw_upc:
        return price
    observation = _observe(source, material)
    observation["platformSubsidyCell"] = _located(subsidy_cell)
    price_cell = observation["priceCells"][0]
    signup = _signup_price({"status": "needs_review", "observations": [observation]}, signup_sources)
    subsidy, subsidy_issue = _decimal(subsidy_cell)
    issues = list(signup["issues"])
    if price_cell["issue"]:
        issues.append(price_cell["issue"])
    if subsidy_issue:
        issues.append(subsidy_issue.replace("ACTIVITY_PRICE", "PLATFORM_SUBSIDY"))
    amount = price_cell["amountDecimal"]
    if not issues:
        with localcontext() as context:
            context.prec = 41
            if Decimal(signup["amountDecimal"]) - Decimal(subsidy) != Decimal(amount):
                issues.append("SHENJIA_PRICE_CONFLICT")
    currency, quantity, unit_issues = _unit(source)
    unit_conflict = any(issue.endswith("_CONFLICT") for issue in unit_issues)
    if "SHENJIA_PRICE_CONFLICT" in issues or unit_conflict:
        status = "conflict"
    elif any(issue.startswith("FORMULA_") for issue in issues):
        status = "formula"
    elif any(issue.startswith("INVALID_") for issue in issues):
        status = "invalid_amount"
    elif issues:
        status = "missing"
    else:
        status = "needs_review"
    return price | {"childActivityId": child_id, "status": status,
                    "amountDecimal": amount if status == "needs_review" else None,
                    "currency": currency, "quantityBasis": quantity,
                    "unitStatus": "conflict" if unit_conflict else "unknown" if unit_issues else "known",
                    "issues": issues + unit_issues, "observations": [observation]}


def _signup_cells(document):
    current = {}
    located = {}
    for row in sorted(document["tableRows"], key=lambda item: _key(item["locator"])[:2]):
        sheet, position, _ = _key(row["locator"])
        if header := _columns(row["cells"]):
            current[sheet] = [cell for cell in header[1] if SIGNUP_HEADER.fullmatch(_label(cell))]
            continue
        if len({_label(cell) for cell in row["cells"]} & GENERIC_HEADERS) >= 2:
            current.pop(sheet, None)
        if sheet in current:
            data = {cell["column"]: cell for cell in row["cells"]}
            located[sheet, position] = [(header, data.get(header["column"])) for header in current[sheet]]
    return located


def _signup_price(price, sources):
    if price["status"] == "unbound":
        return {"status": "unbound", "amountDecimal": None, "issues": price["issues"], "observations": []}

    observations, issues, amounts = [], [], []
    for activity in price["observations"]:
        material = activity["materialId"], activity["sourceSha256"]
        sheet, position, _ = _key(activity["rowLocator"])
        cells = sources[material].get((sheet, position), [])
        if len(cells) != 1:
            issues.append("AMBIGUOUS_SIGNUP_PRICE_HEADER" if cells else "MISSING_SIGNUP_PRICE_HEADER")
        for header, cell in cells:
            amount, issue = _decimal(cell)
            if issue:
                issues.append(issue.replace("ACTIVITY_PRICE", "SIGNUP_PRICE"))
            if amount is not None:
                amounts.append(amount)
            observations.append({"role": activity["role"], "materialId": material[0], "sourceSha256": material[1],
                                 "header": _located(header), "source": _located(cell), "amountDecimal": amount})
    if len(set(amounts)) > 1:
        issues.append("SIGNUP_PRICE_MISMATCH")
    if {"AMBIGUOUS_SIGNUP_PRICE_HEADER", "SIGNUP_PRICE_MISMATCH"}.intersection(issues):
        status = "conflict"
    elif "FORMULA_SIGNUP_PRICE" in issues:
        status = "formula"
    elif "INVALID_SIGNUP_PRICE" in issues:
        status = "invalid_amount"
    elif issues or not amounts:
        status = "missing"
    else:
        status = "needs_review"
    return {"status": status, "amountDecimal": amounts[0] if status == "needs_review" else None,
            "issues": list(dict.fromkeys(issues)), "observations": observations}


def extract_model_price_evidence(documents, analysis):
    if (analysis["contractVersion"] != "activity-intake/1.0" or analysis["engineVersion"] not in ("2.0.0", "2.1.0")
            or analysis.get("engine") != "deepseek_model_first_v1" or analysis.get("modelUsed") is not True):
        raise ValueError("unsupported model activity analysis version")
    material_results = {(doc["materialId"], doc["sha256"]): doc for doc in analysis["materialResults"]}
    if len(material_results) != len(analysis["materialResults"]):
        raise ValueError("duplicate analyzed document")
    sources, signup_sources, seen = {}, {}, set()
    child_details = {}
    for document in documents:
        key = document["materialId"], document["sha256"]
        if key in seen or material_results.get(key) != document:
            raise ValueError("decoded document does not match analysis")
        seen.add(key)
        if document["kind"] == "xlsx" and document["status"] == "parsed" and not document["errors"]:
            sources[key] = _index(document, allow_single=True)
            signup_sources[key] = _signup_cells(document)
            if len(documents) == len(analysis["candidates"]) == 1:
                child_details = _child_detail_rows(document)
    evidence = {ref["evidenceId"]: ref for ref in analysis["evidence"]}
    rows = []
    for candidate in analysis["candidates"]:
        parent = candidate["fields"].get("sourceActivityId", {})
        source_activity = parent.get("value") if parent.get("status") == "explicit" else None
        for row in candidate["productRows"]:
            refs = [evidence[eid] for eid in row["evidenceIds"]]
            if price := _pair(candidate["candidateId"], row, refs, sources, source_activity):
                if child_details and price["status"] == "unbound":
                    price = _child_detail_price(price, row, refs, next(iter(sources)), child_details, signup_sources)
                rows.append(price | {"signupPrice": _signup_price(price, signup_sources)})
    return {"contractVersion": CONTRACT, "inputDigest": analysis["inputDigest"],
            "inputRevision": analysis["inputRevision"], "rows": rows}
