"""Check model citations and assemble its observed activities and goods."""
from collections import Counter, defaultdict
from datetime import date, datetime
import re

from business_agent.activity_primary_recognition.evidence import MAIN_LABEL, _field_supported
from business_agent.workbench_deepseek import adapter
from .diagnostics import RecognitionError, require
from .activity_platforms import supports_platform
from .grouping import merge_shen_jia_child_activity_rows
from .pasted_activity import consolidate
from . import large_shenjia

BASE_FIELDS = ("activityName", "type", "platformCode", "sourceActivityId", "startAt", "endAt",
               "brandIds", "rulesText", "brandBenefit", "remark", "companyBearing")
MODEL_FIELDS = set(BASE_FIELDS) - {"brandIds", "companyBearing"} | {"activityMethod", "brandNames"}
PRODUCT_FIELDS = {"upc", "name", "spec", "internalCode", "childActivityId"}
MISSING = {"value": None, "status": "missing", "evidenceIds": []}
CODE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,199}")
UPC = re.compile(r"[0-9]{8,14}")
MAIN_HEADER = re.compile(rf"(?:{MAIN_LABEL}|报名\s*(?:编号|ID)|直营活动\s*(?:编号|ID))", re.I)
LABELED_ID = re.compile(r"(?:^|\n)\s*(?:活动\s*)?ID\s*[:：]\s*([A-Za-z0-9_-]+)\s*$", re.I)
CLAUSE_BREAK = re.compile(r"[，,；;。\n]+|(?<!\d)\.|\.(?!\d)")
TITLE_OFFER = re.compile(r"(?P<prefix>(?:(?:【[^】\n]*】|\[[^\]\n]*\])\s*)+)(?P<offer>(?:满\s*)?\d{1,8}(?:\.\d{1,2})?\s*[-－–—减]\s*\d{1,8}(?:\.\d{1,2})?.*)$")
SHARED_MONTH_RANGE = re.compile(r"(?<!\d)(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日?\s*(?:至|到|~|～|—|–|-)\s*(\d{1,2})日(?!\d)")
FULL_DATE_RANGE = re.compile(r"(?<!\d)(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})日?\s*(?:至|到|~|～|—|–|-)\s*(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})日?(?!\d)")
MAX_EVIDENCE = 6000


def _require(value, code=adapter.INVALID_OUTPUT):
    adapter.require(value, code)


def _tokens(item):
    if "text" in item:
        return {item["text"][0]}
    return {cell[1] for cell in item["cells"]}


def _ref(value, allowed, lookup):
    if isinstance(value, str):
        token, quote = value, None
    else:
        require(isinstance(value, list) and len(value) == 2 and all(isinstance(v, str) for v in value),
                "reference_shape")
        token, quote = value
    require(token in allowed, "reference_outside_source")
    text = lookup[token]["text"]
    quote = text if quote is None else quote
    require(bool(quote.strip()) and len(quote) <= 500 and quote in text, "reference_quote_not_in_source")
    return token, quote


def _product_ref(value, allowed, lookup):
    """Accept a token or a literal backed by complete cells from the same row."""
    if isinstance(value, list) and len(value) == 1 and isinstance(value[0], list):
        value = value[0]
    if isinstance(value, str) and value not in lookup:
        refs = _literal_refs(value, value, allowed, lookup)
    elif (isinstance(value, list) and len(value) == 2 and all(isinstance(item, str) for item in value)
          and value[0] not in lookup):
        refs = _literal_refs(value[0], value[1], allowed, lookup)
    else:
        refs = [_ref(value, allowed, lookup)]
    return {"value": refs[0][1], "refs": refs}


def _literal_refs(source, quote, allowed, lookup):
    matches = [token for token in allowed if lookup[token]["text"] == source]
    require(bool(matches), "product_literal_not_in_row")
    return [_ref([token, quote], allowed, lookup) for token in sorted(matches, key=lambda token: int(token[1:]))]


def _spec_ref(value, allowed, lookup, name_observation):
    """Normalize equivalent optional spec shapes only against this product name."""
    try:
        return _product_ref(value, allowed, lookup)
    except RecognitionError:
        pass
    if isinstance(value, list) and len(value) == 1:
        value = value[0]
    name_tokens = {token for token, _ in (name_observation or {}).get("refs", [])}
    refs = None
    if isinstance(value, dict):
        require(set(value) == {"value", "refs"} and isinstance(value["refs"], list)
                and 0 < len(value["refs"]) <= 8 and all(isinstance(token, str) for token in value["refs"]),
                "spec_reference_shape")
        quote, refs = value["value"], value["refs"]
    elif isinstance(value, str):
        quote = value
    elif isinstance(value, list) and len(value) == 2 and all(isinstance(item, str) for item in value):
        if value[1] in lookup:
            quote, refs = value[0], [value[1]]
        else:
            require(value[0] == value[1], "spec_reference_shape")
            quote = value[0]
    else:
        require(False, "spec_reference_shape")
    require(isinstance(quote, str) and bool(quote.strip()) and len(quote) <= 500,
            "spec_reference_shape")
    if refs is None:
        refs = sorted((token for token in name_tokens if quote in lookup[token]["text"]),
                      key=lambda token: int(token[1:]))
    require(bool(refs) and all(token in name_tokens for token in refs), "spec_not_in_product_name")
    checked = [_ref([token, quote], allowed, lookup) for token in refs]
    return {"value": quote, "refs": checked}


def _adjacent_main_id(value, refs):
    labels = [ref for ref in refs if ref["locator"].get("kind") == "text"
              and MAIN_HEADER.fullmatch(ref["text"].strip().rstrip(":：").strip())
              and ref["quote"].strip().rstrip(":：").strip() == ref["text"].strip().rstrip(":：").strip()]
    for label in labels:
        line = label["locator"].get("line")
        if type(line) is not int:
            continue
        for ref in refs:
            if (ref["locator"].get("kind") == "text" and ref["locator"].get("line") == line + 1
                    and ref["materialId"] == label["materialId"]
                    and ref["sourceSha256"] == label["sourceSha256"]
                    and ref["text"].strip() == ref["quote"].strip() == value):
                return True
    return False


def _complete_main_id_refs(value, sources, batch, allowed, lookup):
    """Locate an omitted header for a model-observed value in the known main column."""
    if batch.get("layout") != large_shenjia.LAYOUT or not isinstance(value, str):
        return sources
    main_column = batch["sourceColumns"]["main"]
    for token, quote in sources:
        source = lookup[token]
        loc = source["locator"]
        if (loc.get("kind") != "xlsx" or _column(source)[-1] != main_column
                or source["text"].strip() != quote.strip() or quote.strip() != value):
            continue
        scope = _column(source)
        headers = [candidate for candidate in allowed
                   if lookup[candidate]["locator"].get("kind") == "xlsx"
                   and lookup[candidate]["sourceSha256"] == source["sourceSha256"]
                   and _column(lookup[candidate]) == scope
                   and lookup[candidate]["locator"]["row"] < loc["row"]
                   and MAIN_HEADER.fullmatch(lookup[candidate]["text"].strip())]
        if headers:
            header = max(headers, key=lambda candidate: lookup[candidate]["locator"]["row"])
            if all(existing != header for existing, _ in sources):
                return [*sources, (header, lookup[header]["text"])]
    return sources


def _complete_clauses_supported(value, refs):
    """Allow omission of whole clauses, never edits within a clause or reordering."""
    target = [part.strip() for part in CLAUSE_BREAK.split(value) if part.strip()]
    if not target or not refs or any("text" not in ref for ref in refs):
        return False
    sources = {(ref["materialId"], ref["sourceSha256"], ref["locator"].get("kind"),
                ref["locator"].get("sheet")) for ref in refs}
    if len(sources) != 1:
        return False
    kind = next(iter(sources))[2]
    if kind == "text":
        if any(type(ref["locator"].get("line")) is not int for ref in refs):
            return False
        refs = sorted(refs, key=lambda ref: ref["locator"]["line"])
    # Text lines follow document order. Independent table cells may be listed in
    # summary order, while clauses within each source cell always keep their order.
    by_source = defaultdict(list)
    for ref in refs:
        by_source[tuple(sorted(ref["locator"].items()))].append(ref)
    clauses = []
    for entries in by_source.values():
        if len({ref["text"] for ref in entries}) != 1:
            return False
        # The complete source cell supplies boundaries even if the model quoted a substring.
        for part in CLAUSE_BREAK.split(entries[0]["text"]):
            clause = part.strip()
            if clause and any(clause in ref["quote"] for ref in entries):
                clauses.append(clause)
    position = 0
    for clause in target:
        while position < len(clauses) and clauses[position] != clause:
            position += 1
        if position == len(clauses):
            return False
        position += 1
    return True


def _rules_text_supported(value, refs):
    """Rules have a single strict path; substring fallback would drop negations."""
    normalize = lambda text: re.sub(r"\s+", " ", text).strip()
    for ref in refs:
        if not isinstance(ref.get("text"), str):
            continue
        original = ref["text"].strip()
        if original in ref["quote"] and normalize(value) == normalize(original):
            return True
        # A bracketed campaign label followed by a complete numeric coupon offer
        # has a stable suffix boundary. Never discard a restrictive/negative label.
        title = TITLE_OFFER.fullmatch(original)
        if (title and not re.search(r"不|禁|无|非|互斥", title["prefix"])
                and title["offer"].strip() in ref["quote"]
                and normalize(value) == normalize(title["offer"])):
            return True
    return _complete_clauses_supported(value, refs)


def _explicit_range_date_supported(name, value, refs):
    """Normalize explicit calendar dates without adding a year, rollover or time."""
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return False
    for ref in refs:
        text, ranges = ref["quote"], []
        for pattern in (SHARED_MONTH_RANGE, FULL_DATE_RANGE):
            for match in pattern.finditer(text):
                # A date-only observation cannot discard an explicitly supplied clock.
                if re.match(r"\s*(?:T\s*)?\d{1,2}:\d{2}", text[match.end():]):
                    continue
                numbers = tuple(map(int, match.groups()))
                try:
                    if pattern is SHARED_MONTH_RANGE:
                        year, month, first, last = numbers
                        start, end = date(year, month, first), date(year, month, last)
                    else:
                        start, end = date(*numbers[:3]), date(*numbers[3:])
                except ValueError:
                    continue
                if start <= end:
                    ranges.append((start.isoformat(), end.isoformat()))
        if len(set(ranges)) == 1 and ranges[0][name == "endAt"] == value:
            return True
    return False


def _column(ref):
    locator = ref["locator"]
    if "cell" in locator:
        match = re.match(r"[A-Za-z]+", locator["cell"])
        column = match.group() if match else None
    else:
        column = locator.get("column")
    return ref["materialId"], locator.get("sheet"), locator.get("table"), column


def _supported(name, value, refs):
    if name == "brandNames":
        return (isinstance(value, list) and 0 < len(value) <= 24
                and all(isinstance(v, str) and v.strip() and len(v) <= 200
                        and any(v in ref["quote"] for ref in refs) for v in value))
    if not isinstance(value, str) or not value.strip() or len(value) > 500:
        return False
    if name == "rulesText":
        return _rules_text_supported(value, refs)
    if name in ("startAt", "endAt"):
        form_dates = [r for r in refs if r["locator"].get("fieldPath") in ("startAt", "endAt")]
        if form_dates and len(form_dates) == len(refs):
            # A date selected for this group must keep the submitted clock, fraction and offset.
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?(?:Z|[+-]\d{2}:\d{2})?)?", value):
                return False
            try:
                datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return False
            return all(r["locator"]["fieldPath"] == name and value == r.get("text", r["quote"]) == r["quote"]
                       for r in form_dates)
        if _explicit_range_date_supported(name, value, refs):
            return True
    if name == "platformCode":
        return supports_platform(value, refs)
    if name == "sourceActivityId":
        if not CODE.fullmatch(value):
            return False
        if any(ref["locator"]["kind"] == "text"
               and (match := LABELED_ID.fullmatch(ref["quote"])) and match[1] == value for ref in refs):
            return True
        if _adjacent_main_id(value, refs):
            return True
        if _field_supported(name, value, refs, {}):
            return True
        headers = {_column(ref) for ref in refs if MAIN_HEADER.fullmatch(ref["quote"].strip())
                   and _column(ref)[-1] is not None}
        return any(ref["quote"].strip() == value and (
                   ref["locator"].get("fieldPath") == name or _column(ref) in headers) for ref in refs)
    if name == "type" and value in {"platform", "brand", "jiatan_sourcing"}:
        if any(ref["locator"].get("fieldPath") == name and ref["quote"] == value for ref in refs):
            return True
    if name in ("brandBenefit", "remark"):
        return any(value in ref["quote"] for ref in refs)
    hints = {ref["excerptId"]: [name] for ref in refs if ref["locator"].get("fieldPath") == name}
    return _field_supported(name, value, refs, hints)


def read_batch(raw, batch, lookup, *, max_groups=20):
    require(isinstance(raw, dict) and set(raw) == {"groups", "rows"}
            and isinstance(raw["groups"], list) and len(raw["groups"]) <= max_groups
            and isinstance(raw["rows"], list), "batch_result_shape")
    row_tokens = {row["id"]: _tokens(row) for row in batch["rows"]}
    group_tokens = {item[1] for item in batch["form"]}
    for item in (*batch["context"], *batch["rows"]):
        group_tokens.update(_tokens(item))
    groups = {}
    unsupported = []
    for group in raw["groups"]:
        require(isinstance(group, dict) and set(group) == {"id", "fields"}
                and isinstance(group["id"], str) and CODE.fullmatch(group["id"]) and group["id"] not in groups
                and isinstance(group["fields"], dict) and group["fields"].keys() <= MODEL_FIELDS
                and bool(group["fields"]), "group_shape")
        fields = {}
        for name, field in group["fields"].items():
            valid_shape = (isinstance(field, dict) and set(field) == {"value", "refs"}
                           and isinstance(field["refs"], list) and bool(field["refs"]))
            if name in ("brandNames", "rulesText"):
                # Reject the ungrounded optional field while keeping independently validated rows.
                max_refs = 48 if name == "brandNames" else 8
                if not valid_shape or len(field["refs"]) > max_refs:
                    unsupported.append((name, []))
                    continue
            else:
                require(valid_shape and len(field["refs"]) <= 8, "activity_field_shape", field=name)
            try:
                sources = [_ref(ref, group_tokens, lookup) for ref in field["refs"]]
            except RecognitionError as error:
                if name in ("brandNames", "rulesText"):
                    unsupported.append((name, []))
                    continue
                error.details["field"] = name
                raise
            value = field["value"]
            if name == "sourceActivityId":
                sources = _complete_main_id_refs(value, sources, batch, group_tokens, lookup)
            if name == "brandNames" and isinstance(value, str):
                value = [value]
            excerpts = [{"excerptId": token, "quote": quote, **lookup[token]} for token, quote in sources]
            if _supported(name, value, excerpts):
                fields[name] = value, sources
            else:
                unsupported.append((name, sources))
        groups[group["id"]] = fields
    observed = {}
    for row in raw["rows"]:
        require(isinstance(row, dict) and set(row) == {"id", "kind", "group", "fields"}
                and isinstance(row["id"], str) and row["id"] in row_tokens and row["id"] not in observed
                and row["kind"] in ("product", "activity", "context", "ignore")
                and (row["group"] is None or isinstance(row["group"], str) and row["group"] in groups)
                and isinstance(row["fields"], dict) and row["fields"].keys() <= PRODUCT_FIELDS, "row_shape")
        if row["kind"] != "product":
            require(not row["fields"], "non_product_fields", sourceRowId=row["id"])
        else:
            require(bool(row["fields"].keys() & {"upc", "name", "internalCode"}),
                    "product_identifiers_missing", sourceRowId=row["id"])
        fields = {}
        for name, ref in sorted(row["fields"].items(), key=lambda item: item[0] == "spec"):
            try:
                fields[name] = (_spec_ref(ref, row_tokens[row["id"]], lookup, fields.get("name")) if name == "spec"
                                else _product_ref(ref, row_tokens[row["id"]], lookup))
            except RecognitionError as error:
                if name == "spec":
                    source_token = min(row_tokens[row["id"]], key=lambda token: int(token[1:]))
                    unsupported.append(("rawSpec", [(source_token, lookup[source_token]["text"])]))
                    continue
                error.details.update(sourceRowId=row["id"], field=name)
                raise
        observed[row["id"]] = row | {"fields": fields}
    missing = sorted(row_tokens.keys() - observed.keys(), key=lambda rid: int(rid[1:]))
    require(not missing, "missing_row_classifications", code="MODEL_RECOGNITION_ROW_COVERAGE_INCOMPLETE",
            missingRowCount=len(missing), **({"sourceRowId": missing[0]} if missing else {}))
    large_shenjia.check_classifications(batch, observed, groups)
    return groups, [observed[rid] for rid in row_tokens], unsupported


def _value(fields, name):
    return fields[name][0] if name in fields else None


def _identity(fields):
    platform, source_id = _value(fields, "platformCode"), _value(fields, "sourceActivityId")
    if _value(fields, "activityMethod") == "shen_jia" and platform != "meituan":
        return None
    return (platform, source_id) if platform and source_id else None


def _main_id_scopes(fields, lookup):
    observed = fields.get("sourceActivityId")
    if not observed:
        return set()
    value, refs = observed
    headers = {_column(lookup[token]) for token, quote in refs if MAIN_HEADER.fullmatch(quote.strip())}
    values = {_column(lookup[token]) for token, quote in refs if quote.strip() == value}
    return {scope for scope in headers & values if scope[-1] is not None}


def _group_keys(replies, lookup, table_scopes=()):
    """Resolve a missing platform only through a verified main ID in the same table column."""
    anchors = defaultdict(set)
    resolved_fields = defaultdict(lambda: defaultdict(set))
    unresolved = {}
    keys = {}
    main_scopes = defaultdict(list)
    consistent_fields = ("startAt", "endAt", "activityMethod")
    for index, (observations, _, _) in enumerate(replies):
        for gid, fields in observations.items():
            if not fields:
                continue
            local = index, gid
            identity = _identity(fields)
            scopes = _main_id_scopes(fields, lookup)
            for scope in scopes & set(table_scopes):
                main_scopes[scope, _value(fields, "sourceActivityId")].append((local, fields))
            keys[local] = identity or local
            platform = _value(fields, "platformCode")
            source_id = _value(fields, "sourceActivityId")
            if platform:
                for scope in scopes:
                    anchors[scope, source_id].add((platform, source_id))
            if identity:
                for name in consistent_fields:
                    if name in fields:
                        resolved_fields[identity][name].add(_value(fields, name))
            elif scopes and not platform:
                unresolved[local] = fields, scopes

    for local, (fields, scopes) in unresolved.items():
        source_id = _value(fields, "sourceActivityId")
        options = set().union(*(anchors[scope, source_id] for scope in scopes))
        identity = ("meituan", source_id)
        known = resolved_fields.get(identity, {})
        if options == {identity} and known.get("activityMethod") == {"shen_jia"} and all(
                name not in fields or not known.get(name) or known[name] == {_value(fields, name)}
                for name in consistent_fields):
            keys[local] = identity
    for (scope, source_id), entries in main_scopes.items():
        platforms = {_value(fields, "platformCode") for _, fields in entries} - {None}
        # Main-ID grouping within one source table does not invent a missing platform.
        if len(platforms) <= 1:
            for local, _ in entries:
                keys[local] = ("shenjia_source_main_id", scope, source_id)
    return keys


def _assemble(replies, rows, lookup, table_scopes=()):
    groups, products, owners, warnings = {}, [], defaultdict(set), []
    kinds = Counter()
    keys = _group_keys(replies, lookup, table_scopes)
    for index, (observations, read_rows, unsupported) in enumerate(replies):
        aliases = {}
        for gid, fields in observations.items():
            if fields:
                key = keys[index, gid]
                aliases[gid] = key
                group = groups.setdefault(key, {"fields": defaultdict(list), "rows": []})
                for name, value in fields.items():
                    group["fields"][name].append(value)
        warnings.extend(unsupported)
        for row in read_rows:
            kinds[row["kind"]] += 1
            if row["kind"] != "product":
                continue
            key = aliases.get(row["group"])
            material, _ = rows[row["id"]]
            child = row["fields"].get("childActivityId", {}).get("value")
            if key is not None and child:
                owners[(material, child)].add(key)
            products.append((key, row, material, child))

    for key, row, material, child in products:
        possibilities = owners.get((material, child), set())
        if key is None and len(possibilities) == 1:
            key = next(iter(possibilities))
        if key is None:
            key = ("unbound", material)
            groups.setdefault(key, {"fields": defaultdict(list), "rows": []})
        groups[key]["rows"].append(row)
    return groups, warnings, kinds


def _pair(rows, locations):
    observations = defaultdict(list)
    for row in rows:
        fields = row["fields"]
        child = fields.get("childActivityId", {}).get("value")
        upc = fields.get("upc", {}).get("value")
        if child and upc and UPC.fullmatch(upc):
            observations[(locations[row["id"]][0], child, upc)].append(row)
    pairs = {}
    for matching in observations.values():
        if len(matching) == 2 and len({locations[row["id"]][1] for row in matching}) == 2:
            first, second = matching
            pairs[first["id"]] = second
            pairs[second["id"]] = first
    seen = set()
    for row in rows:
        if row["id"] in seen:
            continue
        pair = [row]
        if other := pairs.get(row["id"]):
            pair.append(other)
        seen.update(item["id"] for item in pair)
        yield pair


def _product(pair, bind, row_number, source_children=None):
    refs, values, conflict = [], {}, False
    for item in pair:
        # Preserve the original child-ID cells as deterministic source metadata,
        # including when the model omitted the optional childActivityId field.
        refs.extend((source_children or {}).get(item["id"], []))
        for name, observation in item["fields"].items():
            refs.extend(observation["refs"])
            raw = observation["value"]
            if name in values and values[name].casefold().split() != raw.casefold().split():
                conflict = True
            values.setdefault(name, raw)
    upc = values.get("upc")
    return {"rowId": f"row-{row_number}", "rawUpc": upc, "rawInternalCode": values.get("internalCode"),
            "rawName": values.get("name"), "rawSpec": values.get("spec"), "matchKind": "conflict" if conflict else "unmatched",
            "matchedProductId": None, "candidates": [], "evidenceIds": bind(refs),
            "sourceRowIds": [item["id"] for item in pair]}


def project(replies, payload, lookup, rows, *, max_groups=20, expanded_excel=False):
    table_layouts = large_shenjia.layouts(payload["documents"])
    table_scopes, source_children = large_shenjia.source_details(lookup, table_layouts)
    groups, unsupported, kinds = _assemble(replies, rows, lookup, table_scopes)
    groups, text_warnings = consolidate(groups, replies, rows, lookup)
    _require(len(groups) <= max_groups, "MODEL_RECOGNITION_GROUP_LIMIT")
    evidence, index, candidates, extra, conflicts = [], {}, [], [], []

    def bind(refs):
        ids = []
        for token, quote in refs:
            if (token, quote) not in index:
                eid = f"ev-{len(evidence) + 1}"
                index[token, quote] = eid
                src = lookup[token]
                evidence.append({k: src[k] for k in ("materialId", "sourceSha256", "locator")}
                                | {"evidenceId": eid, "quote": quote})
            ids.append(index[token, quote])
        return list(dict.fromkeys(ids))

    def field_value(options, name):
        if not options:
            return dict(MISSING)
        refs = bind(ref for _, sources in options for ref in sources)
        first = options[0][0]
        if name == "brandNames":
            first = list(dict.fromkeys(brand for names, _ in options for brand in names))
        elif any(value != first for value, _ in options):
            conflicts.append({"field": name, "manualValue": None,
                              "extractedValues": list(dict.fromkeys(value for value, _ in options)), "evidenceIds": refs})
            return {"value": first, "status": "conflict", "evidenceIds": refs}
        return {"value": first, "status": "explicit", "evidenceIds": refs}

    row_number = 1
    for group in groups.values():
        cid = f"candidate-{len(candidates) + 1}"
        fields = {key: field_value(group["fields"].get(key), key) for key in BASE_FIELDS}
        fields["companyBearing"] = {"value": {"state": "unknown", "evidenceIds": []}, "status": "missing", "evidenceIds": []}
        products = []
        for pair in _pair(group["rows"], rows):
            products.append(_product(pair, bind, row_number, source_children))
            row_number += 1
        candidates.append({"candidateId": cid, "fields": fields, "productRows": products})
        extra.append({"candidateId": cid,
                      "activityMethod": field_value(group["fields"].get("activityMethod"), "activityMethod"),
                      "brandNames": field_value(group["fields"].get("brandNames"), "brandNames")})

    warnings = [{"code": "PRODUCT_SPEC_EVIDENCE_UNSUPPORTED" if name == "rawSpec" else "MODEL_FIELD_EVIDENCE_UNSUPPORTED",
                 "message": "商品规格未单独采信，保留商品名和原文待核对。" if name == "rawSpec" else f"模型字段 {name} 的原文依据待核对"}
                | ({"locator": lookup[refs[0][0]]["locator"]} if refs else {})
                for name, refs in unsupported]
    warnings.extend({"code": code, "message": message, "locator": locator}
                    for code, message, locator in text_warnings)
    warnings.extend(large_shenjia.child_upc_conflicts(groups, source_children, lookup))
    candidates, extra = merge_shen_jia_child_activity_rows(
        candidates, extra, payload["documents"], evidence, payload["manualFields"],
        material_names=payload.get("materialNames"))
    missing = sorted({name for candidate in candidates for name, field in candidate["fields"].items()
                      if field["status"] == "missing"})
    _require(len(evidence) <= (large_shenjia.MAX_EVIDENCE if table_layouts or expanded_excel else MAX_EVIDENCE),
             "MODEL_RECOGNITION_EVIDENCE_LIMIT")
    return ({"candidates": candidates, "evidence": evidence, "conflicts": conflicts, "warnings": warnings,
             "missingFields": [{"field": name, "reasonCode": "NOT_PROVIDED"} for name in missing]},
            {"candidateMetadata": extra, "classifiedRows": sum(kinds.values()),
             "productSourceRows": kinds["product"], "rowKinds": dict(kinds)})
