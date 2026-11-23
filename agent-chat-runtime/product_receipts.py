"""Keep parsed headers and directory evidence available for chat follow-ups."""

from collections import OrderedDict
import hashlib
import json
import re
from threading import Lock
from time import monotonic
from uuid import uuid4

from chat_product_lookup import MAX_MATCH_ROWS, STATUS_LABELS

MAX_PRODUCT_RECEIPTS = 512
MAX_RECEIPT_BYTES = 64 * 1024 * 1024
PRODUCT_RECEIPT_TTL = 60 * 60
RECEIPT_ID = re.compile(r'[0-9a-f]{32}')
QUANTITY_QUESTION = re.compile(r'多少|几(?:个|条|行|件|款)')
RECOGNIZED_SUBJECT = re.compile(r'(?:识别|读取|读到|提取|找出|有).*(?:商品|产品|sku)|(?:商品|产品|sku).*(?:识别|读取|读到|提取)')
DIRECT_STATUSES = {'exact_upc','exact_internal_code'}
CODE_CONFLICTS = {'source_conflict','material_code_conflict'}
CODE_FILTER = re.compile(
    r'(?:我|你|我要求|我们)?(?:(?:只要|只看|给我|显示|列出|列一下|筛选|筛出|保留|返回|发我|看下|要求|要|需要|只|先))*'
    r'(?:有|带|查到|匹配到|已确认有|已确认)?(?:公司)?内部(?:商品)?码'
    r'(?:的)?(?:那些|这些|所有)?(?:商品信息|商品明细|商品|明细|产品|名单)?'
    r'(?:列出来|发给我|给我|发一下|就行|即可|吧|呀|呢)?')
COLUMN_QUESTION = re.compile(
    r'(?:请问|看下|确认下|那|这个表|这份表)?(?:[^。，！？]{1,40}的)?([a-z]{1,3})列'
    r'(?:的?(?:表头|字段|内容))?(?:是什么|是啥|叫什么|不是.+吗|是不是.+|是.+吗|写的什么|写的啥)?')
INFO_FOLLOWUP = {'商品信息','商品明细','这些商品','这些商品信息','商品列表','有内部码的'}


def selection_intent(question):
    if count_intent(question):
        return None
    if question in INFO_FOLLOWUP or CODE_FILTER.fullmatch(question):
        if question == '内部码' or question == '公司内部码':
            return None
        return 'confirmed' if '已确认' in question else 'catalog'
    return None


def column_intent(question):
    match = COLUMN_QUESTION.fullmatch(question)
    return match.group(1).upper() if match else None


def is_confirmed(row):
    return row['status'] in DIRECT_STATUSES or (
        (row.get('catalogIdentity') or {}).get('status')=='exact_internal_code_upc')


def single_candidate(row):
    return len(row['candidates'])==1 and (row['status'] not in CODE_CONFLICTS or is_confirmed(row))


def count_intent(question):
    if not QUANTITY_QUESTION.search(question):
        return None
    if re.search(r'内部码|匹配.*(?:商品|产品|sku)|(?:商品|产品|sku).*匹配',question):
        return 'matched'
    if RECOGNIZED_SUBJECT.search(question):
        return 'recognized'
    return None


def product_counts(result):
    lookup = result.get('productLookup') or {}
    search = result.get('productSearch') or {}
    if lookup.get('status')=='available' and lookup.get('inputRows'):
        rows=lookup['rows']
        confirmed=[row for row in rows if is_confirmed(row)]
        unique=[row for row in rows if single_candidate(row)]
        ambiguous=sum(len(row['candidates'])>1 for row in rows)
        absent=sum(not row['candidates'] for row in rows)
        return {
            'kind':'catalog','sourceDate':lookup['sourceDate'],'inputRows':lookup['inputRows'],
            'checkedRows':len(rows),'page':lookup.get('page'),'directRows':len(confirmed),
            'uniqueRows':len(unique),'uniqueCodes':len({row['candidates'][0]['internalCode'] for row in unique}),
            'ambiguousRows':ambiguous,'noCurrentCandidateRows':absent,
            'conflictingRows':len(rows)-len(unique)-ambiguous-absent,
            'distinctUpcs':len({row['upc'] for row in rows if isinstance(row.get('upc'),str)
                                and re.fullmatch(r'[0-9]{8,14}',row['upc'])}),
        }
    if search.get('status')=='available':
        return {'kind':'name','candidateCount':search['totalCandidates'],'query':search['query']}
    return None


def describe_counts(counts, intent):
    if counts['kind']=='name':
        return f"按「{counts['query']}」查到 {counts['candidateCount']} 个名称候选，结果待确认。"
    rows=counts['inputRows']
    checked=counts['checkedRows']
    scope=f'原表共 {rows} 行商品，已核对 {checked} 行' if checked<rows else f'本次核对了 {rows} 行商品'
    page = counts.get('page')
    if page and page['returnedRows']:
        scope = f"本地已识别商品 {rows} 条；本页核对第 {page['start']}–{page['end']} 条，共 {checked} 条"
    registered=(f"。核对范围内有 {counts['distinctUpcs']} 个不同 UPC"
                if intent=='recognized' and counts['distinctUpcs'] else '')
    conflicts = f"另有 {counts['conflictingRows']} 行目录或原表编码有冲突。" if counts.get('conflictingRows') else ''
    return (
        f"{scope}{registered}。\n\n"
        f"• {counts['uniqueRows']} 行查到唯一目录内部码，涉及 {counts['uniqueCodes']} 个不同内部码"
        f"。其中 {counts['directRows']} 行已通过精确匹配规则。\n"
        f"• {counts['ambiguousRows']} 行有多个候选，{counts['noCurrentCandidateRows']} 行无当前目录候选。{conflicts}\n\n"
        f"目录快照日期为 {counts['sourceDate']}。"
    )


def product_snapshot(result, attachments, previous=None):
    snapshot = dict(previous or {})
    if attachments:
        rows = [row | {'filename':item['filename']} for item in attachments for row in item.get('productRows',())]
        identity = hashlib.sha256(json.dumps([
            {'filename':item['filename'],'sha256':item.get('sha256'),
             'rows':item.get('productRows',[]),'headers':item.get('sheetHeaders',[])}
            for item in attachments],sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        if snapshot.get('materialIdentity') != identity:
            snapshot = {}
        snapshot.update(materialIdentity=identity,
                        materialFiles=[(item['filename'],item.get('sha256')) for item in attachments],
                        headers=[header for item in attachments for header in item.get('sheetHeaders',())],
                        rawRows=rows,rawInputRows=len(rows),
                        sourcePartial=any(item.get('sourcePartial',False) for item in attachments))
    if result.get('productSearch') is not None or result.get('productLookup') is not None:
        for field in ('counts','items','lastQuery'):
            snapshot.pop(field,None)
        lookup = result.get('productLookup')
        if lookup is not None:
            if not snapshot.get('materialIdentity') or not snapshot.get('rawRows'):
                snapshot['rawRows'] = lookup.get('_sourceRows',lookup.get('rows',[]))
                snapshot['rawInputRows'] = lookup.get('inputRows',0)
            snapshot['page'] = lookup.get('page')
        elif not snapshot.get('materialIdentity'):
            snapshot['rawRows'], snapshot['rawInputRows'] = [], 0
            snapshot.pop('page',None)
    counts = product_counts(result)
    if counts:
        snapshot['counts'] = counts
        snapshot['scope'] = list(dict.fromkeys(row['filename'] for row in
                                                (result.get('productLookup') or {}).get('rows',())
                                                if row.get('filename')))
        items = {}
        if counts['kind']=='catalog':
            for row in result['productLookup']['rows']:
                if not single_candidate(row):
                    continue
                candidate, = row['candidates']
                code = candidate['internalCode']
                item = items.setdefault(code,{key:candidate.get(key) for key in ('internalCode','name','brand','spec')})
                item.setdefault('sourceRows',[]).append({
                    'filename':row.get('filename'),'location':row.get('location'),
                    'upc':row.get('upc'),'status':row['status'],'confirmed':is_confirmed(row)})
        else:
            for candidate in result['productSearch']['candidates']:
                code = candidate['internalCode']
                items[code] = {key:candidate.get(key) for key in ('internalCode','name','brand','spec','sourceDate')}
                items[code]['sourceRows'] = []
        snapshot['items'] = list(items.values())
    if result.get('productSearch') is not None:
        lookup = result['productSearch']
        snapshot['lastQuery'] = {'kind':'name','query':lookup['query'],'status':lookup['status'],
                                 'candidateCount':lookup['totalCandidates']}
    elif result.get('productLookup') is not None:
        lookup = result['productLookup']
        snapshot['lastQuery'] = {'kind':'identifiers','status':lookup['status'],
                                 'sourceDate':lookup.get('sourceDate'),'queriedRows':len(lookup.get('rows',[])),
                                 'totalRows':lookup.get('inputRows',0),'page':lookup.get('page')}
    if result.get('salesQuery'):
        sales = result['salesQuery']
        snapshot['lastSales'] = {key:sales.get(key) for key in
                                 ('status','platformCode','window','source','requestedScope','queriedOn','warnings')}
        snapshot['lastSales']['rows'] = sales.get('rows',[])[:25]
    return snapshot if snapshot.get('counts') or snapshot.get('headers') or snapshot.get('rawRows') or snapshot.get('lastQuery') or snapshot.get('lastSales') else None


def select_products(snapshot, intent):
    items = []
    for item in snapshot.get('items',()):
        rows = item['sourceRows']
        if intent=='confirmed':
            rows = [row for row in rows if row['confirmed']]
            if not rows:
                continue
        items.append(item | {'sourceRows':rows})
    counts = snapshot['counts']
    return {'items':items, 'kind':counts['kind'], 'scope':snapshot.get('scope',[]),
            'sourceDate':counts.get('sourceDate'), 'inputRows':counts.get('inputRows'),
            'checkedRows':counts.get('checkedRows'), 'matchedRows':sum(len(item['sourceRows']) for item in items),
            'directoryCandidates':counts.get('candidateCount'), 'intent':intent}


def describe_selection(selection):
    items = selection['items']
    if not items:
        return ('这批商品已确认匹配为 0 条。' if selection['intent']=='confirmed'
                else '这批商品暂时没有查到唯一目录内部码。')
    if selection['kind']=='catalog':
        intro = f"筛出 {selection['matchedRows']} 行，按内部码去重后有 {len(items)} 个商品。"
        if selection['checkedRows'] < selection['inputRows']:
            intro += f"本次核对了 {selection['inputRows']} 行中的 {selection['checkedRows']} 行。"
        if selection['sourceDate']:
            intro += f"目录快照日期 {selection['sourceDate']}。"
    else:
        intro = f"当前列出 {len(items)} 个带内部码的商品名称候选，待核对。"
    lines = [intro,'']
    for item in items:
        rows = item['sourceRows']
        statuses = list(dict.fromkeys(
            STATUS_LABELS.get(row['status'],'待核对') for row in rows if not row['confirmed']))
        text = ' · '.join(str(value) for value in (item['name'],item['spec'],item['internalCode']) if value)
        if statuses:
            text += ' · 待确认（'+'、'.join(statuses)+'）'
        elif rows:
            text += ' · 已核对'
        lines.append('• '+text)
    return '\n'.join(lines)


def describe_column(headers, column, question):
    hits = [header for header in headers if column in header['cells']]
    named = [header for header in hits if header['sheet'] and header['sheet'].casefold() in question.casefold()]
    if named:
        hits = named
    if not hits:
        return f'当前对话中没有保存 {column} 列的可核对表头。'
    lines = []
    for header in hits:
        label = header['cells'][column]
        location = ' / '.join(filter(None,(header['filename'],header['sheet'])))
        lines.append(f"{location} 的 `{column}{header['row']}` 是「{label}」。")
    return '\n'.join(lines)


class ProductReceipts:
    def __init__(self):
        self.items=OrderedDict()
        self.guard=Lock()
        self.sizes={}
        self.total_bytes=0

    def remember(self, actor_id, snapshot):
        key=uuid4().hex
        size=len(json.dumps(snapshot,ensure_ascii=False).encode())
        with self.guard:
            now=monotonic()
            for old,entry in list(self.items.items()):
                if entry[1]<=now:
                    self.items.pop(old)
                    self.total_bytes-=self.sizes.pop(old)
            self.items[key]=(actor_id,now+PRODUCT_RECEIPT_TTL,snapshot)
            self.sizes[key]=size
            self.total_bytes+=size
            while len(self.items)>MAX_PRODUCT_RECEIPTS or self.total_bytes>MAX_RECEIPT_BYTES:
                old,_=self.items.popitem(last=False)
                self.total_bytes-=self.sizes.pop(old)
        return key

    def read(self, actor_id, key):
        with self.guard:
            entry=self.items.get(key)
            if entry and entry[1] <= monotonic():
                del self.items[key]
                self.total_bytes-=self.sizes.pop(key)
                return None
            return entry[2] if entry and entry[0]==actor_id else None
