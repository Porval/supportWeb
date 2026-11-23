"""Independent Activity Agent, using a private local socket and case memory in MySQL."""

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import select
import socket
import socketserver
import struct
import subprocess
import sys
import time
from urllib.request import Request

from business_relations import business_policy
from memory import CaseMemory, ContextUnavailable

CONTRACT = 'activity-intake/1.0'
LIMIT = 32 * 1024 * 1024
RESPONSE_LIMIT = 32 * 1024 * 1024
MAX_RUN_SECONDS = 260
LARGE_RUN_SECONDS = 2400


def large_payload(payload):
    """Recognize the bounded large-table layout without calling a model."""
    try:
        from business_agent.activity_model_first.large_shenjia import layouts
    except ImportError:
        # The chat files and release package can be updated independently.
        return False
    documents = payload.get('documents', [])
    return sum(len(doc.get('tableRows', [])) for doc in documents) > 1000 and bool(layouts(documents))


def run_budget(payload):
    from business_agent.activity_model_first import source
    from business_agent.workbench_deepseek import adapter

    if not hasattr(source, 'excel_budget_seconds'):
        return LARGE_RUN_SECONDS if large_payload(payload) else MAX_RUN_SECONDS
    if not any(doc.get('kind') == 'xlsx' for doc in payload.get('documents', [])):
        return MAX_RUN_SECONDS
    try:
        batches, _, _ = source.plan(payload)
    except adapter.AdapterError:
        # The worker returns the input error and its counts through the normal envelope.
        return MAX_RUN_SECONDS
    layouts = {batch.get('layout') for batch in batches}
    if source.EXCEL_LAYOUT in layouts:
        return max(MAX_RUN_SECONDS, source.excel_budget_seconds(len(batches)))
    if source.large_shenjia.LAYOUT in layouts and large_payload(payload):
        return max(LARGE_RUN_SECONDS, source.excel_budget_seconds(len(batches)))
    return MAX_RUN_SECONDS


def case_scope(payload):
    # Reuse this operator-confirmed grouping on the same original material only.
    # Keep existing structural matching for unseen workbooks.
    if len(payload.get('documents', [])) == 1:
        doc = payload['documents'][0]
        if doc.get('kind') == 'xlsx':
            ref = json.loads(Path(__file__).with_name('freemore-cross-platform-coupon-v1.json').read_text(encoding='utf-8'))
            if doc.get('sha256') == ref['sourceSha256']:
                return ref['scope']

    from business_agent.activity_model_first import grouping
    from business_agent.activity_model_first.coupon_tier_sheet import tier_schedule

    # Older frozen recognition packages lack the verified child-export predicate.
    child_export = getattr(grouping, 'confirmed_child_export', None)
    if (child_export and child_export(payload.get('documents', []), payload.get('manualFields'),
                                      payload.get('materialNames'))):
        return 'single_shenjia_child_export/v1'

    # The socket may still serve an older frozen release during a local cutover.
    from importlib.util import find_spec
    if find_spec('business_agent.activity_model_first.horizontal_coupon_matrix') is not None:
        from business_agent.activity_model_first.horizontal_coupon_matrix import coupon_matrix, SCOPE
        if coupon_matrix(payload):
            return SCOPE

    if tier_schedule(payload):
        return 'brand_coupon_tier_sheet/v1'
    documents = payload.get('documents', [])
    sheets = {row['locator'].get('sheet') or '' for doc in documents for row in doc['tableRows']}
    if {'活动提报模板','活动参与upc'} <= sheets:
        return 'declared_coupon_upc/v1'
    if '券生效时间说明' in sheets and any('美垂仓' in name and '券' in name for name in sheets):
        return 'brand_coupon_plan/v1'
    if any('美团神价' in name for name in sheets) and any('美团混补' in name for name in sheets):
        return 'brand_meituan_dual_plan/v1'
    if any(re.match(r'美团(?:3方券|2方混补|\+淘闪三方混补)', name) for name in sheets):
        return 'coupon_subsidy_ledger/v1'
    headers = {cell['text'].strip() for doc in documents for row in doc['tableRows'][:3] for cell in row['cells']}
    if {'神价ID','子活动ID','神价UPC'} <= headers:
        return 'mixed_shenjia_sheet/v1'
    if ('系统活动价' in headers and any(text.startswith('补贴后单价') for text in headers)
            and any('拟申请补贴' in text for text in headers)):
        return 'item_subsidy_sheet/v1'
    text = '\n'.join(block['text'] for doc in documents for block in doc['textBlocks'])
    if '品商共补' in text and '品牌联合券' in text:
        return 'platform_joint_coupon_text/v1'
    if len(re.findall(r'(?:活动ID|ID)\s*[:：]\s*\d{8,}',text)) >= 2:
        return 'multi_id_coupon_text/v1'
    if '品类券加码' in text and re.search(r'活动ID\s*[:：]?\s*\d{8,}', text):
        return 'category_coupon_text/v1'
    return None


def has_seven_space_coupon_rows(documents):
    """Match the brand column in the coupon ledger, including a joint brand entry."""
    required = {'品牌', 'UPC', '券类型', '活动券（档位）', '平台', '活动时间', '报名要求'}
    for doc in documents:
        if doc.get('kind') != 'xlsx':
            continue
        brand_columns = {}
        for row in doc.get('tableRows', []):
            sheet = row['locator'].get('sheet')
            cells = {cell['column']: cell['text'].strip() for cell in row['cells']}
            if required <= set(cells.values()):
                brand_columns[sheet] = next(col for col, value in cells.items() if value == '品牌')
                continue
            brand = cells.get(brand_columns.get(sheet), '')
            if '七度空间' in {name.strip() for name in re.split(r'[+＋、&＆]', brand)}:
                return True
    return False


def recognition_cases(store, payload):
    """Load the latest applicable rules, including shared dates alongside grouping."""
    scope = case_scope(payload)
    hints = store.approved(scope) if scope else []
    docs = payload.get('documents', [])
    if has_seven_space_coupon_rows(docs):
        hints += store.approved('seven_space_coupon_sku_ledger/v1')
    fields = (payload.get('manualFields') or {}).get('fields', {})
    text = '\n'.join(b['text'] for d in docs for b in d.get('textBlocks', []))
    ids = set(re.findall(r'(?<!\d)\d{8,14}(?!\d)', text))
    tiers = set(re.findall(r'(?:满\s*)?\d+\s*(?:减|[-－−])\s*\d+', text))
    common_period = (fields.get('startAt') and fields.get('endAt') and '券' in text
                     and len(ids) >= 2 and len(tiers) >= 2)
    # A known material can be resubmitted without its original manual fields.
    case = json.loads(Path(__file__).with_name('hansi-december-coupon-dates-v1.json').read_text(encoding='utf-8'))
    if common_period or any(d.get('sha256') == case['sourceSha256'] for d in docs):
        hints += store.approved(case['scope'])
    return list({item['key']: item for item in hints}.values())


def pack(raw):
    return struct.pack('>I', len(raw)) + raw


def read_frame(sock, limit):
    def take(count):
        chunks = bytearray()
        while len(chunks) < count:
            chunk = sock.recv(min(count - len(chunks), 65536))
            if not chunk:
                raise EOFError
            chunks.extend(chunk)
        return bytes(chunks)

    size, = struct.unpack('>I', take(4))
    if not 0 < size <= limit:
        raise ValueError('Invalid frame size')
    return take(size)


def imports(release):
    sys.path.insert(0, str(Path(release).resolve() / 'released'))


def json_bytes(data):
    return json.dumps(data, ensure_ascii=False, separators=(',', ':')).encode()


def unavailable(rid=None):
    return {'contractVersion': CONTRACT, 'requestId': rid, 'ok': False,
            'error': {'code': 'UNAVAILABLE', 'details': {}}}


def contextual_transport(base, hints):
    supplement = '\n\n已确认的历史案例规则，用于判断材料结构。字段值和引用必须来自本次材料。\n'
    supplement += '\n'.join(item['hint'] for item in hints)

    from registration_requirement import POLICY
    from brand_coupon_grouping import POLICY as COUPON_GROUPING_POLICY
    supplement += '\n' + POLICY + ' 仅用本次材料为判断提供原文依据。'

    supplement += '\n本次归组按以下最新审核口径，优先于案例中的旧拆场口径。' + COUPON_GROUPING_POLICY

    supplement += '\n' + business_policy()

    def transport(request, timeout):
        body = json.loads(request.data)
        body['messages'][0]['content'] += supplement
        updated = Request(request.full_url, data=json_bytes(body), method=request.get_method(),
                          headers=dict(request.header_items()))
        return base(updated, timeout)

    return transport


def run_worker(args, raw):
    from business_agent.activity_model_first.worker import handle
    from business_agent.workbench_deepseek import adapter

    try:
        request = adapter.decode(raw)
        if (not isinstance(request, dict) or request.get('contractVersion') != CONTRACT
                or request.get('operation') != 'analyze'
                or not isinstance(request.get('requestId'), str)
                or not isinstance(request.get('payload'), dict)):
            return handle(raw, lambda: adapter.load_private_config(args.deepseek_settings))
        from business_agent.activity_model_first import source
        from business_agent.activity_model_first.diagnostics import RecognitionError
        if hasattr(source, 'check_excel_size'):
            try:
                for doc in request['payload'].get('documents', []):
                    if doc.get('kind') == 'xlsx':
                        source.check_excel_size(doc)
            except RecognitionError as error:
                return {'contractVersion': CONTRACT, 'requestId': request['requestId'], 'ok': False,
                        'error': {'code': error.code, 'message': error.code, 'details': error.details}}
        if large_payload(request['payload']) and not getattr(args, 'enable_large_materials', False):
            return {'contractVersion': CONTRACT, 'requestId': request['requestId'], 'ok': False,
                    'error': {'code': 'MODEL_INPUT_LARGE_DISABLED',
                              'details': {'reason': 'large_materials_not_enabled'}}}
        store = CaseMemory(args.mysql, args.runtime_profile)
        hints = recognition_cases(store, request['payload'])

        def load_model():
            model = adapter.load_private_config(args.deepseek_settings)
            store.note_use(request['requestId'], hints)
            return model

        from shared_coupon_period import repair
        result = handle(raw, load_model, transport=contextual_transport(adapter.provider_call, hints))
        from registration_requirement import annotate
        result = annotate(repair(result, request['payload']))
    except ContextUnavailable:
        return unavailable(request.get('requestId'))
    except (adapter.AdapterError, KeyError, ValueError, TypeError, AttributeError):
        result = handle(raw, lambda: adapter.load_private_config(args.deepseek_settings),
                        transport=contextual_transport(adapter.provider_call, []))
        if not result.get('ok'):
            return result

    # Each worker loads the shared grouping policy for both formal intake and chat previews.
    from brand_coupon_grouping import reconcile
    from recognized_brands import bind_catalog_brands
    return bind_catalog_brands(reconcile(result, request['payload']), request['payload'])


class AgentServer(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True


class AgentConnection(socketserver.BaseRequestHandler):
    def handle(self):
        try:
            self.request.settimeout(4)
            raw = read_frame(self.request, LIMIT)
            if raw == b'status':
                self.reply(json_bytes(self.server.status()))
            else:
                self.recognize(raw)
        except (OSError, ValueError, EOFError):
            return

    def reply(self, raw):
        if not 0 < len(raw) <= RESPONSE_LIMIT:
            raise ValueError('Invalid reply size')
        self.request.sendall(pack(raw))

    def recognize(self, raw):
        args = self.server.args
        command = [sys.executable, '-B', __file__, 'worker', '--release', args.release,
                   '--deepseek-settings', args.deepseek_settings, '--mysql', args.mysql,
                   '--runtime-profile', args.runtime_profile]
        if getattr(args, 'enable_large_materials', False):
            command.append('--enable-large-materials')
        with subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as proc:
            try:
                seconds = (run_budget(json.loads(raw).get('payload', {}))
                           if getattr(args, 'enable_large_materials', False) else MAX_RUN_SECONDS)
            except (ValueError, KeyError, TypeError, AttributeError):
                seconds = MAX_RUN_SECONDS
            expires = time.monotonic() + seconds
            try:
                while time.monotonic() < expires:
                    try:
                        response, _ = proc.communicate(input=raw, timeout=.25)
                        if proc.returncode == 0:
                            self.reply(response)
                        return
                    except subprocess.TimeoutExpired:
                        raw = None
                        if select.select([self.request], [], [], 0)[0]:
                            return
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.communicate()


def status(args):
    from business_agent.activity_model_first.provider import PROMPT_VERSION
    from business_agent.workbench_deepseek import adapter

    ready = False
    try:
        ready = CaseMemory(args.mysql, args.runtime_profile).health()
        model = adapter.load_private_config(args.deepseek_settings).model
    except (ContextUnavailable, adapter.AdapterError):
        model = None
    from business_agent.activity_model_first import source
    result = {'enabled': ready and model is not None, 'mysqlReady': ready,
              'model': model, 'promptVersion': PROMPT_VERSION}
    if hasattr(source, 'EXCEL_DETAIL_ROWS'):
        result.update(plannerManifestSha256=sha256(Path(source.__file__).with_name('SHA256SUMS').read_bytes()).hexdigest(),
                      maxExcelDetailRows=source.EXCEL_DETAIL_ROWS, maxExcelTextChars=source.EXCEL_TEXT_CHARS)
    return {'contractVersion': CONTRACT, 'ok': True, 'result': result}


def serve(args):
    path = Path(args.socket)
    if path.parent.stat().st_mode & 0o077 or not path.parent.is_dir() or path.parent.is_symlink():
        raise ValueError('Agent socket requires a private parent directory')
    if path.exists():
        raise ValueError('Agent socket already exists')
    with AgentServer(str(path), AgentConnection) as server:
        path.chmod(0o600)
        server.args = args
        server.status = lambda: status(args)
        try:
            server.serve_forever()
        finally:
            path.unlink(missing_ok=True)


if __name__ == '__main__':
    cli = argparse.ArgumentParser()
    cli.add_argument('operation', choices=('serve', 'worker'))
    cli.add_argument('--release', required=True)
    cli.add_argument('--runtime-profile', required=True)
    cli.add_argument('--deepseek-settings', required=True)
    cli.add_argument('--socket')
    cli.add_argument('--enable-large-materials', action='store_true',
                     help='Enable verified large workbook processing and batch time budgets')
    cli.add_argument('--mysql', default='/usr/local/mysql/bin/mysql')
    args = cli.parse_args()
    imports(args.release)
    if args.operation == 'serve':
        serve(args)
    else:
        sys.stdout.buffer.write(json_bytes(run_worker(args, sys.stdin.buffer.read(LIMIT + 1))))
