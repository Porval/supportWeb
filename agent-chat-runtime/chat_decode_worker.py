"""Decode chat uploads with local budgets using the installed material parser.

Usage: python -B chat_decode_worker.py /absolute/path/to/frozen/worker.py
One activity-intake/1.0 decode request on stdin; one response on stdout.
The frozen parser and formal intake worker are never modified on disk.
"""

import base64
import io
import importlib.util
import json
from pathlib import Path
import posixpath
import re
import sys


CONTRACT = 'activity-intake/1.0'
MAX_INPUT = 70 * 1024 * 1024
MAX_OUTPUT = 16 * 1024 * 1024
CHAT_LIMITS = {
    'maxBytes': 50 * 1024 * 1024,
    'maxExpandedBytes': 128 * 1024 * 1024,
    'maxEntryBytes': 64 * 1024 * 1024,
    'maxTextChars': 2_000_000,
    'maxRows': 10_000,
}

# Same explicit store-sheet names as chat_attachments.STORE_SECTION. The
# additional merchant-list name is accepted only with a store-identity header.
STORE_SECTION = re.compile(
    r'门店(?:清单|列表|范围|明细|名单|资料|信息|表|地址|名称|编码|编号|ID)|'
    r'店铺(?:清单|列表|名单|地址|名称|ID)|(?:适用|参与|参加|报名|指定)门店|'
    r'^(?:门店|店铺|仓店|stores?|poi[_ ]?(?:id|name))$', re.IGNORECASE)
MERCHANT_LIST = re.compile(r'^(?:活动)?(?:覆盖|适用|参与|参加|报名|指定)?商家(?:清单|名单|列表)$')
STORE_IDENTIFIER = re.compile(r'(?:门店|店铺|仓店)(?:ID|编码|编号)$|^poi[_ ]?id$', re.IGNORECASE)
STORE_NAMES = {'门店名称', '店铺名称', '仓店名称', 'poi_name', 'poi name'}


def store_identity_header(parser, body, shared):
    """Inspect only early headers of an already security-checked worksheet."""
    for _event, row in parser.ET.iterparse(io.BytesIO(body), events=('end',)):
        if parser.tag(row) != 'row':
            continue
        if int(row.attrib.get('r', '0')) > 10:
            return False
        values = []
        for cell in row:
            if parser.tag(cell) != 'c' or any(parser.tag(x) == 'f' for x in cell):
                continue
            kind = cell.attrib.get('t', 'n')
            value = next((x.text or '' for x in cell if parser.tag(x) == 'v'), '')
            if kind == 's':
                value = shared[int(value)]
            elif kind == 'inlineStr':
                value = ''.join(x.text or '' for x in cell.iter() if parser.tag(x) == 't')
            elif kind != 'str':
                continue
            values.append(value.strip())
        if (any(STORE_IDENTIFIER.search(value) for value in values)
                and any(value.casefold() in STORE_NAMES for value in values)):
            return True
        row.clear()
    return False


def exclude_store_sheets(parser, parts, excluded):
    """Filter the parse view, after original archive/relationship validation.

    Original bytes, SHA and retained sheet/cell references stay untouched. We
    neither skip validation of excluded XML nor relabel a truncated parse.
    """
    if 'xl/workbook.xml' not in parts or 'xl/_rels/workbook.xml.rels' not in parts:
        return parts
    workbook = parser.xml(parts['xl/workbook.xml'])
    rels = {x.attrib['Id']: x.attrib['Target']
            for x in parser.xml(parts['xl/_rels/workbook.xml.rels'])}
    shared = None
    removed = []
    for parent in workbook.iter():
        for sheet in list(parent):
            if parser.tag(sheet) != 'sheet':
                continue
            rid = next(v for k, v in sheet.attrib.items() if k.endswith('}id'))
            target = rels[rid]
            path = target.lstrip('/') if target.startswith('/') else posixpath.normpath('xl/' + target)
            parser.require(path.startswith('xl/worksheets/') and path in parts, 'INVALID_OFFICE')
            name = sheet.attrib['name']
            is_store = bool(STORE_SECTION.search(name))
            if not is_store and MERCHANT_LIST.fullmatch(name.strip()):
                if shared is None:
                    shared = ([] if 'xl/sharedStrings.xml' not in parts else
                              [''.join(x.text or '' for x in entry.iter() if parser.tag(x) == 't')
                               for entry in parser.xml(parts['xl/sharedStrings.xml'])])
                is_store = store_identity_header(parser, parts[path], shared)
            if is_store:
                parent.remove(sheet)
                removed.append(name)
    if not removed:
        return parts
    filtered = dict(parts)
    filtered['xl/workbook.xml'] = parser.ET.tostring(workbook, encoding='utf-8')
    excluded.extend(removed)
    return filtered


class DecodeError(ValueError):
    def __init__(self, code, message):
        self.code, self.message = code, message
        super().__init__(code)


def require(condition, code='INVALID_INPUT', message='Invalid decode request'):
    if not condition:
        raise DecodeError(code, message)


def pairs(values):
    result = {}
    for key, value in values:
        require(key not in result, 'INVALID_JSON', 'Duplicate JSON key')
        result[key] = value
    return result


def reject_constant(_value):
    raise DecodeError('INVALID_JSON', 'Invalid JSON constant')


def load_parser(worker_path):
    worker = Path(worker_path)
    require(worker.is_absolute() and worker.name == 'worker.py' and worker.is_file(),
            'PARSER_UNAVAILABLE', 'Material parser is unavailable')
    source = worker.resolve().with_name('api.py')
    require(source.is_file(), 'PARSER_UNAVAILABLE', 'Material parser is unavailable')
    # These sibling imports come from the trusted installed release, never uploads.
    sys.path.insert(0, str(source.parent))
    spec = importlib.util.spec_from_file_location('chat_frozen_material_parser', source)
    parser = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(parser)
    require(parser.CONTRACT == CONTRACT, 'PARSER_UNAVAILABLE', 'Unsupported parser contract')
    parser.DEFAULT_LIMITS = {**parser.DEFAULT_LIMITS, **CHAT_LIMITS}
    original_archive = parser.office_archive
    parser.chat_excluded_sheets = []

    def archive_view(data, limits, **options):
        # 1.0.8 passes xlsx=True for its existing blank-format compaction;
        # older installed parsers use only the two positional arguments.
        parts = original_archive(data, limits, **options)
        return exclude_store_sheets(parser, parts, parser.chat_excluded_sheets)

    parser.office_archive = archive_view
    return parser


def failure(request_id, code, message):
    return {'contractVersion': CONTRACT, 'requestId': request_id, 'ok': False,
            'error': {'code': code, 'message': message, 'details': {}}}


def main():
    request_id = None
    parser = None
    try:
        require(len(sys.argv) == 2, 'PARSER_UNAVAILABLE', 'Material parser is unavailable')
        raw = sys.stdin.buffer.read(MAX_INPUT + 1)
        require(len(raw) <= MAX_INPUT, 'LIMIT_EXCEEDED', 'Decode input exceeds 70 MiB')
        request = json.loads(raw.decode('utf-8'), object_pairs_hook=pairs,
                             parse_constant=reject_constant)
        require(isinstance(request, dict) and
                set(request) == {'contractVersion', 'requestId', 'operation', 'payload'})
        require(request['contractVersion'] == CONTRACT and
                isinstance(request['requestId'], str) and 0 < len(request['requestId']) <= 200)
        request_id = request['requestId']
        require(request['operation'] == 'decode', 'INVALID_OPERATION',
                'Chat material worker supports decode only')
        payload = request['payload']
        require(isinstance(payload, dict))
        payload = dict(payload)
        limits = payload.pop('limits', None)
        require(set(payload) == {'materialId', 'filename', 'mediaType', 'sha256', 'contentBase64'})
        encoded = payload.pop('contentBase64')
        require(isinstance(encoded, str))
        require(len(encoded) <= ((CHAT_LIMITS['maxBytes'] + 2) // 3) * 4,
                'LIMIT_EXCEEDED', 'Material exceeds 50 MiB')
        data = base64.b64decode(encoded, validate=True)
        require(len(data) <= CHAT_LIMITS['maxBytes'], 'LIMIT_EXCEEDED', 'Material exceeds 50 MiB')
        parser = load_parser(sys.argv[1])
        parser.limits_for(limits)  # A request may only lower this chat process's limits.
        result = parser.decode_material({**payload, 'contentBytes': data}, limits)
        for sheet in parser.chat_excluded_sheets:
            result['warnings'].append(parser.issue(
                'STORE_SHEET_EXCLUDED', '已在本地排除门店清单工作表；本次仅解析其余材料。',
                {'kind': 'xlsx', 'sheet': sheet}))
        response = {'contractVersion': CONTRACT, 'requestId': request_id, 'ok': True,
                    'result': result}
    except DecodeError as error:
        response = failure(request_id, error.code, error.message)
    except Exception as error:
        if parser is not None and isinstance(error, parser.IntakeError):
            response = failure(request_id, error.code, error.message)
        else:
            # No source paths, material text or stack traces enter the wire response.
            response = failure(request_id, 'INVALID_INPUT', 'Invalid request or material structure')
    encoded = json.dumps(response, ensure_ascii=False, separators=(',', ':'),
                         allow_nan=False).encode('utf-8')
    if len(encoded) > MAX_OUTPUT:
        encoded = json.dumps(failure(request_id, 'LIMIT_EXCEEDED',
                                    'Decode output exceeds 16 MiB'),
                             separators=(',', ':')).encode('utf-8')
    sys.stdout.buffer.write(encoded + b'\n')


if __name__ == '__main__':
    main()
