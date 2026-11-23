"""Keep structured workbook decisions bound to source cells."""

from business_agent.workbench_deepseek.adapter import encode

from .coupon_workbook import MISSING, _field

FIELD_NAMES = ('activityName','type','platformCode','sourceActivityId','startAt','endAt','brandIds',
               'rulesText','brandBenefit','remark','companyBearing')


class StructuredProjection:
    def __init__(self, payload, result, metadata, lookup):
        self.manual = payload['manualFields']
        self.form = (self.manual or {}).get('fields', {})
        self.result = result
        self.metadata = metadata
        self.evidence = list(result['evidence'])
        self.keys = {(item['materialId'], tuple(sorted(item['locator'].items())), item['quote']):
                     item['evidenceId'] for item in self.evidence}
        self.ids = set(self.keys.values())
        self.next_id = len(self.evidence) + 1
        self.positions = {(item['materialId'], item['locator'].get('sheet'), item['locator']['row']): item['rowId']
                          for item in lookup.values() if 'row' in item['locator']}
        self.groups = []
        self.extra = []
        self.conflicts = []
        self.product_count = 0

    def cite(self, material, locator, text):
        key = (material['materialId'], tuple(sorted(locator.items())), text)
        if key not in self.keys:
            while f'ev-{self.next_id}' in self.ids:
                self.next_id += 1
            eid = f'ev-{self.next_id}'
            self.next_id += 1
            self.ids.add(eid)
            self.keys[key] = eid
            self.evidence.append({'evidenceId':eid, 'materialId':material['materialId'],
                                  'sourceSha256':material['sha256'], 'locator':locator, 'quote':text})
        return self.keys[key]

    def cell(self, doc, cell):
        return self.cite(doc, cell['locator'], cell['text'])

    def user_field(self, name):
        value = self.form.get(name)
        if not value:
            return dict(MISSING)
        text = value if isinstance(value,str) else encode(value).decode()
        return _field(value,[self.cite(self.manual, {'kind':'form','fieldPath':name}, text[:500])])

    def product(self, doc, row, upc, name, *, internal=None, related=()):
        loc = row['locator']
        source = self.positions.get((doc['materialId'], loc.get('sheet'), loc['row']))
        if not source:
            return None
        self.product_count += 1
        return {'rowId': f'row-{self.product_count}', 'sourceRowIds':[source],
                'rawUpc':upc['text'] if upc else None, 'rawName':name['text'] if name else None,
                'rawInternalCode':internal['text'] if internal else None,'rawSpec':None,
                'matchedProductId':None,'candidates':[],'matchKind':'unmatched',
                'evidenceIds':list(dict.fromkeys(self.cell(doc,c) for c in (upc,name,internal,*related) if c))}

    def add(self, fields, products=(), *, brands=None, method=None):
        identifier = f'candidate-{len(self.groups)+1}'
        data = {key:dict(MISSING) for key in FIELD_NAMES}
        data['companyBearing'] = dict(MISSING) | {'value':{'state':'unknown','evidenceIds':[]}}
        data.update(fields)
        self.groups.append({'candidateId':identifier,'fields':data,'productRows':list(filter(None, products))})
        self.extra.append({'candidateId':identifier,'brandNames':brands or dict(MISSING),
                           'activityMethod':method or dict(MISSING)})

    def finish(self, *, warnings=(), distinct_source_rows=None):
        coverage_count = self.product_count if distinct_source_rows is None else distinct_source_rows
        if distinct_source_rows is not None:
            observed = {rid for group in self.groups for row in group['productRows'] for rid in row['sourceRowIds']}
            if len(observed) != distinct_source_rows:
                raise ValueError('Inconsistent matrix source-row coverage')
        if (not self.groups or len(self.evidence)>6000
                or coverage_count > self.metadata['coverage']['sourceRows']):
            return self.result,self.metadata
        missing = {key for group in self.groups for key,val in group['fields'].items() if val['status']=='missing'}
        return self.result | {'candidates':self.groups,'evidence':self.evidence,
                              'missingFields':[{'field':k,'reasonCode':'NOT_PROVIDED'} for k in sorted(missing)],
                              'conflicts':self.conflicts,'warnings':self.result['warnings']+list(warnings),
                              'outcome':'needs_confirmation'}, self.metadata | {
                                  'candidateMetadata':self.extra,'coverage':self.metadata['coverage'] | {
                                      'productSourceRows':coverage_count}}
