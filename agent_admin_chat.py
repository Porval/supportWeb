"""Single-use entry point into the Activity Agent's approved conversation engine."""
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import zoneinfo

ROOT = Path(__file__).resolve().parent
AGENT = ROOT / "agent-service"
CHAT = ROOT / "agent-chat-runtime"
INDEX_SHA = "c33cddeb992d5e3d7aa0b4e5c9423b653536ea088eeb6865e67db47c8fdd9a6f"
MAX_WIRE = 256 * 1024
MYSQL_DIST = "mysql-8.4.13-linux-glibc2.28-x86_64-minimal"
PUBLIC_FIELDS = {
    "contractVersion", "requestId", "source", "model", "modelLabel", "modelNameSource",
    "modelVersion", "modelVersionSource", "promptVersion", "caseReferences",
    "toolsUsed", "toolResults", "toolReceipts",
}
LOCAL_SOURCES = {
    "worksheet_cells", "attachment_receipt", "intake_contract", "product_page", "product_catalog",
    "product_name_search", "product_selection", "attachment_column", "product_count",
    "case_library", "runtime_config", "sales_source",
}
ERROR_MAPPING = {
    "INVALID_REQUEST":"CHAT_INPUT_INVALID", "INVALID_HISTORY":"CHAT_INPUT_INVALID",
    "INVALID_MESSAGE":"CHAT_INPUT_INVALID", "CHAT_TOO_LONG":"CHAT_INPUT_INVALID",
    "CONTEXT_UNAVAILABLE":"CHAT_CASES_UNAVAILABLE", "MODEL_SETTINGS":"CHAT_NOT_CONFIGURED",
    "MODEL_AUTH":"CHAT_PROVIDER_AUTH", "MODEL_QUOTA":"CHAT_PROVIDER_QUOTA",
    "MODEL_TIMEOUT":"CHAT_PROVIDER_TIMEOUT", "MODEL_CONNECTION":"CHAT_PROVIDER_UNAVAILABLE",
    "MODEL_UNAVAILABLE":"CHAT_PROVIDER_UNAVAILABLE", "MODEL_TRUNCATED":"CHAT_PROVIDER_TRUNCATED",
    "INTAKE_CONTRACT_UNAVAILABLE":"CHAT_CONTRACT_UNAVAILABLE",
}


def load():
    if (ROOT / "zoneinfo/Asia/Shanghai").is_file():
        zoneinfo.reset_tzpath((str(ROOT / "zoneinfo"),))
    manifest = CHAT / "SHA256SUMS"
    raw = manifest.read_bytes()
    if manifest.is_symlink() or hashlib.sha256(raw).hexdigest() != INDEX_SHA:
        raise ValueError("chat code index mismatch")
    expected_members = {"SHA256SUMS"}
    for line in raw.decode("ascii").splitlines():
        expected, relative = line.split("  ",1)
        expected_members.add(relative)
        path = CHAT / relative
        if path != path.resolve() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError("chat code mismatch")
    actual_members = {p.relative_to(CHAT).as_posix() for p in CHAT.rglob("*") if p.is_file()}
    if actual_members != expected_members:
        raise ValueError("unexpected chat runtime contents")
    sys.path[:0] = [str(CHAT), str(AGENT / "context")]
    import service
    service.imports(AGENT)
    # The sales history worker belongs to the workbench runtime. Keep the Agent
    # copy of the recognition engine and provider first in the import path.
    sys.path.append(str(ROOT / "runtime/released"))
    import agent_chat, memory, business_relations
    from business_agent.workbench_deepseek import adapter
    for obj,expected in ((agent_chat, CHAT / "agent_chat.py"), (memory, AGENT / "context/memory.py"),
            (business_relations, AGENT / "context/business_relations.py"),
            (adapter, AGENT / "released/business_agent/workbench_deepseek/adapter.py")):
        if Path(obj.__file__).resolve() != expected:
            raise ValueError("unexpected Agent module")
    contract = agent_chat.load_intake_contract(AGENT)
    if not contract or not business_relations.business_policy():
        raise ValueError("activity intake contract unavailable")
    return agent_chat, memory, adapter, contract


class ChatCaseAccess:
    """Allow vetted case SELECTs and the Agent's case reference audit."""
    def __init__(self, memory):
        self.memory = memory

    def execute(self, sql):
        query = sql.strip().removesuffix(";")
        if not query.startswith("SELECT JSON_OBJECT(") or ";" in query or "FROM approved_case" not in query:
            raise ValueError("unsupported chat case query")
        return self.memory.execute("START TRANSACTION READ ONLY;\n" + query + ";\nROLLBACK;")

    def note_use(self, request_id, cases):
        self.memory.note_use(request_id, cases)


def browser_fields(value):
    if isinstance(value, dict):
        return {key:browser_fields(item) for key,item in value.items() if not key.startswith("_")}
    if isinstance(value, list):
        return [browser_fields(item) for item in value]
    return value


def verified_receipt(result, agent_chat, adapter):
    src = result.get("source")
    request_id = result.get("requestId")
    model = result.get("model")
    if (result.get("contractVersion") != agent_chat.CONTRACT or result.get("promptVersion") != agent_chat.PROMPT_VERSION
        or not isinstance(request_id,str) or not re.fullmatch(r"CHAT-[0-9a-f]{32}",request_id)
        or src not in LOCAL_SOURCES | {"deepseek"}
        or (src == "deepseek" and not isinstance(model,str))
        or (model is not None and (not isinstance(model,str) or not adapter.MODEL_ID.fullmatch(model)))):
        raise ValueError("invalid Agent receipt")
    text = result.get("reply")
    if not isinstance(text,str) or not text.strip() or len(text)>60000:
        raise ValueError("invalid Agent reply")
    receipt = {name:browser_fields(result[name]) for name in PUBLIC_FIELDS if name in result}
    history = result.get("historyReply") or text
    if not isinstance(history,str):
        history = text
    out = {"reply":text,"model":model,"receipt":receipt,"historyReply":history,
           "privateSnapshot":result.get("_receiptSnapshot")}
    if len(json.dumps(out,ensure_ascii=False).encode()) > MAX_WIRE:
        raise ValueError("oversize Agent receipt")
    return out


def sales_settings(data_root):
    configured = os.environ.get("JIATAN_ACTIVITY_SALES_SETTINGS")
    source = Path(configured) if configured else data_root / "jt024-workbench-v1/integrations/sales/settings.json"
    try:
        info = source.lstat()
        if (source.is_absolute() and source == source.resolve() and stat.S_ISREG(info.st_mode) and
            info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) in (0o400,0o600)):
            return source
    except (OSError, RuntimeError):
        pass
    return None


def run(raw, services=None, *, store=None):
    if not 0 < len(raw) <= 45000:
        return {"error":"CHAT_INPUT_INVALID"}
    agent_chat, memory, adapter, contract = services or load()
    try:
        supplied = adapter.decode(raw)
        messages = agent_chat.read_messages({"messages":supplied})
        if any(any(0xd800 <= ord(c) <= 0xdfff for c in item["content"]) for item in messages):
            return {"error":"CHAT_INPUT_INVALID"}
    except (agent_chat.ChatError, adapter.AdapterError, ValueError, TypeError):
        return {"error":"CHAT_INPUT_INVALID"}
    settings = Path.home() / ".activityagent"
    data = Path(os.environ.get("ACTIVITY_DATA_DIR") or (Path.home() / "data"))
    mysql = data / "mysql-dist" / MYSQL_DIST
    os.environ["LD_LIBRARY_PATH"] = str(mysql / "lib/private") + ":" + str(mysql / "lib")
    if sys.platform.startswith("linux"):
        os.environ["SSL_CERT_FILE"] = "/etc/pki/tls/certs/ca-bundle.crt"
    cache = os.environ.get("ACTIVITY_CHAT_PRODUCT_CACHE")
    catalog = Path(cache) if cache else None
    if catalog is not None and (not catalog.is_absolute() or catalog.resolve() != catalog or not catalog.is_file()):
        catalog = None
    try:
        case_store = store if store is not None else memory.CaseMemory(mysql / "bin/mysql", settings / "runtime.cnf")
        result = agent_chat.answer(settings / "deepseek.json", ChatCaseAccess(case_store), messages,
            intake_contract=contract, recognition_release=AGENT, product_cache=catalog,
            product_master=None, tool_viewer=None, tool_cookie=None, tool_editor=None,
            sales_settings=sales_settings(data), recognition_socket=None, conversation_memory=None)
        return verified_receipt(result, agent_chat, adapter)
    except agent_chat.ChatError as exc:
        return {"error":ERROR_MAPPING.get(exc.code,"CHAT_PROVIDER_INVALID")}
    except memory.ContextUnavailable:
        return {"error":"CHAT_CASES_UNAVAILABLE"}


if __name__ == "__main__":
    try:
        if sys.argv[1:] == ["--check"]:
            engine, _, _, contract = load()
            result = {"ready":True,"contractVersion":engine.CONTRACT,"promptVersion":engine.PROMPT_VERSION,
                      "intakeContractVersion":contract["contractVersion"]}
        elif not sys.argv[1:]:
            result = run(sys.stdin.buffer.read(45001))
        else:
            raise ValueError("unexpected arguments")
    except Exception:
        result = {"error":"CHAT_PROVIDER_UNAVAILABLE"}
    sys.stdout.buffer.write(json.dumps(result,ensure_ascii=False,allow_nan=False).encode() + b"\n")
    if sys.argv[1:] == ["--check"] and "error" in result:
        raise SystemExit(1)
