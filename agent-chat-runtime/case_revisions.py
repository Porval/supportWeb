"""Persist explicit operator corrections as append-only case revisions."""

from hashlib import sha256
import json
import re

from memory import ContextUnavailable, literal

# The workbench uses this server-side permission to identify full administrators.
EDITOR_PERMISSION = 'accounts.manage'
MAX_CASE_TEXT = 500
COMMAND_ID = re.compile(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}')
CASE_CORRECTION_QUESTION = re.compile(
    r'(?:(?:案例库|案例口径|识别规则|历史口径|案例).*(?:可以改|能改|能不能改|可以修改|怎么改|如何修改|怎么更新|能更新)'
    r'|(?:能不能|能否|能|可以|怎么|怎样|如何)(?:帮我)?(?:修正|修改|更正|更新|改一下|改)(?:本地|以前的|已有的)?'
    r'(?:案例库|案例口径|识别规则|历史口径|案例))')


class CaseRevisionError(Exception):
    def __init__(self, status, code, message):
        self.status, self.code, self.message = status, code, message
        super().__init__(code)


def may_revise(editor, viewer):
    return bool(editor and viewer.get('id') and EDITOR_PERMISSION in viewer['permissions'])


def revision_request(body, actor_id):
    fields = {'commandId','caseKey','expectedRevision','confirmedSummary','recognitionHint'}
    if not isinstance(body,dict) or set(body) != fields:
        raise CaseRevisionError(400,'CASE_REVISION_INVALID','请填写案例、版本和两段确认内容。')
    command, key, revision = (body[field] for field in ('commandId','caseKey','expectedRevision'))
    if (not isinstance(command,str) or not COMMAND_ID.fullmatch(command)
            or not isinstance(key,str) or not re.fullmatch(r'[a-z0-9-]{1,80}',key)
            or type(revision) is not int or not 1 <= revision < 65535
            or not isinstance(actor_id,str) or not 1 <= len(actor_id) <= 160):
        raise CaseRevisionError(400,'CASE_REVISION_INVALID','案例、版本或确认人信息不正确。')
    normalized = dict(body)
    for field in ('confirmedSummary','recognitionHint'):
        value = body[field]
        if not isinstance(value,str) or not 1 <= len(value.strip()) <= MAX_CASE_TEXT:
            raise CaseRevisionError(400,'CASE_TEXT_INVALID','业务口径和识别提示均须为 1 至 500 字。')
        normalized[field] = value.strip()
    digest = sha256(json.dumps(normalized | {'actorId':actor_id}, ensure_ascii=False,
                               sort_keys=True).encode('utf-8')).hexdigest()
    return normalized,digest


def get_command(editor, command):
    result = editor.execute(f"""
        SELECT JSON_OBJECT('caseKey',case_key,'revision',case_revision,'actorId',actor_id,
                           'changeSha256',change_sha256)
        FROM case_revision_audit WHERE command_id={literal(command)};
    """).strip()
    return json.loads(result) if result else None


def command_result(record, actor_id, digest):
    if record['actorId'] != actor_id or record['changeSha256'] != digest:
        raise CaseRevisionError(409,'CASE_COMMAND_CONFLICT','这次保存的编号已使用，请重新提交。')
    return {'caseKey':record['caseKey'],'revision':record['revision']}


def save_revision(editor, body, actor_id):
    command,digest = revision_request(body,actor_id)
    command_id,key,base = (command[field] for field in ('commandId','caseKey','expectedRevision'))
    existing = get_command(editor,command_id)
    if existing:
        return command_result(existing,actor_id,digest)
    lookup, uid, event, change_hash, summary, hint = map(literal, (
        key,actor_id,command_id,digest,command['confirmedSummary'],command['recognitionHint']))
    try:
        result = editor.execute(f"""
            START TRANSACTION;
            SET @case_base_revision = NULL, @case_inserted = 0;
            SELECT revision INTO @case_base_revision FROM approved_case
            WHERE case_key={lookup} ORDER BY revision DESC LIMIT 1;
            INSERT INTO approved_case
                (case_key,revision,case_scope,source_sha256,confirmed_summary,recognition_hint)
            SELECT case_key,revision+1,case_scope,source_sha256,{summary},{hint}
            FROM approved_case
            WHERE case_key={lookup} AND revision={base} AND active=TRUE
              AND @case_base_revision={base}
              AND (BINARY confirmed_summary<>BINARY {summary} OR BINARY recognition_hint<>BINARY {hint});
            SET @case_inserted=ROW_COUNT();
            INSERT INTO case_revision_audit
                (case_key,case_revision,command_id,actor_id,change_sha256)
            SELECT {lookup},{base+1},{event},{uid},{change_hash} WHERE @case_inserted=1;
            COMMIT;
            SELECT JSON_OBJECT('currentRevision',@case_base_revision,'saved',@case_inserted);
        """)
    except ContextUnavailable:
        existing = get_command(editor,command_id)
        if existing:
            return command_result(existing,actor_id,digest)
        latest = editor.execute(f'SELECT MAX(revision) FROM approved_case WHERE case_key={lookup};').strip()
        if latest.isdigit() and int(latest) != base:
            raise CaseRevisionError(409,'CASE_REVISION_STALE','案例已经更新，请重新加载当前版本。') from None
        raise
    state = json.loads(result)
    if state['saved']==1:
        return {'caseKey':key,'revision':base+1}
    existing = get_command(editor,command_id)
    if existing:
        return command_result(existing,actor_id,digest)
    if state['currentRevision']!=base:
        raise CaseRevisionError(409,'CASE_REVISION_STALE','案例已经更新，请重新加载当前版本。')
    raise CaseRevisionError(409,'CASE_UNCHANGED','请填写需要修正的内容。')
