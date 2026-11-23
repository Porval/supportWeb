"""Gather brand evidence from the current, already validated product matching result."""

from collections import defaultdict
import unicodedata

VERSION = "product-brand-evidence/1.0"
REVIEW_STATUSES = {"details_need_review", "intake_review_required", "identifier_not_text"}
EMPTY_NAMES = {"-", "—", "未知", "未填写"}


def _key(name):
    return unicodedata.normalize("NFKC", name).strip().casefold()


def product_brand_evidence(rows, *, products=(), brands=()):
    """Return directory brands with row evidence and optional authoritative master IDs."""
    brand_ids = {brand["id"] for brand in brands}
    master_products = defaultdict(list)
    for product in products:
        key = product.get("internalCode"), product.get("upc")
        if all(key):
            master_products[key].append(product)

    groups = {}
    for row in rows:
        candidates = row.get("candidates", [])
        if len(candidates) != 1 or row.get("uniqueUpcHit") is not True:
            continue
        candidate, = candidates
        name = candidate["brand"].strip()
        normalized = _key(name)
        if not normalized or normalized in EMPTY_NAMES:
            continue
        code = candidate["internalCode"]
        status = row["directoryStatus"]
        identity = row.get("catalogIdentity", {})
        if identity.get("status") == "exact_internal_code_upc" and identity.get("internalCode") == code:
            basis = "exact_internal_code_upc"
            evidence_ids = identity["evidenceIds"]
        elif status == "exact_upc" and row.get("matchedInternalCode") == code:
            basis = "exact_upc"
            evidence_ids = [ref["evidenceId"] for ref in row["sourceReferences"]]
        elif status in REVIEW_STATUSES:
            basis = "directory_candidate"
            evidence_ids = [ref["evidenceId"] for ref in row["sourceReferences"]]
        else:
            continue

        group = groups.setdefault(normalized, {"directoryBrand": name, "products": [], "masterIds": set()})
        group["products"].append({"rowId": row["rowId"], "internalCode": code,
                                  "basis": basis, "evidenceIds": evidence_ids})
        master = master_products.get((code, row.get("rawUpc")), ())
        if basis != "directory_candidate" and len(master) == 1:
            brand_id = master[0].get("brandId")
            if brand_id in brand_ids:
                group["masterIds"].add(brand_id)

    results = []
    for group in groups.values():
        ids = group.pop("masterIds")
        verified = any(product["basis"] != "directory_candidate" for product in group["products"])
        results.append(group | {"evidenceStatus": "product_identity_verified" if verified else "product_candidate",
                                "brandId": next(iter(ids)) if len(ids) == 1 else None,
                                "masterStatus": "matched" if len(ids) == 1 else "conflict" if ids else "unmapped"})
    return sorted(results, key=lambda result: (result["evidenceStatus"] != "product_identity_verified",
                                               _key(result["directoryBrand"])))
