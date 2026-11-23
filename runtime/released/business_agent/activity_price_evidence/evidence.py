"""Read attachment activity prices from the product rows already bound by Worker 1.0.7."""
from collections import Counter, defaultdict
from dataclasses import dataclass
from decimal import Decimal
import re
from unicodedata import normalize


CONTRACT = "activity-price-evidence/1.0"
SUMMARY = ("神价ID", "神价名称", "活动周期", "子活动ID", "神价商品", "神价UPC")
DETAIL = {"子活动ID", "子活动名称", "UPC", "UPC对应商品名称"}
PRICE_HEADER = re.compile(r"活动价(?:\((?:(CNY|RMB|人民币|元|USD|美元|HKD|港元)(?:/(件|组))?|/(件|组))\))?")
IDENTIFIER = re.compile(r"[0-9]+")
PLAIN_DECIMAL = re.compile(r"[0-9]{1,24}(?:\.[0-9]{1,16})?")
CURRENCIES = dict.fromkeys(("CNY", "RMB", "人民币", "元"), "CNY") | {
    "USD": "USD", "美元": "USD", "HKD": "HKD", "港元": "HKD"}
QUANTITY = {"件": "single_item", "组": "bundle"}
ABSENT = {"", "-", "—"}
GENERIC_HEADERS = {"活动名称", "活动编号", "商品名称", "内部码", "UPC", "条码"}


@dataclass
class SourceRow:
    role: str
    locator: dict
    child: dict
    upc: dict | None
    price_headers: list
    prices: list
    quantity: dict | None
    grouped: dict | None


def _located(cell):
    if cell is None:
        return None
    return {"locator": cell["locator"], "rawText": cell["text"], "sourceType": cell["sourceType"]}


def _key(locator):
    return locator.get("sheet"), locator.get("row"), locator.get("cell")


def _label(cell):
    if cell["sourceType"] != "string":
        return ""
    return "".join(normalize("NFKC", cell["text"]).upper().split())


def _columns(cells):
    labels = tuple(_label(cell) for cell in cells)
    for start in range(len(labels) - len(SUMMARY) + 1):
        end = start + len(SUMMARY)
        if labels[start:end] == SUMMARY:
            # The helper block also has a 子活动ID; keep the first header's columns.
            stop = labels.index("子活动ID", end) if "子活动ID" in labels[end:] else len(labels)
            return "summary", cells[start:stop]
    if all(labels.count(label) == 1 for label in DETAIL):
        return "detail", cells
    return None


def _index(document):
    current = {}
    recognized = set()
    children = defaultdict(list)
    by_anchor = {}
    for row in sorted(document["tableRows"], key=lambda item: _key(item["locator"])[:2]):
        sheet = row["locator"]["sheet"]
        if header := _columns(row["cells"]):
            current[sheet] = header
            recognized.add(sheet)
            continue
        if len({_label(cell) for cell in row["cells"]} & GENERIC_HEADERS) >= 2:
            current.pop(sheet, None)
        if sheet not in current:
            continue
        role, headers = current[sheet]
        data = {cell["column"]: cell for cell in row["cells"]}
        labels = {_label(cell): cell["column"] for cell in headers}
        child = data.get(labels["子活动ID"])
        if (child is None or child["sourceType"] not in ("string", "number")
                or not IDENTIFIER.fullmatch(child["text"].strip())):
            continue
        upc = data.get(labels["神价UPC" if role == "summary" else "UPC"])
        price_headers = [cell for cell in headers if PRICE_HEADER.fullmatch(_label(cell))]
        source = SourceRow(role, row["locator"], child, upc, price_headers,
                           [data.get(cell["column"]) for cell in price_headers],
                           data.get(labels.get("购买数量")), data.get(labels.get("是否组包")))
        children[child["text"].strip()].append(source)
        by_anchor[_key(child["locator"])] = source
    return children, by_anchor, recognized


def _decimal(cell):
    raw = cell["text"].strip() if cell is not None else ""
    if cell is not None and (cell["sourceType"] == "formula" or raw.startswith("=")):
        return None, "FORMULA_ACTIVITY_PRICE"
    if raw in ABSENT:
        return None, "MISSING_ACTIVITY_PRICE"
    if cell["sourceType"] not in ("string", "number") or not PLAIN_DECIMAL.fullmatch(raw):
        return None, "INVALID_ACTIVITY_PRICE"
    amount = format(Decimal(raw), "f")
    return (amount.rstrip("0").rstrip(".") if "." in amount else amount), None


def _observe(row, material):
    amounts = [_decimal(cell) for cell in row.prices]
    return {"role": row.role, "materialId": material[0], "sourceSha256": material[1],
            "rowLocator": row.locator, "childIdCell": _located(row.child), "upcCell": _located(row.upc),
            "priceHeaders": [_located(cell) for cell in row.price_headers],
            "priceCells": [{"source": _located(cell), "amountDecimal": amount, "issue": issue}
                           for cell, (amount, issue) in zip(row.prices, amounts)],
            "purchaseQuantityCell": _located(row.quantity), "groupedCell": _located(row.grouped)}


def _text(cell, allowed_types):
    return cell["text"].strip() if cell and cell["sourceType"] in allowed_types else None


def _unit(summary, detail):
    currencies, bases = set(), set()
    for row in (summary, detail):
        for header in row.price_headers:
            currency, per, alternate_per = PRICE_HEADER.fullmatch(_label(header)).groups()
            if currency:
                currencies.add(CURRENCIES[currency])
            if per or alternate_per:
                bases.add(QUANTITY[per or alternate_per])
    grouped = _text(detail.grouped, ("string",))
    quantity_text = _text(summary.quantity, ("string", "number")) or ""
    if grouped == "否" and re.fullmatch(r"0*1(?:\.0+)?", quantity_text):
        bases.add("single_item")
    issues = []
    if len(currencies) > 1:
        issues.append("CURRENCY_CONFLICT")
    elif not currencies:
        issues.append("CURRENCY_UNSPECIFIED")
    if len(bases) > 1 or bases == {"bundle"} and grouped == "否":
        issues.append("QUANTITY_BASIS_CONFLICT")
        bases.clear()
    elif not bases:
        issues.append("QUANTITY_BASIS_UNSPECIFIED")
    return (next(iter(currencies)) if len(currencies) == 1 else None,
            next(iter(bases)) if len(bases) == 1 else None, issues)


def _pair(candidate, row, refs, sources):
    relevant = defaultdict(list)
    for ref in refs:
        source_key = ref["materialId"], ref["sourceSha256"]
        if source_key in sources and ref["locator"].get("sheet") in sources[source_key][2]:
            relevant[source_key].append(ref)
    if not relevant:
        return None
    result = {"candidateId": candidate, "rowId": row["rowId"], "childActivityId": None,
              "status": "unbound", "amountDecimal": None, "currency": None,
              "quantityBasis": None, "unitStatus": "unknown",
              "issues": ["CHILD_ACTIVITY_BINDING_UNAVAILABLE"], "observations": []}
    if len(relevant) != 1:
        return result
    material, located_refs = next(iter(relevant.items()))
    children, anchors, _ = sources[material]
    references = {_key(ref["locator"]): (ref["locator"], ref["quote"]) for ref in located_refs}

    def cited(cell):
        return cell and references.get(_key(cell["locator"])) == (cell["locator"], cell["text"][:500])

    tied = [anchors[key] for key in references if key in anchors and cited(anchors[key].child)]
    child_ids = {source.child["text"].strip() for source in tied}
    if len(child_ids) != 1:
        return result
    child_id = next(iter(child_ids))
    result["childActivityId"] = child_id
    result["observations"] = [_observe(source, material) for source in tied]
    if Counter(source.role for source in children[child_id]) != {"summary": 1, "detail": 1}:
        result["issues"] = ["CHILD_ACTIVITY_PAIR_MISSING_OR_AMBIGUOUS"]
        return result
    by_role = {source.role: source for source in tied}
    if set(by_role) != {"summary", "detail"}:
        return result
    summary, detail = by_role["summary"], by_role["detail"]
    raw_upc = row["rawUpc"]
    if (not isinstance(raw_upc, str) or not re.fullmatch(r"[0-9]{8,14}", raw_upc.strip())
            or any(not cited(source.upc) for source in (summary, detail))
            or _text(summary.upc, ("string", "number")) != raw_upc.strip()
            or _text(detail.upc, ("string",)) != raw_upc.strip()):
        return result
    result["observations"] = [_observe(source, material) for source in (summary, detail)]
    currency, quantity, unit_issues = _unit(summary, detail)
    unit_status = ("conflict" if any(issue.endswith("_CONFLICT") for issue in unit_issues)
                   else "unknown" if unit_issues else "known")
    issues = [item["issue"] for observation in result["observations"]
              for item in observation["priceCells"] if item["issue"]]
    if any(not source.price_headers for source in (summary, detail)):
        issues.append("MISSING_ACTIVITY_PRICE_HEADER")
    if any(len(source.price_headers) > 1 for source in (summary, detail)):
        issues.append("AMBIGUOUS_ACTIVITY_PRICE_HEADER")
        status = "conflict"
    elif "FORMULA_ACTIVITY_PRICE" in issues:
        status = "formula"
    elif "INVALID_ACTIVITY_PRICE" in issues:
        status = "invalid_amount"
    elif issues:
        status = "missing"
    else:
        first, second = [observation["priceCells"][0]["amountDecimal"] for observation in result["observations"]]
        status = "needs_review" if first == second else "conflict"
        if status == "needs_review" and unit_status == "conflict":
            status = "conflict"
        if first != second:
            issues.append("ACTIVITY_PRICE_MISMATCH")
        if status == "needs_review":
            result["amountDecimal"] = first
    return result | {"status": status, "currency": currency, "quantityBasis": quantity,
                     "unitStatus": unit_status, "issues": list(dict.fromkeys([*issues, *unit_issues]))}


def extract_price_evidence(documents, analysis):
    """Return read-only activity price evidence for matched worksheet rows."""
    if analysis["contractVersion"] != "activity-intake/1.0" or analysis["engineVersion"] != "1.0.7":
        raise ValueError("unsupported activity analysis version")
    material_results = {(doc["materialId"], doc["sha256"]): doc for doc in analysis["materialResults"]}
    if len(material_results) != len(analysis["materialResults"]):
        raise ValueError("duplicate analyzed document")
    sources = {}
    seen = set()
    for document in documents:
        key = document["materialId"], document["sha256"]
        if key in seen or material_results.get(key) != document:
            raise ValueError("decoded document does not match analysis")
        seen.add(key)
        if document["kind"] == "xlsx" and document["status"] == "parsed" and not document["errors"]:
            sources[key] = _index(document)
    evidence = {ref["evidenceId"]: ref for ref in analysis["evidence"]}
    rows = []
    for candidate in analysis["candidates"]:
        for row in candidate["productRows"]:
            refs = [evidence[ref] for ref in row["evidenceIds"]]
            if price := _pair(candidate["candidateId"], row, refs, sources):
                rows.append(price)
    return {"contractVersion": CONTRACT, "inputDigest": analysis["inputDigest"],
            "inputRevision": analysis["inputRevision"], "rows": rows}
