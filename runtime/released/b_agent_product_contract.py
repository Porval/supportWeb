"""Validate Agent product envelopes without re-running matching decisions."""

CONTRACT = 'jiatan-data-agent-upc/1.1'
STATUSES = {
    'missing_upc', 'invalid_upc', 'unmatched', 'exact_upc',
    'multiple_candidates', 'details_need_review', 'material_code_conflict',
    'source_conflict', 'historical_candidate', 'identifier_not_text',
    'intake_review_required',
}


def require(condition):
    if not condition:
        raise ValueError('invalid standardized Agent product output')


def text(value, nullable=False):
    return (nullable and value is None) or isinstance(value, str)


def refs(value, allowed):
    require(isinstance(value, list) and all(isinstance(v, str) for v in value))
    require(len(value) == len(set(value)) and set(value) <= allowed)


def validate_product_output(value, analysis, catalog_digest):
    require(isinstance(value, dict))
    require(set(value) == {'schemaVersion', 'catalog', 'inputDigest', 'rows',
                           'catalogIdentityRuleVersion', 'confirmedIdentityCount'})
    require(value['schemaVersion'] == CONTRACT)
    require(value['inputDigest'] == analysis['inputDigest'])
    catalog = value['catalog']
    require(isinstance(catalog, dict) and catalog.get('exportSha256') == catalog_digest)
    for key in ('status', 'syncDay', 'lastFetchedAt', 'sourceDate', 'sourceSha256', 'exportSha256'):
        require(isinstance(catalog.get(key), str))
    for key in ('distinctUpcs', 'ambiguousUpcs'):
        require(type(catalog.get(key)) is int and catalog[key] >= 0)
    require(value['catalogIdentityRuleVersion'] == 'catalog-identity/1.0')
    source_rows = [(c['candidateId'], r) for c in analysis['candidates'] for r in c['productRows']]
    originals = {(cid, r['rowId']): r for cid, r in source_rows}
    require(len(originals) == len(source_rows))
    rows = value['rows']
    require(isinstance(rows, list) and len(rows) == len(originals))
    require(type(value['confirmedIdentityCount']) is int and 0 <= value['confirmedIdentityCount'] <= len(rows))
    indexed = {}
    mandatory = {'candidateId', 'rowId', 'evidenceIds', 'upc', 'status', 'internalCode', 'candidates', 'uniqueUpcHit'}
    optional = {'historicalCodes', 'catalogIdentity'}
    for row in rows:
        require(isinstance(row, dict) and mandatory <= set(row) <= mandatory | optional)
        require(isinstance(row['candidateId'], str) and isinstance(row['rowId'], str))
        key = (row['candidateId'], row['rowId'])
        require(key in originals and key not in indexed)
        allowed_refs = set(originals[key]['evidenceIds'])
        refs(row['evidenceIds'], allowed_refs)
        require(isinstance(row['status'], str) and row['status'] in STATUSES)
        require(type(row['uniqueUpcHit']) is bool)
        require(text(row['upc'], nullable=True) and text(row['internalCode'], nullable=True))
        require(isinstance(row['candidates'], list))
        for candidate in row['candidates']:
            require(isinstance(candidate, dict) and set(candidate) == {'internalCode', 'name', 'spec', 'brand', 'detailsToCheck'})
            require(all(text(candidate[k]) for k in ('internalCode', 'name', 'spec', 'brand')))
            details = candidate['detailsToCheck']
            require(isinstance(details, list) and all(isinstance(d, str) and d in {'item_name', 'spec'} for d in details))
        if 'historicalCodes' in row:
            require(isinstance(row['historicalCodes'], list) and all(text(v) for v in row['historicalCodes']))
        if 'catalogIdentity' in row:
            identity = row['catalogIdentity']
            require(isinstance(identity, dict) and set(identity) == {'status', 'internalCode', 'evidenceIds'})
            require(identity['status'] == 'exact_internal_code_upc' and text(identity['internalCode']))
            refs(identity['evidenceIds'], allowed_refs)
        indexed[key] = row
    require(set(indexed) == set(originals))
    return indexed
