"""Bounded HTTP bridge to the Agent child launched by the same release."""
from contextlib import contextmanager
import http.client
import json
import os
import select
from pathlib import Path
import socket
import stat
import threading
import time


ANALYZE = "/v1/activity-agent/analyze"
READY = "/readyz"
LIMIT = 32 * 1024 * 1024
CHUNK = 64 * 1024
# The .49 planner permits up to 256 batches and one repair per batch, on three lanes.
PUBLIC_DEADLINE = 60 + ((256 + 2) // 3) * 2 * 120 + 30
PROXY_SLOTS = threading.BoundedSemaphore(2)


class BridgeError(Exception):
    def __init__(self, code, status=502):
        super().__init__(code)
        self.code, self.status = code, status


def _remaining(deadline, stop):
    if stop is not None and stop.is_set():
        raise BridgeError("INTERRUPTED", 503)
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise BridgeError("TIMEOUT", 504)
    return remaining


def _port():
    value = os.environ.get("JIATAN_ACTIVITY_AGENT_PORT", "")
    if (not value.isascii() or not value.isdecimal() or len(value) > 5
            or not 1024 <= int(value) <= 65535 or value == os.environ.get("PORT", "18766")):
        raise BridgeError("CHILD_UNAVAILABLE", 503)
    return int(value)


def _service_bearer():
    # Same private directory contract as gateway.py:init_token. No creation, logging or caching.
    parent = Path(os.environ.get("ACTIVITY_AGENT_CONFIG_DIR", str(Path.home() / ".activityagent")))
    if not parent.is_absolute() or ".." in parent.parts or any(path.is_symlink() for path in parent.parents):
        raise BridgeError("SERVICE_CREDENTIAL_UNAVAILABLE", 503)
    try:
        directory = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            info = os.fstat(directory)
            if stat.S_IMODE(info.st_mode) != 0o700 or info.st_uid != os.geteuid():
                raise BridgeError("SERVICE_CREDENTIAL_UNAVAILABLE", 503)
            fd = os.open("http.token", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
            with os.fdopen(fd, "rb") as stream:
                info = os.fstat(fd)
                if (not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600
                        or info.st_uid != os.geteuid() or info.st_nlink != 1 or not 32 <= info.st_size <= 4096):
                    raise BridgeError("SERVICE_CREDENTIAL_UNAVAILABLE", 503)
                token = stream.read(4097).strip()
        finally:
            os.close(directory)
    except OSError:
        raise BridgeError("SERVICE_CREDENTIAL_UNAVAILABLE", 503) from None
    if not 32 <= len(token) <= 4096 or any(c < 33 or c > 126 for c in token):
        raise BridgeError("SERVICE_CREDENTIAL_UNAVAILABLE", 503)
    return "Bearer " + token.decode("ascii")


def _size(headers, cap):
    fields = headers.get_all("Content-Length", [])
    if (len(fields) != 1 or not fields[0].isascii() or not fields[0].isdecimal()
            or len(fields[0]) > 9 or not 0 < int(fields[0]) <= cap):
        raise ValueError("invalid size")
    return int(fields[0])


@contextmanager
def _exchange(method, path, deadline, *, stop=None, length=0, bearer=None, read=None, stop_upload=None):
    """Caller supplies one of the fixed routes. Socket shutdown wakes blocked header/body I/O."""
    if (method, path) not in (("GET", READY), ("POST", ANALYZE)):
        raise ValueError("Unsupported Agent operation")
    client = http.client.HTTPConnection("127.0.0.1", _port(), timeout=min(3, _remaining(deadline, stop)))
    done = threading.Event()
    uploading = threading.Event()
    early_reply = threading.Event()
    watcher = interrupt = None
    try:
        client.connect()
        interrupt = client.sock.dup()
        def watch():
            while not done.wait(min(.2, max(.001, deadline - time.monotonic()))):
                # The Agent authenticates before reading the body and may immediately reject
                # the caller. Wake a slow uploader so it cannot occupy a slot until timeout.
                if stop_upload is not None and uploading.is_set() and not early_reply.is_set():
                    if select.select([interrupt], [], [], 0)[0]:
                        early_reply.set()
                        stop_upload()
                if time.monotonic() >= deadline or (stop is not None and stop.is_set()):
                    try:
                        interrupt.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                    return
        watcher = threading.Thread(target=watch, name="activity-agent-http-deadline", daemon=True)
        watcher.start()
        client.sock.settimeout(_remaining(deadline, stop))
        client.putrequest(method, path, skip_accept_encoding=True)
        client.putheader("Connection", "close")
        if method == "POST":
            client.putheader("Content-Type", "application/json")
            client.putheader("Content-Length", str(length))
            client.putheader("Authorization", bearer)
        client.endheaders()
        remaining = length
        uploading.set()
        while remaining and not early_reply.is_set():
            _remaining(deadline, stop)
            try:
                block = read(min(CHUNK, remaining))
            except OSError:
                if early_reply.is_set():
                    break
                raise
            if early_reply.is_set():
                break
            if not block or len(block) > remaining:
                raise BridgeError("INCOMPLETE_REQUEST", 400)
            try:
                client.send(block)
            except (BrokenPipeError, ConnectionResetError):
                # Gateway can reject an invalid caller token before reading its body.
                break
            remaining -= len(block)
        uploading.clear()
        with client.getresponse() as response:
            if response.headers.get("Transfer-Encoding") or response.headers.get_content_type() != "application/json":
                raise BridgeError("INVALID_AGENT_RESPONSE")
            try:
                size = _size(response.headers, LIMIT if path == ANALYZE else 8192)
            except ValueError:
                raise BridgeError("INVALID_AGENT_RESPONSE") from None
            yield response, size
    except (http.client.HTTPException, OSError):
        _remaining(deadline, stop)
        raise BridgeError("CHILD_UNAVAILABLE", 503) from None
    finally:
        done.set()
        if watcher is not None:
            watcher.join()
        client.close()
        if interrupt is not None:
            interrupt.close()


def _read_exact(response, size):
    result = response.read(size)
    if len(result) != size:
        raise BridgeError("INCOMPLETE_AGENT_RESPONSE")
    return result


def agent_ready(*, require_token=False, timeout=5):
    try:
        with _exchange("GET", READY, time.monotonic() + timeout) as (res, length):
            state = json.loads(_read_exact(res, length)) if res.status == 200 else None
        if not isinstance(state, dict) or not isinstance(state.get("configuredModel"), str) or not state["configuredModel"].strip():
            return False
        if not all(state.get(key) is True for key in ("ready", "mysqlClient", "mysqlProfile", "activeCasesAvailable")):
            return False
        if require_token:
            _service_bearer()
        return True
    except (BridgeError, ValueError, RecursionError):
        return False


def analyze(payload, stop, deadline):
    """Workbench-only entry. Receives the byte-exact .49 envelope and returns its raw receipt."""
    if not isinstance(payload, bytes) or not 0 < len(payload) <= LIMIT:
        raise BridgeError("INPUT_INVALID", 413)
    import io
    _port()  # No child binding: fail before reading the service credential.
    with _exchange("POST", ANALYZE, deadline, stop=stop, length=len(payload),
                   bearer=_service_bearer(), read=io.BytesIO(payload).read) as (res, size):
        if res.status != 200:
            code = {400:"INPUT_INVALID", 401:"SERVICE_AUTH", 413:"INPUT_INVALID", 415:"INPUT_INVALID",
                    429:"AGENT_BUSY", 503:"UNAVAILABLE", 504:"TIMEOUT"}.get(res.status, "INVALID_OUTPUT")
            raise BridgeError(code, res.status)
        return _read_exact(res, size)


def _error(handler, status, code):
    raw = json.dumps({"error":code}, separators=(",", ":")).encode()
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(raw)))
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Connection", "close")
    handler.end_headers()
    if handler.command != "HEAD":
        handler.wfile.write(raw)


def serve_agent_route(handler):
    """Handle the two public Agent routes after host/origin checks. Never use service credentials."""
    path, method = handler.path, handler.command
    if path not in (READY, ANALYZE):
        return False
    handler.close_connection = True
    length, bearer, acquired, started = 0, None, False, False
    try:
        if (method, path) not in (("GET", READY), ("POST", ANALYZE)):
            raise BridgeError("METHOD_NOT_ALLOWED", 405)
        if method == "POST":
            auth = handler.headers.get_all("Authorization", [])
            if (len(auth) != 1 or not auth[0].startswith("Bearer ") or not 7 < len(auth[0]) <= 4103
                    or any(ord(c) < 33 or ord(c) > 126 for c in auth[0][7:])):
                raise BridgeError("UNAUTHORIZED", 401)
            bearer = auth[0]
            if (len(handler.headers.get_all("Content-Type", [])) != 1
                    or handler.headers.get_content_type() != "application/json" or handler.headers.get("Transfer-Encoding") is not None):
                raise BridgeError("JSON_BODY_REQUIRED", 415)
            try:
                length = _size(handler.headers, LIMIT)
            except ValueError:
                raise BridgeError("REQUEST_SIZE_INVALID", 413) from None
            acquired = PROXY_SLOTS.acquire(blocking=False)
            if not acquired:
                raise BridgeError("AGENT_BUSY", 429)
        deadline = time.monotonic() + (PUBLIC_DEADLINE if method == "POST" else 5)
        def stop_upload():
            connection = getattr(handler, "connection", None)
            if connection is not None:
                try:
                    connection.shutdown(socket.SHUT_RD)
                except OSError:
                    pass
        with _exchange(method, path, deadline, length=length, bearer=bearer, read=handler.rfile.read1,
                       stop_upload=stop_upload if method == "POST" else None) as (res, size):
            handler.send_response(res.status)
            handler.send_header("Content-Type", "application/json; charset=utf-8")
            handler.send_header("Content-Length", str(size))
            handler.send_header("Connection", "close")
            handler.send_header("Cache-Control", "no-store")
            handler.end_headers()
            started = True
            while size:
                chunk = res.read1(min(CHUNK, size))
                if not chunk:
                    raise BridgeError("INCOMPLETE_AGENT_RESPONSE")
                handler.wfile.write(chunk)
                size -= len(chunk)
    except BridgeError as error:
        if not started:
            _error(handler, error.status, error.code)
    except (BrokenPipeError, ConnectionResetError, TimeoutError):
        pass
    finally:
        if acquired:
            PROXY_SLOTS.release()
    return True
