"""Bounded, source-addressed planning for tables with explicit Shen Jia main IDs.

This module partitions input and checks model output. It never manufactures model
classifications or activities from worksheet rows.
"""
from collections import defaultdict
import re

from business_agent.workbench_deepseek import adapter
from .diagnostics import require

LAYOUT = "shenjia-main-id/1"
MAX_SOURCE_ROWS = 6000
MAX_GROUPS = 64
MAX_BATCHES = 256
MAX_BATCH_ROWS = 80
MAX_BATCH_BYTES = 80 * 1024
MAX_EVIDENCE = 40000
MAIN = re.compile(r"(?:神价ID|神价主活动(?:编号|ID)|美团主活动(?:编号|ID)|主活动(?:编号|ID))", re.I)
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,199}")
UPC = re.compile(r"[0-9]{8,14}")


def layouts(documents):
    output = {}
    for document in documents:
        if document.get("kind") != "xlsx" or document.get("textBlocks"):
            continue
        by_sheet = defaultdict(list)
        for row in document["tableRows"]:
            by_sheet[row["locator"].get("sheet")].append(row)
        for sheet, rows in by_sheet.items():
            for row in rows:
                cells = {cell["column"]: cell["text"].strip() for cell in row["cells"]}
                main = [column for column, text in cells.items() if MAIN.fullmatch(text)]
                child = [column for column, text in cells.items() if text.lower() == "子活动id"]
                upc = [column for label in ("神价UPC", "UPC", "商品UPC", "商品条码")
                       for column, text in cells.items() if text.upper() == label.upper()]
                name = [column for label in ("神价商品", "商品名称", "商品名")
                        for column, text in cells.items() if text == label]
                if len(main) != 1 or not child or not upc or not name:
                    continue
                context = {main[0]} | {column for column, text in cells.items()
                                      if text in {"神价名称", "主活动名称", "活动名称", "活动周期", "活动时间"}}
                output[document["materialId"], sheet] = {
                    "headerRow": row["locator"]["row"], "main": main[0], "child": child,
                    "upc": upc[0], "name": name[0], "contextColumns": context,
                }
                break
    return output


def pack(items, form_items, spec, document, section):
    """Keep each explicit main identity in its own batch, with real header refs."""
    header = next(item for item in items if item["position"] == spec["headerRow"])
    prefix = [item for item in items if item["position"] <= spec["headerRow"]]
    context = prefix[-2:]
    buckets = {}
    for item in items:
        if item["position"] <= spec["headerRow"]:
            continue
        cells = {cell[0]: cell for cell in item["cells"]}
        value = cells.get(spec["main"], [None, None, ""])[2].strip()
        key = value if IDENTIFIER.fullmatch(value) else None
        buckets.setdefault(key, []).append(item)
    adapter.require(len([key for key in buckets if key is not None]) <= MAX_GROUPS,
                    "MODEL_RECOGNITION_GROUP_LIMIT")
    if not buckets:
        buckets[None] = []
    # Activity-specific form IDs/names cannot label every distinct main ID.
    shared_form = [item for item in form_items if item[0] in {"platformCode", "type"}]
    output = []
    for main_id, source_rows in buckets.items():
        anchor = []
        if main_id:
            first = source_rows[0]
            anchor = [first | {"cells": [cell for cell in first["cells"]
                                         if cell[0] in spec["contextColumns"]]}]
        batch_context = context + anchor
        base = {"document": document, "section": section, "layout": LAYOUT,
                "mainActivityId": main_id, "sourceColumns": {key: spec[key] for key in ("main", "upc", "name")},
                "context": batch_context, "form": shared_form}
        current = []
        for item in (prefix if not output else []) + source_rows:
            trial = base | {"rows": [*current, item]}
            if current and (len(trial["rows"]) > MAX_BATCH_ROWS or len(adapter.encode(trial)) > MAX_BATCH_BYTES):
                output.append(base | {"rows": current})
                current = []
            current.append(item)
            adapter.require(len(adapter.encode(base | {"rows": current})) <= MAX_BATCH_BYTES,
                            "MODEL_INPUT_ROW_LIMIT")
        if current:
            output.append(base | {"rows": current})
    return output


def check_classifications(batch, observed, groups):
    """Reject omitted/misassigned real identifier rows; repair must come from model."""
    if batch.get("layout") != LAYOUT:
        return
    columns = batch["sourceColumns"]
    for source in batch["rows"]:
        cells = {column: text.strip() for column, _, text in source["cells"]}
        main_id = cells.get(columns["main"], "")
        upc = cells.get(columns["upc"], "")
        name = cells.get(columns["name"], "")
        # Headers, instructions and examples remain the model's classifications.
        if not IDENTIFIER.fullmatch(main_id) or not UPC.fullmatch(upc) or not name or name.startswith(("示例", "example")):
            continue
        row = observed[source["id"]]
        require(row["kind"] == "product", "main_id_product_omitted", sourceRowId=row["id"])
        fields = groups.get(row["group"], {})
        require(fields.get("sourceActivityId", (None,))[0] == main_id,
                "main_id_product_group_mismatch", sourceRowId=row["id"], field="sourceActivityId")


def source_details(lookup, table_layouts):
    scopes, children = set(), defaultdict(list)
    for token, source in lookup.items():
        loc = source["locator"]
        spec = table_layouts.get((source["materialId"], loc.get("sheet")))
        if not spec or loc.get("kind") != "xlsx":
            continue
        column = re.sub(r"[0-9]", "", loc.get("cell", ""))
        scopes.add((source["materialId"], loc.get("sheet"), None, spec["main"]))
        if column in spec["child"] and loc["row"] > spec["headerRow"] and IDENTIFIER.fullmatch(source["text"].strip()):
            children[source["rowId"]].append((token, source["text"]))
    return scopes, children


def child_upc_conflicts(groups, children, lookup):
    warnings = []
    for group in groups.values():
        child_rows = defaultdict(list)
        for row in group["rows"]:
            upc = row["fields"].get("upc", {}).get("value")
            for token, child in children.get(row["id"], []):
                if upc:
                    child_rows[lookup[token]["materialId"], child].append((upc, token))
        for (_, child), refs in child_rows.items():
            upcs = list(dict.fromkeys(upc for upc, _ in refs))
            if len(upcs) > 1:
                warnings.append({"code": "CHILD_ACTIVITY_UPC_CONFLICT",
                                 "message": f"子活动 ID {child} 对应 {len(upcs)} 个不同 UPC，原商品行全部保留，请提报人核对。",
                                 "locator": lookup[refs[0][1]]["locator"]})
    return warnings
