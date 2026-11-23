"""Trailing calendar sales lookups using the same verified goods_report source."""
from datetime import datetime
from decimal import localcontext

from .comparison import (
    CHINA, DAY, PLATFORMS, _amount, _days, _error, _fetch,
    _identifiers, _period, _period_change, _period_value,
)

VERSION = "activity-sales-history/1.0"
WINDOWS = {"trailing_28_days", "trailing_28_days_compare", "last_3_complete_months"}


def _windows(today, window):
    if window == "trailing_28_days":
        return {"trailing_28_days": _days(today - 28 * DAY, today - DAY)}
    if window == "trailing_28_days_compare":
        return {"current": _days(today - 28 * DAY, today - DAY),
                "previous": _days(today - 56 * DAY, today - 29 * DAY)}
    end = today.replace(day=1) - DAY
    result = {}
    for _ in range(3):
        start = end.replace(day=1)
        result[end.isoformat()[:7]] = _days(start, end)
        end = start - DAY
    return dict(reversed(result.items()))


def sales_history(request, source=None, *, today=None):
    """Use the server-authorized platform and product IDs for a sales history query."""
    request = request if isinstance(request, dict) else {}
    today = today or datetime.now(CHINA).date()
    platform = request.get("platformCode")
    window = request.get("window")
    result = {"schemaVersion": VERSION, "platformCode": platform if isinstance(platform, str) else None,
              "window": window if isinstance(window, str) else None, "status": None,
              "periods": [], "rows": [], "source": None, "warnings": []}
    if request.get("schemaVersion", VERSION) != VERSION or result["window"] not in WINDOWS:
        return _error(result, "conflict", "INVALID_HISTORY_WINDOW", "请提供有效的销量查询窗口和版本。")
    windows = _windows(today, window)
    result["periods"] = [{"key": key, **_period(days)} for key, days in windows.items()]
    scope = request.get("scope")
    if result["platformCode"] not in PLATFORMS or not isinstance(scope, dict):
        return _error(result, "needs_scope", "SCOPE_NOT_VERIFIED", "请核实查询平台和商品范围。")
    try:
        codes = _identifiers(scope.get("internalCodes", []))
        store_ids = _identifiers(scope.get("platformStoreIds", []))
    except ValueError:
        return _error(result, "conflict", "INVALID_IDENTIFIERS", "商品内部码或门店标识无效。")
    all_stores = scope.get("allPlatformStores") is True
    if store_ids and all_stores:
        return _error(result, "conflict", "CONFLICTING_STORES", "指定门店与全平台门店范围冲突。")
    if not codes or not (store_ids or all_stores):
        return _error(result, "needs_scope", "SCOPE_NOT_VERIFIED", "请核实内部码和门店范围。")
    query = {"report": "goods_report", "platformCode": platform, "internalCodes": codes,
             "platformStoreIds": store_ids, "fields": ["sales_qty"],
             "periods": {p["key"]: {"startDate": p["startDate"], "endDate": p["endDate"]} for p in result["periods"]}}
    fetched = _fetch(result, query, windows, source, today.isoformat())
    if fetched is None:
        return result
    records, names, conflicts, complete_days, all_pages = fetched
    for period in result["periods"]:
        period["missingDates"] = [day for day in windows[period["key"]] if day not in complete_days]
    complete = True
    stores = store_ids or [None]
    with localcontext() as context:
        context.prec = 80
        for code in codes:
            totals, quantities = [], {}
            for key, days in windows.items():
                value, recorded, coverage = _period_value(days, [code], stores, 0, records, conflicts, complete_days, all_pages)
                status = "ready" if value is not None else "missing_data" if recorded is not None else "no_data"
                complete = complete and status == "ready"
                quantities[key] = value
                totals.append({"key": key, "salesQty": _amount(value), "recordedSalesQty": _amount(recorded),
                               "status": status, "coverage": coverage})
            row = {"internalCode": code, "name": names.get(code, (None, None))[1], "periods": totals}
            if window == "trailing_28_days_compare":
                status, delta, change = _period_change(quantities["current"], quantities["previous"])
                row["comparison"] = {"status": status, "delta": _amount(delta), "changePct": _amount(change)}
            result["rows"].append(row)
    if not complete:
        return _error(result, "partial", "COVERAGE_INCOMPLETE", "部分周期、商品或日报未覆盖，已记录件数单列。")
    result["status"] = "ready"
    return result
