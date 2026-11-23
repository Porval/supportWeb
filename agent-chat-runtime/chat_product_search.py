"""Search the Data Agent's product snapshots for unconfirmed name candidates."""

from datetime import date
from decimal import Decimal
from difflib import SequenceMatcher
from functools import lru_cache
import json
import re
import unicodedata

MAX_QUERY = 120
MAX_CANDIDATES = 12
MAX_MASTER_BYTES = 32 * 1024 * 1024
QUERY_PREFIX = re.compile(
    r'^(?:(?:请问|麻烦|帮我|给我|我想|我要|我需要|你帮我|能不能|可以|请|直接|从商品主档|让数据agent)\s*)*'
    r'(?:查找|查看|查询|查|搜索|搜|找|看看|看)(?:一下|一查|下)?\s*')
PACK = re.compile(r'(?:×|\*|x)\s*(\d{1,4})(?![0-9a-z])')
PRODUCT_WORDS = re.compile(r'商品|产品|货号|内部码|牙膏|漱口水|牙刷|面膜|面霜|乳液|精华|洗发|护发|洁面|眉笔|棉巾|湿巾|纸巾|香水|防晒')
OTHER_QUERY = re.compile(r'活动|案例|销量|营业额|库存|供货价|采购价|历史|平台|报名|销售额|券档|补贴|规则|到期|接口|权限')
FOLLOWUP = re.compile(r'^(?:好|那|你|请|帮我)*(?:(?:让|叫)(?:他|它|她|数据agent))?(?:先|直接|去)?(?:查|查找|搜|找)(?:一下|下|一查|看看)?(?:吧|呀|呢)?$')
SPEC = re.compile(r'(?<![\d.])(\d{1,6}(?:\.\d{1,4})?)\s*(毫升|千克|公斤|毫克|克|升|kg|mg|ml|g|l|片|支|瓶|盒|包|件)(?![a-z])')
UNIT = {'千克':('g',1000), '公斤':('g',1000), 'kg':('g',1000),'克':('g',1),
        '毫克':('g',Decimal('0.001')), 'mg':('g',Decimal('0.001')), 'g':('g',1),
        '毫升':('ml',1), 'ml':('ml',1), '升':('ml',1000), 'l':('ml',1000)}
NORMAL_TEXT = re.compile(r'[^a-z0-9\u3400-\u9fff]+')
BRAND_PART = re.compile(r'[\u3400-\u9fff]+|[a-z][a-z0-9]+')
LABELS = {'exact':'名称包含查询词', 'similar':'名称相近'}


def fold(value):
    return unicodedata.normalize('NFKC',value).casefold()


def normalized_name(value):
    return NORMAL_TEXT.sub('',fold(value))


@lru_cache(maxsize=16384)
def quantities(value):
    checked = set()
    text = fold(value)
    checked.update((Decimal(amount),'pack') for amount in PACK.findall(text))
    for amount,unit in SPEC.findall(text):
        kind,factor = UNIT.get(unit,(unit,1))
        checked.add((Decimal(amount)*factor,kind))
    return frozenset(checked)


@lru_cache(maxsize=4096)
def brand_aliases(brand):
    value = fold(brand)
    return frozenset(part for part in (normalized_name(value), *BRAND_PART.findall(value))
                     if len(part)>=2 and part not in ('未知', '其他', '通用', '自有', 'unknown'))


def search_phrase(text):
    query = QUERY_PREFIX.sub('',fold(text),count=1).strip(' ，,。？?')
    if query == fold(text).strip(' ，,。？?') or OTHER_QUERY.search(query):
        return None
    if not PRODUCT_WORDS.search(query) and not quantities(query):
        return None
    query = re.sub(r'^(?:公司|商品主档(?:里的)?)', '', query)
    query = re.sub(r'^商品名称[：:]|(?:对应的)?(?:商品信息|列表|内部码|货号|有哪些)$','',query).strip()
    return query if 2 <= len(normalized_name(query)) and len(query)<=MAX_QUERY else None


def contextual_name_query(messages):
    current = messages[-1]['content']
    if phrase := search_phrase(current):
        return phrase
    if not FOLLOWUP.fullmatch(normalized_name(current)):
        return None
    for message in reversed(messages[::2][:-1]):
        text = message['content']
        flat = normalized_name(text)
        if FOLLOWUP.fullmatch(flat) or '数据agent' in flat and ('吗' in flat or '可以' in flat):
            continue
        return search_phrase(text)
    return None


@lru_cache(maxsize=4)
def read_master(path, inode, modified_ns, size):
    with path.open('rb') as stream:
        raw = stream.read(MAX_MASTER_BYTES+1)
    if len(raw)>MAX_MASTER_BYTES:
        raise ValueError('Product master is too large')
    data = json.loads(raw)
    as_of = data['as_of']
    if date.fromisoformat(as_of).isoformat()!=as_of or data['kind']!='read_only_export_snapshot':
        raise ValueError('Invalid product master version')
    records = {}
    for item in data['items']:
        code,name = item.get('item_code'),item.get('item_name')
        if not isinstance(code,str) or not code or not isinstance(name,str) or not name:
            continue
        if code in records:
            raise ValueError('Duplicate product master identity')
        records[code] = {'internalCode':code,'name':name,'brand':str(item.get('brand') or ''),
                          'spec':str(item.get('规格') or ''),'sourceDate':as_of,'source':'product_master'}
    return records


def catalog_records(master, cache):
    from business_agent.data_agent_catalog import catalog

    products = {}
    scopes = []
    if master:
        try:
            info = master.stat()
            products.update(read_master(master,info.st_ino,info.st_mtime_ns,info.st_size))
            if products:
                scopes.append('product_master')
        except (OSError,ValueError,KeyError,AttributeError,TypeError):
            pass
    if cache:
        try:
            snapshot = catalog.read_cache(cache)
            as_of = snapshot['export']['catalog']['saved_on']
            for code,item in snapshot['export']['attributes'].items():
                if code not in products or as_of >= products[code]['sourceDate']:
                    products[code] = {'internalCode':code,'name':item['item_name'],'brand':item['brand'],
                                      'spec':item['spec'],'sourceDate':as_of,'source':'upc_catalog'}
            scopes.append('upc_catalog')
        except (OSError,ValueError,KeyError,AttributeError,TypeError):
            pass
    return products,scopes


def name_score(needle, name):
    if needle in name:
        return 1.0
    if len(needle)<4:
        return 0.0
    anchors = {needle[i:i+2] for i in range(len(needle)-1)}
    if not any(anchor in name for anchor in anchors):
        return 0.0
    width = len(needle)
    return max(SequenceMatcher(None,needle,name[start:start+width],autojunk=False).ratio()
               for start in range(max(1,len(name)-width+1)))


def search_product_names(master,cache,query):
    catalog,scopes = catalog_records(master,cache)
    answer = {'query':query,'status':'available' if scopes else 'unavailable',
              'sources':scopes,'totalCandidates':0,'candidates':[]}
    if not scopes:
        return answer
    compact = normalized_name(query)
    brands = {alias for item in catalog.values() for alias in brand_aliases(item['brand']) if alias in compact}
    brand = max(brands,key=lambda alias:(len(alias),alias),default='')
    expected_specs = quantities(query)
    name = fold(query)
    name = PACK.sub('',SPEC.sub('',name))
    needle = normalized_name(name).replace(brand,'',1) if brand else normalized_name(name)
    needle = re.sub(r'^的|的$','',needle)
    if not needle and not brand:
        return answer
    ranked = []
    for product in catalog.values():
        if brand and brand not in brand_aliases(product['brand']):
            continue
        stored_specs = quantities(product['name']+' '+product['spec'])
        if not expected_specs.issubset(stored_specs):
            continue
        name_key = normalized_name(SPEC.sub('',fold(product['name'])))
        score = name_score(needle,name_key) if needle else 1.0
        if score < .76:
            continue
        basis = [LABELS['exact' if score==1 else 'similar']] if needle else ['品牌匹配']
        if brand and needle:
            basis.append('品牌匹配')
        if expected_specs:
            basis.append('规格匹配')
        ranked.append((score,product | {'matchBasis':basis,'status':'name_candidate'}))
    ranked.sort(key=lambda pair:(-pair[0],pair[1]['name'],pair[1]['internalCode']))
    return answer | {'totalCandidates':len(ranked),'candidates':[item for _,item in ranked[:MAX_CANDIDATES]]}


def describe_product_search(result):
    if result['status'] == 'unavailable':
        return '暂时读不到数据 Agent 同步的商品目录。'
    query,total = result['query'],result['totalCandidates']
    if not total:
        return f'在已同步的商品目录里，暂未找到「{query}」的候选。'
    lines = [f'「{query}」找到 {total} 个商品候选。']
    for row in result['candidates']:
        fields = (row['name'],f"内部码 {row['internalCode']}",row['spec'],
                  '、'.join(row['matchBasis']), f"目录日期 {row['sourceDate']}")
        lines.append('• '+' · '.join(value for value in fields if value))
    if total>len(result['candidates']):
        lines.append(f'已显示前 {len(result["candidates"])} 个，可用规格或关键词缩小范围。')
    lines.append('名称匹配结果待确认。')
    return '\n'.join(lines)
