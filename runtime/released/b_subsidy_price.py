"""Project the three original subsidy amounts by the Agent's cited product row."""
from decimal import Decimal
import re

from account_primitives import fail
from business_agent.activity_model_first.submission_checks import AMOUNT, AMOUNT_FIELDS, HEADERS, header_label

KEYS = {'price': 'systemPriceDecimal', 'subsidy': 'subsidyDecimal', 'net': 'netPriceDecimal'}


def source_prices(analysis, candidate):
    evidence = {entry['evidenceId']: entry for entry in analysis['evidence']}
    source = {}
    for document in analysis['materialResults']:
        if document['kind'] != 'xlsx' or document['status'] != 'parsed':
            continue
        headers = {}
        for row in document['tableRows']:
            locator = row['locator']
            sheet, position = locator['sheet'], locator['row']
            cells = {cell['column']: cell for cell in row['cells']}
            headings = [(HEADERS.get(header_label(cell['text'])), key) for key, cell in cells.items()]
            headings = [(label, key) for label, key in headings if label]
            if len(headings) >= 2:
                columns = dict(headings)
                headers[sheet] = columns if len(headings) == len(columns) and set(columns) == set(HEADERS.values()) else None
                continue
            if not (columns := headers.get(sheet)) or not cells.get(columns['name'], {}).get('text', '').strip():
                continue
            values = {}
            refs = {}
            for name, field in KEYS.items():
                cell = cells.get(columns[name])
                raw = cell['text'].strip() if cell else ''
                values[field] = raw if AMOUNT.fullmatch(raw) and cell['sourceType'] in ('number', 'string') else None
                if cell:
                    refs[field] = {'location': cell['locator']['cell'], 'raw': raw}
            price, subsidy, net = (values[KEYS[name]] for name in AMOUNT_FIELDS)
            expected_formula = f"={columns['price']}{position}-{columns['subsidy']}{position}"
            if price is not None and subsidy is not None:
                expected = Decimal(price) - Decimal(subsidy)
                if expected >= 0 and net is None and refs.get(KEYS['net'], {}).get('raw') == expected_formula:
                    net = values[KEYS['net']] = format(expected, '.2f')
                status = 'conflict' if expected < 0 or net is not None and Decimal(net) != expected else 'ready' if net is not None else 'missing'
            else:
                status = 'missing'
            source[document['materialId'], sheet, position] = {
                **values, 'status': status, 'source': {'materialId': document['materialId'], 'sheet': sheet,
                                                       'row': position, 'cells': refs}}
    result = []
    for row in candidate['productRows']:
        locations = {(e['materialId'], e['locator'].get('sheet'), e['locator'].get('row'))
                     for ref in row['evidenceIds'] if (e := evidence.get(ref))}
        located = [source[key] for key in locations if key in source]
        if len(located) == 1:
            result.append(located[0] | {'rowId': row['rowId'], 'name': row['rawName']})
    return result


def apply_corrections(rows, corrections, actor_id, at):
    if not isinstance(corrections, list) or len(corrections) > len(rows):
        fail(422, 'INVALID_SUBSIDY_PRICE', '补贴价格补正行无效')
    by_id = {row['rowId']: row for row in rows}
    changes = {}
    for item in corrections:
        required = {'rowId', 'reason', *KEYS.values()}
        if not isinstance(item, dict) or set(item) != required:
            fail(422, 'INVALID_SUBSIDY_PRICE', '请填写三项价格和修正说明')
        row_id, reason = item['rowId'], item['reason']
        if (not isinstance(row_id, str) or row_id not in by_id or row_id in changes
                or by_id[row_id]['status'] not in ('conflict', 'missing')
                or not isinstance(reason, str) or not reason.strip() or len(reason) > 200):
            fail(422, 'INVALID_SUBSIDY_PRICE', '请选择待核对价格并填写修正说明')
        values = {field: item[field] for field in KEYS.values()}
        if any(not isinstance(value, str) or not AMOUNT.fullmatch(value) for value in values.values()):
            fail(422, 'INVALID_SUBSIDY_PRICE', '价格须为非负金额，最多两位小数')
        price, subsidy, net = (Decimal(values[KEYS[name]]) for name in AMOUNT_FIELDS)
        if price - subsidy != net:
            fail(422, 'SUBSIDY_PRICE_CONFLICT', '系统活动价减申请补贴应等于补贴后单价')
        changes[row_id] = by_id[row_id] | values | {
            'status': 'corrected', 'correction': {'actorId': actor_id, 'at': at, 'reason': reason.strip()}}
    return [changes.get(row['rowId'], row) for row in rows]


def needs_correction(rows):
    return any(row['status'] in ('missing', 'conflict') for row in rows)
