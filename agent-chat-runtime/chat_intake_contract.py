"""Read the Activity Agent's business fields from the installed intake release."""

import json
import re

SCHEMA = 'released/business_agent/activity_model_first/schemas/analysis.schema.json'
PLATFORMS = {'美团':'meituan', '淘宝闪购':'taobao_instant', '京东':'jd'}
PLATFORM_QUESTION = re.compile(r'(?:目前|当前|现在)?(?:活动|活动提报|提报)?(?:可选|可用|支持|适用)?(?:的)?平台(?:名|选项|目录|列表)?(?:有|是|包括|都有哪些|有哪些|有什么|支持哪些|有哪些可选|选哪些)?(?:哪些|哪几个|什么|啥)?(?:呢|呀|吗)?')
def describe_platforms():
    return '活动平台可选美团（meituan）、淘宝闪购（taobao_instant）和京东（jd）。价探招商表示活动发起方。'

# These fields are validated separately in the release's b_model_intake.validate_metadata.
METADATA_FIELDS = {'activityMethod':'活动方式', 'brandNames':'材料识别出的品牌名称'}
LABELS = {
    'activityName':'活动名称', 'type':'活动发起方', 'platformCode':'平台',
    'sourceActivityId':'平台活动编号', 'startAt':'开始时间', 'endAt':'结束时间',
    'brandIds':'品牌主档 ID', 'rulesText':'优惠机制与规则', 'brandBenefit':'品牌利益点',
    'remark':'备注', 'companyBearing':'公司是否承担费用',
    'rowId':'商品行 ID', 'rawInternalCode':'原表内部码', 'rawUpc':'原表 UPC／条码',
    'rawName':'原表商品名称', 'rawSpec':'原表规格', 'matchKind':'匹配状态',
    'matchedProductId':'已匹配商品 ID', 'candidates':'匹配候选',
    'sourceRowIds':'原表行标识', 'evidenceIds':'原文证据引用',
}
STATUS_LABELS = {'explicit':'已识别', 'missing':'缺失', 'conflict':'冲突'}
DETAIL_FIELDS = ('evidence', 'missingFields', 'conflicts', 'warnings', 'errors')
FIELD_QUESTION = re.compile(
    r'(?:请)?(?:你|活动agent)?(?:告诉我|列出|说明|给我看一下)?'
    r'(?:(?:提交给|传给|给|发给|返回给|对接)?后端(?:的)?(?:活动|识别|回执)?|'
    r'(?:活动识别|识别结果|提报识别)(?:回执|接口|给后端)?(?:的)?|'
    r'(?:活动|提报|回执|接口)(?:的)?)?'
    r'(?:标准|固定|契约|回执|输出|识别|对接|活动|结构化)*字段'
    r'(?:有|是|包括|都有什么|都包含|清单|列表)?(?:什么|啥|哪些|哪几个)?(?:吗|呢|呀)?')


def is_field_question(text):
    return FIELD_QUESTION.fullmatch(text) is not None


def load_intake_contract(release):
    try:
        schema = json.loads((release / SCHEMA).read_text(encoding='utf-8'))
        properties = schema['properties']
        candidate = properties['candidates']['items']['properties']
        activity = candidate['fields']['properties']
        products = candidate['productRows']['items']['properties']
        return {
            'contractVersion':properties['contractVersion']['const'],
            'activityPlatforms':PLATFORMS,
            'activityFields':{key:LABELS.get(key, key) for key in activity},
            'candidateMetadata':METADATA_FIELDS,
            'productFields':{key:LABELS.get(key, key) for key in products},
            'fieldStatuses':activity['activityName']['properties']['status']['enum'],
            'detailFields':[key for key in DETAIL_FIELDS if key in properties],
        }
    except (OSError, ValueError, KeyError, AttributeError, TypeError):
        return None


def field_list(fields):
    return '、'.join(f'{label}（{code}）' for code, label in fields.items())


def describe_intake_contract(contract):
    statuses = '、'.join(f'{STATUS_LABELS.get(code, code)}（{code}）' for code in contract['fieldStatuses'])
    return (
        f'工作台活动识别使用 {contract["contractVersion"]}。回执里的 result.candidates 按活动分组。\n\n'
        f'• 活动字段（fields）  {field_list(contract["activityFields"])}。\n\n'
        f'• 活动平台  {field_list({code:label for label,code in PLATFORMS.items()})}。价探招商表示活动发起方。\n\n'
        f'• 分组补充信息（metadata.candidateMetadata）  {field_list(contract["candidateMetadata"])}，'
        '通过 candidateId 对应活动分组。\n\n'
        f'• 商品明细（productRows）  {field_list(contract["productFields"])}。\n\n'
        '• 取值依据  活动字段带 value、status 和 evidenceIds。'
        f'当前字段状态为 {statuses}。'
        '原文定位、待补字段和冲突也会单独返回。\n\n'
        '在这个对话里可以按这套字段整理材料草稿。正式识别回执由工作台活动提报生成。'
    )
