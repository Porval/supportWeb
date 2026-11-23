"""Configured HTTPS client for the Data Agent's read-only goods report tool."""
from datetime import timedelta
from decimal import Decimal
from http.client import HTTPException
import json
import math
import os
import re
import ssl
import stat
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPSHandler, HTTPRedirectHandler, Request, build_opener
from uuid import uuid4

from .comparison import (
    FIELDS, MAX_DAYS, PLATFORMS, SOURCE_VERSION, SourceUnavailable, UnsupportedScope,
    _day, _identifiers,
)

URL_ENV = "JIATAN_DATA_AGENT_SALES_URL"
TOKEN_ENV = "JIATAN_DATA_AGENT_SALES_TOKEN"
TIMEOUT_ENV = "JIATAN_DATA_AGENT_SALES_TIMEOUT_SECONDS"
REQUEST_ID = "X-Request-ID"
MAX_BODY_BYTES = 32 * 1024 * 1024
MAX_REQUEST_BYTES = 1024 * 1024
QUERY_KEYS = {"report", "platformCode", "internalCodes", "platformStoreIds", "periods", "fields"}
DAY = timedelta(days=1)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _endpoint(value):
    if not isinstance(value, str) or any(ord(c) <= 32 or ord(c) >= 127 for c in value):
        raise ValueError("Data Agent endpoint must be an HTTPS URL")
    try:
        url = urlsplit(value)
        port = url.port
    except ValueError:
        raise ValueError("Data Agent endpoint must be an HTTPS URL") from None
    if (
        url.scheme != "https" or not url.hostname or port == 0 or url.username is not None
        or not url.path or url.path == "/" or url.query or url.fragment or "\\" in value
    ):
        raise ValueError("Data Agent endpoint must be a dedicated HTTPS URL without credentials or parameters")
    return url.geturl()


def _validate_query(query):
    if not isinstance(query, dict) or set(query) != QUERY_KEYS or query["report"] != "goods_report":
        raise ValueError("Invalid goods_report query fields")
    if query["platformCode"] not in PLATFORMS:
        raise ValueError("Invalid goods_report platform")
    if not _identifiers(query["internalCodes"]):
        raise ValueError("The goods_report item scope must not be empty")
    for name in ("internalCodes", "platformStoreIds"):
        if len(query[name]) != len(_identifiers(query[name])):
            raise ValueError("Duplicate goods_report identifiers")
    fields = query["fields"]
    if not isinstance(fields, list) or not fields or len(fields) > len(FIELDS):
        raise ValueError("Invalid goods_report metrics")
    if any(not isinstance(field, str) or field not in FIELDS.values() for field in fields) or len(set(fields)) != len(fields):
        raise ValueError("Invalid goods_report metrics")
    periods = query["periods"]
    if not isinstance(periods, dict) or not 1 <= len(periods) <= 3:
        raise ValueError("Invalid goods_report periods")
    windows = []
    for key, period in periods.items():
        if (not isinstance(key, str)
                or not re.fullmatch(r"current|previous|trailing_28_days|[0-9]{4}-(?:0[1-9]|1[0-2])", key)
                or not isinstance(period, dict) or set(period) != {"startDate", "endDate"}):
            raise ValueError("Invalid goods_report period fields")
        start, end = _day(period["startDate"]), _day(period["endDate"])
        if not 0 <= (end - start).days < MAX_DAYS:
            raise ValueError("Invalid goods_report date range")
        windows.append((start, end))
    windows.sort()
    if any((second[0] - first[1]) != DAY for first, second in zip(windows, windows[1:])):
        raise ValueError("Goods report periods must be adjacent")


def _pairs(pairs):
    parsed = {}
    for key, value in pairs:
        if key in parsed:
            raise ValueError("Duplicate JSON key")
        parsed[key] = value
    return parsed


def _reject_constant(_):
    raise ValueError("Invalid JSON number")


def _read_json(response, cap):
    if response.headers.get_content_type() != "application/json":
        raise ValueError("Expected application/json")
    if response.headers.get("Content-Encoding", "identity").strip().lower() != "identity":
        raise ValueError("Unexpected response encoding")
    lengths = response.headers.get_all("Content-Length", [])
    length = int(lengths[0]) if len(lengths) == 1 else None
    if len(lengths) > 1 or (length is not None and not 0 < length <= cap):
        raise ValueError("Invalid response length")
    content = response.read(cap + 1)
    if not content or len(content) > cap or (length is not None and len(content) != length):
        raise ValueError("Invalid response length")
    return json.loads(content.decode("utf-8"), object_pairs_hook=_pairs, parse_float=Decimal,
                      parse_constant=_reject_constant)


def _http_error(error, request_id):
    if error.code in (401, 403):
        return SourceUnavailable("SOURCE_AUTH_DENIED", "数据 Agent 未接受价探的服务凭据或查询权限。")
    if error.code in (404, 405):
        return SourceUnavailable("SOURCE_ROUTE_UNAVAILABLE", "数据 Agent 的只读查询入口不存在或未开放。")
    if error.code == 429:
        return SourceUnavailable("SOURCE_RATE_LIMITED", "数据 Agent 查询限流，请稍后重试。")
    if error.code == 422 and error.headers and error.headers.get(REQUEST_ID) == request_id:
        try:
            result = _read_json(error, 8192)
        except (ValueError, OSError, HTTPException):
            result = None
        if isinstance(result, dict) and result.get("code") == "UNSUPPORTED_SCOPE":
            return UnsupportedScope()
    if 300 <= error.code <= 399:
        return SourceUnavailable("SOURCE_REDIRECT", "数据 Agent 返回跳转，请核对配置的只读入口。")
    return SourceUnavailable("SOURCE_HTTP_ERROR", "数据 Agent 当前无法完成只读查询。")


class DataAgentGoodsReport:
    """A server-configured HTTPS source for sales_history and compare_sales."""

    def __init__(self, endpoint, token, *, timeout=15):
        self._endpoint = _endpoint(endpoint)
        if not isinstance(token, str) or not re.fullmatch(r"[!-~]{1,2048}", token):
            raise ValueError("Data Agent service token must be configured")
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 1 <= timeout <= 60:
            raise ValueError("Data Agent network timeout must be 1 to 60 seconds")
        self._token = token
        self._timeout = timeout
        self._opener = build_opener(_NoRedirect(), HTTPSHandler(context=ssl.create_default_context()))

    def __call__(self, query):
        _validate_query(query)
        payload = json.dumps(query, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(payload) > MAX_REQUEST_BYTES:
            raise ValueError("Goods report query exceeds the transport budget")
        request_id = uuid4().hex
        request = Request(self._endpoint, data=payload, method="POST", headers={
            "Authorization": "Bearer " + self._token,
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Accept-Encoding": "identity",
            REQUEST_ID: request_id,
        })
        try:
            with self._opener.open(request, timeout=self._timeout) as response:
                if response.status != 200 or response.headers.get(REQUEST_ID) != request_id:
                    raise ValueError("Response request binding failed")
                receipt = _read_json(response, MAX_BODY_BYTES)
                if (not isinstance(receipt, dict) or receipt.get("schemaVersion") != SOURCE_VERSION
                        or receipt.get("report") != "goods_report" or receipt.get("request") != query):
                    raise ValueError("Response scope binding failed")
                return receipt
        except HTTPError as exc:
            with exc:
                raise _http_error(exc, request_id) from None
        except ssl.SSLError:
            raise SourceUnavailable("SOURCE_TLS_FAILED", "数据 Agent 的 HTTPS 证书或连接无法验证。") from None
        except TimeoutError:
            raise
        except URLError as exc:
            if isinstance(exc.reason, TimeoutError):
                raise TimeoutError() from None
            if isinstance(exc.reason, ssl.SSLError):
                raise SourceUnavailable("SOURCE_TLS_FAILED", "数据 Agent 的 HTTPS 证书或连接无法验证。") from None
            raise SourceUnavailable("SOURCE_NETWORK_ERROR", "当前无法连接数据 Agent 的只读入口。") from None
        except (ValueError, RecursionError, HTTPException):
            raise SourceUnavailable("INVALID_SOURCE_RECEIPT", "数据 Agent 未返回可核对的日报回执。") from None
        except OSError:
            raise SourceUnavailable("SOURCE_NETWORK_ERROR", "当前无法读取数据 Agent 的回执。") from None


def source_from_env(environ=None):
    """Build the optional source from server-only configuration."""
    config = os.environ if environ is None else environ
    endpoint = config.get(URL_ENV)
    token = config.get(TOKEN_ENV)
    if endpoint is None and token is None and TIMEOUT_ENV not in config:
        return None
    try:
        timeout = float(config.get(TIMEOUT_ENV, "15"))
    except (TypeError, ValueError):
        raise ValueError("Data Agent network timeout must be 1 to 60 seconds") from None
    return DataAgentGoodsReport(endpoint, token, timeout=timeout)


def source_from_private_file(path):
    """Read the activity Agent's configuration without exporting it to the caller."""
    try:
        if not os.path.isabs(path):
            raise ValueError()
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
        with os.fdopen(os.open(path, flags), "rb") as settings:
            info = os.fstat(settings.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or stat.S_IMODE(info.st_mode) not in (0o400, 0o600)):
                raise ValueError()
            raw = settings.read(8193)
        if len(raw) > 8192:
            raise ValueError()
        config = {}
        for line in raw.decode("utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            key, separator, value = line.partition("=")
            if not separator or key not in (URL_ENV, TOKEN_ENV, TIMEOUT_ENV) or key in config:
                raise ValueError()
            config[key] = value
        if not config.get(URL_ENV) or not config.get(TOKEN_ENV):
            raise ValueError()
        return source_from_env(config)
    except (OSError, TypeError, ValueError):
        raise SourceUnavailable("SOURCE_CONFIG_INVALID", "活动 Agent 无法读取线上数据 Agent 的私有配置。") from None
