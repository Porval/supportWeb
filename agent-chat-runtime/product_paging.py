"""Bounded pages over the complete locally parsed product rows."""

import re

MAX_PAGE_ROWS = 100
PAGE_PROPERTIES = {
    'offset': {'type':'integer','minimum':0,'description':'从第几个商品之后开始，0 表示第 1 条；与 Excel 原行号不同。'},
    'limit': {'type':'integer','minimum':1,'maximum':MAX_PAGE_ROWS,'description':'本页商品数，最多 100 条。'},
}


def validate_page(offset=0, limit=MAX_PAGE_ROWS):
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= MAX_PAGE_ROWS:
        raise ValueError('Invalid product page')
    return offset, limit


def page_info(total, offset=0, limit=MAX_PAGE_ROWS):
    validate_page(offset,limit)
    returned = max(0,min(limit,total-offset))
    return {'offset':offset,'limit':limit,'totalRows':total,'returnedRows':returned,
            'start':offset+1 if returned else None,'end':offset+returned if returned else None,
            'hasMore':offset+returned<total,
            'nextOffset':offset+returned if offset+returned<total else None,
            'previousOffset':max(0,offset-limit) if offset else None}


def page_intent(question, previous=None):
    """Only explicit navigation/ranges, never coupon amounts or general activity prose."""
    previous = previous or {}
    question = re.sub(r'\s+','',question).lower()
    if re.fullmatch(r'(?:请|再|看|看下|查看|继续看|继续|翻到|给我)?(?:下|后)一(?:页|批)(?:商品|商品信息|商品明细)?(?:吧|呀|呢)?',question):
        offset = previous.get('nextOffset')
        if offset is None:
            offset = previous.get('totalRows',0) if previous else 0
        return offset, previous.get('limit',MAX_PAGE_ROWS)
    if re.fullmatch(r'(?:请|看|看下|查看|翻到|给我)?上一(?:页|批)(?:商品|商品信息|商品明细)?(?:吧|呀|呢)?',question):
        return previous.get('previousOffset') or 0, previous.get('limit',MAX_PAGE_ROWS)
    if re.fullmatch(r'(?:请|看|查看|回到|给我)?(?:第一页|首页)(?:商品|商品信息|商品明细)?',question):
        return 0, previous.get('limit',MAX_PAGE_ROWS)
    if '行' in question and any(word in question for word in ('原表','excel','工作表')):
        return None
    match = re.search(r'(?:第)?([0-9]+)[-–—~～至到]([0-9]+)(?:条|个|行)?(?:的)?商品',question)
    if not match:
        match = re.fullmatch(r'(?:请|看|看下|查看|列出|给我)?第([0-9]+)[-–—~～至到]([0-9]+)(?:条|个)(?:吧|呀|呢)?',question)
    if not match:
        return None
    start,end = map(int,match.groups())
    validate_page(start-1,end-start+1)
    return start-1,end-start+1
