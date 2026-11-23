"""Read one authorized model-first intake request and return its structured result."""
import argparse
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import os
import sys
from threading import Lock

from business_agent.workbench_deepseek import adapter
from .activity_platforms import reconcile_platforms
from .horizontal_coupon_matrix import coupon_matrix, reconcile_matrix
from .child_workbook_rules import HEADERS, child_workbook_rule
from .diagnostics import RecognitionError
from .coupon_workbook import coupon_scope, reconcile_coupon
from .coupon_ledger import MAX_GROUPS as LEDGER_GROUP_LIMIT, is_coupon_ledger, reconcile_ledger
from .coupon_tier_sheet import reconcile_tier_schedule
from .projection import project, read_batch
from .provider import PROMPT_VERSION, call
from .source import EXCEL_LAYOUT, plan, scope_fingerprint
from .submission_checks import price_conflicts
from .subsidy_workbook import _sheet as subsidy_sheet, reconcile_subsidy
from .planning_workbooks import plan_type, reconcile_plans
from .union_coupon_text import reconcile_union_text
from . import large_shenjia

CONTRACT = "activity-intake/1.0"
ENGINE = "deepseek_model_first_v1"
ENGINE_VERSION = "2.1.0"
SETTINGS_ENV = "JIATAN_INTAKE_DEEPSEEK_SETTINGS"
MAX_INPUT_BYTES = 32 * 1024 * 1024
MAX_OUTPUT_BYTES = 32 * 1024 * 1024
PARALLEL_BATCHES = 3
MAX_PROVIDER_ATTEMPTS = 32


def analyze(payload, config, *, transport=adapter.provider_call, planned=None):
    batches, lookup, rows = plan(payload) if planned is None else planned
    ledger = is_coupon_ledger(payload["documents"])
    shenjia = any(batch.get("layout") == large_shenjia.LAYOUT for batch in batches)
    expanded_excel = any(batch.get("layout") == EXCEL_LAYOUT for batch in batches)
    config = adapter.checked_config(config)
    replies, models, thinking = [], set(), False
    attempts = 0
    repair_slots = len(batches) if shenjia or (expanded_excel and len(batches) > MAX_PROVIDER_ATTEMPTS) else MAX_PROVIDER_ATTEMPTS - len(batches)
    repair_lock = Lock()

    def take_repair_slot():
        nonlocal repair_slots
        with repair_lock:
            if repair_slots <= 0:
                return False
            repair_slots -= 1
            return True

    def one(item):
        batch_index, batch = item
        repair = None
        while True:
            attempt = 1 if repair is None else 2
            stage = "provider"
            try:
                response, model, observed = call(config, batch, transport, repair=repair, ledger=ledger)
                stage = "evidence"
                return read_batch(response, batch, lookup, max_groups=LEDGER_GROUP_LIMIT if ledger else 20), model, observed, attempt
            except RecognitionError as error:
                repairable = stage == "evidence" or error.code in {
                    "MODEL_RECOGNITION_EMPTY_CONTENT", "MODEL_RECOGNITION_JSON_INVALID",
                    "MODEL_RECOGNITION_OUTPUT_LIMIT",
                }
                if attempt == 1 and repairable and take_repair_slot():
                    repair = {key: error.details[key] for key in ("reason", "field", "sourceRowId", "missingRowCount")
                              if key in error.details}
                    continue
                error.details.update(batchIndex=batch_index, stage=stage, attempt=attempt)
                raise
            except adapter.AdapterError as error:
                raise RecognitionError(error.code, "batch_call", batchIndex=batch_index,
                                       stage=stage, attempt=attempt) from None

    ordered = {}
    pool = ThreadPoolExecutor(max_workers=PARALLEL_BATCHES)
    pending, next_index = {}, 0
    try:
        while pending or next_index < len(batches):
            while next_index < len(batches) and len(pending) < PARALLEL_BATCHES:
                pending[pool.submit(one, (next_index, batches[next_index]))] = next_index
                next_index += 1
            done, _ = wait(pending, return_when=FIRST_COMPLETED)
            # Observe all completed errors before submitting any more work.
            completed = [(future, future.result()) for future in done]
            for future, response in completed:
                ordered[pending.pop(future)] = response
    finally:
        for future in pending:
            future.cancel()
        pool.shutdown(wait=True, cancel_futures=True)
    for index in range(len(batches)):
        reply, model, observed, attempt = ordered[index]
        replies.append(reply)
        models.add(model)
        thinking |= observed
        attempts += attempt
    adapter.require(len(models) == 1, "MODEL_RECOGNITION_INCONSISTENT_MODEL")
    supported_layout = (ledger or plan_type(payload["documents"]) or subsidy_sheet(payload["documents"])
                        or coupon_scope(payload) or shenjia or expanded_excel)
    projection, metrics = project(replies, payload, lookup, rows, max_groups=64 if supported_layout else 20,
                                  expanded_excel=expanded_excel)
    adapter.require(metrics["classifiedRows"] == len(rows), "MODEL_RECOGNITION_ROW_COVERAGE_INCOMPLETE")
    products = [row for candidate in projection["candidates"] for row in candidate["productRows"]]
    source_ids = [rid for row in products for rid in row["sourceRowIds"]]
    adapter.require(len(source_ids) == len(set(source_ids)) == metrics["productSourceRows"],
                    "MODEL_RECOGNITION_ROW_COVERAGE_INCOMPLETE")
    documents = payload["documents"]
    warnings = [warning for doc in documents for warning in doc["warnings"]] + projection.pop("warnings")
    result = {"contractVersion": CONTRACT, "engine": ENGINE, "engineVersion": ENGINE_VERSION,
              "modelUsed": True, "promptVersion": PROMPT_VERSION,
              "parserVersions": list(dict.fromkeys(doc["parserVersion"] for doc in documents)),
              "matcherVersion": "model-evidence/1.0",
              "inputRevision": payload["inputRevision"], "inputDigest": payload["inputDigest"],
              "catalogRevision": payload["catalogSnapshot"]["revision"],
              "outcome": "needs_confirmation" if projection["candidates"] else "needs_input",
              "materialResults": documents, "warnings": warnings, "errors": [], **projection}
    metadata = {"provider": "deepseek", "model": next(iter(models)),
                "thinking": "enabled" if thinking else "disabled" if shenjia else "unknown",
                "promptVersion": PROMPT_VERSION, "providerAttempts": attempts,
                "coverage": {"batches": len(batches), "sourceRows": len(rows),
                             "classifiedRows": metrics["classifiedRows"], "productSourceRows": metrics["productSourceRows"]},
                "candidateMetadata": metrics["candidateMetadata"], "inputScopeSha256": scope_fingerprint(batches)}
    result, metadata = reconcile_matrix(coupon_matrix(payload), payload, result, metadata, lookup)
    result, metadata = reconcile_coupon(payload, result, metadata, lookup)
    result, metadata = reconcile_tier_schedule(payload, result, metadata, lookup)
    result, metadata = reconcile_subsidy(payload, result, metadata, lookup)
    result, metadata = reconcile_union_text(payload, result, metadata, lookup)
    result, metadata = reconcile_plans(payload, result, metadata, lookup)
    result, metadata = reconcile_ledger(payload, result, metadata, lookup)
    limit = 64 if supported_layout else 20
    adapter.require(len(result["candidates"]) <= limit, "MODEL_RECOGNITION_GROUP_LIMIT")
    if len(result["candidates"]) == 1:
        candidate, = result["candidates"]
        rules = candidate["fields"]["rulesText"]
        form = (payload["manualFields"] or {}).get("fields", {})
        header_only = bool(rules["value"]) and all(line.strip() in HEADERS[2:] for line in rules["value"].splitlines())
        method = metadata["candidateMetadata"][0]["activityMethod"]
        observed = child_workbook_rule(documents, candidate, result["evidence"], method=method,
                                       material_names=payload.get("materialNames"))
        if observed:
            conflict = observed["status"] == "conflict"
            if conflict:
                result["warnings"].append({"code": "CHILD_WORKBOOK_PRICE_CONFLICT",
                                           "message": observed["summary"], "locator": observed["evidence"][-1]["locator"]})
            replace_summary = not form.get("rulesText") and (
                rules["status"] == "missing" or rules["status"] == "explicit" and header_only)
            if replace_summary and len(result["evidence"]) + len(observed["evidence"]) <= 6000:
                refs = []
                used_ids = {item["evidenceId"] for item in result["evidence"]}
                index = len(used_ids) + 1
                for source in observed["evidence"]:
                    while f"ev-{index}" in used_ids:
                        index += 1
                    eid = f"ev-{index}"
                    result["evidence"].append(source | {"evidenceId": eid})
                    refs.append(eid)
                    index += 1
                candidate["fields"]["rulesText"] = {"value": None if conflict else observed["summary"],
                                                     "status": observed["status"], "evidenceIds": refs}
                result["missingFields"] = [field for field in result["missingFields"] if field["field"] != "rulesText"]
    reconcile_platforms(result, payload.get("manualFields"))
    result["warnings"].extend(price_conflicts(documents))
    return result, metadata


def handle(raw, load_config, *, transport=adapter.provider_call):
    request_id = None
    try:
        adapter.require(isinstance(raw, bytes) and 0 < len(raw) <= MAX_INPUT_BYTES, adapter.INPUT_INVALID)
        request = adapter.decode(raw)
        adapter.require(isinstance(request, dict) and set(request) == {"contractVersion", "requestId", "operation", "payload"}
                        and request["contractVersion"] == CONTRACT and request["operation"] == "analyze"
                        and isinstance(request["requestId"], str) and 0 < len(request["requestId"]) <= 200, adapter.INPUT_INVALID)
        request_id = request["requestId"]
        planned = plan(request["payload"])
        result, metadata = analyze(request["payload"], load_config(), transport=transport, planned=planned)
        response = {"contractVersion": CONTRACT, "requestId": request_id, "ok": True, "result": result, "metadata": metadata}
        adapter.require(len(adapter.encode(response)) <= MAX_OUTPUT_BYTES, "MODEL_RECOGNITION_OUTPUT_LIMIT")
        return response
    except adapter.AdapterError as error:
        code = error.code
        details = error.details if isinstance(error, RecognitionError) else {}
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, RecursionError):
        code = adapter.INPUT_INVALID
        details = {}
    return {"contractVersion": CONTRACT, "requestId": request_id, "ok": False,
            "error": {"code": code, "message": code, "details": details}}


def main():
    cli = argparse.ArgumentParser()
    cli.add_argument("--status", action="store_true")
    args = cli.parse_args()

    def load():
        return adapter.load_private_config(os.environ.get(SETTINGS_ENV))

    if args.status:
        try:
            model = load().model
        except adapter.AdapterError:
            model = None
        response = {"contractVersion": CONTRACT, "ok": True,
                    "result": {"enabled": model is not None, "model": model, "promptVersion": PROMPT_VERSION}}
    else:
        response = handle(sys.stdin.buffer.read(MAX_INPUT_BYTES + 1), load)
    sys.stdout.buffer.write(adapter.encode(response) + b"\n")


if __name__ == "__main__":
    main()
