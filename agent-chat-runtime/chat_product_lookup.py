"""Match explicitly identified product UPCs against the local Data Agent snapshot."""

import re
import unicodedata

from product_paging import MAX_PAGE_ROWS, page_info, validate_page

MAX_MATCH_ROWS = MAX_PAGE_ROWS
MAX_CANDIDATES_SHOWN = 5
HEADERS = {
    'upc': {'upc', 'upc码', '商品upc', 'upc条码', '商品条形码', '商品条码', '条码', '条形码',
            '神价upc', '商品编码upc', '商品条码upc', '商品69码', '69码', 'ean', 'sku条码'},
    'name': {'商品名称', '商品名', '产品名称', '产品名', 'upc对应商品名称', '神价商品', '品名'},
    'internalCode': {'内部码', '商品内部码', '内部商品码', '公司内部码', '系统内部码', 'sku内部码'},
    'spec': {'规格', '商品规格', '产品规格'},
}
HEADER_ALIAS = {alias:field for field, aliases in HEADERS.items() for alias in aliases}
LOOKUP_WORDS = re.compile(r'内部码|内部商品码|商品主档|商品目录|商品匹配|匹配商品|商品接口|商品的接口')
LOOKUP_ACTION = re.compile(r'查|核对|匹配|对应|找|映射')
DRAFT_ACTION = re.compile(r'整理|格式化|总结|草稿|按(?:标准|固定)字段|活动信息')
UPC_LIST = r'[0-9]{8,14}(?:[\s，,、]+[0-9]{8,14})*'
EXPLICIT_UPC = re.compile(r'(?:upc|条形码|条码)\s*[：:=]?\s*(' + UPC_LIST + r')(?![0-9.A-Za-z])', re.IGNORECASE)
UPC_ONLY = re.compile(UPC_LIST)
COLUMN_NUMBER = re.compile(r'[0-9]{8,14}')
EXPLICIT_CODE = re.compile(r'(?:公司|商品)?内部(?:商品)?码\s*[：:=]?\s*([a-zA-Z0-9][a-zA-Z0-9_-]{2,79})(?![a-zA-Z0-9_-])')
STATUS_LABELS = {
    'exact_upc':'唯一映射', 'exact_internal_code':'目录内部码精确匹配',
    'invalid_internal_code':'原表内部码格式待核对', 'source_unavailable':'UPC 目录暂不可读', 'details_need_review':'名称或规格待核对',
    'identifier_not_text':'原表 UPC 为数字单元格，候选待核对',
    'multiple_candidates':'一对多，待确认', 'source_conflict':'目录来源冲突',
    'material_code_conflict':'原表内部码与目录冲突', 'historical_candidate':'仅历史候选',
    'invalid_upc':'原表 UPC 格式待核对', 'unmatched':'目录无匹配',
    'missing_upc':'原表未填写 UPC', 'intake_review_required':'识别结果待核对',
}


def header_value(text):
    return re.sub(r'[\s（）()：:]+', '', unicodedata.normalize('NFKC', text).casefold())


def product_rows(rows):
    """Yield source-bound rows from explicit UPC headers, including repeated headers."""
    columns_by_section = {}
    for row in rows:
        location = row['locator']
        section = location.get('sheet'), location.get('table')
        cells = {cell['column']:cell for cell in row['cells']}
        headers = {}
        for column, cell in cells.items():
            if field := HEADER_ALIAS.get(header_value(cell['text'])):
                headers.setdefault(field, []).append(column)
        if 'upc' in headers or 'internalCode' in headers:
            columns_by_section[section] = {key:values[0] for key,values in headers.items() if len(values) == 1}
            continue
        columns = columns_by_section.get(section)
        if not columns:
            continue
        values = {key:cells.get(column, {}) for key,column in columns.items()}
        data = {key:cell.get('text', '').strip() for key,cell in values.items()}
        upc_cell = values.get('upc',{})
        code_cell = values.get('internalCode',{})
        upc = data.get('upc')
        if upc_cell.get('sourceType') == 'formula':
            upc = None
        if not upc and not (data.get('name') or data.get('internalCode')):
            continue
        if upc and not COLUMN_NUMBER.fullmatch(upc) and not (data.get('name') or data.get('internalCode') or upc_cell.get('sourceType') == 'number'):
            continue
        if 'sheet' in location:
            place = f"{location['sheet']} · 第 {location['row']} 行"
        elif 'table' in location:
            place = f"表 {location['table']} · 第 {location['row']} 行"
        else:
            place = f"第 {location['row']} 行"
        yield {
            'upc':upc, 'name':data.get('name'), 'rawInternalCode':data.get('internalCode'),
            'spec':data.get('spec'), 'location':place,
            'upcIsText':upc_cell.get('sourceType') == 'string',
            'codeIsText':code_cell.get('sourceType') == 'string',
        }


def typed_upcs(text):
    if UPC_ONLY.fullmatch(text.strip()):
        return re.findall(r'[0-9]{8,14}', text)
    return [upc for group in EXPLICIT_UPC.findall(text) for upc in re.findall(r'[0-9]{8,14}', group)]


def typed_internal_codes(text):
    return EXPLICIT_CODE.findall(text)


def is_product_question(text):
    return bool(LOOKUP_WORDS.search(text) or UPC_ONLY.fullmatch(text.strip())
                or re.search(r'upc|条形码|条码', text, re.IGNORECASE) and LOOKUP_ACTION.search(text))


def asks_for_draft(text):
    return DRAFT_ACTION.search(text) is not None


def query_rows(attachments, messages):
    question = messages[-1]['content']
    explicit = typed_upcs(question)
    explicit_codes = typed_internal_codes(question)
    rows = [row | {'filename':attachment['filename']} for attachment in attachments
            for row in attachment.get('productRows', ())]
    if explicit:
        matching = [row for row in rows if row['upc'] in explicit]
        present = {row['upc'] for row in matching}
        return matching + [{'upc':upc, 'upcIsText':True} for upc in dict.fromkeys(explicit) if upc not in present]
    if explicit_codes:
        return [{'upc':None,'rawInternalCode':code,'codeIsText':True,'upcIsText':False}
                for code in dict.fromkeys(explicit_codes)]
    if rows:
        return rows
    for message in reversed(messages[::2][:-1]):
        prior = typed_upcs(message['content'])
        if prior:
            return [{'upc':upc, 'upcIsText':True} for upc in dict.fromkeys(prior)]
    return []


def lookup_catalog(cache, rows, master=None, *, offset=0, limit=MAX_MATCH_ROWS):
    from business_agent.data_agent_catalog import catalog

    validate_page(offset,limit)
    page = page_info(len(rows),offset,limit)
    try:
        record = catalog.read_cache(cache) if cache else None
    except (OSError, ValueError, KeyError, AttributeError, TypeError):
        record = None
    edition = record['export'] if record else None
    code_entries = {}
    if any(not row['upc'] and row.get('rawInternalCode') for row in rows):
        from chat_product_search import catalog_records
        code_entries,_ = catalog_records(master,cache)
    if edition is None and not code_entries:
        return {'status':'unavailable','rows':[],'inputRows':len(rows),'page':page,'_sourceRows':rows}
    source_date = (edition['catalog']['saved_on'] if edition else
                   max(item['sourceDate'] for item in code_entries.values()))
    matches = []
    for index, source_row in enumerate(rows[offset:offset+limit],offset+1):
        row = {**source_row,'productIndex':index}
        upc = row['upc']
        if upc and edition:
            hit = catalog._lookup(edition,upc,raw_code=row.get('rawInternalCode'),
                                  name=row.get('name'),spec=row.get('spec'))
        elif upc:
            hit = {'upc':upc,'status':'source_unavailable','internalCode':None,'candidates':[]}
        else:
            code = row.get('rawInternalCode')
            item = code_entries.get(code)
            status = 'missing_upc' if not code else 'invalid_internal_code' if not row.get('codeIsText') else 'unmatched'
            hit = {'upc':None,'status':status,'internalCode':None,'candidates':[]}
            if item:
                hit['candidates'] = [item]
                if row.get('codeIsText'):
                    differences = [field for field in ('name','spec') if row.get(field)
                                   and catalog._normalize(row[field]) != catalog._normalize(item[field])]
                    if differences:
                        hit['status'] = 'details_need_review'
                    else:
                        hit.update(status='exact_internal_code',internalCode=code)
        if hit['status'] == 'exact_upc' and not row['upcIsText']:
            hit.update(status='identifier_not_text', internalCode=None)
        matches.append({**row, **hit})
    return {'status':'available','sourceDate':source_date,'lastSyncedAt':record['fetchedAt'] if record else None,
            'inputRows':len(rows),'rows':matches,'page':page,'_sourceRows':rows}


def lookup_recognized_products(cache, result):
    from business_agent.data_agent_catalog import catalog

    try:
        standard = catalog.match_analysis_standardized(cache,result) if cache else None
    except (OSError,ValueError,KeyError,AttributeError,TypeError):
        standard = None
    if standard is None:
        return {'status':'unavailable','rows':[]}
    originals = {(item['candidateId'],row['rowId']):row for item in result['candidates']
                 for row in item['productRows']}
    rows = []
    for index,entry in enumerate(standard['rows'],1):
        row = originals[entry['candidateId'],entry['rowId']]
        rows.append({**entry,'name':row['rawName'],'rawInternalCode':row['rawInternalCode'],
                     'spec':row.get('rawSpec'),'upcIsText':entry['status']!='identifier_not_text',
                     'codeIsText':True,'productIndex':index})
    return {'status':'available','rows':rows[:MAX_MATCH_ROWS],'inputRows':len(rows),
            '_sourceRows':rows,'page':page_info(len(rows)),
            'sourceDate':standard['catalog']['sourceDate'],'lastSyncedAt':standard['catalog']['lastFetchedAt']}


def describe_product_lookup(result):
    if result['status'] == 'unavailable':
        return '活动 Agent 暂时读不到数据 Agent 的商品目录，内部码待查询。'
    source = f"来源为数据 Agent 商品目录，快照日期 {result['sourceDate']}。"
    rows = result['rows']
    page = result.get('page')
    if not rows and page and page['totalRows']:
        return f"本地共保存 {page['totalRows']} 条商品，这一页没有更多商品。可以说‘上一页’或指定商品序号。"
    if not rows:
        return f'可以按 UPC 或原有内部码核对公司商品。{source}请提供条码、内部码或相应的商品明细。'
    exact = sum(row['status'] == 'exact_upc' for row in rows)
    exact += sum(row['status']=='exact_internal_code' for row in rows)
    unique = sum(len(row['candidates'])==1 for row in rows)
    lines = [f'核对了 {len(rows)} 行。{unique} 行找到唯一目录内部码，{exact} 行通过精确匹配。{source}']
    if page:
        lines[0] = f"商品第 {page['start']}–{page['end']} 条，共 {page['totalRows']} 条（按商品顺序，原表行号见各条出处）。" + lines[0]
    for row in rows:
        status = STATUS_LABELS[row['status']]
        product = ' · '.join(str(value)[:90]
                             for value in (row.get('upc') or row.get('rawInternalCode') or '标识待补',row.get('name'))
                             if value)
        if row.get('internalCode'):
            detail = f"公司内部码 {row['internalCode']}"
        else:
            candidates = [item['internalCode'] + (
                f"（{item['name'][:55]} {item['spec'][:35]}）" if item['name'] or item['spec'] else '')
                for item in row['candidates']] or row.get('historicalCodes', [])
            detail = status
            if row.get('rawInternalCode'):
                detail += f" · 原表内部码 {row['rawInternalCode'][:90]}"
            if candidates:
                detail += ' · 候选内部码 ' + '、'.join(candidates[:MAX_CANDIDATES_SHOWN])
                if len(candidates) > MAX_CANDIDATES_SHOWN:
                    detail += f' 等 {len(candidates)} 个'
        place = ' / '.join(value for value in (row.get('filename'), row.get('location')) if value)
        lines.append(f"• {row.get('productIndex','')} {product} → {detail}" + (f'（{place}）' if place else ''))
    if page and page['hasMore']:
        lines.append(f"后面还有 {page['totalRows']-page['end']} 条，可直接说‘下一页’，或指定‘第 101–200 条商品’。")
    return '\n'.join(lines)
