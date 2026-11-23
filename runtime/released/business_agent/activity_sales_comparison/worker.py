"""One-shot local activity Agent sales entry point."""
import json
import os
import sys

from .comparison import _day, compare_sales
from .history import sales_history
from .online_source import _pairs, _reject_constant, source_from_private_file

CONTRACT = "activity-sales-worker/1.0"
SETTINGS_ENV = "JIATAN_ACTIVITY_SALES_SETTINGS"
MAX_INPUT_BYTES = 256 * 1024
OPERATIONS = {"history": sales_history, "compare": compare_sales}


def _failure(code):
    return {"contractVersion": CONTRACT, "ok": False, "error": {"code": code}}


def handle(raw, settings_path):
    try:
        if not 0 < len(raw) <= MAX_INPUT_BYTES:
            raise ValueError()
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_reject_constant)
        if (not isinstance(payload, dict) or set(payload) != {"contractVersion", "operation", "request", "today"}
                or payload["contractVersion"] != CONTRACT or not isinstance(payload["request"], dict)
                or not isinstance(payload["operation"], str) or payload["operation"] not in OPERATIONS):
            raise ValueError()
        today = _day(payload["today"])
    except (TypeError, ValueError, RecursionError):
        return _failure("ACTIVITY_SALES_INPUT_INVALID")

    def query_source(query):
        return source_from_private_file(settings_path)(query)

    source = query_source if settings_path else None
    try:
        result = OPERATIONS[payload["operation"]](payload["request"], source, today=today)
    except Exception:
        return _failure("ACTIVITY_SALES_UNAVAILABLE")
    return {"contractVersion": CONTRACT, "ok": True, "result": result}


def main():
    payload = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
    result = handle(payload, os.environ.get(SETTINGS_ENV))
    raw = json.dumps(result, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    sys.stdout.buffer.write(raw + b"\n")


if __name__ == "__main__":
    main()
