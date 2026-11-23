"""One-request JSON adapter for the local activity Agent."""
import base64
import binascii
import hashlib
import json
import sys

from .splitter import CONTRACT, MAX_FILE, MAX_OUTPUT, BatchError, split_workbook

MAX_INPUT = (MAX_FILE + 2) // 3 * 4 + 4096


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def handle(raw):
    try:
        if not 0 < len(raw) <= MAX_INPUT:
            raise ValueError("invalid input size")
        request = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique)
        if (not isinstance(request, dict) or set(request) != {"contractVersion", "operation", "payload"}
                or request["contractVersion"] != CONTRACT or request["operation"] not in ("split", "inspect")):
            raise ValueError("invalid request")
        payload = request["payload"]
        if (not isinstance(payload, dict) or set(payload) != {"filename", "sha256", "contentBase64"}
                or not isinstance(payload["filename"], str) or len(payload["filename"]) > 200):
            raise ValueError("invalid payload")
        if not payload["filename"].lower().endswith(".xlsx"):
            raise BatchError("FORMAT_NOT_SUPPORTED", "此入口支持 XLSX 神价合并表。")
        data = base64.b64decode(payload["contentBase64"], validate=True)
        if hashlib.sha256(data).hexdigest() != payload["sha256"]:
            raise BatchError("HASH_MISMATCH", "原表校验值不一致。")
        result = split_workbook(data, include_workbooks=request["operation"] == "split")
        for item in result["activities"]:
            if "content" in item:
                item["contentBase64"] = base64.b64encode(item.pop("content")).decode("ascii")
        return {"contractVersion": CONTRACT, "ok": True, "result": result}
    except BatchError as exc:
        error = {"code": exc.code, "message": str(exc)}
        if exc.details:
            error["details"] = exc.details
    except (ValueError, TypeError, RecursionError, binascii.Error):
        error = {"code": "INVALID_REQUEST", "message": "神价批量拆分请求格式无效。"}
    return {"contractVersion": CONTRACT, "ok": False, "error": error}


def main():
    response = handle(sys.stdin.buffer.read(MAX_INPUT + 1))
    wire = json.dumps(response, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(wire) > MAX_OUTPUT:
        wire = b'{"contractVersion":"shenjia-batch/1.0","ok":false,"error":{"code":"WORKBOOK_LIMIT","message":"Response too large"}}'
    sys.stdout.buffer.write(wire + b"\n")


if __name__ == "__main__":
    main()
