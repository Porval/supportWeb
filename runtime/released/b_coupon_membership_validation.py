"""Validate original spreadsheet evidence for one SKU participating in multiple coupon groups."""
import json
from b_state import canon


def _position(locator):
    return (locator.get('sheet'), locator.get('row'), locator.get('cell'))


def bind_coupon_sections(result, matrix):
    if matrix is None or len(result['candidates']) != len(matrix.sections):
        return None
    doc = matrix.doc
    original_texts = {
        _position(cell['locator']): cell['text']
        for table_row in doc['tableRows'] for cell in table_row['cells']
    }
    evidence = {
        item['evidenceId']: _position(item['locator'])
        for item in result['evidence']
        if item['materialId'] == doc['materialId']
        and item['sourceSha256'] == doc['sha256']
        and item['locator']['kind'] == 'xlsx'
        and original_texts.get(_position(item['locator'])) == item['quote']
    }
    headers_by_section = {
        frozenset(_position(header['locator']) for _, header in section.tiers): section
        for section in matrix.sections
    }
    all_headers = set().union(*headers_by_section)
    used_headers = set()
    bindings = []

    for candidate in result['candidates']:
        rule = candidate['fields']['rulesText']
        if rule['status'] != 'explicit':
            return None
        refs = {evidence.get(key) for key in rule['evidenceIds']}
        headers = frozenset(refs & all_headers)
        section = headers_by_section.get(headers)
        if section is None or headers in used_headers:
            return None
        used_headers.add(headers)
        section_refs = {_position(cell['locator']) for cell in (section.heading, section.period)}
        if not section_refs <= refs:
            return None

        bindings.append((candidate, section))
    return bindings, evidence, all_headers


def verify_coupon_memberships(result, request, lookup):
    from business_agent.activity_model_first.horizontal_coupon_matrix import coupon_matrix

    matrix = coupon_matrix(request)
    if matrix is None or len(result['candidates']) != len(matrix.sections):
        return False

    doc = matrix.doc
    rows_by_position = {}
    for entry in lookup.values():
        locator = entry['locator']
        if entry['materialId'] == doc['materialId'] and locator.get('kind') == 'xlsx':
            rows_by_position[(locator['sheet'], locator['row'])] = entry['rowId']

    scoped = bind_coupon_sections(result, matrix)
    if scoped is None:
        return False
    groups, evidence, all_headers = scoped
    for candidate, section in groups:
        expected = {}
        for source_row, cells in matrix.rows:
            marks = [(header, cells[col]) for col, header in section.tiers if col in cells]
            if not marks:
                continue
            location = source_row['locator']
            source_id = rows_by_position.get((location['sheet'], location['row']))
            if not source_id or source_id in expected:
                return False
            identity = (cells[matrix.columns['UPC码']]['text'], cells[matrix.columns['商品名称']]['text'])
            mark_refs = {_position(cell['locator']) for pair in marks for cell in pair}
            expected[source_id] = identity, mark_refs

        seen_sources = set()
        for product in candidate['productRows']:
            sources = product['sourceRowIds']
            if len(sources) != 1 or sources[0] not in expected or sources[0] in seen_sources:
                return False
            source_id = sources[0]
            seen_sources.add(source_id)
            sku, marks = expected[source_id]
            row_refs = {evidence.get(key) for key in product['evidenceIds']}
            if sku != (product.get('rawUpc'), product.get('rawName')) or not marks <= row_refs:
                return False
            if row_refs & all_headers != marks & all_headers:
                return False
        if seen_sources != expected.keys():
            return False
    return True


def verify_saved_coupon_memberships(result, form, filenames, input_scope_hash):
    # Only call for a formerly validated multi-membership receipt. Recreate the
    # same stable row labels as the saved input and bind to its exact scope.
    from business_agent.activity_model_first.source import plan, scope_fingerprint
    from business_agent.workbench_deepseek.adapter import AdapterError

    request = json.loads(canon({
        'contractVersion': 'activity-intake/1.0',
        'inputRevision': result['inputRevision'],
        'inputDigest': result['inputDigest'],
        'manualFields': form,
        'documents': result['materialResults'],
        'catalogSnapshot': {'revision': result['catalogRevision']},
        'materialNames': filenames,
    }))
    try:
        batches, lookup, _ = plan(request)
    except AdapterError:
        return False
    return (scope_fingerprint(batches) == input_scope_hash
            and verify_coupon_memberships(result, request, lookup))
