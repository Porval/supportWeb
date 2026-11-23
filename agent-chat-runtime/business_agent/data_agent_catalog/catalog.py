"""Read the Data Agent's UPC export and cache it locally for activity intake."""

import argparse
from collections import Counter
from datetime import date, datetime
import fcntl
import hashlib
import json
from pathlib import Path
import re
import sys
from tempfile import NamedTemporaryFile
import unicodedata
from zoneinfo import ZoneInfo


VERSION = "jiatan-data-agent-upc/1.0"
STANDARD_VERSION = "jiatan-data-agent-upc/1.1"
IDENTITY_RULE_VERSION = "catalog-identity/1.0"
CHINA = ZoneInfo("Asia/Shanghai")
UPC = re.compile(r"[0-9]{8,14}")
SHA256 = re.compile(r"[0-9a-f]{64}")
MAX_BYTES = 32 * 1024 * 1024


def _unique_fields(pairs):
    fields = dict(pairs)
    if len(fields) != len(pairs):
        raise ValueError("重复的 JSON 字段")
    return fields


def _read_json(path):
    with path.open("rb") as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("商品快照超限")
    return json.loads(raw, object_pairs_hook=_unique_fields), hashlib.sha256(raw).hexdigest()


def _valid_codes(codes):
    return (isinstance(codes, list) and bool(codes)
            and all(isinstance(code, str) and code and code.strip() == code for code in codes)
            and len(codes) == len(set(codes)))


def validate_export(export):
    catalog, upcs, attrs = (export[key] for key in ("catalog", "upcs", "attributes"))
    if date.fromisoformat(catalog["saved_on"]).isoformat() != catalog["saved_on"]:
        raise ValueError("商品快照日期格式异常")
    if not SHA256.fullmatch(catalog["source_sha256"]) or not SHA256.fullmatch(catalog["index_sha256"]):
        raise ValueError("商品快照缺少来源校验值")
    if not isinstance(upcs, dict) or not upcs or not isinstance(attrs, dict):
        raise ValueError("商品快照缺少 UPC 映射")
    for upc, codes in upcs.items():
        if not UPC.fullmatch(upc) or not _valid_codes(codes):
            raise ValueError("UPC 或内部码格式异常")
    indexed_codes = {code for codes in upcs.values() for code in codes}
    if indexed_codes != attrs.keys():
        raise ValueError("UPC 与商品明细不完整")
    for item in attrs.values():
        if not all(isinstance(item.get(key), str) for key in ("item_name", "brand", "spec")):
            raise ValueError("商品明细字段不完整")
    unique = sum(len(codes) == 1 for codes in upcs.values())
    expected = {"products_with_upc": len(attrs), "distinct_upcs": len(upcs),
                "unique_upcs": unique, "ambiguous_upcs": len(upcs) - unique}
    if (any(type(catalog.get(key)) is not int or catalog[key] != value
            for key, value in expected.items())
            or type(catalog.get("products")) is not int or catalog["products"] < len(attrs)):
        raise ValueError("商品快照覆盖数量不一致")
    entries, history = export["entries"], export["history"]
    statuses = Counter()
    for upc, entry in entries.items():
        codes = upcs.get(upc, [])
        historical = entry["codes"]
        if not UPC.fullmatch(upc) or not _valid_codes(historical):
            raise ValueError("历史 UPC 样例格式异常")
        if not codes:
            expected_status = "historical_candidate"
        else:
            expected_status = "catalog_confirmed" if set(codes) == set(historical) else "conflict"
        if entry["status"] != expected_status:
            raise ValueError("历史样例与目录冲突状态不一致")
        statuses[expected_status] += 1
    if history["distinct_upcs"] != len(entries) or history["statuses"] != statuses:
        raise ValueError("历史样例覆盖数量不一致")
    conflicts = export.get("catalog_conflicts", {})
    if not isinstance(conflicts, dict) or any(not UPC.fullmatch(upc) or not conflict
                                              for upc, conflict in conflicts.items()):
        raise ValueError("目录冲突信息不完整")


def read_cache(cache):
    record, _ = _read_json(cache)
    if record["schemaVersion"] != VERSION:
        raise ValueError("未知缓存版本")
    date.fromisoformat(record["syncDay"])
    if not SHA256.fullmatch(record["exportSha256"]):
        raise ValueError("缓存缺少来源版本")
    validate_export(record["export"])
    return record


def _replace_cache(cache, record):
    with NamedTemporaryFile(mode="w", encoding="utf-8", dir=cache.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            json.dump(record, stream, ensure_ascii=False, separators=(",", ":"))
            stream.close()
            temporary.replace(cache)
        finally:
            temporary.unlink(missing_ok=True)


def _status(record, state):
    catalog = record["export"]["catalog"]
    return {"status": state, "syncDay": record["syncDay"], "lastFetchedAt": record["fetchedAt"],
            "sourceDate": catalog["saved_on"], "sourceSha256": catalog["source_sha256"],
            "exportSha256": record["exportSha256"], "distinctUpcs": catalog["distinct_upcs"],
            "ambiguousUpcs": catalog["ambiguous_upcs"]}


def sync(source, cache, *, clock=lambda: datetime.now(CHINA)):
    source, cache = Path(source).resolve(), Path(cache).absolute()
    if source == cache.resolve() or (source.exists() and cache.exists() and source.samefile(cache)):
        raise ValueError("商品源与价探缓存必须独立")
    cache.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with cache.with_suffix(cache.suffix + ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        now = clock().astimezone(CHINA)
        today = now.date().isoformat()
        previous = read_cache(cache) if cache.exists() else None
        if previous and previous["syncDay"] >= today:
            return _status(previous, "already_synced")
        export, digest = _read_json(source)
        validate_export(export)
        if previous and export["catalog"]["saved_on"] < previous["export"]["catalog"]["saved_on"]:
            raise ValueError("数据 Agent 商品快照早于本地版本")
        record = {"schemaVersion": VERSION, "syncDay": today, "fetchedAt": now.isoformat(),
                  "exportSha256": digest, "export": export}
        changed = previous is None or digest != previous["exportSha256"]
        _replace_cache(cache, record)
        return _status(record, "updated" if changed else "unchanged")


def _normalize(value):
    return "".join(unicodedata.normalize("NFKC", value).casefold().split())


def _lookup(export, upc, *, raw_code=None, name=None, spec=None):
    result = {"upc": upc, "status": "unmatched", "internalCode": None, "candidates": []}
    if not isinstance(upc, str) or not UPC.fullmatch(upc.strip()):
        return result | {"status": "invalid_upc"}
    upc = upc.strip()
    codes = export["upcs"].get(upc, [])
    history = export["entries"].get(upc)
    for code in codes:
        item = export["attributes"][code]
        differences = [field for field, value in (("item_name", name), ("spec", spec))
                       if value and _normalize(value) != _normalize(item[field])]
        result["candidates"].append({"internalCode": code, "name": item["item_name"],
                                     "spec": item["spec"], "brand": item["brand"],
                                     "detailsToCheck": differences})
    result["upc"] = upc
    if history:
        result["historicalCodes"] = history["codes"]
    if upc in export.get("catalog_conflicts", {}) or (history and history["status"] == "conflict"):
        result["status"] = "source_conflict"
    elif codes and raw_code and raw_code.strip() not in codes:
        result["status"] = "material_code_conflict"
    elif len(codes) > 1:
        result["status"] = "multiple_candidates"
    elif codes and result["candidates"][0]["detailsToCheck"]:
        result["status"] = "details_need_review"
    elif codes:
        result.update(status="exact_upc", internalCode=codes[0])
    elif history:
        result["status"] = "historical_candidate"
    return result


def _source_key(material_id, digest, locator, quote):
    return material_id, digest, json.dumps(locator, sort_keys=True), quote


def _text_cell_refs(analysis):
    cells = {
        _source_key(doc["materialId"], doc["sha256"], cell["locator"], cell["text"][:500])
        for doc in analysis.get("materialResults", [])
        if doc["status"] == "parsed" and not doc["errors"]
        for table_row in doc["tableRows"]
        for cell in table_row["cells"]
        if cell["sourceType"] == "string" and cell["text"].strip()
    }
    return {ref["evidenceId"]: ref for ref in analysis.get("evidence", [])
            if _source_key(ref["materialId"], ref["sourceSha256"], ref["locator"], ref["quote"]) in cells}


def _catalog_identity(row, match, text_refs, *, separate_spec_review=False):
    raw_code = row.get("rawInternalCode")
    if (not isinstance(raw_code, str) or not raw_code.strip()
            or row.get("matchKind") == "manual_selected"
            or match["status"] not in {"exact_upc", "details_need_review", "intake_review_required",
                                       "identifier_not_text"}
            or len(match["candidates"]) != 1):
        return None
    item = match["candidates"][0]
    if item["internalCode"] != raw_code.strip():
        return None
    if not separate_spec_review and "spec" in item["detailsToCheck"]:
        return None

    code_refs, upc_refs = {}, {}
    for eid in row["evidenceIds"]:
        if ref := text_refs.get(eid):
            quote = ref["quote"].strip()
            if quote == raw_code.strip():
                code_refs.setdefault(ref["materialId"], eid)
            if quote == match["upc"]:
                upc_refs.setdefault(ref["materialId"], eid)
    # The intake Worker binds the product row and its citations. Confirm the
    # two identifiers against distinct original text cells in the same file.
    for material, code_eid in code_refs.items():
        if (upc_eid := upc_refs.get(material)) and (
                text_refs[code_eid]["locator"] != text_refs[upc_eid]["locator"]):
            return {"status": "exact_internal_code_upc", "internalCode": item["internalCode"],
                    "evidenceIds": [code_eid, upc_eid]}
    return None


def _match_analysis(cache, analysis, *, verify_catalog_identity, standardized):
    if analysis["contractVersion"] != "activity-intake/1.0":
        raise ValueError("未知活动分析契约")
    record = read_cache(Path(cache))
    unreliable_locations = {
        json.dumps(warning["locator"], sort_keys=True)
        for warning in analysis.get("warnings", [])
        if warning["code"] == "IDENTIFIER_CELL_NOT_TEXT" and warning.get("locator")
    }
    unreliable_evidence = {
        evidence["evidenceId"] for evidence in analysis.get("evidence", [])
        if json.dumps(evidence["locator"], sort_keys=True) in unreliable_locations
    }
    text_refs = _text_cell_refs(analysis) if verify_catalog_identity else {}
    # Model-first rows may carry a verbatim spec extracted from the product name.
    # Its differences stay in directory status and do not erase two exact identifiers.
    separate_spec_review = (standardized and analysis.get("engine") == "deepseek_model_first_v1"
                            and analysis.get("modelUsed") is True)
    rows = []
    row_keys = set()
    for candidate in analysis["candidates"]:
        for row in candidate["productRows"]:
            upc = row["rawUpc"]
            if upc is None or (standardized and isinstance(upc, str) and not upc.strip()):
                if not standardized:
                    continue
                match = {"upc": upc, "status": "missing_upc", "internalCode": None, "candidates": []}
            else:
                match = _lookup(record["export"], upc, raw_code=row.get("rawInternalCode"),
                                name=row.get("rawName"), spec=row.get("rawSpec"))
            if match["status"] == "exact_upc":
                if unreliable_evidence.intersection(row["evidenceIds"]):
                    match.update(status="identifier_not_text", internalCode=None)
                elif row.get("matchKind") in {"conflict", "manual_selected"}:
                    match.update(status="intake_review_required", internalCode=None)
            if text_refs:
                identity = _catalog_identity(row, match, text_refs, separate_spec_review=separate_spec_review)
                if identity is not None:
                    match["catalogIdentity"] = identity
            if standardized:
                key = candidate["candidateId"], row["rowId"]
                if key in row_keys:
                    raise ValueError("重复的活动商品行")
                row_keys.add(key)
                # Count the current directory mapping, regardless of review status.
                match["uniqueUpcHit"] = len(match["candidates"]) == 1
            rows.append({"candidateId": candidate["candidateId"], "rowId": row["rowId"],
                         "evidenceIds": row["evidenceIds"], **match})
    result = {"schemaVersion": STANDARD_VERSION if standardized else VERSION, "catalog": _status(record, "cached"),
              "inputDigest": analysis["inputDigest"], "rows": rows}
    if verify_catalog_identity:
        result["catalogIdentityRuleVersion"] = IDENTITY_RULE_VERSION
        result["confirmedIdentityCount"] = sum("catalogIdentity" in row for row in rows)
    return result


def match_analysis(cache, analysis, *, verify_catalog_identity=False):
    """Read-only companion result for activity-intake/1.0 product rows."""
    return _match_analysis(cache, analysis, verify_catalog_identity=verify_catalog_identity, standardized=False)


def match_analysis_standardized(cache, analysis, *, verify_catalog_identity=True):
    """Return one directory result per (candidateId, rowId), including missing UPC."""
    return _match_analysis(cache, analysis, verify_catalog_identity=verify_catalog_identity, standardized=True)


def main():
    parser = argparse.ArgumentParser()
    actions = parser.add_subparsers(dest="action", required=True)
    refresh = actions.add_parser("sync")
    refresh.add_argument("--source", type=Path, required=True)
    refresh.add_argument("--cache", type=Path, required=True)
    match = actions.add_parser("match-analysis")
    match.add_argument("--cache", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = (sync(args.source, args.cache) if args.action == "sync"
                  else match_analysis(args.cache, json.load(sys.stdin)))
    except (OSError, ValueError, KeyError, AttributeError, TypeError) as exc:
        print(json.dumps({"status": "failed", "reason": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
