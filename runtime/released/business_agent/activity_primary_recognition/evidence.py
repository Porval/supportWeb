"""Bind model fields to approved source excerpts and stable activity identities."""
from datetime import date, datetime
import re
import unicodedata

from business_agent.workbench_deepseek.adapter import (
    INPUT_INVALID, INVALID_OUTPUT, MAX_EXCERPTS, MAX_GROUPS, prepare_excerpts, require,
)

FIELDS = ("activityName", "type", "activityMethod", "platformCode", "sourceActivityId",
          "startAt", "endAt", "rulesText")
PLATFORMS = {"meituan": ("美团", "meituan"), "taobao_instant": ("淘宝闪购", "淘闪", "taobao_instant"),
             "jiatan": ("价探", "jiatan")}
TYPE_LABELS = {"platform": ("平台活动", "平台发起", "平台主办"),
               "brand": ("品牌活动", "品牌发起", "品牌主办"),
               "jiatan_sourcing": ("价探采销", "价探采购", "价探招商")}
METHOD_LABELS = {"shen_jia": "神价", "bao_hao_jia": "爆好价", "platform_red_packet": "平台红包"}
SUBSIDY_LABELS = {"single": ("单方", "一方", "1方"), "dual": ("双方", "两方", "二方", "2方"),
                  "triple": ("三方", "3方")}
SHA = re.compile(r"[0-9a-f]{64}")
SOURCE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,199}")
MAIN_LABEL = r"(?:神价主活动(?:编号|ID)|美团主活动(?:编号|ID)|主活动(?:编号|ID)|神价ID|平台活动(?:编号|ID)|活动(?:编号|ID))"
TIER = re.compile(r"满\s*\d+(?:\.\d+)?\s*(?:元)?\s*减\s*\d+(?:\.\d+)?")
DATE_TOKEN = re.compile(
    r"(?<!\d)(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})日?"
    r"(?:(?:[T\s]+|(?<=日))(\d{1,2}):(\d{2})(?::(\d{2}))?(Z|[+-]\d{2}:\d{2})?)?(?!\d)"
)
EXCLUDED = re.compile(r"https?://|\bBearer\s+\S+|\bsk-[A-Za-z0-9_-]{16,}|"
                      r"(?:密钥|密码|访问令牌|api[_ -]?key|authorization)\s*[:：=]", re.I)
EXAMPLE = re.compile(r"^\s*(?:示例(?:行)?|example|sample|请填写|待填写)\s*(?:[:：]|$)", re.I)
MAX_ITEMS = 24
TEXT_TITLE = re.compile(r"(?:活动名称|活动名|活动主题)\s*[:：]\s*\S")
TEXT_FIELD = re.compile(
    r"(?:品牌(?:发起|主办)|平台(?:发起|主办)|"
    r"(?:活动(?:类型|方式|日期|时间|周期|范围)|开始日期|结束日期|开始时间|结束时间|"
    r"报名编号所属平台|适用平台|平台|适用范围|品牌(?:名称)?|"
    r"优惠(?:规则|机制|条件)|活动规则|券档)\s*[:：]|"
    + MAIN_LABEL + r"\s*[:：])", re.I
)
EXPLICIT_MAIN_ID = re.compile(r"(?<!子)" + MAIN_LABEL + r"\s*[:：=]\s*([A-Za-z0-9][A-Za-z0-9_-]{0,199})", re.I)
SEPARATE_ACTIVITY = re.compile(r"另有|另设|另一个活动|另一场|独立(?:活动|神价)|单独提报|另行报名")


def _plain(value):
    return unicodedata.normalize("NFKC", value).strip()


def _extend_text_anchor(local, anchors):
    """Add unowned lines only inside one complete, explicitly labelled activity."""
    if len(set(anchors.values())) != 1:
        return anchors
    entries = list(local.values())
    source = {(item["materialId"], item["sourceSha256"], item["locator"]["kind"]) for item in entries}
    if len(source) != 1 or any(not item.get("complete", True) for item in entries):
        return anchors
    _, _, kind = next(iter(source))
    position = {"text": "line", "docx": "paragraph"}.get(kind)
    if position is None or any(type(item["locator"].get(position)) is not int for item in entries):
        return anchors
    numbered = {item["locator"][position]: item for item in entries}
    titles = [item for item in entries if TEXT_TITLE.match(item["text"].strip())]
    if len(numbered) != len(entries) or len(titles) != 1 or titles[0]["excerptId"] not in anchors:
        return anchors
    if any(SEPARATE_ACTIVITY.search(item["text"]) for item in entries):
        return anchors
    all_ids = {activity_id for item in entries for activity_id in EXPLICIT_MAIN_ID.findall(item["text"])}
    if len(all_ids) != 1:
        return anchors

    title, = titles
    cid = anchors[title["excerptId"]]
    group = [title]
    line = title["locator"][position] + 1
    while line in numbered and TEXT_FIELD.match(numbered[line]["text"].strip()):
        group.append(numbered[line])
        line += 1
    scope = {item["excerptId"]: cid for item in group}
    if not anchors.keys() <= scope.keys() or not any(EXPLICIT_MAIN_ID.search(item["text"]) for item in group):
        return anchors
    return scope


def prepare(payload):
    """Validate the backend's approved excerpts before opening model credentials."""
    allowed = {"contractVersion", "inputRevision", "inputDigest", "analysisDigest", "consent", "excerpts",
               "candidateAnchors", "fieldHints"}
    required = {"contractVersion", "inputRevision", "inputDigest", "analysisDigest", "consent", "excerpts"}
    require(isinstance(payload, dict) and required <= payload.keys() <= allowed
            and payload["consent"] is True and type(payload["inputRevision"]) is int
            and payload["inputRevision"] > 0 and isinstance(payload["inputDigest"], str)
            and SHA.fullmatch(payload["inputDigest"]) and isinstance(payload["analysisDigest"], str)
            and payload["analysisDigest"].startswith("sha256:")
            and SHA.fullmatch(payload["analysisDigest"][7:]), INPUT_INVALID)
    excerpts = payload["excerpts"]
    require(isinstance(excerpts, list) and 0 < len(excerpts) <= MAX_EXCERPTS, INPUT_INVALID)
    local, safe = {}, []
    for entry in excerpts:
        keys = {"excerptId", "materialId", "sourceSha256", "locator", "text"}
        require(isinstance(entry, dict) and keys <= entry.keys() <= keys | {"complete"}
                and type(entry.get("complete", True)) is bool
                and isinstance(entry["sourceSha256"], str) and SHA.fullmatch(entry["sourceSha256"])
                and isinstance(entry["excerptId"], str) and isinstance(entry["text"], str)
                and not EXCLUDED.search(entry["text"]) and not EXAMPLE.match(entry["text"]), INPUT_INVALID)
        local[entry["excerptId"]] = entry
        safe.append({key: value for key, value in entry.items() if key not in {"sourceSha256", "complete"}})
    outbound, _, anchors = prepare_excerpts(safe, payload.get("candidateAnchors"))
    anchors = _extend_text_anchor(local, anchors)
    for item in outbound:
        if cid := anchors.get(item["excerptId"]):
            item["candidateId"] = cid
    hints = payload.get("fieldHints", {})
    require(isinstance(hints, dict) and hints.keys() <= local.keys(), INPUT_INVALID)
    for values in hints.values():
        require(isinstance(values, list) and 0 < len(values) <= 3
                and all(isinstance(name, str) and name in {"sourceActivityId", "startAt", "endAt"} for name in values)
                and len(set(values)) == len(values), INPUT_INVALID)
    return outbound, local, anchors, hints


def _read_refs(refs, local, anchors):
    require(isinstance(refs, list) and 0 < len(refs) <= MAX_EXCERPTS, INVALID_OUTPUT)
    evidence, seen = [], set()
    for ref in refs:
        require(isinstance(ref, dict) and set(ref) == {"excerptId", "quote"}, INVALID_OUTPUT)
        eid, quote = ref["excerptId"], ref["quote"]
        require(isinstance(eid, str) and eid in local and isinstance(quote, str) and 0 < len(quote) <= 500
                and quote.strip() and quote in local[eid]["text"] and (eid, quote) not in seen, INVALID_OUTPUT)
        seen.add((eid, quote))
        evidence.append({key: local[eid][key] for key in ("excerptId", "materialId", "sourceSha256", "locator")}
                        | {"quote": quote})
    return evidence, {anchors.get(ref["excerptId"]) for ref in evidence}


def _date_from_token(match):
    year, month, day, hour, minute, second, zone = match.groups()
    value = f"{year}-{int(month):02d}-{int(day):02d}"
    if hour is None:
        return date.fromisoformat(value).isoformat()
    value += f"T{int(hour):02d}:{minute}"
    if second is not None:
        value += ":" + second
    if zone:
        value += zone.replace("Z", "+00:00")
    datetime.fromisoformat(value)
    return value


def _supported_date(field, value, refs, hints):
    value = value.replace("Z", "+00:00")
    for ref in refs:
        quote = _plain(ref["quote"])
        tokens = list(DATE_TOKEN.finditer(quote))
        try:
            dates = [_date_from_token(token) for token in tokens]
        except ValueError:
            continue
        label = r"开始(?:日期|时间)?|起始(?:日期|时间)?" if field == "startAt" else r"结束(?:日期|时间)?|截止(?:日期|时间)?"
        if len(tokens) == 1 and dates[0] == value and (
                re.search(label, quote[:tokens[0].start()]) or field in hints.get(ref["excerptId"], [])):
            return True
        if (len(tokens) == 2 and re.fullmatch(r"\s*(?:至|到|~|—|–|-)\s*", quote[tokens[0].end():tokens[1].start()])
                and dates[field == "endAt"] == value):
            return True
    return False


def _method_supported(value, quotes):
    if value in METHOD_LABELS:
        label = METHOD_LABELS[value]
        return any(label in quote and not re.search(r"(?:非|不参加|不属于)\s*" + label, quote) for quote in quotes)
    if value == "other":
        return any(re.search(r"(?:活动方式|玩法)\s*[:：]?\s*其他", quote) for quote in quotes)
    match = re.fullmatch(r"(category|brand)_threshold_subsidy_(single|dual|triple)", value)
    if not match or match.groups() == ("category", "triple"):
        return False
    category, share = match.groups()
    label = "品类" if category == "category" else "品牌"
    return any(label + "满减" in quote and any(party in quote for party in SUBSIDY_LABELS[share])
               for quote in quotes)


def _field_supported(field, value, refs, hints):
    quotes = [_plain(ref["quote"]) for ref in refs]
    if field == "platformCode":
        platforms = [value] if isinstance(value, str) else value
        return (isinstance(platforms, list) and 0 < len(platforms) <= len(PLATFORMS)
                and all(isinstance(code, str) and code in PLATFORMS for code in platforms)
                and len(platforms) == len(set(platforms))
                and all(any(alias in quote.lower() for quote in quotes for alias in PLATFORMS[code])
                        or code == "meituan" and _method_supported("shen_jia", quotes) for code in platforms))
    require(isinstance(value, str) and 0 < len(value) <= 500 and value.strip() == value, INVALID_OUTPUT)
    if field in {"activityName", "rulesText"}:
        return any(_plain(value) in quote for quote in quotes)
    if field == "sourceActivityId":
        if not SOURCE_ID.fullmatch(value):
            return False
        token = re.escape(value) + r"(?![A-Za-z0-9_-])"
        return any(re.search(r"(?<!子)" + MAIN_LABEL + r"\s*[:：=]?\s*" + token, ref["quote"], re.I)
                   or ("sourceActivityId" in hints.get(ref["excerptId"], [])
                       and ref["quote"].strip() == value) for ref in refs)
    if field == "type":
        return any(re.search(re.escape(label) + r"(?!\s*(?:编号|ID))", quote, re.I)
                   for quote in quotes for label in TYPE_LABELS.get(value, ()))
    if field == "activityMethod":
        return _method_supported(value, quotes)
    return _supported_date(field, value, refs, hints)


def _item(entry, local, anchors, hints, *, field=None):
    keys = {"value", "refs"} | ({"field"} if field else set())
    require(isinstance(entry, dict) and set(entry) == keys, INVALID_OUTPUT)
    refs, owners = _read_refs(entry["refs"], local, anchors)
    value = entry["value"]
    if field:
        supported = _field_supported(field, value, refs, hints)
    else:
        require(isinstance(value, str) and 0 < len(value) <= 200 and value.strip() == value, INVALID_OUTPUT)
        supported = any(value in ref["quote"] for ref in refs)
    return {"value": value, "evidence": refs}, owners, supported


def checked_groups(output, local, anchors, hints):
    require(isinstance(output, dict) and set(output) == {"groups"} and isinstance(output["groups"], list)
            and len(output["groups"]) <= MAX_GROUPS, INVALID_OUTPUT)
    parsed = []
    for raw in output["groups"]:
        require(isinstance(raw, dict) and set(raw) == {"candidateId", "fields", "brands", "couponTiers"}, INVALID_OUTPUT)
        cid = raw["candidateId"]
        require(cid is None or isinstance(cid, str) and cid in anchors.values(), INVALID_OUTPUT)
        fields, brands, tiers, issues, group_refs = {}, [], [], [], []
        seen = set()
        for key, bucket, limit in (("fields", fields, len(FIELDS)), ("brands", brands, MAX_ITEMS),
                                   ("couponTiers", tiers, MAX_ITEMS)):
            require(isinstance(raw[key], list) and len(raw[key]) <= limit, INVALID_OUTPUT)
            for entry in raw[key]:
                field = entry.get("field") if key == "fields" and isinstance(entry, dict) else None
                if key == "fields":
                    require(isinstance(field, str) and field in FIELDS and field not in seen, INVALID_OUTPUT)
                    seen.add(field)
                item, owners, supported = _item(entry, local, anchors, hints, field=field)
                require(owners == {cid}, INVALID_OUTPUT)
                group_refs.extend(item["evidence"])
                if key == "couponTiers":
                    supported = supported and TIER.search(_plain(item["value"])) is not None
                if not supported:
                    issues.append({"code": "FIELD_EVIDENCE_UNSUPPORTED", "field": field or key,
                                   "evidence": item["evidence"]})
                elif field:
                    bucket[field] = item
                else:
                    bucket.append(item)
        if group_refs:
            truncated = [ref for ref in group_refs if not local[ref["excerptId"]].get("complete", True)]
            if truncated:
                issues.append({"code": "SOURCE_EXCERPT_TRUNCATED", "evidence": truncated})
            parsed.append({"candidateIds": [cid] if cid else [], "fields": fields, "brands": brands,
                           "couponTiers": tiers, "issues": issues,
                           "sourceMaterialIds": list(dict.fromkeys(ref["materialId"] for ref in group_refs))})
    return parsed
