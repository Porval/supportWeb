"""Administrator-only, account-scoped DeepSeek conversation in the hosted workbench."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from account_primitives import fail
from workbench_settings import ADMIN_PHONE

AGENT_PORT_ENV = "JIATAN_ACTIVITY_AGENT_PORT"
AGENT_CLI = Path(__file__).resolve().with_name("agent_admin_chat.py")
CHAT_ADMIN_USERNAME = ADMIN_PHONE
MAX_MESSAGE = 3000
MAX_CONTEXT = 7200
MAX_REPLY = 60000
TIMEOUT = 130
RESERVATION = TIMEOUT + 25
DDL = """CREATE TABLE IF NOT EXISTS hosted_chat_turn(
    owner TEXT NOT NULL, command_id TEXT NOT NULL, base_revision INTEGER NOT NULL,
    user_text TEXT NOT NULL, created REAL NOT NULL, state TEXT NOT NULL,
    reply TEXT, model TEXT, error_code TEXT, PRIMARY KEY(owner,command_id))"""
ERRORS = {
    "CHAT_CASES_UNAVAILABLE": (503, "活动 Agent 案例库暂不可用。"),
    "CHAT_CONTRACT_UNAVAILABLE": (503, "活动识别契约暂不可用。"),
    "CHAT_INPUT_INVALID": (422, "发送给活动 Agent 的对话历史超出接口范围。"),
    "CHAT_NOT_CONFIGURED": (503, "工作台尚未连接活动 Agent 的管理员对话接口。"),
    "CHAT_PROVIDER_AUTH": (503, "活动 Agent 对话鉴权未通过。"),
    "CHAT_PROVIDER_QUOTA": (503, "DeepSeek 对话额度不足。"),
    "CHAT_PROVIDER_LIMIT": (429, "DeepSeek 当前限制请求频率，请稍后再试。"),
    "CHAT_PROVIDER_TIMEOUT": (504, "DeepSeek 对话超时，请重试。"),
    "CHAT_PROVIDER_UNAVAILABLE": (503, "暂时无法连接 DeepSeek。"),
    "CHAT_PROVIDER_INVALID": (502, "DeepSeek 回复格式异常，请重试。"),
    "CHAT_PROVIDER_TRUNCATED": (502, "DeepSeek 回复超过长度限制，请缩小提问范围后重试。"),
}


def agent_ready():
    raw = os.environ.get(AGENT_PORT_ENV, "")
    return bool(re.fullmatch(r"[0-9]{1,5}", raw) and 1 <= int(raw) <= 65535 and
                AGENT_CLI.is_file() and not AGENT_CLI.is_symlink())


def call_agent(messages):
    env = {"HOME": str(Path.home()), "SSL_CERT_FILE": "/etc/pki/tls/certs/ca-bundle.crt"}
    for name in ("ACTIVITY_DATA_DIR", "ACTIVITY_CHAT_PRODUCT_CACHE", "JIATAN_ACTIVITY_SALES_SETTINGS"):
        if os.environ.get(name):
            env[name] = os.environ[name]
    raw = json.dumps(messages, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
    try:
        proc = subprocess.run([sys.executable, "-I", "-B", str(AGENT_CLI)], input=raw,
                cwd=AGENT_CLI.parent, env=env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                timeout=TIMEOUT, check=True)
        if len(proc.stdout) > 256 * 1024 + 1:
            return {"error":"CHAT_PROVIDER_INVALID"}
        result = json.loads(proc.stdout)
        if not isinstance(result,dict):
            return {"error":"CHAT_PROVIDER_INVALID"}
        if set(result) == {"error"} and isinstance(result["error"],str) and result["error"] in ERRORS:
            return result
        receipt = result.get("receipt")
        if (set(result) == {"reply","model","receipt","historyReply","privateSnapshot"} and
            isinstance(receipt,dict) and receipt.get("contractVersion") == "activity-agent-chat/1.0" and
            receipt.get("model") == result["model"] and isinstance(receipt.get("source"),str) and
            (receipt["source"] != "deepseek" or isinstance(result["model"],str)) and
            isinstance(result["reply"],str) and 0 < len(result["reply"]) <= MAX_REPLY and
            isinstance(result["historyReply"],str)):
            return result
        return {"error":"CHAT_PROVIDER_INVALID"}
    except subprocess.TimeoutExpired:
        return {"error":"CHAT_PROVIDER_TIMEOUT"}
    except (OSError, subprocess.SubprocessError, ValueError, RecursionError):
        return {"error":"CHAT_PROVIDER_UNAVAILABLE"}


def current_actor(db, accounts, sid):
    _, actor = accounts.valid(db, sid, True)
    accounts.require(db, actor, "accounts.manage")
    if actor["username"] != CHAT_ADMIN_USERNAME or not accounts.is_review_admin(actor):
        fail(403, "FORBIDDEN", "当前账号未获管理员直聊授权")
    return actor["id"]


def chat_schema(db):
    db.execute(DDL)
    columns = {row[1] for row in db.execute("PRAGMA table_info(hosted_chat_turn)")}
    for name in ("receipt_json","private_snapshot","history_reply"):
        if name not in columns:
            db.execute("ALTER TABLE hosted_chat_turn ADD COLUMN " + name + " TEXT")


def history(db, owner, limit=40):
    return list(reversed(db.execute("""SELECT user_text,reply,model,base_revision,receipt_json,history_reply FROM hosted_chat_turn
        WHERE owner=? AND state='done' ORDER BY base_revision DESC LIMIT ?""", (owner,limit)).fetchall()))


def status(db, owner, configured):
    rows = history(db, owner)
    revision = rows[-1]["base_revision"] + 1 if rows else 0
    items = []
    for row in rows:
        items.append({"role": "user", "content": row["user_text"]})
        assistant = {"role":"assistant", "content":row["reply"], "model":row["model"]}
        if row["receipt_json"]:
            assistant["receipt"] = json.loads(row["receipt_json"])
        items.append(assistant)
    return {"conversationId": "admin-" + hashlib.sha256(owner.encode()).hexdigest()[:32],
            "revision": revision, "messages": items, "configured": bool(configured),
            "provider": "deepseek", "historyTruncated": revision > len(rows)}


def context(rows, message):
    selected = []
    remaining = MAX_CONTEXT - len(message)
    for row in reversed(rows[-4:]):
        user = row["user_text"]
        if remaining <= len(user) + 16:
            break
        limit = min(MAX_MESSAGE, remaining-len(user))
        reply = row["history_reply"] or row["reply"]
        if len(reply) > limit:
            note = "…（上轮回复节选）"
            reply = reply[:limit-len(note)] + note
        selected.append((user,reply))
        remaining -= len(user) + len(reply)
    messages = []
    for user, reply in reversed(selected):
        messages.extend([{"role": "user", "content": user}, {"role": "assistant", "content": reply}])
    return messages + [{"role": "user", "content": message}]


def raise_provider(code):
    status_code, message = ERRORS.get(code, ERRORS["CHAT_PROVIDER_UNAVAILABLE"])
    fail(status_code, code, message)


def dispatch_chat(handler, headers, path, q):
    if path != "/agent/chat" or q:
        fail(404, "NOT_FOUND", "对话接口不存在")
    if handler.command not in ("GET", "POST"):
        fail(405, "METHOD_NOT_ALLOWED", "请求方式不受支持")
    accounts = handler.server.accounts
    sid = handler.token()
    with accounts.tx() as db:
        owner = current_actor(db, accounts, sid)
        handler.trace_actor = owner
        chat_schema(db)
        db.execute("CREATE INDEX IF NOT EXISTS hosted_chat_latest ON hosted_chat_turn(owner,state,base_revision)")
        db.execute("CREATE INDEX IF NOT EXISTS hosted_chat_quota ON hosted_chat_turn(created)")
        if handler.command == "GET":
            return status(db, owner, agent_ready())
    accounts.csrf(sid, handler.headers.get("X-CSRF-Token", ""))
    payload = handler.body(20000)
    if not isinstance(payload, dict) or set(payload) != {"expectedRevision","commandId","message"}:
        fail(422, "INVALID_CHAT_REQUEST", "请提交本次消息、会话版本和请求编号")
    base, command, text = (payload[key] for key in ("expectedRevision", "commandId", "message"))
    if (type(base) is not int or not 0 <= base <= 2147483647 or not isinstance(command, str) or
        not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", command) or not isinstance(text, str) or
        not 1 <= len(text.strip()) <= MAX_MESSAGE or any(0xD800 <= ord(c) <= 0xDFFF for c in text)):
        fail(422, "INVALID_CHAT_REQUEST", "消息须为 1 至 3000 字，并提供有效的会话版本和请求编号")
    text = text.strip()
    now = handler.server.clock()
    ready = agent_ready()
    with accounts.tx() as db:
        owner = current_actor(db, accounts, sid)
        db.execute("""UPDATE hosted_chat_turn SET state='failed',error_code='CHAT_PROVIDER_TIMEOUT'
            WHERE owner=? AND state='pending' AND created<=?""", (owner, now-RESERVATION))
        row = db.execute("SELECT * FROM hosted_chat_turn WHERE owner=? AND command_id=?", (owner,command)).fetchone()
        if row:
            if row["user_text"] != text or row["base_revision"] != base:
                fail(409, "COMMAND_CONFLICT", "此请求编号已用于另一条消息")
            if row["state"] == "done":
                return status(db, owner, ready)
            if row["state"] == "failed":
                raise_provider(row["error_code"])
            fail(409, "CHAT_BUSY", "上一条消息仍在处理")
        if not ready:
            raise_provider("CHAT_NOT_CONFIGURED")
        latest = history(db, owner, 8)
        revision = latest[-1]["base_revision"] + 1 if latest else 0
        if base != revision:
            fail(409, "CHAT_REVISION_CONFLICT", "会话已更新，请刷新后重试")
        if db.execute("SELECT 1 FROM hosted_chat_turn WHERE owner=? AND state='pending'", (owner,)).fetchone():
            fail(409, "CHAT_BUSY", "上一条消息仍在处理")
        recent, daily = db.execute("""SELECT COUNT(CASE WHEN created>? THEN 1 END), COUNT(*)
                FROM hosted_chat_turn WHERE owner=? AND created>?""", (now-60,owner,now-86400)).fetchone()
        if recent >= 10 or daily >= 120:
            fail(429, "CHAT_RATE_LIMIT", "本账号已达到对话调用限额", {"retryAfterSeconds": 60 if recent >= 10 else 3600})
        if db.execute("SELECT COUNT(*) FROM hosted_chat_turn WHERE state='pending' AND created>?",
                      (now-RESERVATION,)).fetchone()[0] >= 4:
            fail(429, "CHAT_CAPACITY", "当前对话请求较多，请稍后再试", {"retryAfterSeconds": 10})
        db.execute("""INSERT INTO hosted_chat_turn(owner,command_id,base_revision,user_text,created,state)
                   VALUES(?,?,?,?,?,'pending')""", (owner,command,base,text,now))
        messages = context(latest, text)
    result = call_agent(messages)
    code = result.get("error")
    with accounts.tx() as db:
        current_actor(db, accounts, sid)
        cursor = db.execute("""UPDATE hosted_chat_turn SET state=?,reply=?,model=?,error_code=?,receipt_json=?,private_snapshot=?,history_reply=?
                     WHERE owner=? AND command_id=? AND state='pending'""",
                 ("failed" if code else "done", result.get("reply"),result.get("model"),code,
                  json.dumps(result["receipt"],ensure_ascii=False) if "receipt" in result else None,
                  json.dumps(result["privateSnapshot"],ensure_ascii=False) if result.get("privateSnapshot") else None,
                  result.get("historyReply"),owner,command))
        if cursor.rowcount != 1:
            fail(409,"CHAT_REVISION_CONFLICT","会话已更新，请刷新后核对")
        output = status(db, owner, ready)
    if code:
        raise_provider(code)
    return output
