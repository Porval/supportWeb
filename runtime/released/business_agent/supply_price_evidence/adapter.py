"""Read the Data Agent's authorized product export and resolve reference supply quotes."""

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from hashlib import sha256
from html.parser import HTMLParser
import json
from pathlib import Path
import re
from unicodedata import normalize
from zoneinfo import ZoneInfo


CONTRACT = "supply-price-evidence/1.0"
MAX_EXPORT_BYTES = 32 * 1024 * 1024
COLUMNS = {"内部码": "internalCode", "规格": "spec", "UPC": "upc", "供货价": "rawAmount"}
UPC = re.compile(r"[0-9]{8,14}")
UPC_SEPARATORS = re.compile(r"[,，、;；/\s]+")
DECIMAL = re.compile(r"(?:[0-9]{1,24}|[0-9]{1,3}(?:,[0-9]{3}){1,7})(?:\.[0-9]{1,16})?")
SHA256 = re.compile(r"[0-9a-f]{64}")
SCOPE_ISSUES = ["STORE_SCOPE_UNVERIFIED", "PRICING_UNIT_UNVERIFIED", "TAX_BASIS_UNVERIFIED",
                "VALIDITY_UNVERIFIED", "PRICE_VERSION_UNVERIFIED", "CURRENCY_UNVERIFIED"]


class _ExportParser(HTMLParser):
    """Keep only the approved columns of the HTML-based .xls export."""

    def __init__(self):
        super().__init__()
        self.row = None
        self.cell = None
        self.columns = None
        self.row_number = 0
        self.records = defaultdict(list)

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            if self.row is not None:
                raise ValueError("overlapping export rows")
            self.row = []
        elif self.row is not None and tag in {"td", "th"}:
            if self.cell is not None:
                raise ValueError("overlapping export cells")
            self.cell = []

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag):
        if tag in {"td", "th"} and self.cell is not None:
            self.row.append("".join(self.cell))
            self.cell = None
        elif tag == "tr" and self.row is not None:
            self.row_number += 1
            if self.cell is not None:
                raise ValueError("incomplete export cell")
            if self.columns is None:
                self.columns = [cell.strip() for cell in self.row]
                if any(self.columns.count(key) != 1 for key in COLUMNS):
                    raise ValueError("missing or ambiguous supply quote columns")
            else:
                if len(self.row) != len(self.columns):
                    raise ValueError("incomplete export row")
                values = {COLUMNS[key]: cell for key, cell in zip(self.columns, self.row) if key in COLUMNS}
                internal_code = values["internalCode"].strip()
                if not internal_code:
                    raise ValueError("missing export internal code")
                self.records[internal_code].append(values | {"sourceRowNumber": self.row_number})
            self.row = None


@dataclass(frozen=True)
class SupplyExport:
    source: dict
    rows_by_code: dict


def load_export(path, *, snapshot_date, expected_sha256):
    """Read a configured source whose date and hash were supplied by the data owner."""
    if date.fromisoformat(snapshot_date).isoformat() != snapshot_date:
        raise ValueError("snapshot date must be YYYY-MM-DD")
    if not SHA256.fullmatch(expected_sha256):
        raise ValueError("invalid export SHA-256")
    path = Path(path)
    with path.open("rb") as stream:
        raw = stream.read(MAX_EXPORT_BYTES + 1)
    fetched_at = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()
    if len(raw) > MAX_EXPORT_BYTES or sha256(raw).hexdigest() != expected_sha256:
        raise ValueError("supply export exceeds the size limit or failed checksum")
    text = raw.decode("utf-8-sig")
    parser = _ExportParser()
    parser.feed(text)
    parser.close()
    if (not parser.records or parser.row is not None or not text.rstrip().lower().endswith("</html>")
            or parser.row_number != len(re.findall(r"</tr\s*>", text, flags=re.IGNORECASE))):
        raise ValueError("supply export is incomplete")
    return SupplyExport({
        "provider": "company_data_agent", "kind": "catalog_export", "fileName": path.name,
        "field": "供货价", "snapshotDate": snapshot_date, "sourceSha256": expected_sha256,
        "fetchedAt": fetched_at, "sizeBytes": len(raw), "recordCount": parser.row_number - 1,
    }, dict(parser.records))


def _normalized_spec(value):
    if not isinstance(value, str):
        return ""
    return "".join(normalize("NFKC", value).split()).casefold()


def _amount(raw):
    value = raw.strip()
    if not DECIMAL.fullmatch(value):
        return None
    decimal = format(Decimal(value.replace(",", "")), "f")
    return decimal.rstrip("0").rstrip(".") if "." in decimal else decimal


def _result(row, status, issues, quote=None):
    return {"candidateId": row["candidateId"], "rowId": row["rowId"], "status": status,
            "referenceQuote": quote, "storeSupplyPrice": None, "issues": issues}


def _resolve(row, snapshot):
    candidates = row["candidates"]

    def issue(reason, status="missing"):
        return _result(row, status, [reason])

    if not candidates:
        return issue("PRODUCT_CANDIDATE_UNAVAILABLE")
    if len(candidates) != 1 or row.get("uniqueUpcHit") is not True or row.get("directoryStatus") in {
        "material_code_conflict", "source_conflict", "multiple_candidates", "identifier_not_text",
    }:
        return issue("PRODUCT_IDENTITY_AMBIGUOUS", "conflict")
    candidate, = candidates
    code = candidate["internalCode"]
    expected_spec = _normalized_spec(candidate.get("spec"))
    matches = snapshot.rows_by_code.get(code, [])
    if not matches:
        return issue("INTERNAL_CODE_NOT_IN_QUOTE_SOURCE")
    if len(matches) != 1:
        return issue("INTERNAL_CODE_MULTIPLE_SOURCE_ROWS", "conflict")
    quote, = matches
    actual_spec = _normalized_spec(quote["spec"])
    if not expected_spec or not actual_spec:
        return issue("SPECIFICATION_UNAVAILABLE")
    if expected_spec != actual_spec:
        return issue("SPECIFICATION_MISMATCH", "conflict")
    upc = row["rawUpc"]
    if not isinstance(upc, str) or not UPC.fullmatch(upc) or upc not in UPC_SEPARATORS.split(quote["upc"]):
        return issue("UPC_NOT_CORROBORATED", "conflict")
    raw_amount = quote["rawAmount"].strip()
    if not raw_amount:
        return issue("SUPPLY_QUOTE_MISSING")
    if raw_amount.startswith("-") and DECIMAL.fullmatch(raw_amount[1:]):
        return issue("SUPPLY_QUOTE_NEGATIVE")
    amount = _amount(raw_amount)
    if amount is None:
        return issue("SUPPLY_QUOTE_INVALID")
    reference = {
        "priceType": "catalog_reference", "internalCode": code, "spec": quote["spec"], "upc": upc,
        "amountDecimal": amount, "rawAmount": quote["rawAmount"], "currency": None,
        "sourceRowNumber": quote["sourceRowNumber"],
        "scopeType": None, "storeId": None, "storeGroupId": None, "isStoreSpecific": None,
        "isUniformPrice": None, "pricingUnit": None, "taxIncluded": None,
        "validFrom": None, "validTo": None, "priceVersion": None,
    }
    return _result(row, "reference_only", list(SCOPE_ISSUES), reference)


def lookup_supply_prices(product_recognition, snapshot):
    """Bind reference quotes to already-authorized product-recognition rows."""
    if (product_recognition.get("contractVersion") != "activity-product-recognition/1.0"
            or product_recognition.get("status") != "ready"):
        raise ValueError("unsupported product recognition")
    source_binding = product_recognition["binding"]
    binding = {key: source_binding[key] for key in ("activityId", "versionId", "inputDigest", "catalogExportSha256")}
    catalog = product_recognition.get("catalog") or {}
    binding["catalogSourceSha256"] = catalog.get("sourceSha256")
    same_source = (catalog.get("sourceSha256") == snapshot.source["sourceSha256"]
                   and catalog.get("sourceDate") == snapshot.source["snapshotDate"])
    if same_source:
        rows = [_resolve(row, snapshot) for row in product_recognition["rows"]]
    else:
        rows = [_result(row, "missing", ["CATALOG_SOURCE_MISMATCH"])
                for row in product_recognition["rows"]]
    keys = {(row["candidateId"], row["rowId"]) for row in rows}
    if len(keys) != len(rows):
        raise ValueError("duplicate recognition row")
    return {"contractVersion": CONTRACT, "status": "ready" if same_source else "source_mismatch",
            "binding": binding, "source": dict(snapshot.source), "statusCounts": dict(Counter(row["status"] for row in rows)), "rows": rows}


def main():
    cli = argparse.ArgumentParser()
    cli.add_argument("--source", type=Path, required=True)
    cli.add_argument("--snapshot-date", required=True)
    cli.add_argument("--expected-sha256", required=True)
    cli.add_argument("--recognition", type=Path, required=True)
    args = cli.parse_args()
    snapshot = load_export(args.source, snapshot_date=args.snapshot_date, expected_sha256=args.expected_sha256)
    result = lookup_supply_prices(json.loads(args.recognition.read_bytes()), snapshot)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
