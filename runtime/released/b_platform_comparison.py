"""Conservative deterministic comparison of a platform list against one approved snapshot."""
from collections import Counter, defaultdict
from hashlib import sha256
from pathlib import PurePosixPath
import unicodedata

from account_primitives import APIError, fail
from b_domain import upload_payload

CSV = 'text/csv'
XLSX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
FORMATS = {'.csv': CSV, '.xlsx': XLSX}
JSON_LIMIT = 7 * 1024 * 1024
FILE_LIMIT = 5 * 1024 * 1024
MATCH_RULE = 'platform-material-identifiers/1.1'
HEADERS = {
    'productid': 'id', '内部商品id': 'id', 'internalcode': 'internalCode',
    '内部商品码': 'internalCode', '内部码': 'internalCode', 'upc': 'upc', '条码': 'upc',
}


def parse_file(value, decode):
    if not isinstance(value, dict):
        fail(422, 'INVALID_MATERIAL', '清单必须包含文件名、类型和内容', {'field': 'listFile'})
    try:
        filename, media, content, _ = upload_payload(value)
    except APIError as error:
        code = 'INPUT_LIMIT' if error.status == 413 else 'INVALID_MATERIAL' if error.code in ('INVALID_REQUEST','MATERIAL_HASH_MISMATCH') else error.code
        fail(error.status, code, error.message, {'field': 'listFile'})
    filename = PurePosixPath(filename.replace('\\', '/')).name
    if not filename.strip() or any(unicodedata.category(c) in ('Cc', 'Cs') for c in filename):
        fail(422, 'INVALID_MATERIAL', '清单文件名无效', {'field': 'listFile'})
    if FORMATS.get(PurePosixPath(filename).suffix.casefold()) != media:
        fail(422, 'UNSUPPORTED_MATERIAL', '清单仅支持 UTF-8 CSV 和安全 XLSX', {'field': 'listFile'})
    digest = sha256(content).hexdigest()
    parsed = decode({'materialId': 'platform-list', 'filename': filename, 'mediaType': media,
                     'sha256': digest, 'contentBytes': content})
    if parsed['status'] not in ('parsed', 'partial'):
        code = next((e['code'] for e in parsed['errors']), '')
        if code == 'LIMIT_EXCEEDED':
            fail(413, 'INPUT_LIMIT', '清单超出解析预算', {'field': 'listFile'})
        fail(422, 'INVALID_MATERIAL', '清单无法安全读取', {'field': 'listFile'})
    populated = [row for row in parsed['tableRows'] if any(cell['text'].strip() for cell in row['cells'])]
    if not populated or len(populated)==1 and any(cell['text'].strip().casefold() in HEADERS for cell in populated[0]['cells']):
        fail(422, 'INVALID_MATERIAL', '清单没有有效表格内容', {'field': 'listFile'})
    return filename, media, content, digest, parsed


def comparison_row(result, product, approved, platform, *, cells=(), locators=(), reasons=(), raw=()):
    return {
        'result': result, 'productId': product.get('id') if product else None,
        'productNameSnapshot': product.get('name') if product else None,
        'internalCodeSnapshot': product.get('internalCode') if product else None,
        'approvedPresence': approved, 'platformPresence': platform,
        'rawValues': [{'column': cell['column'], 'value': cell['text'], 'sourceType': cell['sourceType']}
                      for cell in cells] + list(raw),
        'locators': [cell['locator'] for cell in cells] or list(locators),
        'reasonCodes': sorted(set(reasons)),
    }


def table_rows(parsed, products):
    indexes = defaultdict(set)
    for identity, product in products.items():
        for field in ('id', 'internalCode', 'upc'):
            value = product.get(field)
            if isinstance(value, str) and value:
                indexes[field, value].add(identity)
    sheets = defaultdict(list)
    for row in parsed['tableRows']:
        if any(cell['text'].strip() for cell in row['cells']):
            sheets[row['locator'].get('sheet')].append(row)
    incomplete = parsed['status'] != 'parsed'
    reasons = {'SOURCE_INCOMPLETE'} if incomplete else set()
    matched, pending = defaultdict(list), []
    if len(sheets) != 1:
        reasons.add('SCHEMA_UNCONFIRMED')
    for table in sheets.values():
        header = table[0]
        columns = {cell['column']: HEADERS[cell['text'].strip().casefold()]
                   for cell in header['cells'] if cell['sourceType'] == 'string'
                   and cell['text'].strip().casefold() in HEADERS}
        if not columns or len(columns) != len(set(columns.values())) or len(sheets) != 1:
            reasons.add('SCHEMA_UNCONFIRMED')
            pending.extend(comparison_row('needs_check', None, 'unknown', 'unknown', cells=row['cells'],
                                          reasons=['SCHEMA_UNCONFIRMED']) for row in table)
            continue
        for row in table[1:]:
            identifiers = [cell for cell in row['cells'] if cell['column'] in columns and cell['text'].strip()]
            issues, identities = set(), set()
            if not identifiers:
                issues.add('IDENTIFIER_UNMATCHED')
            for cell in identifiers:
                if cell['sourceType'] != 'string' or cell['text'].lstrip().startswith('='):
                    issues.add('IDENTIFIER_NOT_TEXT')
                    continue
                matches = indexes[columns[cell['column']], cell['text']]
                if len(matches) != 1:
                    issues.add('IDENTIFIER_AMBIGUOUS' if matches else 'IDENTIFIER_UNMATCHED')
                else:
                    identities.update(matches)
            if len(identities) > 1:
                issues.add('IDENTIFIER_CONFLICT')
            if issues:
                reasons.update(issues)
                pending.append(comparison_row('needs_check', None, 'unknown', 'unknown',
                                              cells=row['cells'], reasons=issues))
            else:
                matched[next(iter(identities))].extend(row['cells'])
    return matched, pending, reasons


def compare(snapshot, products, basis, parsed):
    counts = dict.fromkeys(('approvedProducts', 'platformProducts', 'same', 'missingFromPlatform',
                            'addedOnPlatform', 'unmatchedRows'))
    if parsed is None:
        return {'basis': basis, 'status': 'scope_only', 'counts': counts, 'rows': [],
                'reasonCodes': ['SCOPE_NOT_ENUMERATED']}
    matched, pending, reasons = table_rows(parsed, products)
    platform_complete = not reasons
    counts['unmatchedRows'] = len(pending) if parsed['status'] == 'parsed' else None
    approved_raw = snapshot.get('products')
    valid_approved = isinstance(approved_raw,list) and all(isinstance(pid,str) for pid in approved_raw)
    source_ids = set(approved_raw) if valid_approved else set()
    approved = source_ids & products.keys()
    unresolved = snapshot.get('unresolvedRows')
    scope = snapshot.get('productApplicability') or {}
    approved_complete = (valid_approved and approved == source_ids and unresolved == []
                         and isinstance(scope,dict) and scope.get('kind') == 'explicit_skus')
    if not approved_complete:
        reasons.add('SOURCE_INCOMPLETE')
    for raw in unresolved if isinstance(unresolved,list) else ():
        pending.append(comparison_row('needs_check', None, 'unknown', 'unknown', raw=[raw],
                                      reasons=['SOURCE_INCOMPLETE']))
    for pid in sorted(source_ids - approved):
        pending.append(comparison_row('needs_check', None, 'present', 'unknown',
                                      raw=[{'productId': pid}], reasons=['IDENTIFIER_UNMATCHED']))
    rows = []
    for pid in sorted(approved | matched.keys()):
        if pid in approved and pid in matched:
            result, left, right = 'same', 'present', 'present'
        elif pid in approved:
            result, left, right = ('missing_from_platform', 'present', 'absent') if platform_complete else ('needs_check', 'present', 'unknown')
        else:
            result, left, right = ('added_on_platform', 'absent', 'present') if approved_complete else ('needs_check', 'unknown', 'present')
        rows.append(comparison_row(result, products[pid], left, right, cells=matched.get(pid, ()),
                                   reasons=['SOURCE_INCOMPLETE'] if result == 'needs_check' else ()))
    rows.extend(pending)
    totals = Counter(row['result'] for row in rows)
    counts.update(approvedProducts=len(approved) if approved_complete else None,
                  platformProducts=len(matched) if platform_complete else None)
    if approved_complete and platform_complete:
        counts.update(same=totals['same'], missingFromPlatform=totals['missing_from_platform'],
                      addedOnPlatform=totals['added_on_platform'])
    return {'basis': basis, 'status': 'complete' if approved_complete and platform_complete else 'partial',
            'counts': counts, 'rows': [row | {'rowId': str(i)} for i, row in enumerate(rows, 1)],
            'reasonCodes': sorted(reasons)}


def confirmed_comparison_inputs(db, activity_id, version_id, platform_code, snapshot, products):
    """Consume the final review owner's immutable SKU set, never infer a replacement list."""
    from b_registration_review import saved, TABLE
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (TABLE,)).fetchone():
        return snapshot, products, {}
    record = saved(db, activity_id, version_id, platform_code)
    if not record or record.get('status') != 'finalized':
        return snapshot, products, {}
    catalog = dict(products)
    approved, unresolved = [], []
    for row in record.get('finalRows', []):
        code = row.get('internalCode')
        if not isinstance(code, str) or not code:
            unresolved.append({'reasonCode': 'FINAL_IDENTITY_UNAVAILABLE', 'rowId': row.get('rowId')})
            continue
        local = [(identity, product) for identity, product in products.items() if product.get('internalCode') == code]
        if len(local) == 1:
            identity = local[0][0]
        else:
            # This internal key is not exposed as a local product ID.
            identity = 'confirmed-internal-code:' + code
            item = row.get('productSnapshot') or {}
            catalog[identity] = {'id': None, 'internalCode': code,
                                 'name': item.get('name'), 'upc': item.get('upc')}
        approved.append(identity)
    effective = snapshot | {'products': approved, 'unresolvedRows': unresolved, 'productApplicability': {'kind': 'explicit_skus'}}
    return effective, catalog, {'finalSelectionDigest': record.get('finalDigest'),
                                'finalDecisionId': record.get('finalDecisionId')}
