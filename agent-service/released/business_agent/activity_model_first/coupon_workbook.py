"""Reconcile a coupon declaration and its UPC detail using explicit workbook evidence."""
from collections import defaultdict
from copy import deepcopy
from decimal import Decimal
import re
from unicodedata import normalize

VERSION = "coupon-workbook/1.0"
DECLARATION = {"品牌商名称", "活动时间", "活动力度", "品牌补贴", "美商补"}
DETAIL = {"活动力度", "upc", "商品名称", "商品码", "报名情况"}
STORE_HEADERS = {"美团门店id", "淘宝闪购id", "京东id", "门店名称", "城市"}
TIER = re.compile(r"(?:满\s*)?(\d{1,12}(?:\.\d{1,2})?)\s*(?:元\s*)?[-减]\s*(\d{1,12}(?:\.\d{1,2})?)(?:\s*元)?")
AMOUNT = re.compile(r"\d{1,12}(?:\.\d{1,2})?")
UPC = re.compile(r"[0-9]{8,14}")
MERCHANT = re.compile(r"(?:门店(?:承担|补贴|出资|出)?|商家补贴|美商补)\s*[:：]?\s*(\d+(?:\.\d{1,2})?)\s*元")
MISSING = {"value": None, "status": "missing", "evidenceIds": []}


def _text(cell):
    return cell["text"].strip()


def _label(cell):
    return normalize("NFKC", _text(cell)).casefold()


def _tier(raw):
    match = TIER.fullmatch(normalize("NFKC", raw))
    if not match:
        return None
    threshold, discount = map(Decimal, match.groups())
    return (threshold, discount) if 0 < discount < threshold else None


def _table(rows, required):
    if not rows:
        return None
    headers = defaultdict(list)
    for cell in rows[0]["cells"]:
        headers[_label(cell)].append(cell)
    if not all(len(headers[label]) == 1 for label in required):
        return None
    columns = {entries[0]["column"]: label for label, entries in headers.items() if len(entries) == 1}
    converted = []
    for row in rows[1:]:
        if any(_label(cell) in {"示例", "示例行", "example", "sample"} for cell in row["cells"]):
            continue
        cells = {columns[cell["column"]]: cell for cell in row["cells"] if cell["column"] in columns}
        converted.append((row["locator"]["row"], cells))
    return converted


def coupon_scope(payload):
    """Select only unambiguous one-coupon templates and identify ancillary store sheets."""
    declaration = detail = None
    excluded, notes = set(), []
    manual = payload.get("manualFields") or {}
    if any((manual.get("fields") or {}).get(key) for key in ("manualProductRows", "rulesText")):
        return None
    documents = payload["documents"]
    if sum(doc["kind"] == "xlsx" for doc in documents) != 1:
        return None
    for doc in documents:
        if doc["status"] != "parsed" or doc["errors"]:
            return None
        if doc["kind"] in ("txt", "md") and not doc["tableRows"]:
            notes.extend((doc, block) for block in doc["textBlocks"] if _text(block))
            continue
        if doc["kind"] != "xlsx" or doc["textBlocks"]:
            return None
        sheets = defaultdict(list)
        for row in doc["tableRows"]:
            if any(_text(cell) for cell in row["cells"]):
                sheets[row["locator"].get("sheet")].append(row)
        for sheet, rows in sheets.items():
            declared = _table(rows, DECLARATION)
            products = _table(rows, DETAIL)
            if declared is not None and len(declared) == 1 and declaration is None:
                declaration = doc, declared[0][1]
            elif products and detail is None:
                detail = sheet, products
            elif STORE_HEADERS <= {_label(cell) for cell in rows[0]["cells"]}:
                excluded.add((doc["materialId"], sheet))
            else:
                return None
    if not declaration or not detail:
        return None
    doc, fields = declaration
    if not DECLARATION <= fields.keys():
        return None
    tier = _tier(_text(fields["活动力度"]))
    brand = _text(fields["品牌商名称"])
    if not tier or not brand or any(fields[key]["sourceType"] == "formula" for key in DECLARATION):
        return None
    sheet, rows = detail
    if any(not {"活动力度", "upc", "商品名称"} <= cells.keys()
           or _tier(_text(cells["活动力度"])) != tier
           or not UPC.fullmatch(_text(cells["upc"]))
           or not _text(cells["商品名称"])
           or any(cell["sourceType"] == "formula" for cell in cells.values())
           for _, cells in rows):
        return None
    for _, block in notes:
        text = normalize("NFKC", _text(block))
        matches = list(TIER.finditer(text))
        if not matches or matches[0].start() != 0 or any(_tier(match[0]) != tier for match in matches):
            return None
    return doc, fields, sheet, rows, notes, excluded


def _field(value, refs, status="explicit"):
    return {"value": value, "status": status, "evidenceIds": list(dict.fromkeys(refs))}


def reconcile_coupon(payload, result, metadata, lookup=None):
    """Use cell-level evidence to rebuild one group while preserving the model receipt provenance."""
    scoped = coupon_scope(payload)
    if scoped is None or result.get("materialResults") != payload["documents"] or not result["candidates"]:
        return result, metadata
    identities = {(candidate["fields"]["platformCode"]["value"], candidate["fields"]["sourceActivityId"]["value"])
                  for candidate in result["candidates"] if candidate["fields"]["sourceActivityId"]["value"]}
    if len(identities) > 1:
        return result, metadata
    if lookup is None:
        from .source import plan
        _, lookup, _ = plan(payload)
    doc, summary, sheet, rows, notes, _ = scoped
    positions = {entry["locator"]["row"]: entry["rowId"] for entry in lookup.values()
                 if entry["materialId"] == doc["materialId"] and entry["locator"].get("sheet") == sheet
                 and "row" in entry["locator"]}
    if len(result["evidence"]) + 4 * len(rows) + len(notes) + 20 > 6000:
        return result, metadata
    if any(position not in positions for position, _ in rows):
        return result, metadata
    result = deepcopy(result)
    metadata = deepcopy(metadata)
    by_id = {e["evidenceId"]: e for e in result["evidence"]}
    evidence = {(e["materialId"], tuple(sorted(e["locator"].items())), e["quote"]): eid for eid, e in by_id.items()}
    next_id = len(by_id) + 1

    def cite(source, locator, quote):
        nonlocal next_id
        key = source["materialId"], tuple(sorted(locator.items())), quote
        if key not in evidence:
            while f"ev-{next_id}" in by_id:
                next_id += 1
            eid = f"ev-{next_id}"
            entry = {"evidenceId": eid, "materialId": source["materialId"], "sourceSha256": source["sha256"],
                     "locator": locator, "quote": quote}
            result["evidence"].append(entry)
            by_id[eid] = entry
            evidence[key] = eid
            next_id += 1
        return evidence[key]

    def cell_ref(cell):
        return cite(doc, cell["locator"], cell["text"])

    selected = result["candidates"][0]
    result["conflicts"] = []
    result["warnings"] = [warning for warning in result["warnings"] if warning["code"] != "COUPON_SUBSIDY_CONFLICT"]
    fields = {name: dict(MISSING) for name in selected["fields"]}
    manual = payload.get("manualFields") or {}
    form = manual.get("fields", {})
    name = (form.get("activityName") or "").strip()
    if name and len(name) <= 200:
        fields["activityName"] = _field(name, [cite(manual, {"kind":"form", "fieldPath":"activityName"}, name)])
    if form.get("type") in {"platform", "brand", "jiatan_sourcing"}:
        fields["type"] = _field(form["type"], [cite(manual, {"kind":"form", "fieldPath":"type"}, form["type"])])
    fields["platformCode"] = dict(selected["fields"]["platformCode"])
    for field in ("sourceActivityId", "startAt", "endAt", "remark", "brandBenefit"):
        value = form.get(field)
        if isinstance(value, str) and value.strip():
            fields[field] = _field(value, [cite(manual, {"kind":"form", "fieldPath":field}, value[:500])])
    if form.get("brandIds"):
        field = form["brandIds"]
        from business_agent.workbench_deepseek.adapter import encode
        fields["brandIds"] = _field(field, [cite(manual, {"kind":"form", "fieldPath":"brandIds"}, encode(field).decode()[:500])])
    fields["companyBearing"] = _field({"state": "unknown", "evidenceIds": []}, [], "missing")

    threshold, discount = _tier(_text(summary["活动力度"]))
    threshold_text, discount_text = (format(value.normalize(), "f") for value in (threshold, discount))
    rule = f"满{threshold_text}减{discount_text}元"
    brand = _text(summary["品牌商名称"])
    rules_refs = [cell_ref(summary["活动力度"])]
    brand_ref = cell_ref(summary["品牌商名称"])
    amounts = {}
    for field in ("品牌补贴", "美商补"):
        raw = _text(summary[field])
        if AMOUNT.fullmatch(raw):
            amounts[field] = Decimal(raw)
            rule += f"，{field}{raw}元"
            rules_refs.append(cell_ref(summary[field]))
    note_values, merchant_values = [], set()
    for note_doc, block in notes:
        raw = _text(block)
        note_values.append(raw)
        rules_refs.append(cite(note_doc, block["locator"], block["text"]))
        merchant_values.update(Decimal(m) for m in MERCHANT.findall(normalize("NFKC", raw)))
    has_conflict = len(merchant_values) > 1 or (
        bool(merchant_values) and "美商补" in amounts and merchant_values != {amounts["美商补"]}
    )
    note_text = "；".join(note_values)
    fields["rulesText"] = _field(rule if has_conflict else "；".join(filter(None, [rule, note_text])),
                                 rules_refs, "conflict" if has_conflict else "explicit")
    if has_conflict:
        result["conflicts"].append({"field":"rulesText", "manualValue":note_text,
                                    "extractedValues":[rule], "evidenceIds":rules_refs})
        result["warnings"].append({"code":"COUPON_SUBSIDY_CONFLICT",
            "message":"补贴金额待确认。原表美商补与提报文字的门店承担金额不一致。",
            "locator":summary["美商补"]["locator"]})
    # Treat the declaration's month/day text as evidence without inventing the missing year.
    period_ref = cell_ref(summary["活动时间"])
    for key in ("startAt", "endAt"):
        if fields[key]["status"] == "missing" and _text(summary["活动时间"]):
            fields[key] = _field(None, [period_ref], "missing")
    if fields["activityName"]["status"] == "missing":
        fields["activityName"] = _field(None, [brand_ref], "missing")

    products = []
    for index, (position, cells) in enumerate(rows, 1):
        internal_code = cells.get("商品码")
        row_fields = {"rawUpc": _text(cells["upc"]), "rawName": _text(cells["商品名称"]),
                      "rawInternalCode": (_text(internal_code) or None) if internal_code else None,
                      "rawSpec": None}
        refs = [cell_ref(cells[key]) for key in ("upc", "商品名称", "商品码", "报名情况")
                if key in cells and _text(cells[key])]
        products.append({"rowId":f"row-{index}", **row_fields, "matchKind":"unmatched", "matchedProductId":None,
                         "candidates":[], "evidenceIds":refs, "sourceRowIds":[positions[position]]})
    result["candidates"] = [{"candidateId":selected["candidateId"], "fields":fields, "productRows":products}]
    result["missingFields"] = [{"field":key, "reasonCode":"NOT_PROVIDED"}
                               for key, field in fields.items() if field["status"] == "missing"]
    result["warnings"] = [w for w in result["warnings"]
                          if not (name and w.get("code") == "MODEL_FIELD_EVIDENCE_UNSUPPORTED"
                                  and w.get("locator") == {"fieldPath":"activityName", "kind":"form"})]
    result["outcome"] = "needs_confirmation"
    metadata["coverage"]["productSourceRows"] = len(products)
    metadata["candidateMetadata"] = [{"candidateId":selected["candidateId"],
        "activityMethod":dict(MISSING),
        "brandNames":_field([brand], [brand_ref])}]
    return result, metadata
