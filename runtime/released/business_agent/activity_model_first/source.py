"""Represent decoded inputs as addressable rows without interpreting business data."""
from collections import defaultdict
from hashlib import sha256
import re

from business_agent.workbench_deepseek import adapter
from .coupon_workbook import coupon_scope
from .horizontal_coupon_matrix import coupon_matrix
from .coupon_tier_sheet import tier_schedule
from .planning_workbooks import plan_type
from .coupon_ledger import MAIN as LEDGER_SHEETS, is_coupon_ledger
from .subsidy_workbook import _sheet as subsidy_sheet
from .diagnostics import RecognitionError
from . import large_shenjia

MAX_ROWS = 1000
MAX_BATCHES = 32
MAX_BATCH_ROWS = 10
LARGE_BATCH_ROWS = 40
MAX_BATCH_BYTES = 26 * 1024
EXCEL_DETAIL_ROWS = 5000
EXCEL_TEXT_CHARS = 2_000_000
EXCEL_LAYOUT = "general_excel_5000/v1"
EXCEL_BATCH_BYTES = 64 * 1024


def excel_budget_seconds(batch_count):
    """Upper bound for batches on three workers, including one repair per batch."""
    return 60 + ((batch_count + 2) // 3) * 2 * 120


def excel_detail_count(doc):
    # The decode contract counts populated table rows and grants one header per workbook.
    return max(0, len(doc["tableRows"]) - 1)


def check_excel_size(doc):
    detail_rows = excel_detail_count(doc)
    if detail_rows > EXCEL_DETAIL_ROWS:
        raise RecognitionError("MODEL_INPUT_EXCEL_ROW_LIMIT", "excel_detail_rows",
                               stage="planning", actualRows=detail_rows, maxRows=EXCEL_DETAIL_ROWS)
    chars = sum(len(cell["text"]) for row in doc["tableRows"] for cell in row["cells"])
    chars += sum(len(block["text"]) for block in doc["textBlocks"])
    if chars > EXCEL_TEXT_CHARS:
        raise RecognitionError("MODEL_INPUT_EXCEL_TEXT_LIMIT", "excel_text_chars",
                               stage="planning", actualChars=chars, maxChars=EXCEL_TEXT_CHARS)

SHA = re.compile(r"[0-9a-f]{64}")


def _append(context, entry):
    token = f"s{len(context) + 1}"
    context[token] = entry
    return [token, entry["text"]]


def plan(payload):
    required = {"contractVersion", "inputRevision", "inputDigest", "manualFields", "documents", "catalogSnapshot"}
    adapter.require(isinstance(payload, dict) and required <= payload.keys() <= required | {"limits", "materialNames"}
                    and payload["contractVersion"] == "activity-intake/1.0"
                    and type(payload["inputRevision"]) is int and payload["inputRevision"] > 0
                    and isinstance(payload["inputDigest"], str) and SHA.fullmatch(payload["inputDigest"])
                    and isinstance(payload["documents"], list)
                    and isinstance(payload["catalogSnapshot"], dict)
                    and isinstance(payload["catalogSnapshot"].get("revision"), str), adapter.INPUT_INVALID)
    names = payload.get("materialNames", {})
    adapter.require(isinstance(names, dict) and all(isinstance(name, str) and 0 < len(name) <= 500
                    for name in names.values()), adapter.INPUT_INVALID)
    lookup, rows, parts = {}, {}, defaultdict(list)
    form = payload["manualFields"]
    form_items = []
    if form:
        adapter.require(isinstance(form, dict) and set(form) == {"materialId", "sha256", "fields"}
                        and isinstance(form["fields"], dict), adapter.INPUT_INVALID)
        for key, value in form["fields"].items():
            if value is None or value == [] or isinstance(value, str) and not value.strip():
                continue
            text = value if isinstance(value, str) else adapter.encode(value).decode()
            token = _append(lookup, {"materialId": form["materialId"], "sourceSha256": form["sha256"],
                                     "locator": {"kind": "form", "fieldPath": key}, "text": text, "rowId": None})
            form_items.append([key, *token])

    excel_docs = [doc for doc in payload["documents"] if doc.get("kind") == "xlsx"]
    for doc in excel_docs:
        check_excel_size(doc)
    coupon = coupon_scope(payload)
    structured = plan_type(payload['documents'])
    ledger = is_coupon_ledger(payload['documents'])
    excluded = coupon[-1] if coupon else set()
    for index, doc in enumerate(payload["documents"], 1):
        adapter.require(doc["status"] == "parsed" and not doc["errors"], "MODEL_INPUT_DECODE_INCOMPLETE")
        for item in doc["tableRows"]:
            locator = item["locator"]
            if ledger and locator.get("sheet") not in LEDGER_SHEETS:
                continue
            matrix_product = structured == "matrix" and locator.get("sheet") != "券生效时间说明" and locator["row"] > 2
            nonblank = [cell for cell in item["cells"] if cell["text"].strip()
                        and (not matrix_product or cell["column"] in {"A","D","G"})]
            if not nonblank:
                continue
            if (doc["materialId"], locator.get("sheet")) in excluded:
                continue
            key = index, locator.get("sheet", "table")
            rid = f"r{len(rows) + 1}"
            cells = [[c["column"], *_append(lookup, {"materialId": doc["materialId"], "sourceSha256": doc["sha256"],
                      "locator": c["locator"], "text": c["text"], "rowId": rid})]
                     for c in nonblank]
            value = {"id": rid, "position": locator["row"], "cells": cells}
            parts[key].append(value)
            rows[rid] = doc["materialId"], key[1]
        for item in doc["textBlocks"]:
            if not item["text"].strip():
                continue
            rid = f"r{len(rows) + 1}"
            value = {"id": rid, "text": _append(lookup, {"materialId": doc["materialId"], "sourceSha256": doc["sha256"],
                     "locator": item["locator"], "text": item["text"], "rowId": rid})}
            parts[(index, "text")].append(value)
            rows[rid] = doc["materialId"], "text"

    shenjia_layouts = large_shenjia.layouts(payload["documents"])
    large_layout = bool(shenjia_layouts) and all(scope in shenjia_layouts for scope in rows.values())
    other_rows = sum(len(pts) for (index, _), pts in parts.items()
                     if payload["documents"][index - 1].get("kind") != "xlsx")
    adapter.require(other_rows <= MAX_ROWS and bool(rows or form_items), adapter.INPUT_INVALID)
    if large_layout:
        adapter.require(len(rows) <= large_shenjia.MAX_SOURCE_ROWS, "MODEL_INPUT_BATCH_LIMIT")
    if not parts:
        parts[(0, "form")] = []
    schedule = tier_schedule(payload)
    matrix = coupon_matrix(payload)
    # Leave room for the header when packing forty product rows in the same request.
    batch_rows = (LARGE_BATCH_ROWS + 1 if schedule else
                  LARGE_BATCH_ROWS if matrix or structured or coupon or ledger or subsidy_sheet(payload["documents"])
                  else MAX_BATCH_ROWS)
    if large_layout:
        batches = []
        for (index, section), items in parts.items():
            spec = shenjia_layouts[payload["documents"][index - 1]["materialId"], section]
            batches.extend(large_shenjia.pack(items, form_items, spec, index, section))
    else:
        header = matrix.header_row if matrix else None
        expanded = bool(excel_docs) and len(rows) > MAX_ROWS
        if not expanded:
            try:
                batches = _pack(parts, form_items, batch_rows, matrix_header=header)
                if len(batches) > MAX_BATCHES:
                    batches = _pack(parts, form_items, LARGE_BATCH_ROWS, matrix_header=header)
                expanded = bool(excel_docs) and len(batches) > MAX_BATCHES
            except adapter.AdapterError as err:
                if not excel_docs or err.code != "MODEL_INPUT_ROW_LIMIT":
                    raise
                expanded = True
        if expanded:
            batches = _pack(parts, form_items, LARGE_BATCH_ROWS, matrix_header=header,
                            layout=EXCEL_LAYOUT, byte_limit=EXCEL_BATCH_BYTES)
    extended = large_layout or any(b.get("layout") == EXCEL_LAYOUT for b in batches)
    adapter.require(len(batches) <= (large_shenjia.MAX_BATCHES if extended else MAX_BATCHES),
                    "MODEL_INPUT_BATCH_LIMIT")
    planned = [r["id"] for batch in batches for r in batch["rows"]]
    assert len(planned) == len(rows) and set(planned) == rows.keys()
    return batches, lookup, rows


def _pack(parts, form_items, max_rows, *, matrix_header=None, layout=None, byte_limit=MAX_BATCH_BYTES):
    batches = []
    for (index, section), items in parts.items():
        context = ([row for row in items if row.get("position", 0) <= matrix_header] if matrix_header
                   else [row for row in items[:2] if "cells" in row])
        base = {"document": index, "section": section, "context": context}
        if layout:
            base["layout"] = layout
        batch = {**base, "form": form_items if not batches else [], "rows": []}
        for item in items:
            trial = batch | {"rows": [*batch["rows"], item]}
            if batch["rows"] and (len(trial["rows"]) > max_rows or len(adapter.encode(trial)) > byte_limit):
                batches.append(batch)
                batch = {**base, "form": [], "rows": [item]}
            else:
                batch = trial
            adapter.require(len(adapter.encode(batch)) <= byte_limit, "MODEL_INPUT_ROW_LIMIT")
        adapter.require(len(adapter.encode(batch)) <= byte_limit, "MODEL_INPUT_ROW_LIMIT")
        batches.append(batch)
    return batches


def scope_fingerprint(batches):
    return sha256(adapter.encode(batches)).hexdigest()
