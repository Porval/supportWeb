"""Read the Data Agent's existing goods_report cache using its read-only schema."""
from contextlib import closing
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import sqlite3

from .comparison import CHINA, FIELDS, PLATFORMS, SOURCE_VERSION, UNITS, UnsupportedScope


class GoodsReportSnapshot:
    """Adapter for an explicitly configured, authorized Data Agent snapshot."""

    def __init__(self, path):
        self.path = Path(path).resolve()

    def __call__(self, request):
        if request["platformStoreIds"]:
            raise UnsupportedScope()
        start = min(p["startDate"] for p in request["periods"].values())
        end = max(p["endDate"] for p in request["periods"].values())
        source_platform = PLATFORMS[request["platformCode"]]
        codes_json = json.dumps(request["internalCodes"], separators=(",", ":"))
        fields = [field for field in FIELDS.values() if field in request["fields"]]
        projection = ", ".join(f"record -> '$.{field}'" for field in fields)
        try:
            with closing(sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=5)) as conn:
                conn.execute("PRAGMA query_only=ON")
                conn.execute("BEGIN")
                days, updates = {}, []
                states = conn.execute(
                    """SELECT day, status, expected, received, updated FROM ingest
                       WHERE source='goods_report' AND day BETWEEN ? AND ?""", (start, end))
                for day, status, expected, received, updated in states:
                    if status == "complete" and expected == received and received:
                        days[day] = "complete"
                    else:
                        days[day] = status if status in {"empty", "missing"} else "partial"
                    if updated:
                        stamp = datetime.fromisoformat(updated)
                        updates.append(stamp if stamp.tzinfo is not None else stamp.replace(tzinfo=CHINA))
                rows = conn.execute(
                    f"""SELECT day, json_extract(record, '$.item_code'), json_extract(record, '$.item_name'),
                               {projection}
                       FROM records WHERE source='goods_report' AND day BETWEEN ? AND ?
                       AND CAST(json_extract(record, '$.item_code') AS TEXT) IN (SELECT value FROM json_each(?))
                       AND json_extract(record, '$.platform') = ?""",
                    (start, end, codes_json, source_platform))
                records = [
                    {"date": day, "internalCode": str(code), "name": name, "platformCode": request["platformCode"],
                     "platformStoreId": None, **{
                         field: json.loads(raw, parse_float=Decimal) if raw is not None else None
                         for field, raw in zip(fields, values)
                     }}
                    for day, code, name, *values in rows
                ]
        except sqlite3.Error as exc:
            raise RuntimeError("Data Agent goods_report snapshot unavailable") from exc
        updated = max(updates).astimezone(CHINA).isoformat() if updates else None
        fingerprint = sha256(json.dumps([request, days, updated], sort_keys=True).encode()).hexdigest()
        return {"schemaVersion": SOURCE_VERSION, "report": "goods_report", "request": request,
                "queryRef": "goods-snapshot:" + fingerprint, "updatedAt": updated, "days": days,
                "fieldUnits": {field: UNITS[key] for key, field in FIELDS.items() if field in request["fields"]},
                "paginationComplete": True, "records": records}
