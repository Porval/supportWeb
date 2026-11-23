"""Read the declared tiers in a structured coupon and product schedule."""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
import re

from .coupon_workbook import AMOUNT, UPC, _table, _text

HEADERS = {'满减券', '满', '减', '投放渠道名称', '具体时间', '商品69码', '商品名称'}
LABEL = re.compile(r'(\d+(?:\.\d{1,2})?)\s*[-－]\s*(\d+(?:\.\d{1,2})?)(?:\s*(联合券))?')
PERIOD = re.compile(r'(\d{1,2})\.(\d{1,2})\s*[-－至]\s*(\d{1,2})\.(\d{1,2})')


@dataclass
class CouponTier:
    threshold: Decimal
    discount: Decimal
    channel: str
    period: tuple
    joint: bool
    rows: list = field(default_factory=list)

    @property
    def name(self):
        threshold, discount = (format(value.normalize(), 'f') for value in (self.threshold, self.discount))
        return f'满{threshold}减{discount}元' + ('联合券' if self.joint else '')

    @property
    def summary(self):
        sm, sd, em, ed = self.period
        return f'{self.name}，{sm}月{sd}日至{em}月{ed}日，投放渠道{self.channel}，{len(self.rows)}条商品明细'


def tier_schedule(payload):
    form = (payload.get('manualFields') or {}).get('fields') or {}
    documents = payload['documents']
    if (len(documents) != 1 or not form.get('activityName') or form.get('type') != 'brand'
            or any(form.get(key) for key in ('manualProductRows', 'rulesText', 'sourceActivityId'))):
        return None
    doc, = documents
    if doc['kind'] != 'xlsx' or doc['status'] != 'parsed' or doc['errors'] or doc['textBlocks']:
        return None
    table = [row for row in doc['tableRows'] if any(_text(cell) for cell in row['cells'])]
    if not table or not 2 <= len(table) <= 161 or len({row['locator'].get('sheet') for row in table}) != 1:
        return None
    if {_text(cell) for cell in table[0]['cells'] if _text(cell)} != HEADERS:
        return None
    rows = _table(table, HEADERS)
    if not rows or len(rows) != len(table) - 1:
        return None
    tiers = []
    for position, cells in rows:
        required = HEADERS - {'满减券'}
        if (not required <= cells.keys() or any(cell['sourceType'] not in ('number', 'string')
                                              for cell in cells.values() if _text(cell))
                or not all(_text(cells[key]) for key in required)):
            return None
        if not UPC.fullmatch(_text(cells['商品69码'])) or not all(AMOUNT.fullmatch(_text(cells[key])) for key in ('满', '减')):
            return None
        threshold, discount = (Decimal(_text(cells[key])) for key in ('满', '减'))
        period_match = PERIOD.fullmatch(_text(cells['具体时间']))
        if not period_match or not 0 < discount <= threshold:
            return None
        period = tuple(map(int, period_match.groups()))
        try:
            date(2000, *period[:2])
            date(2000, *period[2:])
        except ValueError:
            return None
        signature = (threshold, discount, _text(cells['投放渠道名称']), period)
        label = cells.get('满减券')
        if label and _text(label):
            match = LABEL.fullmatch(_text(label))
            if not match or (Decimal(match[1]), Decimal(match[2])) != signature[:2]:
                return None
            tiers.append(CouponTier(*signature, joint=bool(match[3])))
        elif not tiers or signature != (tiers[-1].threshold, tiers[-1].discount, tiers[-1].channel, tiers[-1].period):
            return None
        tiers[-1].rows.append((position, cells))
    return doc, tiers


def reconcile_tier_schedule(payload, result, metadata, lookup):
    from business_agent.workbench_deepseek.adapter import encode
    from .coupon_workbook import MISSING, _field
    from .projection import BASE_FIELDS

    schedule = tier_schedule(payload)
    if schedule is None or not result.get('candidates') or result.get('conflicts'):
        return result, metadata
    candidates = result['candidates']
    extra = metadata['candidateMetadata']
    # This narrow rule is for one brand's dated coupon schedule. It must not
    # erase an unresolved field, independent activity ID or platform boundary.
    if (any(c['fields']['sourceActivityId']['value'] for c in candidates)
            or any(f['status'] == 'conflict' for c in candidates for f in c['fields'].values())
            or any(f['status'] == 'conflict' for item in extra
                   for key, f in item.items() if key != 'candidateId')
            or any(row['matchKind'] == 'conflict' for c in candidates for row in c['productRows'])):
        return result, metadata
    doc, tiers = schedule
    manual = payload['manualFields']
    form = manual['fields']
    shared_keys = ('type', 'platformCode', 'brandIds', 'brandBenefit', 'remark', 'companyBearing')
    for key in shared_keys:
        values = [c['fields'][key]['value'] for c in candidates if c['fields'][key]['status'] == 'explicit']
        if form.get(key) is not None:
            values.append(form[key])
        if values and any(value != values[0] for value in values):
            return result, metadata
    brand_fields = [item['brandNames'] for item in extra if item['brandNames']['status'] == 'explicit']
    brands = list(dict.fromkeys(brand for f in brand_fields for brand in f['value']))
    brand_ids = form.get('brandIds') or next(
        (c['fields']['brandIds']['value'] for c in candidates if c['fields']['brandIds']['value']), [])
    if len(brands) > 1 or len(brand_ids) > 1:
        return result, metadata

    periods = list(dict.fromkeys(tier.period for tier in tiers))
    # Only a complete date explicitly supplied by the submitter establishes a
    # year. A model's date or the current calendar year is not such evidence.
    confirmed_dates = {}
    for key in ('startAt', 'endAt'):
        raw = form.get(key)
        if raw:
            try:
                confirmed_dates[key] = date.fromisoformat(raw[:10])
            except (ValueError, TypeError):
                return result, metadata
    year = None
    if confirmed_dates:
        years = {value.year for value in confirmed_dates.values()}
        bounds = {'startAt': min(period[:2] for period in periods),
                  'endAt': max(period[2:] for period in periods)}
        if (len(years) != 1 or any(period[:2] > period[2:] for period in periods)
                or any((value.month, value.day) != bounds[key] for key, value in confirmed_dates.items())):
            return result, metadata
        year = years.pop()
        try:
            for period in periods:
                date(year, *period[:2])
                date(year, *period[2:])
        except ValueError:
            return result, metadata
    sheet = tiers[0].rows[0][1]['满']['locator']['sheet']
    positions = {entry['locator']['row']: entry['rowId'] for entry in lookup.values()
                 if entry['materialId'] == doc['materialId'] and entry['locator'].get('sheet') == sheet}
    if any(position not in positions for tier in tiers for position, _ in tier.rows):
        return result, metadata
    evidence = list(result['evidence'])
    by_key = {(item['materialId'], tuple(sorted(item['locator'].items())), item['quote']): item['evidenceId'] for item in evidence}
    ids = {item['evidenceId'] for item in evidence}
    next_id = len(ids) + 1

    def cite(document, locator, quote):
        nonlocal next_id
        key = (document['materialId'], tuple(sorted(locator.items())), quote)
        if key not in by_key:
            while f'ev-{next_id}' in ids:
                next_id += 1
            eid = f'ev-{next_id}'
            next_id += 1
            ids.add(eid)
            by_key[key] = eid
            evidence.append({'evidenceId': eid, 'materialId': document['materialId'],
                             'sourceSha256': document['sha256'], 'locator': locator, 'quote': quote})
        return by_key[key]

    def cite_cell(cell):
        return cite(doc, cell['locator'], cell['text'])

    def field_from_form(key):
        value = form.get(key)
        if not value:
            return dict(MISSING)
        raw = value if isinstance(value, str) else encode(value).decode()
        return _field(value, [cite(manual, {'kind': 'form', 'fieldPath': key}, raw[:500])])

    model_rows = {row['sourceRowIds'][0]: row for candidate in result['candidates']
                  for row in candidate['productRows'] if len(row['sourceRowIds']) == 1}
    expected_rows = {positions[position] for tier in tiers for position, _ in tier.rows}
    # Reconciliation may regroup model-recognized products, but cannot turn an
    # ignored or unclassified source row into a successfully recognized product.
    if set(model_rows) != expected_rows:
        return result, metadata
    brand_refs = list(dict.fromkeys(eid for f in brand_fields for eid in f['evidenceIds']))
    common = {name: dict(MISSING) for name in BASE_FIELDS}
    common['companyBearing'] = _field({'state': 'unknown', 'evidenceIds': []}, [], 'missing')
    for key in shared_keys:
        observed = [c['fields'][key] for c in candidates if c['fields'][key]['status'] == 'explicit']
        if observed:
            common[key] = _field(observed[0]['value'], list(dict.fromkeys(
                eid for f in observed for eid in f['evidenceIds'])))
        if form.get(key) is not None:
            common[key] = field_from_form(key)
    methods = [item['activityMethod'] for item in extra if item['activityMethod']['status'] == 'explicit']
    method = dict(MISSING)
    if methods and all(item['value'] == methods[0]['value'] for item in methods):
        method = _field(methods[0]['value'], list(dict.fromkeys(eid for f in methods for eid in f['evidenceIds'])))
    groups, group_metadata, row_number = [], [], 0
    for group_number, period in enumerate(periods, 1):
        selected = [tier for tier in tiers if tier.period == period]
        sm, sd, em, ed = period
        period_label = f'{sm}月{sd}日—{em}月{ed}日'
        cid = f'{candidates[0]["candidateId"]}-period-{group_number}' if len(periods) > 1 else candidates[0]['candidateId']
        fields = {key: dict(value) for key, value in common.items()}
        fields['activityName'] = field_from_form('activityName')
        if len(periods) > 1:
            fields['activityName']['value'] += f'（{period_label}）'
            fields['activityName']['evidenceIds'] = [*fields['activityName']['evidenceIds'],
                                                     cite_cell(selected[0].rows[0][1]['具体时间'])]
        period_refs = [cite_cell(tier.rows[0][1]['具体时间']) for tier in selected]
        if year is not None:
            year_refs = [eid for key in confirmed_dates for eid in field_from_form(key)['evidenceIds']]
            fields['startAt'] = _field(date(year, sm, sd).isoformat(), list(dict.fromkeys(period_refs + year_refs)))
            fields['endAt'] = _field(date(year, em, ed).isoformat(), list(dict.fromkeys(period_refs + year_refs)))
        summaries = [tier.summary + '，原表行号' + '、'.join(str(position) for position, _ in tier.rows)
                     for tier in selected]
        rule_refs = [cite_cell(tier.rows[0][1][key]) for tier in selected
                     for key in ('满减券', '满', '减', '投放渠道名称', '具体时间')]
        fields['rulesText'] = _field('\n'.join([f'满减券共 {len(selected)} 档', *summaries]), rule_refs)
        products = []
        for tier in selected:
            tier_refs = [cite_cell(tier.rows[0][1][key]) for key in ('满减券', '满', '减', '具体时间')]
            for position, cells in tier.rows:
                upc, name = cells['商品69码'], cells['商品名称']
                rid = positions[position]
                row_number += 1
                row = dict(model_rows.get(rid) or {'rawInternalCode': None, 'rawSpec': None,
                    'matchKind': 'unmatched', 'matchedProductId': None, 'candidates': []})
                row.update(rowId=f'row-{row_number}', rawUpc=_text(upc), rawName=_text(name),
                           sourceRowIds=[rid], evidenceIds=list(dict.fromkeys(
                               row.get('evidenceIds', []) + [cite_cell(upc), cite_cell(name)] + tier_refs)))
                if row.get('rawSpec') and row['rawSpec'] not in name['text']:
                    row['rawSpec'] = None
                products.append(row)
        groups.append({'candidateId': cid, 'fields': fields, 'productRows': products})
        group_metadata.append({'candidateId': cid, 'activityMethod': dict(method),
                               'brandNames': _field(brands, brand_refs) if brands else dict(MISSING)})

    if len(evidence) > 6000:
        return result, metadata
    missing = sorted({key for group in groups for key, f in group['fields'].items() if f['status'] == 'missing'})
    reconciled = result | {'candidates': groups, 'evidence': evidence,
                           'missingFields': [{'field': key, 'reasonCode': 'NOT_PROVIDED'} for key in missing],
                           'outcome': 'needs_confirmation'}
    return reconciled, metadata | {'candidateMetadata': group_metadata,
                                   'coverage': metadata['coverage'] | {'productSourceRows': row_number}}
