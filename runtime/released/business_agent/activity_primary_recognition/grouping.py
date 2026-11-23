"""Merge only evidence-backed identities and project the source references."""
from collections import Counter
from datetime import datetime
import json

from .evidence import FIELDS, TIER


def _key(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _value(group, name):
    options = group["fields"].get(name, [])
    return options[0]["value"] if len(options) == 1 else None


def _kind(group):
    method = _value(group, "activityMethod")
    if method == "shen_jia":
        return "shen_jia"
    if (method and "_threshold_subsidy_" in method) or group["couponTiers"]:
        return "coupon"
    if any(TIER.search(item["value"]) for item in group["fields"].get("rulesText", [])):
        return "coupon"
    return "activity"


def _identity(group):
    kind = _kind(group)
    platform = _value(group, "platformCode")
    source_id = _value(group, "sourceActivityId")
    if not isinstance(platform, str) or not source_id:
        return None
    if kind == "shen_jia" and (platform != "meituan" or not source_id.isascii() or not source_id.isdigit()):
        return None
    # An incomplete excerpt may end in the middle of an ID or a constraint.
    if any(issue["code"] == "SOURCE_EXCERPT_TRUNCATED" for issue in group["issues"]):
        return None
    return {"kind": kind, "platformCode": platform, "sourceActivityId": source_id}


def _combine(items, item):
    for existing in items:
        if existing["value"] == item["value"]:
            existing["evidence"].extend(item["evidence"])
            return
    items.append({"value": item["value"], "evidence": list(item["evidence"])})


def _merge(groups):
    by_identity, merged = {}, []
    for raw in groups:
        group = raw | {"fields": {name: [item] for name, item in raw["fields"].items()}}
        for name in ("brands", "couponTiers"):
            group[name] = []
            for item in raw[name]:
                _combine(group[name], item)
        identity = _identity(group)
        key = _key(identity) if identity else None
        existing = by_identity.get(key)
        if existing is None:
            merged.append(group)
            if key:
                by_identity[key] = group
            continue
        for field, (item,) in group["fields"].items():
            _combine(existing["fields"].setdefault(field, []), item)
        for field in ("brands", "couponTiers"):
            for item in group[field]:
                _combine(existing[field], item)
        for field in ("candidateIds", "sourceMaterialIds"):
            existing[field] = list(dict.fromkeys(existing[field] + group[field]))
        existing["issues"].extend(group["issues"])
    return merged


def _reversed(start, end):
    first, last = datetime.fromisoformat(start.replace("Z", "+00:00")), datetime.fromisoformat(end.replace("Z", "+00:00"))
    # Date-only evidence never supplies an exact start or end time.
    if "T" in start and "T" in end and (first.tzinfo is None) == (last.tzinfo is None):
        return first > last
    return first.date() > last.date()


def project(groups):
    evidence_index, evidence, output = {}, [], []

    def bind(refs):
        ids = []
        for ref in refs:
            key = _key(ref)
            if key not in evidence_index:
                eid = f"primary-ev-{len(evidence) + 1}"
                evidence_index[key] = eid
                evidence.append({"evidenceId": eid, **ref})
            ids.append(evidence_index[key])
        return list(dict.fromkeys(ids))

    merged = _merge(groups)
    anchors = Counter(cid for group in merged for cid in group["candidateIds"])
    for group in merged:
        issues = []
        for issue in group["issues"]:
            issues.append({key: value for key, value in issue.items() if key != "evidence"}
                          | {"evidenceIds": bind(issue.get("evidence", []))})
        fields = {}
        for field in FIELDS:
            items = group["fields"].get(field, [])
            if not items:
                continue
            eids = bind(ref for item in items for ref in item["evidence"])
            if len(items) == 1:
                fields[field] = {"value": items[0]["value"], "status": "explicit", "evidenceIds": eids}
            else:
                fields[field] = {"value": None, "status": "conflict", "evidenceIds": eids}
                issues.append({"code": "FIELD_CONFLICT", "field": field, "evidenceIds": eids})
        method = _value(group, "activityMethod")
        platform = _value(group, "platformCode")
        source_id = _value(group, "sourceActivityId")
        start, end = (_value(group, field) for field in ("startAt", "endAt"))
        if method == "shen_jia" and platform is not None and platform != "meituan":
            issues.append({"code": "SHENJIA_PLATFORM_CONFLICT", "field": "platformCode",
                           "evidenceIds": fields["platformCode"]["evidenceIds"]})
        if method == "shen_jia" and source_id and (not source_id.isascii() or not source_id.isdigit()):
            issues.append({"code": "SHENJIA_MAIN_ID_UNVERIFIED", "field": "sourceActivityId",
                           "evidenceIds": fields["sourceActivityId"]["evidenceIds"]})
        if start and end and _reversed(start, end):
            issues.append({"code": "DATE_RANGE_CONFLICT", "evidenceIds":
                           fields["startAt"]["evidenceIds"] + fields["endAt"]["evidenceIds"]})
        required = ["activityName", "type", "activityMethod", "platformCode", "startAt", "endAt", "rulesText"]
        if method == "shen_jia":
            required.append("sourceActivityId")
        for field in required:
            if field not in fields:
                issues.append({"code": "MISSING_FIELD", "field": field})
        candidate_ids = group["candidateIds"]
        if any(anchors[cid] > 1 for cid in candidate_ids):
            issues.append({"code": "LOCAL_GROUP_AMBIGUOUS"})
        lists = {key: [{"value": item["value"], "evidenceIds": bind(item["evidence"])}
                       for item in group[key]] for key in ("brands", "couponTiers")}
        row_refs = [ref for item in (*fields.values(), *lists["brands"], *lists["couponTiers"], *issues)
                    for ref in item.get("evidenceIds", [])]
        output.append({"groupId": f"primary-group-{len(output) + 1}",
                       "candidateId": candidate_ids[0] if len(candidate_ids) == 1 else None,
                       "candidateIds": candidate_ids, "identity": _identity(group),
                       "activityMethod": method, "status": "needs_confirmation" if issues else "recognized",
                       "fields": fields, **lists, "evidenceIds": list(dict.fromkeys(row_refs)),
                       "sourceMaterialIds": group["sourceMaterialIds"], "issues": issues})
    ready = sum(group["status"] == "recognized" for group in output)
    status = "recognized" if output and ready == len(output) else "partial" if ready else "needs_confirmation"
    return {"status": status, "groups": output, "evidence": evidence}
