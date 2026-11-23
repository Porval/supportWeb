"""intake-registration/1: read-only participation guidance from explicit candidate evidence."""
import re
from decimal import Decimal

POLICY = ('报名口径 v1：公司、门店、价探任一方明确需要出资，该活动需要操作人勾选报名。'
          '分档出资时说明触发的券档；出资不明请提报人确认，不默认无需报名。'
          '材料里的商品勾选、拟报名、内部审核通过都不能证明平台已报名；不要自动勾选或执行报名。')
PAYER = re.compile(r'(?:由)?(公司|门店|价探|商家)(?:方)?\s*(?:承担|出资|出钱|补贴)\s*[：:]?\s*(?:(\d+(?:\.\d+)?)\s*(?:元|%)?|是|全部费用|全额)[。.]?')
SHORT = re.compile(r'品\s*\d+(?:\.\d+)?\s*商\s*(\d+(?:\.\d+)?)\s*平台\s*\d+(?:\.\d+)?')
def has_cost(quote):
    if any(word in quote for word in ('若','如果','假设','待确认','待核','？','?', '例如','示例','模板','暂定','拟由','并非','不确定','是否')):
        return False
    for clause in re.split(r'[，,；;\n]', quote):
        clause=clause.strip()
        m=PAYER.fullmatch(clause)
        if m and (m[2] is None or Decimal(m[2])>0):
            return True
        m=SHORT.fullmatch(clause.strip('（）() '))
        if m and Decimal(m[1])>0:
            return True
    return False

def annotate(reply):
    if not reply.get('ok'):
        return reply
    result=reply['result']
    refs={e['evidenceId']:e for e in result['evidence']}
    for group in result['candidates']:
        fields=group['fields']
        bearing=fields.get('companyBearing',{})
        if bearing.get('status')=='conflict':
            continue
        source=None
        for name in ('companyBearing','rulesText','remark'):
            f=fields.get(name,{})
            if f.get('status')!='explicit':
                continue
            for eid in f.get('evidenceIds',[]):
                e=refs.get(eid)
                if e and ((name=='companyBearing' and (f.get('value') or {}).get('state')=='yes')
                          or has_cost(e['quote'])):
                    source=e
                    break
            if source:break
        if source:
            title=(fields.get('activityName') or {}).get('value') or group['candidateId']
            w={'code':'MANUAL_ENROLLMENT_SELECTION_REQUIRED',
               'message':f'{title}：有我方出资，需要操作人勾选报名。',
               'locator':source['locator']}
            if w not in result['warnings'] and len(result['warnings'])<10000:
                result['warnings'].append(w)
    return reply
