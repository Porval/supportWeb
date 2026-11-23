"""Bind a declared common coupon period only when the text and submitted form agree."""
from datetime import date, datetime, timedelta
import re

DAY = r'(?:(\d{4})[年./-])?(\d{1,2})[月./-](\d{1,2})日?'
PERIOD = re.compile(r'(?:共同活动周期|活动周期|活动时间|活动日期|活动有效期)[：:]\s*' + DAY + r'\s*(?:至|到|[-—~～])\s*' + DAY +
                    r'(?:[，,；;]\s*(?:全部|所有|各|[一二三四五六七八九十\d]+)档(?:券)?(?:均|共同|全部)?适用)?[。.]?')
OTHER_DATE = re.compile(r'\d+\s*[月./-]\s*\d+|本档|仅限|另行|除外|例外|另计|有效期|活动时间|活动周期|截止|延期|另有|周[一二三四五六日]|工作日|周末')
DEADLINE = re.compile(r'报名截止(?:日|时间|日期)?[：:]\s*'+DAY+r'[。.]?')
TIER = re.compile(r'(?:满\s*)?\d+\s*(?:减|[-－−])\s*\d+')
ID = re.compile(r'(?<!\d)\d{8,14}(?!\d)')
DATES = ('startAt','endAt')


def repair(reply, payload):
    if not reply.get('ok'):
        return reply
    res = reply['result']
    docs = payload['documents']
    groups = res['candidates']
    if len(docs)!=1 or docs[0]['kind']!='txt' or len(groups)<2 or len(res['evidence'])>5997:
        return reply
    doc = docs[0]
    form = payload.get('manualFields') or {}
    fields = form.get('fields',{})
    try:
        times = [datetime.fromisoformat(fields[k]) for k in DATES]
        if any(t.utcoffset()!=timedelta(hours=8) for t in times) or times[0]>times[1]:
            return reply
    except (KeyError,TypeError,ValueError):
        return reply
    stated = None
    coupon_ids = set()
    for block in doc['textBlocks']:
        text = block['text'].strip()
        m = PERIOD.fullmatch(text)
        if m:
            if stated or coupon_ids:
                return reply
            try:
                pieces=m.groups()
                days=[date(int(y or t.year),int(mo),int(d)) for (y,mo,d),t in zip((pieces[:3],pieces[3:]),times)]
            except ValueError:
                return reply
            if days!=[t.date() for t in times]:
                return reply
            stated=block
        elif DEADLINE.fullmatch(text):
            continue
        else:
            tier=TIER.search(text)
            ids=ID.findall(text)
            # Only remove numeric tiers explicitly labelled as coupon amounts.
            # A dotted or dashed month-day elsewhere might be a separate schedule.
            masked=re.sub(r'满\s*\d+\s*减\s*\d+|(?<=券)\s*\d+\s*[-－−]\s*\d+', '', text)
            if OTHER_DATE.search(masked):
                return reply
            if tier and len(ids)==1 and '券' in text:
                coupon_ids.add(ids[0])
    group_ids=[g['fields']['sourceActivityId'] for g in groups]
    if (not stated or any(f['status']!='explicit' for f in group_ids)
            or set(f['value'] for f in group_ids)!=coupon_ids or len(coupon_ids)!=len(groups)):
        return reply
    if any(c['field'] in DATES for c in res['conflicts']):
        return reply
    pending=[]
    for g in groups:
        for k in DATES:
            f=g['fields'][k]
            if f['status']=='missing':
                pending.append((g,k))
            elif f['status']!='explicit' or f['value'] not in (fields[k], fields[k][:10]):
                return reply
    if not pending:
        return reply
    evidence=res['evidence']
    def bind(mid,digest,locator,quote):
        entry=dict(materialId=mid,sourceSha256=digest,locator=locator,quote=quote)
        for e in evidence:
            if all(e.get(k)==v for k,v in entry.items()):
                return e['evidenceId']
        used={e['evidenceId'] for e in evidence}
        n=len(evidence)+1
        while 'ev-'+str(n) in used:
            n+=1
        eid='ev-'+str(n);evidence.append(dict(entry,evidenceId=eid))
        return eid
    common=bind(doc['materialId'],doc['sha256'],stated['locator'],stated['text'].strip())
    repaired=set()
    for g,k in pending:
        source=bind(form['materialId'],form['sha256'],dict(kind='form',fieldPath=k),fields[k])
        g['fields'][k]=dict(value=fields[k],status='explicit',evidenceIds=[source,common])
        repaired.add(k)
    res['missingFields']=[f for f in res['missingFields'] if f['field'] not in repaired]
    res['warnings']=[w for w in res['warnings'] if not (w.get('code')=='MODEL_FIELD_EVIDENCE_UNSUPPORTED'
                     and w.get('message') in {f'模型字段 {k} 的原文依据待核对' for k in repaired})]
    return reply
