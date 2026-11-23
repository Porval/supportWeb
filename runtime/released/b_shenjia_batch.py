"""Preview and save one independent submission per explicit main Shenjia ID."""
from base64 import b64encode
from datetime import date
import hashlib
import json
from pathlib import Path
import re
import threading
import time

from account_primitives import fail
from b_domain import add_material, fields, history, material, upload_payload
from b_model_recognition import ProviderFailure
from b_model_worker import ModelWorker
from b_state import canon

ACTION = 'submissions.shenjia_batch'
CONTRACT = 'shenjia-batch/1.0'
XLSX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
MAIN_ID = re.compile(r'[0-9]{1,40}')
MAX_ACTIVITIES = 100
MAX_ROWS = 10000


class ShenjiaBatchWorker(ModelWorker):
    contract = CONTRACT
    module = 'business_agent.shenjia_batch.worker'
    output_limit = 32 * 1024 * 1024

    def import_root(self):
        return self.directory.parent.parent

    def decode(self, output):
        def unique(pairs):
            result = dict(pairs)
            if len(result) != len(pairs):
                raise ValueError('duplicate key')
            return result

        try:
            wire = json.loads(output, object_pairs_hook=unique)
        except (ValueError, RecursionError):
            raise ProviderFailure('INVALID_OUTPUT') from None
        if not isinstance(wire, dict) or wire.get('contractVersion') != CONTRACT:
            raise ProviderFailure('INVALID_OUTPUT')
        if wire.get('ok') is True and isinstance(wire.get('result'), dict):
            return wire['result']
        if wire.get('ok') is False and isinstance(wire.get('error'), dict):
            error = wire['error']
            if error.get('code') == 'FORMAT_NOT_SUPPORTED':
                return {'recognized': False}
            message = error.get('message')
            if isinstance(message, str) and 0 < len(message) <= 500:
                details = error.get('details')
                if isinstance(details, dict):
                    sheet, row = details.get('sheet'), details.get('row')
                    if isinstance(sheet, str) and 0 < len(sheet) <= 31 and type(row) is int and 1 <= row <= 1048576:
                        message = f'工作表「{sheet}」第 {row} 行：{message}'
                fail(422, 'SHENJIA_BATCH_INPUT', message)
        raise ProviderFailure('INVALID_OUTPUT')

    def split(self, source, operation):
        payload = canon({'contractVersion': CONTRACT, 'operation': operation, 'payload': {
            'filename': source['filename'], 'sha256': source['sha256'],
            'contentBase64': b64encode(source['content']).decode('ascii'),
        }}).encode()
        try:
            return self.invoke([], payload, threading.Event(), time.monotonic() + 9)
        except ProviderFailure as error:
            if error.reason == 'TIMEOUT':
                fail(504, 'SHENJIA_BATCH_TIMEOUT', '本地拆分超时，请重试。')
            fail(503, 'SHENJIA_BATCH_UNAVAILABLE', '本地活动 Agent 暂时无法拆分神价表。')


def owned_source(db, actor, auth, material_id):
    auth.require(db, actor, 'submissions.create')
    row = material(db, material_id)
    if row['owner_id'] != actor['id']:
        fail(404, 'MATERIAL_UNAVAILABLE', '原件不存在或不可访问')
    if row['media_type'] != XLSX or Path(row['filename']).suffix.casefold() != '.xlsx':
        fail(422, 'INVALID_MATERIAL', '请选择 XLSX 神价表。')
    if row['size'] != len(row['content']) or hashlib.sha256(row['content']).hexdigest() != row['sha256']:
        fail(409, 'MATERIAL_HASH_MISMATCH', '原件校验失败，请重新上传。')
    return row


def read_result(result, source_sha, *, with_files):
    if result.get('recognized') is False:
        return None
    activities = result.get('activities')
    total = result.get('productRowCount')
    if (result.get('sourceSha256') != source_sha or not isinstance(activities, list)
            or not 1 <= len(activities) <= MAX_ACTIVITIES or result.get('activityCount') != len(activities)
            or type(total) is not int or not 1 <= total <= MAX_ROWS):
        fail(502, 'SHENJIA_BATCH_INVALID_OUTPUT', '拆分结果校验失败。')
    seen = set()
    seen_rows = set()
    summary = []
    files = []
    try:
        for activity in activities:
            identifier = activity['sourceActivityId']
            name = activity['activityName']
            count = activity['productRowCount']
            if (not isinstance(identifier, str) or not MAIN_ID.fullmatch(identifier) or identifier in seen
                    or not isinstance(name, str) or not name.strip() or len(name) > 200
                    or type(count) is not int or not 1 <= count <= 1000):
                raise ValueError()
            seen.add(identifier)
            sheet, source_rows = activity['sourceSheet'], activity['sourceRows']
            if (not isinstance(sheet, str) or not 0 < len(sheet) <= 31
                    or not isinstance(source_rows, list) or len(source_rows) != count
                    or any(type(row) is not int or not 1 <= row <= 1048576 for row in source_rows)
                    or source_rows != sorted(set(source_rows))
                    or seen_rows.intersection(source_rows)):
                raise ValueError()
            seen_rows.update(source_rows)
            start, end = (activity.get(key) for key in ('startDate', 'endDate'))
            for value in (start, end):
                if value is not None and date.fromisoformat(value).isoformat() != value:
                    raise ValueError()
            if start and end and start > end:
                raise ValueError()
            summary.append({'sourceActivityId': identifier, 'activityName': name,
                            'startDate': start, 'endDate': end, 'productRowCount': count,
                            'sourceSheet': sheet, 'sourceRows': source_rows})
            if with_files:
                upload = upload_payload({'filename': activity['filename'], 'mediaType': XLSX,
                                         'contentBase64': activity['contentBase64'], 'sha256': activity['sha256']})
                files.append(upload)
        if sum(activity['productRowCount'] for activity in summary) != total:
            raise ValueError()
    except (KeyError, TypeError, ValueError, AttributeError):
        fail(502, 'SHENJIA_BATCH_INVALID_OUTPUT', '拆分结果校验失败。')
    return {'sourceSha256': source_sha, 'productRowCount': total, 'activities': summary}, files


def preview(worker, source):
    parsed = read_result(worker.split(source, 'inspect'), source['sha256'], with_files=False)
    if not parsed or len(parsed[0]['activities']) < 2:
        return {'status': 'single_or_unsupported'}
    return {'status': 'ready', **parsed[0]}


def save(domain, db, actor, source, expected, parsed):
    if not parsed or len(parsed[0]['activities']) < 2 or parsed[0] != expected:
        fail(409, 'SHENJIA_BATCH_CHANGED', '拆分结果已变化，请重新预览。')
    manifest, files = parsed
    existing = db.execute(
        '''SELECT c.response FROM commands c
           JOIN material_blobs m ON m.id=c.resource_id
           WHERE c.action=? AND c.actor_id=? AND m.owner_id=? AND m.sha256=?
           ORDER BY c.rowid LIMIT 1''', (ACTION, actor['id'], actor['id'], source['sha256'])).fetchone()
    if existing:
        previous = json.loads(existing[0])
        return 200, {key: value for key, value in previous.items() if key != 'commandId'} | {
            'sourceMaterialId': source['id'], 'alreadySaved': True}
    submissions = []
    for activity, upload in zip(manifest['activities'], files):
        child = add_material(db, actor, domain.clock(), *upload)
        # Bind the original to the command; only the split workbook enters recognition.
        form = {'activityName': activity['activityName'], 'type': 'platform', 'platformCode': 'meituan',
                'sourceActivityId': activity['sourceActivityId']}
        if activity['startDate']:
            form['startAt'] = activity['startDate'] + 'T00:00:00+08:00'
        if activity['endDate']:
            form['endAt'] = activity['endDate'] + 'T23:59:59.999+08:00'
        _, created = domain.create_submission(db, actor, {'form': form, 'materialIds': [child['materialId']],
                                                        'applicablePlatformCodes': ['meituan']})
        history(db, actor, domain.clock(), submission=created['submissionId'], event='submission.shenjia_split',
                details={'sourceMaterialId': source['id'], 'sourceSha256': source['sha256'],
                         'sourceSheet': activity['sourceSheet'], 'sourceRows': activity['sourceRows'],
                         'sourceActivityId': activity['sourceActivityId'], 'splitMaterialId': child['materialId']})
        submissions.append({**activity, 'submissionId': created['submissionId'], 'materialId': child['materialId']})
    return 201, {'sourceMaterialId': source['id'], 'sourceSha256': source['sha256'],
                 'productRowCount': manifest['productRowCount'], 'submissions': submissions, 'alreadySaved': False}


def check_body(body):
    fields(body, ['sourceMaterialId', 'expected'])
    expected = body['expected']
    fields(expected, ['sourceSha256', 'productRowCount', 'activities'])
    if (not isinstance(expected['activities'], list) or not 2 <= len(expected['activities']) <= MAX_ACTIVITIES
            or not isinstance(expected['sourceSha256'], str) or not re.fullmatch(r'[0-9a-f]{64}', expected['sourceSha256'])):
        fail(422, 'INVALID_REQUEST', '请先预览合并神价表。')
