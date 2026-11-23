"""Expose permission-scoped workbench GET routes to the existing chat Agent."""

import json
import re
from urllib.parse import urlencode

from chat_workbench_records import WorkbenchReadError, workbench_get

WORKBENCH_TOOL_NAME = 'read_workbench'
ID = r'[A-Za-z0-9_-]{1,128}'
DETAIL = re.compile(
    rf'/(?:submissions/{ID}|analysis-runs/{ID}(?:/(?:primary-recognition|model-recognition)/preview)?'
    rf'|materials/{ID}|activities/{ID}(?:/products|/versions/{ID}(?:/history)?)?'
    rf'|brands/{ID}(?:/(?:products|activities))?|products/{ID}/activities)'
)
LIST_ROUTES = {'/submissions', '/reviews', '/activities', '/brands', '/products'}
STATIC_ROUTES = LIST_ROUTES | {'/context', '/capabilities', '/catalogs'}
MAX_RESPONSE = 8 * 1024 * 1024
MAX_TOOL_CHARS = 48000
MAX_NESTED_ITEMS = 15
SENSITIVE_KEYS = {'password', 'passwordhash', 'passwordsalt', 'cookie', 'setcookie', 'authorization',
                  'csrftoken', 'accesstoken', 'refreshtoken', 'apikey', 'secret', 'servicetoken'}

WORKBENCH_READ_TOOL = {
    'type': 'function',
    'function': {
        'name': WORKBENCH_TOOL_NAME,
        'description': (
            '读取当前登录用户有权限的工作台记录，后台按现有账号权限校验。可自行搜索并取得记录 ID 后继续读取详情。'
            '/context 读取本人权限；/capabilities 读取已实现能力；/catalogs 读取活动类型、平台和品牌选项。'
            '/submissions 搜索本人提报（q 名称或编号，brandQ 品牌，state 状态）；/reviews 查询审核列表'
            '（view=pending、handled、mine）。/activities 查询活动（q、type、platformCode、brandId）；'
            '/products 查询工作台商品（q、brandId、platformCode、hasActivities）；/brands 查询品牌（q、hasActivities）。'
            '列表均支持 page、pageSize，默认每页 10 条。详情路径支持 /submissions/{id}、/activities/{id}、'
            '/activities/{id}/products、/activities/{id}/versions/{versionId}、/analysis-runs/{id}、'
            '/brands/{id}、/brands/{id}/activities、/brands/{id}/products、/products/{id}/activities、/materials/{id}。'
            '商品公司主档匹配用 search_product_names 或 lookup_product_codes。提报详情含材料编号与原始名称，'
            '原件解析使用已有提报读取能力。完整性与截断以回执为准。'
        ),
        'parameters': {
            'type': 'object',
            'properties': {
                'path': {'type': 'string', 'description': '相对工作台业务路径，例如 /submissions。'},
                'query': {
                    'type': 'object',
                    'description': '查询参数。例如 {"q":"薇诺娜","page":"1","pageSize":"10"}。',
                    'additionalProperties': {'type': 'string'},
                },
            },
            'required': ['path', 'query'],
            'additionalProperties': False,
        },
    },
}


def _valid_request(path, query):
    if not isinstance(path, str) or not (path in STATIC_ROUTES or DETAIL.fullmatch(path)):
        raise WorkbenchReadError(422, 'WORKBENCH_PATH_INVALID', '请使用工具说明中已有的工作台业务读取路径。')
    if (not isinstance(query, dict) or len(query) > 20
            or any(not isinstance(key, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,39}', key)
                   or not isinstance(value, str) or len(value) > 500 for key, value in query.items())):
        raise WorkbenchReadError(422, 'WORKBENCH_QUERY_INVALID', '工作台查询参数须为字符串，单项不超过 500 字。')
    values = dict(query)
    if path in LIST_ROUTES or path.endswith(('/products', '/activities', '/history')):
        values.setdefault('pageSize', '10')
    for key in ('page', 'pageSize'):
        if key in values and (not re.fullmatch(r'[1-9][0-9]*', values[key])
                              or key == 'pageSize' and int(values[key]) > 100):
            raise WorkbenchReadError(422, 'WORKBENCH_PAGE_INVALID', '页码须为正整数，每页最多 100 条。')
    return path + ('?' + urlencode(values) if values else ''), values


def _safe_view(value, path, truncated, *, paged=False):
    if isinstance(value, dict):
        return {
            key: _safe_view(item, path + '.' + key, truncated, paged=paged and key == 'items')
            for key, item in value.items()
            if re.sub(r'[-_]', '', key).casefold() not in SENSITIVE_KEYS
        }
    if isinstance(value, list):
        limit = len(value) if paged else MAX_NESTED_ITEMS
        if len(value) > limit:
            truncated.append({'path': path, 'returned': limit, 'total': len(value)})
        return [_safe_view(item, f'{path}[{index}]', truncated) for index, item in enumerate(value[:limit])]
    if isinstance(value, str) and len(value) > 2000:
        truncated.append({'path': path, 'returnedCharacters': 2000, 'totalCharacters': len(value)})
        return value[:2000]
    return value


def read_workbench(path, query, cookie, *, port=52670, get=workbench_get):
    """Return a bounded source-labelled view. The cookie is never in returned data."""
    request_path, parameters = _valid_request(path, query)
    if not isinstance(cookie, str) or not cookie or len(cookie) > 4096:
        raise WorkbenchReadError(401, 'LOGIN_REQUIRED', '请先登录价探工作台。')
    raw = get(port, cookie, request_path, MAX_RESPONSE)
    if raw is None:
        return {'status': 'unavailable', 'code': 'WORKBENCH_RECORD_UNAVAILABLE',
                'message': '记录不存在，或当前账号无权读取。', 'path': path}
    try:
        payload = json.loads(raw)
        data = payload['data']
        if not isinstance(data, (dict, list)):
            raise ValueError
    except (ValueError, KeyError, TypeError):
        raise WorkbenchReadError(502, 'WORKBENCH_RECORD_INVALID', '工作台返回了无法核对的数据格式。') from None
    truncated = []
    paged = isinstance(data, dict) and isinstance(data.get('items'), list) and 'page' in data
    result = {'status': 'queried', 'source': 'workbench', 'path': path, 'query': parameters,
              'data': _safe_view(data, 'data', truncated, paged=paged),
              'meta': _safe_view(payload.get('meta'), 'meta', truncated), 'truncated': truncated}
    if paged:
        total, page, page_size = data.get('total'), data.get('page'), data.get('pageSize')
        if all(type(value) is int for value in (total, page, page_size)):
            result['hasMore'] = page * page_size < total
        else:
            result['hasMore'] = None
    if len(json.dumps(result, ensure_ascii=False)) > MAX_TOOL_CHARS:
        return {'status': 'response_too_large', 'source': 'workbench', 'path': path,
                'query': parameters, 'message': '这次记录内容较多，请缩小搜索范围、减少每页条数或读取具体记录。'}
    return result
