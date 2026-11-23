"""Keep child-activity workbook rows with their recognized Shen Jia activity."""

import re

from business_agent.activity_primary_recognition.evidence import EXPLICIT_MAIN_ID, MAIN_LABEL

SHEN_JIA_TITLE = re.compile(r"[^\n]*神价[^\n]*[（(]([0-9]{8})[)）]$")
CHILD_EXPORT_NAME = re.compile(r"([^/\\\n]+[)）])_子活动清单(?:\s*\([0-9]+\))?\.xlsx", re.I)
MAIN_ID_HEADER = re.compile(MAIN_LABEL, re.I)
CHILD_ID_CELL = re.compile(r"[0-9]+(?:\.[0-9]+)?(?:[Ee]\+?[0-9]{1,2})?")


def _child_rows(document):
    """Require a single worksheet with a child-activity header and no main-ID column."""
    rows = document["tableRows"]
    if document["kind"] != "xlsx" or document["textBlocks"] or not rows:
        return None
    sheets = {row["locator"].get("sheet") for row in rows}
    if len(sheets) != 1 or None in sheets:
        return None
    child_column = None
    data_rows = set()
    for row in rows:
        cells = {cell["column"]: cell["text"].strip() for cell in row["cells"]}
        if any(MAIN_ID_HEADER.fullmatch(value) for value in cells.values()):
            return None
        headers = [column for column, value in cells.items() if value == "子活动ID"]
        if headers:
            if data_rows or len(headers) != 1 or "子活动名称" not in cells.values():
                return None
            if child_column not in (None, headers[0]):
                return None
            child_column = headers[0]
            continue
        position = row["locator"]["row"]
        if child_column is None or not CHILD_ID_CELL.fullmatch(cells.get(child_column, "")) or position in data_rows:
            return None
        data_rows.add(position)
    return next(iter(sheets)), data_rows


def _legacy_merge(candidates, metadata, documents, evidence, manual_fields, *, material_names=None):
    """Apply the submitted single-activity identity to an otherwise unassigned group."""
    if len(candidates) != 2 or len(documents) != 1 or not manual_fields:
        return candidates, metadata
    form = manual_fields["fields"]
    title = form.get("activityName")
    if not isinstance(title, str):
        return candidates, metadata
    document, = documents
    match = SHEN_JIA_TITLE.fullmatch(title)
    if match is None:
        export = CHILD_EXPORT_NAME.fullmatch((material_names or {}).get(document["materialId"], ""))
        # The full export title corroborates a missing final bracket in the submitted title.
        if export and export[1][:-1] == title:
            match = SHEN_JIA_TITLE.fullmatch(export[1])
    if not match or form.get("platformCode") not in (None, "meituan"):
        return candidates, metadata
    main_id = match[1]
    if form.get("sourceActivityId") not in (None, main_id):
        return candidates, metadata

    metadata_by_id = {item["candidateId"]: item for item in metadata}
    identified, unassigned = sorted(candidates, key=lambda c: c["fields"]["activityName"]["status"] == "missing")
    fields = identified["fields"]
    method = metadata_by_id[identified["candidateId"]]["activityMethod"]
    other_metadata = metadata_by_id[unassigned["candidateId"]]
    if (fields["activityName"]["status"] != "explicit" or fields["activityName"]["value"] != title
            or method["status"] != "explicit" or method["value"] != "shen_jia"
            or fields["type"]["status"] != "explicit" or fields["type"]["value"] != "platform"
            or fields["platformCode"]["status"] == "conflict" or fields["platformCode"]["value"] not in (None, "meituan")
            or fields["sourceActivityId"]["status"] == "conflict" or fields["sourceActivityId"]["value"] not in (None, main_id)
            or any(field["status"] != "missing" for field in unassigned["fields"].values())
            or any(other_metadata[key]["status"] != "missing" for key in ("activityMethod", "brandNames"))):
        return candidates, metadata

    by_id = {ref["evidenceId"]: ref for ref in evidence}
    def title_quotes(field):
        for eid in field["evidenceIds"]:
            ref = by_id[eid]
            if (ref["materialId"] == manual_fields["materialId"] and ref["sourceSha256"] == manual_fields["sha256"]
                    and ref["locator"] == {"kind": "form", "fieldPath": "activityName"}):
                yield ref["quote"]

    if title not in title_quotes(fields["activityName"]) or not any(
            "神价" in quote and quote in title for quote in title_quotes(method)):
        return candidates, metadata

    child_rows = _child_rows(document)
    if child_rows is None:
        return candidates, metadata
    sheet, expected_rows = child_rows
    products = _ordered_products(identified["productRows"] + unassigned["productRows"], by_id, document, sheet, expected_rows)
    if products is None:
        return candidates, metadata
    return [identified | {"productRows": products}], [metadata_by_id[identified["candidateId"]]]


def _ordered_products(products, by_id, document, sheet, expected_rows):
    positions, row_ids, source_ids = {}, set(), set()
    row_sources = {row["locator"]["row"]: f"r{index}" for index, row in enumerate(document["tableRows"], 1)}
    for row in products:
        refs = [by_id[eid] for eid in row["evidenceIds"]]
        if not refs or any(ref["materialId"] != document["materialId"] or ref["sourceSha256"] != document["sha256"]
                           or ref["locator"].get("sheet") != sheet or ref["locator"]["kind"] != "xlsx" for ref in refs):
            return None
        coordinates = {ref["locator"]["row"] for ref in refs}
        if len(coordinates) != 1 or len(row["sourceRowIds"]) != 1:
            return None
        position, = coordinates
        source_id, = row["sourceRowIds"]
        if (position in positions or row["rowId"] in row_ids or source_id in source_ids
                or row_sources.get(position) != source_id):
            return None
        positions[position] = row
        row_ids.add(row["rowId"])
        source_ids.add(source_id)
    if positions.keys() != expected_rows:
        return None
    return [positions[position] for position in sorted(positions)]


def confirmed_child_export(documents, manual_fields, material_names):
    """Prove a single parent from the submitted title and the same export's original name."""
    if len(documents) != 1 or not manual_fields or not material_names:
        return None
    doc, = documents
    if set(material_names) != {doc["materialId"]} or doc.get("status") != "parsed" or doc.get("errors"):
        return None
    fields = manual_fields["fields"]
    title = fields.get("activityName")
    if not isinstance(title, str) or not 0 < len(title) <= 500:
        return None
    export = CHILD_EXPORT_NAME.fullmatch(material_names.get(doc["materialId"], ""))
    if not export or title not in (export[1], export[1][:-1]):
        return None
    parent = SHEN_JIA_TITLE.fullmatch(export[1])
    if not parent or re.findall(r"(?<!\d)\d{8}(?!\d)", export[1]) != [parent[1]]:
        return None
    if fields.get("sourceActivityId") not in (None, "", parent[1]):
        return None
    if fields.get("platformCode") not in (None, "meituan") or fields.get("applicablePlatformCodes") not in (None, [], ["meituan"]):
        return None
    if fields.get("activityMethod") not in (None, "shen_jia"):
        return None
    shape = _child_rows(doc)
    if not shape or not shape[1]:
        return None
    sheet, positions = shape
    first_row = min(positions)
    headers = [row for row in doc["tableRows"] if row["locator"]["row"] < first_row]
    identity_header = re.compile(rf"(?:{MAIN_LABEL}|主活动号|主活动名称|神价活动(?:ID|编号)|直营活动(?:ID|编号))", re.I)
    if any(identity_header.fullmatch(re.sub(r"\s+", "", cell["text"]).rstrip("：:"))
           for row in headers for cell in row["cells"]):
        return None
    if any(match[1] != parent[1] for row in doc["tableRows"] for cell in row["cells"]
           for match in EXPLICIT_MAIN_ID.finditer(cell["text"])):
        return None
    child_names = {c["column"] for row in headers for c in row["cells"] if c["text"].strip() == "子活动名称"}
    if len(child_names) != 1:
        return None
    return {"sourceActivityId": parent[1], "activityName": title, "activityMethod": "shen_jia",
            "materialId": doc["materialId"], "sourceSha256": doc["sha256"], "sheet": sheet,
            "childNameColumn": next(iter(child_names)), "positions": sorted(positions),
            "titleEvidence": {"materialId": manual_fields["materialId"], "sourceSha256": manual_fields["sha256"],
                              "locator": {"kind": "form", "fieldPath": "activityName"}, "quote": title}}


def _from_child_name(field, by_id, source, child_cells):
    if field["status"] == "missing":
        return True
    if field["status"] != "explicit" or not field["evidenceIds"]:
        return False
    for eid in field["evidenceIds"]:
        ref = by_id[eid]
        if not (ref["materialId"] == source["materialId"] and ref["sourceSha256"] == source["sourceSha256"]
                and ref["locator"].get("sheet") == source["sheet"]
                and ref["locator"].get("kind") == "xlsx"
                and child_cells.get((ref["locator"].get("row"), ref["locator"].get("cell"))) == field["value"]
                and ref["quote"] == field["value"]):
            return False
    return True


def _combine(fields, *, union=False):
    """Preserve compatible values and all their evidence. Abstain on competing values."""
    known = [f for f in fields if f["status"] != "missing"]
    if not known:
        return fields[0]
    if any(f["status"] != "explicit" for f in known):
        return None
    value = known[0]["value"]
    if union:
        value = list(dict.fromkeys(name for f in known for name in f["value"]))
    elif any(f["value"] != value for f in known[1:]):
        return None
    return {"status": "explicit", "value": value,
            "evidenceIds": list(dict.fromkeys(eid for f in known for eid in f["evidenceIds"]))}


def merge_shen_jia_child_activity_rows(candidates, metadata, documents, evidence, manual_fields, *, material_names=None):
    if not material_names:
        return _legacy_merge(candidates, metadata, documents, evidence, manual_fields)
    source = confirmed_child_export(documents, manual_fields, material_names)
    if not source or not candidates:
        return candidates, metadata
    doc, = documents
    by_id = {ref["evidenceId"]: ref for ref in evidence}
    child_cells = {(r["locator"]["row"], c["locator"]["cell"]): c["text"].strip()
                   for r in doc["tableRows"] for c in r["cells"] if c["column"] == source["childNameColumn"]}
    title = source["activityName"]
    anchor = candidates[0]
    for c in candidates:
        name = c["fields"]["activityName"]
        if name["value"] == title and name["status"] == "explicit":
            anchor = c
        elif not _from_child_name(name, by_id, source, child_cells):
            return candidates, metadata
    rows = _ordered_products((p for c in candidates for p in c["productRows"]), by_id, doc,
                             source["sheet"], set(source["positions"]))
    if rows is None:
        return candidates, metadata

    fields = {}
    for key in anchor["fields"]:
        if key == "activityName":
            continue
        field = _combine([c["fields"][key] for c in candidates], union=key == "brandIds")
        if field is None:
            return candidates, metadata
        fields[key] = field
    if fields["sourceActivityId"]["value"] not in (None, source["sourceActivityId"]) or fields["platformCode"]["value"] not in (None, "meituan"):
        return candidates, metadata
    if any(m["activityMethod"]["status"] == "conflict" or m["activityMethod"]["value"] not in (None, "shen_jia") for m in metadata):
        return candidates, metadata
    brands = _combine([m["brandNames"] for m in metadata], union=True)
    if brands is None:
        return candidates, metadata

    title_source = source["titleEvidence"]
    title_id = next((eid for eid, ref in by_id.items() if all(ref[k] == v for k, v in title_source.items())), None)
    if title_id is None:
        i = len(evidence) + 1
        while f"ev-{i}" in by_id:
            i += 1
        title_id = f"ev-{i}"
        evidence.append(dict(title_source, evidenceId=title_id))
    def from_title(value, old_refs=()):
        return {"status": "explicit", "value": value, "evidenceIds": list(dict.fromkeys([*old_refs, title_id]))}
    fields["activityName"] = from_title(title)
    fields["sourceActivityId"] = from_title(source["sourceActivityId"], fields["sourceActivityId"]["evidenceIds"])
    method = from_title("shen_jia", (eid for m in metadata for eid in m["activityMethod"]["evidenceIds"]))
    return [anchor | {"fields": fields, "productRows": rows}], [
        {"candidateId": anchor["candidateId"], "activityMethod": method, "brandNames": brands}]
