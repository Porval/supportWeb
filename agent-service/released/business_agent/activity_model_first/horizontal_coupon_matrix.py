"""Resolve participation in adjacent coupon sections from marked workbook cells."""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
import re

from .coupon_workbook import UPC, _field
from .planning_workbooks import _sheet_rows, labels, values
from .structured_evidence import StructuredProjection

SCOPE = 'horizontal_coupon_matrix/v1'
TIER = re.compile(r'(\d+(?:\.\d{1,2})?)\s*[-－减]\s*(\d+(?:\.\d{1,2})?)')
PERIOD = re.compile(r'(\d{1,2})[./](\d{1,2})\s*[-－—至]\s*(\d{1,2})[./](\d{1,2})')
BRAND = re.compile(r'品牌券(?:[（(][^）)]{1,30}[）)])?')
JOINT = re.compile(r'(?:\d{1,2}月)?(?:价探)?联合(?:活动|券)')
PLATFORMS = {'美团':'meituan','淘宝闪购':'taobao_instant','京东':'jd'}

@dataclass
class Section:
    heading: dict
    period: dict
    kind: str
    tiers: list

@dataclass
class Matrix:
    doc: dict
    header_row: int
    columns: dict
    sections: list
    rows: list
    platform: dict
    title: dict

def column_order(col):
    index = 0
    for letter in col:
        index = index * 26 + ord(letter) - ord('A') + 1
    return index

def coupon_matrix(payload):
    doc, sheets = _sheet_rows(payload['documents'])
    if len(sheets) != 1 or doc['errors'] or doc['textBlocks']:
        return None
    form = (payload.get('manualFields') or {}).get('fields', {})
    if form.get('type') not in (None, 'brand') or any(form.get(k) for k in ('sourceActivityId', 'manualProductRows', 'rulesText')):
        return None
    rows, = sheets.values()
    headers = [i for i,row in enumerate(rows) if {'UPC码','商品名称'} <= labels(row).keys()]
    if len(headers) != 1 or headers[0] < 3:
        return None
    i = headers[0]
    columns = labels(rows[i])
    if any(label not in {'UPC码','商品名称','规格','商品简称','券前价','到手价'} and not TIER.fullmatch(label)
           for label in columns):
        return None
    headings, periods, tier_row = map(values, rows[i-2:i+1])
    titles = [cell for row in rows[:i-2] for cell in values(row).values()]
    platforms = [cell for cell in titles if cell['text'].strip() in PLATFORMS]
    names = [cell for cell in titles if cell not in platforms]
    if len(names) != 1 or len(platforms) != 1 or headings.keys() != periods.keys():
        return None
    sections = []
    for col,cell in sorted(tier_row.items(), key=lambda item:column_order(item[0])):
        m = TIER.fullmatch(cell['text'].strip())
        if not m:
            if sections or col in headings:
                return None
            continue
        if not 0 < Decimal(m[2]) <= Decimal(m[1]):
            return None
        if col in headings:
            title = headings[col]['text'].strip()
            kind = 'brand' if BRAND.fullmatch(title) else 'platform' if JOINT.fullmatch(title) else None
            period = PERIOD.fullmatch(periods[col]['text'].strip())
            if not kind or not period:
                return None
            try:
                sm,sd,em,ed = map(int,period.groups())
                date(2000,sm,sd)
                date(2000,em,ed)
            except ValueError:
                return None
            sections.append(Section(headings[col],periods[col],kind,[]))
        if not sections:
            return None
        sections[-1].tiers.append((col,cell))
    if len(sections) != len(headings) or sorted(s.kind for s in sections) != ['brand','platform']:
        return None
    details = []
    seen = set()
    all_tiers = {col for sec in sections for col,_ in sec.tiers}
    for row in rows[i+1:]:
        cells = values(row)
        upc = cells.get(columns['UPC码'],{}).get('text','').strip()
        name = cells.get(columns['商品名称'],{}).get('text','').strip()
        marked = all_tiers & cells.keys()
        if (not UPC.fullmatch(upc) or not name or upc in seen or not marked
                or any(cells[col]['text'].strip().upper()!='Y' for col in marked)):
            return None
        seen.add(upc)
        details.append((row,cells))
    if not details:
        return None
    return Matrix(doc, rows[i]['locator']['row'], columns, sections, details, platforms[0], names[0])

def reconcile_matrix(matrix, payload, result, metadata, lookup):
    if not matrix:
        return result, metadata
    from .diagnostics import require
    out = StructuredProjection(payload,result,metadata,lookup)
    observed = {rid:row for g in result['candidates'] for row in g['productRows'] for rid in row['sourceRowIds']}
    doc = matrix.doc
    def source_id(row):
        loc = row['locator']
        return out.positions[(doc['materialId'],loc['sheet'],loc['row'])]
    expected = {source_id(row) for row,_ in matrix.rows}
    require(set(observed)==expected,'matrix_product_coverage')
    def cite(cell):
        return out.cell(doc,cell)
    model_brands = {b for group in metadata['candidateMetadata'] for b in (group['brandNames']['value'] or [])}
    common_brands = sorted(b for b in model_brands if b and b in matrix.title['text']
                           and all(b in cells[matrix.columns['商品名称']]['text'] for _,cells in matrix.rows))
    if not common_brands:
        prefix = re.match(r'^(.{1,24}?)闪电仓',matrix.title['text'].strip())
        if prefix and all(cells[matrix.columns['商品名称']]['text'].startswith(prefix[1]) for _,cells in matrix.rows):
            common_brands = [prefix[1]]
    brands = _field(common_brands,[cite(matrix.title)]) if common_brands else None
    for section in matrix.sections:
        title, period = section.heading['text'].strip(), section.period['text'].strip()
        activity_name = f'{title}（{common_brands[0]}）' if len(common_brands)==1 else title
        title_refs = [cite(section.heading), cite(matrix.title)] if common_brands else [cite(section.heading)]
        fields = {'activityName':_field(activity_name,title_refs),
                  'type':_field(section.kind,[cite(section.heading)]),
                  'brandIds':out.user_field('brandIds'), 'remark':out.user_field('remark'),
                  'brandBenefit':out.user_field('brandBenefit'),
                  'platformCode':_field(PLATFORMS[matrix.platform['text'].strip()],[cite(matrix.platform)])}
        counts = {col:0 for col,_ in section.tiers}
        products = []
        for row,cells in matrix.rows:
            marks = [(col,header) for col,header in section.tiers if col in cells]
            if not marks:
                continue
            related = [section.heading,section.period]
            for col,header in marks:
                counts[col] += 1
                related.extend((header,cells[col]))
            upc = cells[matrix.columns['UPC码']]
            name = cells[matrix.columns['商品名称']]
            spec = cells.get(matrix.columns.get('规格'))
            product = out.product(doc,row,upc,name,related=[*related,*([spec] if spec else [])])
            original = observed[source_id(row)]
            require((not original['rawUpc'] or original['rawUpc']==upc['text']) and
                    (not original['rawName'] or original['rawName'] in name['text']),
                    'matrix_product_identity')
            if spec:
                product['rawSpec'] = spec['text']
            products.append(product)
        rules = []
        refs = [cite(section.heading),cite(section.period)]
        for col,header in section.tiers:
            threshold,discount = TIER.fullmatch(header['text'].strip()).groups()
            rules.append(f'满{threshold}减{discount}（{counts[col]}个商品）')
            refs.append(cite(header))
        fields['rulesText'] = _field(f'{title}，'+'、'.join(rules)+f'；原表周期 {period}。',refs)
        sm,sd,em,ed = map(int, PERIOD.fullmatch(period).groups())
        for field,md in [('startAt',(sm,sd)),('endAt',(em,ed))]:
            supplied = out.form.get(field)
            if supplied:
                try:
                    d = date.fromisoformat(supplied[:10])
                except (TypeError,ValueError):
                    d = None
                if d and (d.month,d.day)==md:
                    fields[field]=out.user_field(field)
                else:
                    out.conflicts.append({'field':field,'manualValue':supplied,'extractedValues':[period],
                                          'evidenceIds':[cite(section.period),*out.user_field(field)['evidenceIds']]})
        out.add(fields,products,brands=brands)
    return out.finish(distinct_source_rows=len(expected))
