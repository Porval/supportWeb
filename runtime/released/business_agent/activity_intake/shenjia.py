"""Read Meituan's paired 神价 and child-activity sheets within a single XLSX."""
from collections import Counter, defaultdict
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


def _text(cells, key):
    return cells[key]['text'].strip() if key in cells else ''


def _identifier(cells, key):
    value = _text(cells, key)
    if value and IDENTIFIER.fullmatch(value) and cells[key]['sourceType'] in ('string', 'number'):
        return value
    return None


def _header(cells):
    labels = tuple(cell['text'].strip() for cell in cells)
    for start in range(len(labels) - len(SUMMARY_HEADER) + 1):
        end = start + len(SUMMARY_HEADER)
        if labels[start:end] == SUMMARY_HEADER:
            # The exported worksheet also contains a later helper block with
            # another 子活动ID column. The primary ID is inside the export header.
            stop = labels.index('子活动ID', end) if '子活动ID' in labels[end:] else len(labels)
            return 'summary', {cell['column']: SUMMARY_COLUMNS[label]
                               for cell, label in zip(cells[start:stop], labels[start:stop])
                               if label in SUMMARY_COLUMNS}
    counts = Counter(labels)
    if all(counts[label] == 1 for label in DETAIL_REQUIRED):
        return 'detail', {cell['column']: DETAIL_COLUMNS[label]
                          for cell, label in zip(cells, labels)
                          if label in DETAIL_COLUMNS and counts[label] == 1}
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


def _split_rows(rows):
    parts = {'summary': [], 'detail': []}
    remaining = []
    headers = {}
    for row in rows:
        sheet = row['locator'].get('sheet')
        header = _header(row['cells'])
        if header:
            headers[sheet] = header
            continue
        labels = {cell['text'].strip() for cell in row['cells'] if cell['sourceType'] == 'string'}
        if len(labels & GENERIC_HEADERS) >= 2:
            headers.pop(sheet, None)
        if sheet not in headers:
            remaining.append(row)
            continue
        role, columns = headers[sheet]
        cells = _products(row['cells'], columns)
        child_id = _identifier(cells, 'childId')
        if child_id or any(key in cells for key in ('upc', 'name', 'internalCode', 'childName')):
            parts[role].append((child_id, cells, row['locator']))
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
    summary_counts = Counter(child for child, _, _ in parts['summary'] if child)
    detail_by_child = defaultdict(list)
    for index, (child, _, _) in enumerate(parts['detail']):
        if child:
            detail_by_child[child].append(index)
    group_by_source = {}
    paired_details = set()
    conflicting_details = set()
    evidence_by_location = {}

    def cite(cell):
        locator = cell['locator']
        key = (locator['sheet'], locator['cell'])
        if key not in evidence_by_location:
            evidence_by_location[key] = evidence(document['materialId'], document['sha256'], locator, cell['text'])
        return evidence_by_location[key]

    for child, summary, locator in parts['summary']:
        source = _identifier(summary, 'sourceActivityId')
        if source not in group_by_source:
            group_by_source[source] = new_group()
        group = group_by_source[source]
        _observations(group, summary, add, cite, warn)
        details = detail_by_child.get(child, [])
        detail = {}
        duplicate = bool(child and (summary_counts[child] > 1 or len(details) > 1))
        conflict = not source or duplicate
        if not source:
            warn('SHENJIA_SOURCE_ID_MISSING', '神价ID缺失，保留原表商品行待核对', locator)
        if duplicate:
            warn('SHENJIA_CHILD_ID_DUPLICATE', '子活动ID重复，两侧商品明细保留待核对', locator)
            conflicting_details.update(details)
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
                conflicting_details.add(index)
        elif child is None:
            warn('SHENJIA_CHILD_ID_MISSING', '子活动ID缺失，保留原表商品行待核对', locator)
            conflict = True
        group['rows'].append(_product_row(summary, detail, cite, warn, conflict))

    unresolved = None
    for index, (child, detail, locator) in enumerate(parts['detail']):
        if index in paired_details:
            continue
        if unresolved is None:
            unresolved = new_group()
        warn('SHENJIA_CHILD_UNPAIRED', '子活动明细未能归属到唯一活动，保留该行待核对', locator)
        unresolved['rows'].append(_product_row({}, detail, cite, warn, True))
    return remaining
