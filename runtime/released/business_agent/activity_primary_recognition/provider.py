"""Send one approved primary recognition request through the existing transport."""
from pathlib import Path
from urllib import error, request

from business_agent.workbench_deepseek import adapter

PROMPT_VERSION = "activity-primary-recognition-prompt/1.0.0"


def recognize(config, excerpts, *, transport=adapter.provider_call):
    config = adapter.checked_config(config)
    prompt = Path(__file__).with_name("prompt-v1.txt").read_text(encoding="utf-8")
    body = adapter.encode({
        "model": config.model,
        "messages": [{"role": "system", "content": prompt},
                     {"role": "user", "content": adapter.encode({"excerpts": excerpts}).decode()}],
        "response_format": {"type": "json_object"},
        "max_tokens": adapter.MAX_OUTPUT_TOKENS,
        "stream": False,
    })
    adapter.require(len(body) <= adapter.MAX_REQUEST_BYTES, adapter.INPUT_INVALID)
    outbound = request.Request(adapter.ENDPOINT, data=body, method="POST",
                               headers={"Authorization": "Bearer " + config.api_key,
                                        "Content-Type": "application/json"})
    try:
        raw = transport(outbound, adapter.HTTP_TIMEOUT_SECONDS)
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
    adapter.require(isinstance(raw, bytes) and 0 < len(raw) < adapter.MAX_RESPONSE_BYTES, adapter.INVALID_OUTPUT)
    value = adapter.decode(raw)
    adapter.require(isinstance(value, dict), adapter.INVALID_OUTPUT)
    choices = value.get("choices")
    adapter.require(isinstance(choices, list) and len(choices) == 1 and isinstance(choices[0], dict),
                    adapter.INVALID_OUTPUT)
    choice = choices[0]
    message = choice.get("message")
    model = value.get("model")
    adapter.require(choice.get("finish_reason") == "stop" and isinstance(message, dict)
                    and not message.get("tool_calls") and not message.get("function_call")
                    and isinstance(message.get("content"), str) and isinstance(model, str)
                    and adapter.MODEL_ID.fullmatch(model), adapter.INVALID_OUTPUT)
    reasoning = message.get("reasoning_content")
    usage = adapter.usage_metadata(value)
    return adapter.decode(message["content"]), {
        "model": model, "promptVersion": PROMPT_VERSION,
        "thinking": "enabled" if (isinstance(reasoning, str) and reasoning.strip())
        or usage.get("reasoning_tokens", 0) else "unknown",
    }
