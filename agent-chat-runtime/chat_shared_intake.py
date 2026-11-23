"""Use the workbench's running recognition Agent for an unsaved chat preview."""

from datetime import datetime
from hashlib import sha256
import re
import socket
import sys

from chat_intake_contract import LABELS, PLATFORMS
from service import CONTRACT, LIMIT, RESPONSE_LIMIT, pack, read_frame, run_budget

RECOGNITION_INTENT = re.compile(
    r'(?:识别|解析|分析|总结|整理|看一下|看下|读一下)(?:一下|下)?(?:这份|这个|这张)?'
    r'(?:附件|原表|文件|材料|活动|表格|表|xlsx|excel)')
FORM_FIELD = re.compile(r'(?:^|[；;\n])\s*(活动名称|活动发起方|活动类型|适用平台|平台活动编号)\s*[：:]\s*([^；;\n]+)')
FORM_VALUES = {'品牌活动':'brand', '平台活动':'platform', '价探招商':'jiatan_sourcing'}
RECOGNITION_ERRORS = {
    'MODEL_INPUT_LARGE_DISABLED':'本地暂未启用大表识别；材料已保留，请等待大表验证完成。',
    'MODEL_INPUT_DECODE_INCOMPLETE':'有附件只读取到部分内容，请拆分后再识别。',
    'MODEL_INPUT_BATCH_LIMIT':'材料超出了正式识别的单次处理上限，请分批上传。',
    'MODEL_INPUT_ROW_LIMIT':'原表中有一行内容过长，请拆分后再识别。',
    'MODEL_RECOGNITION_PROVIDER_AUTH':'正式识别 Agent 的 DeepSeek 鉴权失败。',
    'MODEL_RECOGNITION_UNAVAILABLE':'正式提报识别服务暂不可用。',
}


class IntakePreviewError(Exception):
    def __init__(self, status, code, message):
        self.status, self.code, self.message = status, code, message
        super().__init__(code)


def requests_recognition(text):
    return RECOGNITION_INTENT.search(text.casefold()) is not None


def form_fields(text):
    fields = {}
    for label, value in FORM_FIELD.findall(text):
        value = value.strip()
        if label == '活动名称' and value:
            fields['activityName'] = value
        elif label in ('活动发起方','活动类型') and value in FORM_VALUES:
            fields['type'] = FORM_VALUES[value]
        elif label == '适用平台':
            codes = [PLATFORMS.get(name, name.lower()) for name in re.split(r'[、,，/\s]+', value)]
            if all(code in PLATFORMS.values() for code in codes):
                selected = list(dict.fromkeys([*fields.pop('applicablePlatformCodes', []),
                                               *([fields.pop('platformCode')] if 'platformCode' in fields else []), *codes]))
                if len(selected) == 1:
                    fields['platformCode'] = selected[0]
                else:
                    fields['applicablePlatformCodes'] = selected
        elif label == '平台活动编号' and value:
            fields['sourceActivityId'] = value
    return fields


def intake_request(request_id, attachments, text, catalog_revision):
    from business_agent.workbench_deepseek import adapter

    docs = [item['document'] for item in attachments if item.get('document')]
    if not docs:
        raise IntakePreviewError(422,'INTAKE_NO_MATERIAL','请先上传要识别的活动材料。')
    fields = form_fields(text)
    manual = {'materialId':'FORM-'+request_id, 'sha256':sha256(adapter.encode(fields)).hexdigest(),
              'fields':fields}
    names = {item['document']['materialId']:item['filename'] for item in attachments if item.get('document')}
    digest = sha256(adapter.encode({'documents':docs,'form':manual})).hexdigest()
    return {
        'contractVersion':CONTRACT, 'requestId':request_id, 'operation':'analyze',
        'payload':{'contractVersion':CONTRACT, 'inputRevision':1, 'inputDigest':digest,
                   'manualFields':manual, 'documents':docs, 'catalogSnapshot':{'revision':catalog_revision},
                   'materialNames':names},
    }


def socket_request(endpoint, raw, timeout):
    if not endpoint or not endpoint.is_socket():
        raise IntakePreviewError(503,'INTAKE_UNAVAILABLE','工作台的活动识别服务暂不可用。')
    if len(raw) > LIMIT:
        raise IntakePreviewError(413,'INTAKE_INPUT_LIMIT','材料超出单次识别上限，请分批上传。')
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(3)
            connection.connect(str(endpoint))
            connection.sendall(pack(raw))
            connection.settimeout(timeout)
            return read_frame(connection, RESPONSE_LIMIT)
    except TimeoutError:
        raise IntakePreviewError(504,'INTAKE_TIMEOUT','正式识别服务处理超时，可缩小材料范围后重试。') from None
    except (OSError, EOFError, ValueError):
        raise IntakePreviewError(503,'INTAKE_UNAVAILABLE','工作台的活动识别服务暂不可用。') from None


def use_release_validators(release):
    sys.path[:0] = [str(release.resolve() / name) for name in ('otp-runtime','released','baseline')]


def run_preview(endpoint, release, request, *, transport=socket_request):
    from b_model_intake import validate_metadata
    from b_schema import check
    from business_agent.workbench_deepseek import adapter
    from chat_intake_contract import SCHEMA
    import json

    raw = transport(endpoint, adapter.encode(request), run_budget(request['payload']))
    try:
        response = adapter.decode(raw)
        if (not isinstance(response,dict) or response.get('contractVersion') != CONTRACT
                or response.get('requestId') != request['requestId']):
            raise ValueError('Recognition response mismatch')
        if response.get('ok') is not True:
            error = response.get('error') or {}
            code = error.get('code')
            raise IntakePreviewError(422,'INTAKE_RECOGNITION_FAILED',
                RECOGNITION_ERRORS.get(code,'正式识别服务本次未能完成分析，请核对材料后重试。'))
        result, metadata = response['result'], response['metadata']
        check(result, json.loads((release / SCHEMA).read_text(encoding='utf-8')))
        payload = request['payload']
        source_ids = [sid for group in result['candidates'] for row in group['productRows'] for sid in row['sourceRowIds']]
        if len(set(source_ids)) < len(source_ids):
            from b_coupon_membership_validation import verify_coupon_memberships
            from business_agent.activity_model_first.source import plan
            _, lookup, _ = plan(payload)
            validate_metadata(metadata,result,verify_shared_sources=lambda data: verify_coupon_memberships(data,payload,lookup))
        else:
            validate_metadata(metadata,result)
        if (any(result[field] != payload[field] for field in ('inputDigest','inputRevision'))
                or result['catalogRevision'] != payload['catalogSnapshot']['revision']
                or result['materialResults'] != payload['documents']):
            raise ValueError('Recognition source mismatch')
        return result, metadata
    except (adapter.AdapterError, OSError, ValueError, KeyError, TypeError, AttributeError):
        raise IntakePreviewError(502,'INTAKE_OUTPUT_INVALID','正式识别回执无法核对，本次结果待确认。') from None


def preview_periods(result, form, memory, received_at):
    from b_coupon_period_dates import candidate_coupon_periods

    # A later question about a saved upload uses its original conversation receipt.
    documents = result['materialResults']
    if memory and len(documents) == 1:
        digest = documents[0]['sha256']
        for turn in memory.state.get('turns', []):
            if any(ref.get('sha256') == digest for ref in turn.get('attachments', [])):
                stamp = turn.get('createdAt')
                if stamp:
                    received_at = datetime.fromisoformat(stamp.replace('Z', '+00:00')).timestamp()
                    break
    return candidate_coupon_periods(result, form, received_at)


def describe_preview(result, metadata, periods=None):
    groups = result['candidates']
    if not groups:
        return '已由工作台活动识别服务处理材料，本次未识别出可确认的活动。请补充活动原文或提报字段。'
    lines = [f'已用工作台活动识别服务生成预览，共 {len(groups)} 个活动分组。']
    extra = {item['candidateId']:item for item in metadata['candidateMetadata']}
    for i, group in enumerate(groups[:10], 1):
        fields = group['fields']
        lines.extend(['', f'{i}. {fields["activityName"]["value"] or "活动名称待补"}'])
        period = (periods or {}).get(group['candidateId'])
        if period:
            if period['status'] == 'year_unconfirmed':
                lines.append(f"   活动周期  {period['startMonthDay']} 至 {period['endMonthDay']}（年份待确认）")
            else:
                inferred = '（年份按提报时间推定，可修改）' if period['status'] == 'year_inferred' else ''
                lines.append(f"   活动周期  {period['startOn']} 至 {period['endOn']}{inferred}")
        fields_to_show = ('type','platformCode','sourceActivityId','startAt','endAt','rulesText')
        for code in fields_to_show:
            field = fields[code]
            value = field['value']
            if field['status'] == 'conflict':
                lines.append(f'   {LABELS[code]}待确认')
            elif code == 'platformCode' and value not in PLATFORMS.values():
                lines.append('   平台待核对')
            elif value:
                value = {'brand':'品牌活动','platform':'平台活动','jiatan_sourcing':'价探招商'}.get(value,value)
                value = next((label for label,code_value in PLATFORMS.items() if value==code_value),value)
                lines.append(f'   {LABELS[code]}  {value[:1600]}')
        brands = extra.get(group['candidateId'],{}).get('brandNames',{}).get('value')
        if brands:
            lines.append('   品牌  '+ '、'.join(brands))
        lines.append(f'   商品明细 {len(group["productRows"])} 条')
    if len(groups)>10:
        lines.append(f'页面显示前 10 组，完整回执含 {len(groups)} 组。')
    lines.extend(['','以上为聊天预览。正式活动从工作台提报。'])
    return '\n'.join(lines)
