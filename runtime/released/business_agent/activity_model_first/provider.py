"""Bounded calls to the existing fixed DeepSeek endpoint."""
from pathlib import Path
from urllib import error, request

from business_agent.workbench_deepseek import adapter
from .diagnostics import RecognitionError, require
from .large_shenjia import LAYOUT as SHENJIA_LAYOUT
from .source import EXCEL_LAYOUT

PROMPT_VERSION = "activity-model-first-prompt/1.1.1"
MAX_COMPLETION_TOKENS = 16384
HTTP_TIMEOUT_SECONDS = 120
MAX_MODEL_REQUEST_BYTES = 128 * 1024


def call(config, batch, transport, *, repair=None, ledger=False):
    prompt = Path(__file__).with_name("prompt.txt").read_text(encoding="utf-8")
    if repair is not None:
        correction = adapter.encode(repair).decode()
        prompt += (
            "\n上次此批输出未通过校验，校验信息为 " + correction + "。请重新阅读下面同一批原文，"
            '只返回 {"groups":[...],"rows":[...]} 这一个 JSON 对象，两个键的值都必须是数组。'
            "逐一返回 rows 中每个输入 id，且每个 id 只出现一次。活动字段引用本批的原文 token。"
            "商品字段只引用该商品所在行的 token，子串必须逐字出现在该单元格。没有依据的字段省略。"
        )
    if ledger:
        prompt += "\n\n" + Path(__file__).with_name("ledger-prompt-v1.txt").read_text(encoding="utf-8")
    shenjia = batch.get("layout") == SHENJIA_LAYOUT
    if shenjia:
        prompt += ("\n本批按原表明确的神价主 ID 分区，context 保留该主活动的表头和身份原文。"
                   "每条商品需引用其本行字段并归属对应主 ID，不按品牌或子活动 ID 拆分。"
                   "返回 childActivityId 的原单元格 token。同子活动 ID 多个 UPC 的行仍逐条返回。"
                   "只分类 rows，context 不再生成商品；没有原文支持的字段仍省略。")
    output_tokens = min(MAX_COMPLETION_TOKENS, max(4096, 2048 + 176 * len(batch["rows"]))) if shenjia else MAX_COMPLETION_TOKENS
    payload = {"model": config.model, "messages": [
        {"role": "system", "content": prompt},
        {"role": "user", "content": adapter.encode(batch).decode()},
    ], "response_format": {"type": "json_object"}, "max_tokens": output_tokens, "stream": False}
    if shenjia or repair is not None and repair.get("reason") == "provider_truncated_output":
        payload["thinking"] = {"type": "disabled"}
    elif len(batch["rows"]) >= 25:
        payload["reasoning_effort"] = "low"
    body = adapter.encode(payload)
    adapter.require(len(body) <= (MAX_MODEL_REQUEST_BYTES if shenjia or batch.get("layout") == EXCEL_LAYOUT else adapter.MAX_REQUEST_BYTES),
                    "MODEL_INPUT_ROW_LIMIT")
    wire = request.Request(adapter.ENDPOINT, data=body, method="POST",
                           headers={"Authorization": "Bearer " + config.api_key, "Content-Type": "application/json"})
    try:
        raw = transport(wire, HTTP_TIMEOUT_SECONDS)
    except error.HTTPError as exc:
        code = {401: "PROVIDER_AUTH", 403: "PROVIDER_AUTH", 402: "PROVIDER_QUOTA", 429: "PROVIDER_QUOTA",
                408: "TIMEOUT", 504: "TIMEOUT"}.get(exc.code, "PROVIDER_UNAVAILABLE")
        exc.close()
        raise adapter.AdapterError("MODEL_RECOGNITION_" + code) from None
    except error.URLError as exc:
        code = "TIMEOUT" if isinstance(exc.reason, TimeoutError) else "PROVIDER_UNAVAILABLE"
        raise adapter.AdapterError("MODEL_RECOGNITION_" + code) from None
    except TimeoutError:
        raise adapter.AdapterError("MODEL_RECOGNITION_TIMEOUT") from None
    except OSError:
        raise adapter.AdapterError("MODEL_RECOGNITION_PROVIDER_UNAVAILABLE") from None
    require(isinstance(raw, bytes) and 0 < len(raw) < adapter.MAX_RESPONSE_BYTES, "provider_envelope_size")
    try:
        response = adapter.decode(raw)
    except adapter.AdapterError:
        raise RecognitionError(adapter.INVALID_OUTPUT, "provider_envelope_json") from None
    require(isinstance(response, dict), "provider_envelope_shape")
    choices, model = response.get("choices"), response.get("model")
    require(isinstance(choices, list) and len(choices) == 1 and isinstance(choices[0], dict)
            and isinstance(model, str) and adapter.MODEL_ID.fullmatch(model), "provider_choices_shape")
    choice = choices[0]
    finish = choice.get("finish_reason")
    usage = adapter.usage_metadata(response)
    message = choice.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    details = {"finishReason": finish if finish in ("stop", "length", "content_filter", "tool_calls") else "other",
               "visibleContentChars": len(content) if isinstance(content, str) else 0}
    for source, target in (("completion_tokens", "completionTokens"), ("reasoning_tokens", "reasoningTokens")):
        if source in usage:
            details[target] = usage[source]
    if finish == "length":
        raise RecognitionError("MODEL_RECOGNITION_OUTPUT_LIMIT", "provider_truncated_output", **details)
    require(finish == "stop" and isinstance(message, dict) and not message.get("tool_calls")
            and not message.get("function_call"), "provider_finish_or_message_shape", **details)
    require(isinstance(content, str) and bool(content.strip()), "provider_empty_content",
            code="MODEL_RECOGNITION_EMPTY_CONTENT", **details)
    try:
        result = adapter.decode(content)
    except adapter.AdapterError:
        raise RecognitionError("MODEL_RECOGNITION_JSON_INVALID", "provider_visible_json", **details) from None
    reasoning = message.get("reasoning_content")
    observed = (isinstance(reasoning, str) and bool(reasoning.strip())) or bool(usage.get("reasoning_tokens"))
    return result, model, observed
