"""Read-only attachment context for the local Activity Agent chat."""

from base64 import b64decode
from hashlib import sha256
import json
from pathlib import Path
import re
import subprocess
import sys

from chat_product_lookup import HEADER_ALIAS, header_value, product_rows

MAX_FILES = 3
MAX_SHEET_HEADERS = 40
MAX_BYTES = 50 * 1024 * 1024
MAX_CONTEXT_CHARS = 24000
MAX_BASE64_BYTES = ((MAX_BYTES + 2) // 3) * 4
EXTENSIONS = ('.xlsx', '.docx', '.csv', '.tsv', '.txt', '.md')
WORKER_CONTRACT = 'activity-intake/1.0'
STORE_SECTION = re.compile(
    r'门店(?:清单|列表|范围|明细|名单|资料|信息|表|地址|名称|编码|编号|ID)|'
    r'店铺(?:清单|列表|名单|地址|名称|ID)|(?:适用|参与|参加|报名|指定)门店|'
    r'^(?:门店|店铺|仓店|stores?|poi[_ ]?(?:id|name))$', re.IGNORECASE)

STORE_FIELD = re.compile(
    r'(?:门店|店铺|仓店)(?:ID|名称|地址|编码|编号)|poi[_ ]?(?:id|name)', re.IGNORECASE)


class AttachmentError(Exception):
    def __init__(self, status, code, message):
        self.status, self.code, self.message = status, code, message
        super().__init__(code)


def attachment_options(worker):
    return {'enabled':worker is not None and worker.is_file(), 'extensions':list(EXTENSIONS),
            'maxFiles':MAX_FILES, 'maxTotalBytes':MAX_BYTES}


def read_uploads(value):
    if not isinstance(value, list) or len(value) > MAX_FILES:
        raise AttachmentError(400, 'ATTACHMENT_COUNT', '每次最多添加 3 个文件。')
    total = 0
    uploads = []
    for item in value:
        if not isinstance(item, dict) or set(item) != {'filename', 'contentBase64'}:
            raise AttachmentError(400, 'ATTACHMENT_INVALID', '附件数据格式不正确。')
        filename, encoded = item['filename'], item['contentBase64']
        if (not isinstance(filename, str) or not 1 <= len(filename) <= 180
                or not filename.isprintable() or '/' in filename or '\\' in filename
                or not isinstance(encoded, str)):
            raise AttachmentError(400, 'ATTACHMENT_INVALID', '附件名称或内容格式不正确。')
        if Path(filename).suffix.casefold() not in EXTENSIONS:
            raise AttachmentError(415, 'ATTACHMENT_UNSUPPORTED', f'{filename} 的格式暂不支持。')
        if len(encoded) > MAX_BASE64_BYTES:
            raise AttachmentError(413, 'ATTACHMENT_TOO_LARGE', '附件总大小上限为 50 MB。')
        try:
            data = b64decode(encoded, validate=True)
        except ValueError:
            raise AttachmentError(400, 'ATTACHMENT_INVALID', f'{filename} 的内容格式不正确。') from None
        if not data:
            raise AttachmentError(422, 'ATTACHMENT_EMPTY', f'{filename} 是空文件。')
        total += len(data)
        if total > MAX_BYTES:
            raise AttachmentError(413, 'ATTACHMENT_TOO_LARGE', '附件总大小上限为 50 MB。')
        uploads.append((filename, encoded, sha256(data).hexdigest()))
    return uploads


def parse_document(worker, upload, request_id):
    filename, encoded, digest = upload
    request = {'contractVersion':WORKER_CONTRACT, 'requestId':request_id, 'operation':'decode',
               'payload':{'materialId':request_id, 'filename':filename, 'sha256':digest,
                          'mediaType':'application/octet-stream', 'contentBase64':encoded}}
    try:
        result = subprocess.run([sys.executable, '-B', str(Path(__file__).with_name('chat_decode_worker.py')), str(worker)], input=json.dumps(request).encode(),
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=30, check=True)
        reply = json.loads(result.stdout)
    except subprocess.TimeoutExpired:
        return None, '文件读取超时'
    except (OSError, subprocess.CalledProcessError, ValueError):
        return None, '文件读取失败'
    if reply.get('requestId') != request_id:
        return None, '文件读取失败'
    document = reply.get('result', {})
    if reply.get('ok') and document.get('sha256') == digest and document.get('status') in ('parsed', 'partial'):
        return document, None
    errors = document.get('errors', []) + [reply.get('error', {})]
    if any(error.get('code') == 'LIMIT_EXCEEDED' for error in errors):
        return None, '文件内容超出读取上限'
    return None, '文件格式异常或内容无法读取'


def row_section(locator):
    return locator.get('sheet'), locator.get('table')


def filter_document(document):
    blocks = document['textBlocks']
    rows = document['tableRows']
    excluded_blocks = any(STORE_SECTION.match(block['text'].strip().lstrip('# ')) for block in blocks)
    store_sections = {row_section(row['locator']) for row in rows
                      if STORE_SECTION.search(row['locator'].get('sheet', ''))
                      or any(STORE_FIELD.fullmatch(cell['text'].strip()) for cell in row['cells'])}
    filtered = {**document,
                'textBlocks':[] if excluded_blocks else blocks,
                'tableRows':[row for row in rows if row_section(row['locator']) not in store_sections]}
    return filtered, bool(store_sections or excluded_blocks)


def material_excerpts(document):
    blocks = document['textBlocks']
    rows = document['tableRows']
    excerpts = []
    if blocks:
        for block in blocks:
            locator = block['locator']
            place = f"第 {locator['line']} 行" if 'line' in locator else f"第 {locator['paragraph']} 段"
            if block['text'].strip():
                excerpts.append({'location':place, 'text':block['text']})
    for row in rows:
        locator = row['locator']
        if 'sheet' in locator:
            place = f"{locator['sheet']} · 第 {locator['row']} 行"
        elif 'table' in locator:
            place = f"表 {locator['table']} · 第 {locator['row']} 行"
        else:
            place = f"第 {locator['row']} 行"
        cells = [f"{cell['column']}={cell['text']}" for cell in row['cells']
                 if cell['sourceType'] != 'formula' and cell['text'].strip()]
        if cells:
            excerpts.append({'location':place, 'text':' | '.join(cells)})
    return excerpts, list(product_rows(rows))


def read_sheet_headers(filename, document):
    if document['kind'] != 'xlsx':
        return []
    headers = []
    seen = set()
    for row in document['tableRows']:
        cells = {cell['column']:cell['text'].strip()[:140] for cell in row['cells']
                 if cell['sourceType'] == 'string' and cell['text'].strip()}
        roles = {HEADER_ALIAS.get(header_value(value)) for value in cells.values()}
        if 'name' not in roles and not {'upc','internalCode'} & roles:
            continue
        locator = row['locator']
        layout = locator.get('sheet'), tuple(cells.items())
        if layout in seen:
            continue
        seen.add(layout)
        headers.append({'filename':filename,'sheet':locator.get('sheet'),
                        'row':locator['row'],'cells':cells})
        if len(headers) >= MAX_SHEET_HEADERS:
            break
    return headers


ATTACHMENT_RECEIPT_QUESTION = re.compile(
    r'(?:请问)?(?:你|刚才|这个|这份|我的|我)?(?:附件|文件|材料|表格|表)?'
    r'(?:已经|有|是否|是不是)?(?:上传|收到|读到|读取|看见|看到|解析)'
    r'(?:附件|文件|材料|表格|表)?(?:成功)?(?:了)?(?:了没|了吗|吗|嘛|么|没|没有|呢|吧)?')


def asks_attachment_receipt(text):
    return ATTACHMENT_RECEIPT_QUESTION.fullmatch(text) is not None


def describe_attachment_receipt(results):
    if not results:
        return '本轮没有收到附件。添加文件后点击发送，我会返回读取状态。'
    lines = []
    for item in results:
        name, status = item['filename'], item['status']
        if status in ('read','partial'):
            action = '已读取' if status == 'read' else '部分读取'
            line = f'{name} · {action} {item["excerptCount"]} 行或段'
            if count := item.get('productUpcCount'):
                line += f' · 定位到 {count} 条带条码的商品行'
            if item['message']:
                line += ' · ' + item['message']
        else:
            line = f'{name} · {item["message"]}'
        lines.append(line)
    return '\n'.join(lines)


def prepare_attachments(worker, proposed, request_id, *, include_documents=False):
    uploads = read_uploads(proposed)
    if not uploads:
        return [], []
    if not attachment_options(worker)['enabled']:
        raise AttachmentError(503, 'ATTACHMENT_UNAVAILABLE', '活动 Agent 的附件读取暂不可用。')
    context = []
    results = []
    budget = MAX_CONTEXT_CHARS // len(uploads)
    for index, upload in enumerate(uploads, 1):
        filename = upload[0]
        if STORE_SECTION.search(Path(filename).stem.strip()):
            results.append({'filename':filename, 'status':'skipped', 'message':'已跳过门店清单'})
            continue
        document, error = parse_document(worker, upload, f'{request_id}-{index}')
        if error:
            results.append({'filename':filename, 'status':'unreadable', 'message':error})
            continue
        document, excluded = filter_document(document)
        excluded = excluded or any(warning['code'] == 'STORE_SHEET_EXCLUDED'
                                   for warning in document['warnings'])
        excerpts, products = material_excerpts(document)
        selected = []
        left = budget
        for excerpt in excerpts:
            size = len(json.dumps(excerpt, ensure_ascii=False)) + 1
            if size <= left:
                selected.append(excerpt)
                left -= size
        limited = len(selected) < len(excerpts) or document['status'] == 'partial'
        formulas = any(warning['code'] == 'FORMULA_NOT_EVALUATED' for warning in document['warnings'])
        notes = []
        if excluded:
            notes.append('已跳过识别出的门店内容')
        if document['status'] == 'partial':
            notes.append('原文件解析仅覆盖部分行，未读取部分仍需补充')
        elif len(selected) < len(excerpts):
            notes.append('正文仅展示摘要，已解析商品可继续分页读取')
        if formulas:
            notes.append('公式单元格已跳过')
        if not selected:
            notes.append('没有可供分析的文本或表格内容')
        status = 'skipped' if not selected else 'partial' if limited or formulas else 'read'
        results.append({'filename':filename, 'status':status, 'excerptCount':len(selected),
                        'productUpcCount':sum(bool(item['upc']) for item in products),
                        'message':'，'.join(notes)})
        if selected:
            content = {'filename':filename,'sha256':upload[2],'sourcePartial':document['status']=='partial',
                       'coverage':status,'excerpts':selected,'productRows':products,
                       'sheetHeaders':read_sheet_headers(filename,document)}
            if include_documents:
                content['document'] = document
            context.append(content)
    return context, results
