"""Read Meituan's paired 神价 and child-activity sheets within a single XLSX."""
from collections import Counter, defaultdict
from decimal import Decimal
import re
from unicodedata import normalize

from coupon_dates import activity_days, date_boundary


SUMMARY_HEADER = ('神价ID', '神价名称', '活动周期', '子活动ID', '神价商品', '神价UPC')
DETAIL_REQUIRED = {'子活动ID', '子活动名称', 'UPC', 'UPC对应商品名称'}
SUMMARY_COLUMNS = {
    '神价ID': 'sourceActivityId', '神价名称': 'activityName', '活动周期': 'period',
    '子活动ID': 'childId', '神价商品': 'name', '神价UPC': 'upc',
    '自动品牌商品码': 'internalCode', '规格': 'spec', '商品规格': 'alternateSpec',
}
DETAIL_COLUMNS = {
    '子活动ID': 'childId', '子活动名称': 'childName', 'UPC': 'upc',
    'UPC对应商品名称': 'name', '规格': 'spec', '商品规格': 'alternateSpec',
}
PLACEHOLDERS = {'', '-', '—', '示例', '示例行', 'example', 'sample', '请填写', '待填写'}
IDENTIFIER = re.compile(r'[0-9]+')
UPC = re.compile(r'[0-9]{8,14}')
PRODUCT_FIELDS = ('internalCode', 'upc', 'name', 'spec')
GENERIC_HEADERS = {'活动名称', '活动编号', '商品名称', '内部码', 'UPC', '条码'}
PRICE_COLUMNS = {'报名价': 'signup', '平台补贴': 'platformSubsidy', '活动价': 'activityPrice',
                 '购买数量': 'quantity'}
AMOUNT = re.compile(r'[0-9]{1,18}(?:\.[0-9]{1,8})?')
FORMULA_HEADER = re.compile(r'平台信息[【\[]报名价-平台补贴=活动价[】\]](?:·手动)?')
PLATFORM_SECTION = {'平台活动侧', '平台活动侧(手动填写)'}


def _text(cells, key):
    return cells[key]['text'].strip() if key in cells else ''


def _identifier(cells, key):
    value = _text(cells, key)
    if value and IDENTIFIER.fullmatch(value) and cells[key]['sourceType'] in ('string', 'number'):
        return value
    return None


def _header(cells):
    labels = tuple(cell['text'].strip() if cell['sourceType'] == 'string' else '' for cell in cells)
    for start in range(len(labels) - len(SUMMARY_HEADER) + 1):
        end = start + len(SUMMARY_HEADER)
        if labels[start:end] == SUMMARY_HEADER:
            # The exported worksheet also contains a later helper block with
            # another 子活动ID column. The primary ID is inside the export header.
            stop = labels.index('子活动ID', end) if '子活动ID' in labels[end:] else len(labels)
            return 'summary', {cell['column']: SUMMARY_COLUMNS[label]
                               for cell, label in zip(cells[start:stop], labels[start:stop])
                               if label in SUMMARY_COLUMNS}, cells[start:stop]
    counts = Counter(labels)
    if all(counts[label] == 1 for label in DETAIL_REQUIRED):
        return 'detail', {cell['column']: DETAIL_COLUMNS[label]
                          for cell, label in zip(cells, labels)
                          if label in DETAIL_COLUMNS and counts[label] == 1}, ()
    return None


def _products(cells, columns):
    fields = {}
    for cell in cells:
        text = cell['text'].strip()
        key = columns.get(cell['column'])
        if key and text.casefold() not in PLACEHOLDERS:
            fields[key] = cell
    if any(cell['text'].lstrip().startswith(('示例：', '示例:', '[填写', '（填写')) for cell in fields.values()):
        return {}
    return fields


def _summary_context(headers, preceding, header_row):
    labels = defaultdict(list)
    for cell in headers:
        if cell['sourceType'] == 'string':
            labels[cell['text'].strip()].append(cell)
    unique = {label: cells[0] for label, cells in labels.items() if len(cells) == 1}
    price_columns = {unique[label]['column']: key for label, key in PRICE_COLUMNS.items() if label in unique}
    nearby = preceding['cells'] if preceding and preceding['locator']['row'] + 1 == header_row else []
    context = {'priceColumns': price_columns}
    if platform := unique.get('美团原价'):
        context['platformHeader'] = platform
        section_column = unique['神价ID']['column'] if '神价ID' in unique else None
        for cell in nearby:
            if cell['sourceType'] == 'string' and cell['column'] == section_column:
                label = normalize('NFKC', cell['text']).strip()
                if label in PLATFORM_SECTION:
                    context['typeHeader'] = cell
    signup = unique.get('报名价')
    if signup and all(key in price_columns.values() for key in ('signup', 'platformSubsidy', 'activityPrice')):
        for cell in nearby:
            if cell['column'] != signup['column'] or cell['sourceType'] != 'string':
                continue
            label = ''.join(normalize('NFKC', cell['text']).split())
            if FORMULA_HEADER.fullmatch(label):
                context['formulaHeader'] = cell
    return context


def _split_rows(rows):
    parts = {'summary': [], 'detail': []}
    remaining = []
    headers = {}
    preceding = {}
    for row in rows:
        sheet = row['locator'].get('sheet')
        previous = preceding.get(sheet)
        preceding[sheet] = row
        header = _header(row['cells'])
        if header:
            role, columns, header_cells = header
            context = _summary_context(header_cells, previous, row['locator']['row']) if role == 'summary' else {}
            headers[sheet] = role, columns, context
            continue
        labels = {cell['text'].strip() for cell in row['cells'] if cell['sourceType'] == 'string'}
        if len(labels & GENERIC_HEADERS) >= 2:
            headers.pop(sheet, None)
        if sheet not in headers:
            remaining.append(row)
            continue
        role, columns, context = headers[sheet]
        cells = _products(row['cells'], columns)
        child_id = _identifier(cells, 'childId')
        if child_id or any(key in cells for key in ('upc', 'name', 'internalCode', 'childName')):
            prices = _products(row['cells'], context.get('priceColumns', {}))
            parts[role].append((child_id, cells, row['locator'], context, prices))
    return parts, remaining


def _value_key(cell):
    return ''.join(normalize('NFKC', cell['text']).split()).casefold()


def _agree_on_upc(summary, detail):
    text = _text(detail, 'upc')
    return (UPC.fullmatch(text) and detail['upc']['sourceType'] == 'string'
            and 'upc' in summary and summary['upc']['sourceType'] in ('string', 'number')
            and _text(summary, 'upc') == text)


def _observations(group, cells, add, cite, warn):
    source_id = _identifier(cells, 'sourceActivityId')
    if source_id:
        add(group, 'sourceActivityId', source_id, cite(cells['sourceActivityId']))
    if _text(cells, 'activityName') and cells['activityName']['sourceType'] == 'string':
        add(group, 'activityName', _text(cells, 'activityName'), cite(cells['activityName']))
    if 'period' in cells:
        raw = _text(cells, 'period')
        dates = activity_days(raw) if cells['period']['sourceType'] == 'string' and 'T' not in raw else None
        evidence_id = cite(cells['period'])
        if dates and all(day and day.on for day in dates) and dates[0].on <= dates[1].on:
            for key, day in zip(('startAt', 'endAt'), dates):
                add(group, key, date_boundary(key, day.on), evidence_id)
        else:
            warn('SHENJIA_PERIOD_UNRESOLVED', '活动周期待核对，请以原文为准', cells['period']['locator'])


def _observe_mechanism(group, context, child_cell, prices, locator, cite, add, warn):
    if platform := context.get('platformHeader'):
        add(group, 'platformCode', 'meituan', cite(platform))
    if activity_type := context.get('typeHeader'):
        add(group, 'type', 'platform', cite(activity_type))
    formula = context.get('formulaHeader')
    if formula is None:
        return
    mechanism = group.setdefault('shenjiaMechanism', {'evidenceIds': [], 'details': [], 'incomplete': False})
    mechanism['evidenceIds'].append(cite(formula))
    keys = ('signup', 'platformSubsidy', 'activityPrice')
    raw = {key: _text(prices, key) for key in keys}
    cells = [prices[key] for key in keys if key in prices]
    mechanism['evidenceIds'].extend(cite(cell) for cell in cells)
    if child_cell is not None:
        mechanism['evidenceIds'].append(cite(child_cell))
    if not child_cell or len(cells) != len(keys) or any(
            prices[key]['sourceType'] not in ('string', 'number') or not AMOUNT.fullmatch(raw[key]) for key in keys):
        warn('SHENJIA_PRICE_UNRESOLVED', '商品的报名价、平台补贴或活动价待核对', locator)
        mechanism['incomplete'] = True
        return
    if Decimal(raw['signup']) - Decimal(raw['platformSubsidy']) != Decimal(raw['activityPrice']):
        warn('SHENJIA_PRICE_CONFLICT', '报名价减平台补贴与活动价不一致，保留原值待核对', prices['activityPrice']['locator'])
        mechanism['incomplete'] = True
        return
    child = child_cell['text'].strip()
    description = (f"子活动 {child} 报名价 {raw['signup']}，平台补贴 {raw['platformSubsidy']}，"
                   f"活动价 {raw['activityPrice']}")
    quantity = _identifier(prices, 'quantity')
    if quantity is not None and int(quantity) > 0:
        mechanism['evidenceIds'].append(cite(prices['quantity']))
        description += f"，购买数量 {quantity}"
    mechanism['details'].append(description)


def _add_mechanism(group, add):
    mechanism = group.get('shenjiaMechanism')
    if mechanism is None:
        return
    summary = '神价活动按报名价减平台补贴计算活动价。'
    if mechanism['incomplete']:
        summary += '部分商品价格待核对。'
    for description in mechanism['details']:
        if len(summary) + len(description) + 1 > 9900:
            summary += '其余商品价格见原表。'
            break
        summary += description + '。'
    for eid in dict.fromkeys(mechanism['evidenceIds']):
        add(group, 'rulesText', summary, eid)


def _product_row(summary, detail, cite, warn, conflict=False):
    cells = {key: summary.get(key) or detail[key]
             for key in PRODUCT_FIELDS if key in summary or key in detail}
    # Use the paired worksheet's original textual UPC. A numeric cell alone
    # still goes through the existing non-text identifier guard.
    if detail and 'upc' in detail:
        cells['upc'] = detail['upc']
    names = [source['name'] for source in (summary, detail) if 'name' in source]
    specs = [source[key] for source in (summary, detail) for key in ('spec', 'alternateSpec') if key in source]
    if specs:
        cells['spec'] = specs[0]
    for observations in (names, specs):
        if len({_value_key(cell) for cell in observations}) > 1:
            warn('SHENJIA_PRODUCT_FIELDS_CONFLICT', '商品名称或规格存在差异，已保留各处原文',
                 observations[-1]['locator'])
            conflict = True
    evidence = list(dict.fromkeys(cite(cell) for source in (summary, detail) for cell in source.values()))
    return {'cells': cells, 'evidenceIds': evidence, 'sourceConflict': conflict}


def emit(document, rows, new_group, add, evidence, warn):
    if document['kind'] != 'xlsx':
        return rows
    parts, remaining = _split_rows(rows)
    if not parts['summary'] and not parts['detail']:
        return remaining
    summary_counts = Counter(child for child, *_ in parts['summary'] if child)
    detail_by_child = defaultdict(list)
    for index, (child, *_) in enumerate(parts['detail']):
        if child:
            detail_by_child[child].append(index)
    group_by_source = {}
    paired_details = set()
    mechanism_rows = []
    evidence_by_location = {}

    def cite(cell):
        locator = cell['locator']
        key = (locator['sheet'], locator['cell'])
        if key not in evidence_by_location:
            evidence_by_location[key] = evidence(document['materialId'], document['sha256'], locator, cell['text'])
        return evidence_by_location[key]

    for child, summary, locator, context, prices in parts['summary']:
        source = _identifier(summary, 'sourceActivityId')
        if source not in group_by_source:
            group_by_source[source] = new_group()
        group = group_by_source[source]
        _observations(group, summary, add, cite, warn)
        details = detail_by_child.get(child, [])
        detail = {}
        duplicate = bool(child and (summary_counts[child] > 1 or len(details) > 1))
        conflict = not source or duplicate
        mechanism_rows.append((group, context, summary['childId'] if child and not duplicate else None, prices, locator))
        if not source:
            warn('SHENJIA_SOURCE_ID_MISSING', '神价ID缺失，保留原表商品行待核对', locator)
        if duplicate:
            warn('SHENJIA_CHILD_ID_DUPLICATE', '子活动ID重复，两侧商品明细保留待核对', locator)
        elif len(details) == 1:
            index = details[0]
            candidate = parts['detail'][index][1]
            if _agree_on_upc(summary, candidate):
                detail = candidate
                paired_details.add(index)
            else:
                warn('SHENJIA_UPC_CONFLICT', '相同子活动ID的两处UPC无法核对一致，保留两处商品明细',
                     locator)
                conflict = True
        elif child is None:
            warn('SHENJIA_CHILD_ID_MISSING', '子活动ID缺失，保留原表商品行待核对', locator)
            conflict = True
        group['rows'].append(_product_row(summary, detail, cite, warn, conflict))

    unresolved = None
    for index, (_, detail, locator, _, _) in enumerate(parts['detail']):
        if index in paired_details:
            continue
        if unresolved is None:
            unresolved = new_group()
        warn('SHENJIA_CHILD_UNPAIRED', '子活动明细未能归属到唯一活动，保留该行待核对', locator)
        unresolved['rows'].append(_product_row({}, detail, cite, warn, True))
    for group, context, child_cell, prices, locator in mechanism_rows:
        _observe_mechanism(group, context, child_cell, prices, locator, cite, add, warn)
    for group in group_by_source.values():
        _add_mechanism(group, add)
    return remaining
