"""Read explicitly requested submissions using the operator's workbench session."""

from base64 import b64encode
from hashlib import sha256
from http.client import HTTPConnection, HTTPException
import json
from pathlib import Path
import re

from chat_attachments import EXTENSIONS, MAX_BYTES, MAX_FILES

OPERATIONS = '/api/test/v1/operations'
SUBMISSION_ID = r'SUB-[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}'
REFERENCE = re.compile(
    rf'(?:https?://127\.0\.0\.1:52669/#submissions/)?({SUBMISSION_ID})\b',
    re.IGNORECASE,
)
MATERIAL_ID = re.compile(r'[A-Za-z0-9_-]{1,128}')
MAX_DETAIL = 8 * 1024 * 1024


class WorkbenchReadError(Exception):
    def __init__(self, status, code, message):
        self.status, self.code, self.message = status, code, message
        super().__init__(code)


def requested_submission(question):
    refs = {'SUB-' + value[4:].lower() for value in REFERENCE.findall(question)}
    if len(refs) > 1:
        raise WorkbenchReadError(422, 'SUBMISSION_REFERENCE_AMBIGUOUS', '请一次提供一份工作台提报链接或编号。')
    return next(iter(refs), None)


def workbench_get(port, cookie, path, byte_limit):
    conn = HTTPConnection('127.0.0.1', port, timeout=6)
    try:
        conn.request('GET', OPERATIONS + path, headers={'Cookie':cookie})
        with conn.getresponse() as response:
            if response.status in (401, 403, 404):
                return None
            if response.status != 200:
                raise OSError
            content = response.read(byte_limit + 1)
            if len(content) > byte_limit:
                raise WorkbenchReadError(413, 'WORKBENCH_RECORD_TOO_LARGE', '工作台记录超过本次可读取大小。')
            return content
    except (OSError, HTTPException):
        raise WorkbenchReadError(503, 'WORKBENCH_READ_UNAVAILABLE', '工作台读取暂不可用，请稍后重试。') from None
    finally:
        conn.close()


def load_submission(port, cookie, submission_id):
    if not re.fullmatch(SUBMISSION_ID, submission_id):
        raise ValueError('Invalid workbench submission id')
    body = workbench_get(port, cookie, '/submissions/' + submission_id, MAX_DETAIL)
    if body is None:
        raise WorkbenchReadError(404, 'WORKBENCH_RECORD_UNAVAILABLE', '提报不存在，或当前账号无权读取。')
    try:
        record = json.loads(body)['data']
        if record['submissionId'] != submission_id or not isinstance(record['materials'], list):
            raise ValueError
    except (ValueError, TypeError, KeyError):
        raise WorkbenchReadError(502, 'WORKBENCH_RECORD_INVALID', '暂时无法读取工作台提报回执。') from None

    uploads, skipped = [], []
    remaining = MAX_BYTES
    files = record['materials']
    for material in files:
        name, mid = material.get('originalName'), material.get('materialId')
        if not isinstance(name, str) or Path(name).suffix.casefold() not in EXTENSIONS:
            continue
        if (not isinstance(mid, str) or not MATERIAL_ID.fullmatch(mid)
                or material.get('downloadAvailable') is not True or len(uploads) == MAX_FILES
                or type(material.get('size')) is not int or not 0 < material['size'] <= remaining):
            skipped.append(name)
            continue
        content = workbench_get(port, cookie, '/materials/' + mid + '/download', remaining)
        if (content is None or len(content) != material['size']
                or sha256(content).hexdigest() != material.get('sha256')):
            skipped.append(name)
            continue
        uploads.append({'filename':name,'contentBase64':b64encode(content).decode('ascii')})
        remaining -= len(content)
    return {'submissionId':submission_id,'inputRevision':record.get('revision'),
            'materialCount':len(files),'skipped':skipped}, uploads
