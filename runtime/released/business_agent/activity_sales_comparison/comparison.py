"""Deterministic activity sales comparison for the local price Agent."""
from collections import Counter
from copy import deepcopy
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP, localcontext
import re
from zoneinfo import ZoneInfo

VERSION = "activity-sales-comparison/1.0"
CHINA = ZoneInfo("Asia/Shanghai")
PLATFORMS = {"meituan": "美团", "taobao_instant": "淘宝闪购"}
FIELDS = {"salesQty": "sales_qty", "salesRevenue": "revenue"}
UNITS = {"salesQty": "件", "salesRevenue": "元"}
SOURCE_VERSION = "goods-report-activity-read/1.0"
MAX_CODES = 5_000
MAX_DAYS = 366
TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")
NUMBER = re.compile(r"[+-]?\d{1,20}(?:\.\d{1,12})?", re.ASCII)
DAY = timedelta(days=1)
REFUND_NOTE = "销售额沿用商品日报 revenue 的营业额口径。退款处理尚未核实。"


class UnsupportedScope(Exception):
    """The authoritative report lacks the requested scope dimension."""


class SourceUnavailable(RuntimeError):
    """A configured source could not complete a verified query."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class InvalidReceipt(Exception):
    """The configured source did not establish provenance or query scope."""


def _day(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        raise ValueError("日期须为 YYYY-MM-DD")
    return date.fromisoformat(value)


def _days(start, end):
    return [(start + DAY * i).isoformat() for i in range((end - start).days + 1)]


def _identifiers(values):
    if not isinstance(values, list) or len(values) > MAX_CODES or any(
        not isinstance(value, str) or not TOKEN.fullmatch(value) for value in values
    ):
        raise ValueError("查询范围须为有效标识列表")
    return sorted(set(values))


def _number(value, field):
    if type(value) is Decimal:
        if not value.is_finite() or not -12 <= value.as_tuple().exponent <= 20 or value.adjusted() >= 20:
            return None
        value = format(value, "f")
    if type(value) not in (str, int) or not NUMBER.fullmatch(str(value)):
        return None
    amount = Decimal(str(value))
    if field == "sales_qty":
        integral = amount.to_integral_value()
        return integral if integral == amount else None
    return amount


def _amount(value):
    return None if value is None else format(value if value else abs(value), "f")


def _period(days):
    return {"startDate": days[0], "endDate": days[-1], "dayCount": len(days), "missingDates": None}


def _error(result, status, code, message, **details):
    result["status"] = status
    result["warnings"].append({"code": code, "message": message, **details})
    return result


def _scope(scope, resolver, result):
    codes = _identifiers(scope.get("internalCodes", []))
    brands = _identifiers(scope.get("brandIds", []))
    stores = _identifiers(scope.get("platformStoreIds", []))
    all_stores = scope.get("allPlatformStores") is True
    if stores and all_stores:
        return _error(result, "conflict", "CONFLICTING_STORES", "指定门店与全平台门店范围冲突。")
    if not (stores or all_stores):
        return _error(result, "needs_scope", "STORES_NOT_VERIFIED", "请核实门店范围或确认全平台。")
    if not (codes or brands):
        return _error(result, "needs_scope", "PRODUCTS_NOT_VERIFIED", "请核实活动商品内部码或品牌 ID。")
    full_brand = scope.get("fullBrandScope") is True
    if full_brand and (codes or not brands):
        return _error(result, "conflict", "CONFLICTING_BRAND_SCOPE", "全品牌范围与指定商品范围冲突。")
    if brands and not (codes or full_brand):
        return _error(result, "needs_scope", "BRAND_SCOPE_NOT_VERIFIED", "请核实活动商品清单或确认全品牌范围。")

    result["scope"] = {"internalCodes": codes, "brandIds": brands, "platformStoreIds": stores,
                       "allPlatformStores": all_stores, "fullBrandScope": full_brand}
    if not brands:
        return {code: {"codes": [code], "name": None} for code in codes}
    if resolver is None:
        return _error(result, "needs_scope", "BRAND_MAPPING_UNAVAILABLE", "缺少可按品牌 ID 查询的权威商品主档映射。")

    mapping = resolver(brands)
    if not isinstance(mapping, dict) or not isinstance(mapping.get("brands"), dict) or not mapping.get("source"):
        raise InvalidReceipt()
    _day(mapping.get("asOf"))
    entities, owners, unmatched = {}, Counter(), []
    for brand in brands:
        entry = mapping["brands"].get(brand)
        known = _identifiers(entry.get("internalCodes", [])) if isinstance(entry, dict) else []
        if not known or entry.get("complete") is not True:
            unmatched.append(brand)
            continue
        owners.update(known if full_brand else set(known) & set(codes))
        name = entry.get("name")
        entities[brand] = {"codes": known if full_brand else sorted(set(known) & set(codes)),
                           "name": name if isinstance(name, str) else None}
    unassigned = sorted(set(codes) - set(owners))
    conflicts = sorted(code for code, count in owners.items() if count > 1)
    result["scope"]["brandMapping"] = {"asOf": mapping["asOf"], "source": mapping["source"],
                                        "unmatchedBrandIds": unmatched, "unmatchedInternalCodes": unassigned,
                                        "conflictingInternalCodes": conflicts}
    if conflicts:
        return _error(result, "conflict", "BRAND_MAPPING_CONFLICT", "商品在核实范围内对应多个品牌 ID。")
    if unmatched or unassigned or any(not entity["codes"] for entity in entities.values()):
        return _error(result, "needs_scope", "BRAND_MAPPING_INCOMPLETE", "部分品牌或商品的权威映射待核实。")
    result["scope"]["internalCodes"] = _identifiers(sorted(owners))
    return entities


def _read_receipt(receipt, query, days, today):
    if (
        not isinstance(receipt, dict) or receipt.get("schemaVersion") != SOURCE_VERSION
        or receipt.get("report") != "goods_report" or receipt.get("request") != query
        or receipt.get("fieldUnits") != {field: UNITS[key] for key, field in FIELDS.items() if field in query["fields"]}
        or not isinstance(receipt.get("queryRef"), str) or not TOKEN.fullmatch(receipt["queryRef"])
        or not isinstance(receipt.get("days"), dict) or not isinstance(receipt.get("records"), list)
        or type(receipt.get("paginationComplete")) is not bool
    ):
        raise InvalidReceipt()
    updated = receipt.get("updatedAt")
    if updated is not None:
        if not isinstance(updated, str) or datetime.fromisoformat(updated).utcoffset() is None:
            raise InvalidReceipt()
    day_set, codes = set(days), set(query["internalCodes"])
    stores = set(query["platformStoreIds"] or [None])
    records, names, conflicts = {}, {}, set()
    deduplicated = 0
    complete_days = set()
    for day, state in receipt["days"].items():
        if day not in day_set or state not in {"complete", "partial", "empty", "missing"}:
            raise InvalidReceipt()
        if state == "complete" and day < today:
            complete_days.add(day)
    if updated is None and (complete_days or receipt["records"]):
        raise InvalidReceipt()
    for row in receipt["records"]:
        if not isinstance(row, dict):
            raise InvalidReceipt()
        day, code, store = row.get("date"), row.get("internalCode"), row.get("platformStoreId")
        if day not in day_set or code not in codes or store not in stores or row.get("platformCode") != query["platformCode"]:
            raise InvalidReceipt()
        grain = (day, code, store)
        numbers = tuple(_number(row.get(field), field) for field in FIELDS.values())
        if grain in records:
            if records[grain] == numbers:
                deduplicated += 1
            else:
                conflicts.update((grain, i) for i, (first, second) in enumerate(zip(records[grain], numbers))
                                 if first != second)
        else:
            records[grain] = numbers
        if isinstance(row.get("name"), str) and row["name"].strip():
            # Prefer the latest daily label and keep an order-independent result.
            names[code] = max(names.get(code, ("", "")), (day, row["name"]))
    return records, names, conflicts, deduplicated, complete_days


def _period_value(days, codes, stores, metric, records, conflicts, complete_days, all_pages):
    expected = len(days) * len(codes) * len(stores)
    total, observed = Decimal(0), 0
    reasons = Counter()
    missing_dates = set()
    if not all_pages:
        reasons["incomplete_pagination"] = 1
        missing_dates.update(days)
    for day in days:
        for code in codes:
            for store in stores:
                grain = (day, code, store)
                if day not in complete_days:
                    reasons["source_day_incomplete"] += 1
                    missing_dates.add(day)
                if (grain, metric) in conflicts:
                    reasons["duplicate_conflict"] += 1
                elif grain not in records:
                    reasons["missing_record"] += 1
                elif records[grain][metric] is None:
                    reasons["missing_or_invalid_value"] += 1
                else:
                    total += records[grain][metric]
                    observed += 1
                    continue
                missing_dates.add(day)
    recorded = total if observed else None
    return recorded if not reasons else None, recorded, {
        "expectedRecords": expected, "validRecords": observed,
        "missingDates": sorted(missing_dates), "reasons": dict(reasons),
    }


def _period_change(current, previous):
    if current is None or previous is None:
        return "missing_data", None, None
    delta = current - previous
    if previous == 0:
        return "zero_base", delta, None
    change = (delta * 100 / previous).quantize(Decimal(".01"), rounding=ROUND_HALF_UP)
    return "ready", delta, change


def _metric(periods, codes, stores, index, records, conflicts, complete_days, all_pages):
    current, recorded_current, current_coverage = _period_value(
        periods["current"], codes, stores, index, records, conflicts, complete_days, all_pages)
    previous, recorded_previous, previous_coverage = _period_value(
        periods["previous"], codes, stores, index, records, conflicts, complete_days, all_pages)
    status, delta, change = _period_change(current, previous)
    return {"current": _amount(current), "previous": _amount(previous), "delta": _amount(delta),
            "changePct": _amount(change), "recordedCurrent": _amount(recorded_current),
            "recordedPrevious": _amount(recorded_previous), "status": status,
            "coverage": {"current": current_coverage, "previous": previous_coverage}}


def _fetch(result, query, periods, source, today):
    if source is None:
        _error(result, "source_unavailable", "SOURCE_NOT_CONFIGURED", "数据 Agent 只读查询入口尚未配置。")
        return None
    days = [day for window in periods.values() for day in window]
    try:
        receipt = source(deepcopy(query))
        records, names, conflicts, deduplicated, complete_days = _read_receipt(receipt, query, days, today)
    except UnsupportedScope:
        _error(result, "unsupported_scope", "STORE_DIMENSION_UNAVAILABLE", "商品日报缺少指定门店维度。")
        return None
    except SourceUnavailable as exc:
        _error(result, "source_unavailable", exc.code, str(exc))
        return None
    except TimeoutError:
        _error(result, "source_unavailable", "SOURCE_TIMEOUT", "数据 Agent 只读查询超时。")
        return None
    except (InvalidReceipt, ValueError, TypeError, OSError, RuntimeError):
        _error(result, "source_unavailable", "INVALID_SOURCE_RECEIPT", "数据 Agent 未返回可核对的日报回执。")
        return None
    all_pages = receipt["paginationComplete"]
    fields = {key: field for key, field in FIELDS.items() if field in query["fields"]}
    result["source"] = {"report": "goods_report", "fields": fields, "unit": {key: UNITS[key] for key in fields},
                        "updatedAt": receipt["updatedAt"], "queryRef": receipt["queryRef"],
                        "coverage": {"request": query, "paginationComplete": all_pages,
                                     "coveredDayCounts": {key: len(set(window) & complete_days) for key, window in periods.items()},
                                     "identicalDuplicatesExcluded": deduplicated,
                                     "conflictingGrains": len({grain for grain, _ in conflicts})},
                        }
    if "salesRevenue" in fields:
        result["source"].update(revenueDefinition="source_turnover", refundTreatment="unverified")
        result["warnings"].append({"code": "REFUND_TREATMENT_UNVERIFIED", "message": REFUND_NOTE})
    return records, names, conflicts, complete_days, all_pages


def compare_sales(request, source=None, *, resolve_brands=None, today=None):
    """Accept a server-authorized activity request; source is a trusted query callable."""
    request = request if isinstance(request, dict) else {}
    today = (today or datetime.now(CHINA).date()).isoformat()
    result = {key: request.get(key) if isinstance(request.get(key), str) else None
              for key in ("activityId", "activityVersionId", "platformCode", "dateBasis")}
    result.update(schemaVersion=VERSION, status=None, preview=False, scope=None,
                  periods={"current": None, "previous": None}, rows=[], source=None, warnings=[])
    if request.get("schemaVersion", VERSION) != VERSION:
        return _error(result, "conflict", "INVALID_COMPARISON_VERSION", "活动复盘契约版本无效。")
    if not (request.get("startDate") and request.get("endDate")) or request.get("dateBasis") is None:
        fields = [key for key in ("dateBasis", "startDate", "endDate") if not request.get(key)]
        return _error(result, "needs_dates", "DATES_NOT_VERIFIED", "请核实活动生效日期及其依据。", fields=fields)
    if request["dateBasis"] not in ("platform_actual", "approved_plan"):
        return _error(result, "conflict", "INVALID_DATE_BASIS", "日期依据无效。")
    result["preview"] = request["dateBasis"] == "approved_plan" or request.get("previewThrough") is not None
    try:
        start, end = _day(request["startDate"]), _day(request["endDate"])
        if start > end or (end - start).days >= MAX_DAYS:
            raise ValueError()
        if request.get("previewThrough") is not None:
            through = _day(request["previewThrough"])
            if not start <= through <= end or through.isoformat() >= today:
                raise ValueError()
            result["previewThrough"] = request["previewThrough"]
            end = through
        span = (end - start).days + 1
        periods = {"current": _days(start, end), "previous": _days(start - DAY * span, start - DAY)}
    except (ValueError, OverflowError):
        return _error(result, "conflict", "INVALID_DATE_RANGE", "请核对活动起止日与阶段截至日，单期须为 1 至 366 天。")
    result["periods"] = {key: _period(days) for key, days in periods.items()}
    if (not all(result[key] and TOKEN.fullmatch(result[key]) for key in ("activityId", "activityVersionId"))
            or result["platformCode"] not in PLATFORMS):
        return _error(result, "needs_scope", "ACTIVITY_OR_PLATFORM_NOT_VERIFIED", "请核实活动、活动版本与查询平台。")
    scope = request.get("scope")
    if not isinstance(scope, dict):
        return _error(result, "needs_scope", "SCOPE_NOT_VERIFIED", "请核实活动商品及门店范围。")
    try:
        entities = _scope(scope, resolve_brands, result)
        if result["status"]:
            return result
    except ValueError:
        return _error(result, "conflict", "INVALID_IDENTIFIERS", "查询范围或品牌映射包含无效标识。")
    except (InvalidReceipt, OSError, RuntimeError):
        return _error(result, "source_unavailable", "INVALID_BRAND_RECEIPT", "当前无法核对商品主档映射回执。")

    scope = result["scope"]
    query = {"report": "goods_report", "platformCode": result["platformCode"],
             "internalCodes": scope["internalCodes"], "platformStoreIds": scope["platformStoreIds"],
             "periods": {key: {k: p[k] for k in ("startDate", "endDate")} for key, p in result["periods"].items()},
             "fields": list(FIELDS.values())}
    fetched = _fetch(result, query, periods, source, today)
    if fetched is None:
        return result
    records, names, conflicts, complete_days, all_pages = fetched
    for key, window in periods.items():
        result["periods"][key]["missingDates"] = [d for d in window if d not in complete_days]
    stores = scope["platformStoreIds"] or [None]
    entity_type = "brand" if scope["brandIds"] else "product"
    with localcontext() as context:
        context.prec = 80
        for entity_id, entity in entities.items():
            row = {"entityType": entity_type, "entityId": entity_id,
                   "name": entity["name"] if entity_type == "brand" else names.get(entity_id, (None, None))[1]}
            for index, key in enumerate(FIELDS):
                row[key] = _metric(periods, entity["codes"], stores, index, records, conflicts, complete_days, all_pages)
            result["rows"].append(row)
    if any(row[field]["status"] == "missing_data" for row in result["rows"] for field in FIELDS):
        return _error(result, "partial", "COVERAGE_INCOMPLETE", "部分日报、商品或指标未覆盖，已记录值单列，缺项不补零。")
    result["status"] = "preview" if result["preview"] else "ready"
    return result
