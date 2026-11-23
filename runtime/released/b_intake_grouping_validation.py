"""Reject results that split one official child-activity export into multiple activities."""

from business_agent.activity_model_first.grouping import confirmed_child_export

ERROR_CODE = 'AGENT_RESULT_GROUPING_CONFLICT'
ERROR_MESSAGE = '这份神价子活动清单属于同一场活动，Agent 拆成了多组。原材料已保留，请重新识别。'


def splits_single_child_export(result, manual_fields, material_names):
    if (result.get('engine') != 'deepseek_model_first_v1' or len(result.get('candidates', [])) < 2
            or manual_fields['fields'].get('type') != 'platform'):
        return False
    documents = result.get('materialResults', [])
    if len(documents) != 1:
        return False
    document, = documents
    if document['status'] != 'parsed' or document.get('errors'):
        return False
    return bool(confirmed_child_export(documents, manual_fields, material_names))
