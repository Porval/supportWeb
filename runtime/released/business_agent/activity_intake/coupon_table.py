"""Deterministic recognition of the two-party coupon detail table."""
import re
from unicodedata import normalize

from coupon_dates import observe_period

REQUIRED = {'品牌', '报名id', '平台', '品牌补贴', '价探补贴'}
PLATFORMS = {'美团', '淘宝', '淘宝闪购', 'taobao_instant', 'meituan'}
AMOUNT = r'[0-9]{1,9}(?:\.[0-9]{1,2})?'
TIER = re.compile(rf'^(?:满\s*)?({AMOUNT})\s*[-－—减]\s*({AMOUNT})\s*(?:元)?$')
BRAND_SCOPE = {'不限品牌', '全部品牌', '全品牌', '多品牌', '待确认', '未知', '无', '-', '/'}
COMPLETE_BRANDS = {'完整参与品牌名单','全部参与品牌'}
PARALLEL_RELATIONS = {'两档同时生效','各档同时生效','各档并行生效','同时生效','并行生效'}


def value(cell):
    if not cell or cell['sourceType'] == 'formula':
        return ''
    return cell['text'].strip()


def table_records(rows):
    """Yield exact detail rows and their active headers, or other table rows."""
    columns = None
    active_header = None
    identity = None
    for row in rows:
        current = (row['locator'].get('sheet'), row['locator'].get('table'))
        if current != identity:
            columns = None
            identity = current
        cells = {cell['column']: cell for cell in row['cells']}
        header = {value(cell).lower(): col for col, cell in cells.items() if cell['sourceType'] == 'string'}
        if REQUIRED <= header.keys():
            columns = header
            active_header = row
            if '券档' not in columns and columns['品牌'] in ('B', '2'):
                columns['券档'] = 'A' if columns['品牌'] == 'B' else '1'
            continue
        if columns is None or {'活动名称','优惠机制'} <= header.keys() or {'upc','商品名称'} <= header.keys():
            columns = None
            yield row, None, None, None
            continue
        fields = {name: cells[col] for name, col in columns.items() if col in cells and value(cells[col])}
        if not fields or any(value(cell).startswith(('示例', '请填写', '待填写')) for cell in fields.values()):
            continue
        yield row, fields, active_header, columns


def platform_code(cell):
    platform = value(cell)
    if platform in PLATFORMS:
        return {'美团':'meituan','淘宝':'taobao_instant','淘宝闪购':'taobao_instant'}.get(platform,platform)
    return None


def scan(rows):
    """Return recognized table rows by platform and registration ID, plus remaining rows."""
    groups, remaining, issues, period_scopes = {}, [], [], {}
    for row, fields, header, _ in table_records(rows):
        if fields is None:
            remaining.append(row)
            continue
        registration = fields.get('报名id')
        source_id = value(registration)
        platform = platform_code(fields.get('平台'))
        if not re.fullmatch(r'[0-9]{1,60}', source_id) or (registration['sourceType'] == 'number' and len(source_id) > 15) or platform is None:
            issues.append(row['locator'])
            continue
        groups.setdefault((platform, source_id), []).append(fields)
        if sum(value(cell) == '活动时间' for cell in header['cells']) == 1:
            period_scopes[id(fields)] = tuple(header['locator'].items())
    return groups, remaining, issues, period_scopes


def spelling(name):
    return normalize('NFKC', name).strip().casefold()


def rule_variants(records):
    variants={}
    for row in records:
        match=TIER.fullmatch(value(row.get('券档')))
        if match:
            variants.setdefault(match.groups(),[]).append(row)
    return variants


def parallel_rules(variants, records):
    relations=[value(row.get('档位关系')) for row in records if value(row.get('档位关系'))]
    if not relations or any(value not in PARALLEL_RELATIONS for value in relations):
        return False
    if len(variants)!=2 and '两档同时生效' in relations:
        return False
    return all(any(value(row.get('档位关系')) in PARALLEL_RELATIONS for row in rows)
               and all(len({value(row.get(column)) for row in rows if value(row.get(column))})<=1
                       for column in ('品牌补贴','价探补贴')) for rows in variants.values())


def describe_variant(tier, rows):
    threshold,discount=tier
    parts=[f'满{threshold}元减{discount}元']
    cells=[rows[0]['券档']]
    for column in ('品牌补贴','价探补贴'):
        observed={value(row[column]):row[column] for row in rows if column in row}
        if observed:
            amounts=['¥'+amount if re.fullmatch(AMOUNT,amount) else amount for amount in observed]
            parts.append(column+' '+' / '.join(amounts))
            cells.extend(observed.values())
    return '，'.join(parts),cells


def period_cells(records, tiers, parallel, period_scopes):
    """Keep activity-date cells on proven tier rows or unambiguous continuations."""
    if not tiers or (len(tiers)>1 and not parallel):
        return []
    scoped_tiers = {}
    for tier, tier_rows in tiers.items():
        for row in tier_rows:
            scope = period_scopes.get(id(row))
            if scope is not None:
                scoped_tiers.setdefault(scope, set()).add(tier)
    result = []
    for row in records:
        scope = period_scopes.get(id(row))
        if scope is None or '活动时间' not in row:
            continue
        if tier_match := TIER.fullmatch(value(row.get('券档'))):
            result.append((row['活动时间'],tier_match.groups()))
        elif not value(row.get('券档')) and len(scoped_tiers.get(scope, ())) == 1:
            result.append((row['活动时间'],next(iter(scoped_tiers[scope]))))
    return result


def emit(document, rows, catalog, newgroup, add, evidence, warning, *, partition=None):
    if partition is None:
        groups, remaining, issues, period_scopes = scan(rows)
    else:
        platform, records = partition
        groups, remaining, issues = {(platform, None): records}, [], []
        period_scopes = {id(row): () for row in records}
    for locator in issues:
        warning('COUPON_ROW_UNRESOLVED','券档明细缺少有效的报名 ID 或平台，保留原行供核对',locator)
    catalog_by_name = {}
    for brand in catalog['brands']:
        catalog_by_name.setdefault(spelling(brand['name']), []).append(brand['id'])
    for (platform, source_id), records in groups.items():
        group = newgroup()
        ids = {}

        def cite(cell):
            key = tuple(cell['locator'].items())
            if key not in ids:
                ids[key] = evidence(document['materialId'], document['sha256'], cell['locator'], cell['text'])
            return ids[key]

        def unique(column):
            return {value(fields[column]): fields[column] for fields in records if column in fields}

        first = records[0]
        add(group, 'platformCode', platform, cite(first['平台']))
        if source_id is not None:
            add(group, 'sourceActivityId', source_id, cite(first['报名id']))
        anchor = first.get('报名id') or first['券档']
        names = {}
        for record in records:
            cell = record.get('品牌')
            for name in re.split(r'[、,，；;\r\n]+',value(cell)):
                if (name := name.strip()) and name not in BRAND_SCOPE:
                    names.setdefault(spelling(name), (name,cell))
        rules, rule_cells = [], []
        tiers = rule_variants(records)
        group['couponDateTiers'] = frozenset(tiers)
        multi=len(tiers)>1
        parallel=multi and parallel_rules(tiers,records)
        core_summary=None
        activity_names = unique('活动名称')
        for name, cell in activity_names.items():
            add(group, 'activityName', name[:200], cite(cell))
        if len(tiers) == 1:
            (threshold, discount), tier_rows = next(iter(tiers.items()))
            cell=tier_rows[0]['券档']
            label = '美团' if platform == 'meituan' else '淘宝闪购'
            core_summary=f'{label}品牌联合券，满{threshold}元减{discount}元'
            summary=core_summary
            if names:
                summary += f'，材料列明{len(names)}个参与品牌'
                rule_cells.extend(brand_cell for _, brand_cell in names.values())
            rules.append(summary)
            rule_cells.append(cell)
            if not activity_names:
                group['couponDraftName']=f'品牌联合券 {threshold}-{discount}'
                add(group, 'activityName', group['couponDraftName'], cite(cell))
                warning('COUPON_DRAFT_NAME','草稿名称按券档生成，平台正式名称待核对',cell['locator'])
            add(group, 'type', 'platform', cite(cell))
        elif parallel:
            descriptions=[describe_variant(tier,rows) for tier,rows in tiers.items()]
            rules.append('同时生效：'+'。'.join(description for description,_ in descriptions))
            rule_cells.extend(cell for _,cells in descriptions for cell in cells)
            rule_cells.extend(row['档位关系'] for row in records if '档位关系' in row)
            add(group,'type','platform',cite(next(iter(tiers.values()))[0]['券档']))
            if not activity_names:
                group['couponDraftName']='品牌联合券 '+' / '.join('-'.join(tier) for tier in tiers)
                for rows in tiers.values():
                    add(group,'activityName',group['couponDraftName'],cite(rows[0]['券档']))
                warning('COUPON_DRAFT_NAME','草稿名称按并行券档生成，平台正式名称待核对',anchor['locator'])
        else:
            warning('COUPON_TIER_UNRESOLVED','同一报名 ID 的券档缺失或不一致，请核对',anchor['locator'])
        for column in ('品牌补贴','价探补贴','活动时间','报名时间'):
            if multi and column in ('品牌补贴','价探补贴'):
                continue
            values = unique(column)
            if not values:
                continue
            if column in ('品牌补贴','价探补贴'):
                formatted = ['¥'+v if re.fullmatch(AMOUNT,v) else v for v in values]
            elif column == '报名时间':
                formatted = [v.removeprefix(column).lstrip('：: ') for v in values]
            else:
                formatted = list(values)
            rules.append(column+' '+' / '.join(formatted))
            rule_cells.extend(values.values())
            if len(values) > 1:
                warning('COUPON_VALUES_DIFFER','同一报名 ID 下有多个'+column+'原值，请核对',next(iter(values.values()))['locator'])
        if multi and not parallel:
            relation_cells=list(unique('档位关系').values())
            for tier,rows in tiers.items():
                description,variant_cells=describe_variant(tier,rows)
                for cell in (*variant_cells,*relation_cells):
                    add(group,'rulesText',description[:10000],cite(cell))
        else:
            rule='。'.join(rules)[:10000]
            if core_summary and rules:
                core='。'.join([core_summary,*rules[1:]])[:10000]
                group['couponRuleCore']={rule:core}
            for cell in rule_cells:
                add(group, 'rulesText', rule, cite(cell))
        activity_periods = unique('活动时间')
        if not activity_periods:
            warning('COUPON_PERIOD_NOT_LISTED','此券档未单列活动时间，可补充经确认的活动周期',anchor['locator'])
        else:
            scoped_periods = period_cells(records, tiers, parallel, period_scopes)
            complete = (document['status']=='parsed' and not document['errors'] and scoped_periods
                        and observe_period(group,scoped_periods,set(tiers),add,cite))
            if not complete:
                time_cell = next(iter(activity_periods.values()))
                warning('COUPON_PERIOD_RAW','活动时间已保留原文，准确起止日期待确认',time_cell['locator'])
        mapped, missing = {}, []
        brand_entries=[]
        for key, (name,cell) in names.items():
            matches = catalog_by_name.get(key, [])
            if len(matches) == 1 and len(mapped) < 20:
                mapped.setdefault(matches[0], cell)
                brand_entries.append((name,matches[0],cite(cell)))
            else:
                missing.append(name)
                brand_entries.append((name,None,cite(cell)))
        scope_evidence=[cite(cell) for mark,cell in unique('名单范围').items() if mark in COMPLETE_BRANDS]
        if brand_entries or scope_evidence:
            group['couponBrandList']={'entries':brand_entries,'scopeEvidence':scope_evidence}
        for cell in mapped.values():
            add(group, 'brandIds', list(mapped), cite(cell))
        if names:
            remark = '材料参与品牌：'+'、'.join(name for name, _ in names.values())
            if missing:
                remark += '。品牌主档待核对：'+'、'.join(missing)
                warning('COUPON_BRAND_MAPPING_INCOMPLETE','有材料品牌尚未唯一匹配主档',anchor['locator'])
            group['couponBrandRemark']=remark[:5000]
            for _,cell in names.values():
                add(group, 'remark', remark[:5000], cite(cell))
    return remaining
