"""One private, consent-bound DeepSeek primary recognition attempt."""
import argparse
import os
import sys

from business_agent.workbench_deepseek import adapter
from .evidence import checked_groups, prepare
from .grouping import project
from .provider import PROMPT_VERSION, recognize

CONTRACT_VERSION = "activity-primary-recognition/1.0"
SETTINGS_ENV = "JIATAN_INTAKE_DEEPSEEK_SETTINGS"
MAX_INPUT_BYTES = 256 * 1024


def handle(raw, load_config, *, transport=adapter.provider_call):
    try:
        adapter.require(isinstance(raw, bytes) and 0 < len(raw) <= MAX_INPUT_BYTES, adapter.INPUT_INVALID)
        try:
            payload = adapter.decode(raw)
        except adapter.AdapterError:
            raise adapter.AdapterError(adapter.INPUT_INVALID) from None
        adapter.require(isinstance(payload, dict) and payload.get("contractVersion") == CONTRACT_VERSION,
                        adapter.INPUT_INVALID)
        outbound, local, anchors, hints = prepare(payload)
        response, metadata = recognize(load_config(), outbound, transport=transport)
        result = project(checked_groups(response, local, anchors, hints))
        result.update({name: payload[name] for name in ("inputRevision", "inputDigest", "analysisDigest")})
        return {"contractVersion": CONTRACT_VERSION, "ok": True, "result": result | metadata}
    except adapter.AdapterError as error:
        return {"contractVersion": CONTRACT_VERSION, "ok": False, "error": {"code": error.code}}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--status", action="store_true")
    args = parser.parse_args()

    def load():
        return adapter.load_private_config(os.environ.get(SETTINGS_ENV))

    if args.status:
        try:
            model = load().model
        except adapter.AdapterError:
            model = None
        result = {"contractVersion": CONTRACT_VERSION, "ok": True,
                  "result": {"enabled": model is not None, "model": model, "promptVersion": PROMPT_VERSION}}
    else:
        raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
        result = handle(raw, load)
    sys.stdout.buffer.write(adapter.encode(result) + b"\n")


if __name__ == "__main__":
    main()
