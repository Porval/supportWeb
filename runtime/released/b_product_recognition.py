"""Version-bound, read-only product rechecks; never alters adopted business data."""
import base64
from collections import Counter
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

from b_state import canon, fingerprint
from runtime_bindings import matches_upstream
from b_schema import check
from business_agent.data_agent_catalog import catalog as company
from business_agent.data_agent_catalog.brand_evidence import product_brand_evidence
from b_agent_product_contract import validate_product_output
from business_agent.activity_price_evidence.evidence import extract_price_evidence
from business_agent.activity_price_evidence_108 import model_prices
from business_agent.activity_model_first.child_workbook_rules import child_workbook_rule

CONTRACT = 'activity-product-recognition/1.0'
RELEASE = Path(__file__).resolve().parent
WORKER = RELEASE / 'business_agent/activity_intake/worker.py'
SCHEMAS = WORKER.parent / 'schemas'
MAX_OUTPUT = 12 * 1024 * 1024


def with_manual_products(result,version,candidate_id):
    products=version.get('snapshot',{}).get('manualCorrections',{}).get('products',[])
    if not products or result.get('status')!='ready':
        return result
    rows=[{'rowId':item['rowId'],'candidateId':candidate_id,'rawUpc':item['productSnapshot'].get('upc'),
           'rawInternalCode':item['internalCode'],'rawName':item['productSnapshot']['name'],
           'rawSpec':item['productSnapshot'].get('spec'),'materialMatchKind':'manual_selected',
           'directoryStatus':'manual_selected','matchedInternalCode':item['internalCode'],
           'candidates':[item['productSnapshot']],'uniqueUpcHit':False,
           'sourceReferences':[item['sourceReference']],'adoptionStatus':'manually_confirmed',
           'activityPriceCandidate':{'status':'unavailable','amountDecimal':None},
           **({'signupPriceCandidate':{'status':'needs_review','amountDecimal':item['signupPrice'],
                                       'currency':None,'quantityBasis':None,
                                       'source':'submitter_reviewed_original'}} if item.get('signupPrice') else {})}
          for item in products]
    summary=result['summary']|{'sourceRows':result['summary']['sourceRows']+len(rows),
                              'manuallyAdded':len(rows)}
    return result|{'rows':[*result['rows'],*rows],'summary':summary,
                   'derivedAnalysisDigest':'sha256:'+fingerprint([result['derivedAnalysisDigest'],products])}


class ProductRecognition:
    def __init__(self, source, directory):
        self.source = Path(source)
        self.directory = Path(directory)
        self.cache = self.directory / 'company-upc.json'
        self.stop = threading.Event()
        self.thread = None
        self.guard = threading.BoundedSemaphore(1)
        self.price_sha = hashlib.sha256((RELEASE / 'business_agent/activity_price_evidence/evidence.py').read_bytes()).hexdigest()
        self.worker_sha = hashlib.sha256((WORKER.parent / 'MANIFEST.json').read_bytes()).hexdigest()
        self.identity_rule = company.IDENTITY_RULE_VERSION
        self.catalog_sha = hashlib.sha256(Path(company.__file__).read_bytes()).hexdigest()
        self.model_price_sha = hashlib.sha256(Path(model_prices.__file__).read_bytes()).hexdigest()

    def refresh(self):
        try:
            return company.sync(self.source, self.cache)
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return {'status': 'unavailable'}

    def start(self):
        self.refresh()
        def loop():
            while not self.stop.wait(60):
                self.refresh()
        self.thread = threading.Thread(target=loop, name='company-upc-daily', daemon=True)
        self.thread.start()

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=2)

    def wire(self, operation, payload, deadline):
        request_id = 'product-recheck-' + fingerprint(payload)
        request = {'contractVersion': 'activity-intake/1.0', 'requestId': request_id,
                   'operation': operation, 'payload': payload}
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('recognition deadline')
        process = subprocess.run([sys.executable, '-B', str(WORKER)],
                                 input=canon(request).encode(), stdout=subprocess.PIPE,
                                 stderr=subprocess.DEVNULL, timeout=remaining, check=True)
        if len(process.stdout) > MAX_OUTPUT:
            raise ValueError('recognition output too large')
        response = json.loads(process.stdout)
        if response.get('ok') is not True or response.get('requestId') != request_id:
            raise ValueError('invalid worker response')
        result = response['result']
        check(result, json.loads((SCHEMAS / ('document.schema.json' if operation == 'decode' else 'analysis.schema.json')).read_text()))
        return result

    def _reanalyze(self, db, source, originals, input_digest, revision, deadline):
        documents = []
        for mid in source['allMaterialIds']:
            if mid == source['formMaterialId']:
                continue
            material = originals[mid]
            document = self.wire('decode', {
                'materialId': mid, 'filename': material['filename'], 'mediaType': material['media_type'],
                'sha256': material['sha256'], 'contentBase64': base64.b64encode(material['content']).decode()}, deadline)
            if document['materialId'] != mid or document['sha256'] != material['sha256']:
                raise ValueError('document binding mismatch')
            documents.append(document)
        catalog = {'revision': json.loads(db.execute("SELECT value FROM b_meta WHERE key='catalogRevision'").fetchone()[0])}
        for table, fields in [('products', ('id','internalCode','upc','name','spec','brandId')), ('brands', ('id','name'))]:
            catalog[table] = [{k: value.get(k) for k in fields} for (body,) in db.execute('SELECT body FROM '+table) for value in [json.loads(body)]]
        analysis = self.wire('analyze', {'contractVersion': 'activity-intake/1.0',
            'inputRevision': revision, 'inputDigest': input_digest,
            'manualFields': {'materialId': source['formMaterialId'],
                'sha256': originals[source['formMaterialId']]['sha256'], 'fields': source['form']},
            'documents': documents, 'catalogSnapshot': catalog}, deadline)
        if analysis['inputDigest'] != input_digest or analysis['inputRevision'] != revision or analysis['catalogRevision'] != catalog['revision']:
            raise ValueError('analysis binding mismatch')
        located = {(d['materialId'], canon(cell['locator']), cell['text'][:500])
                   for d in documents for cell in d['textBlocks'] + [c for row in d['tableRows'] for c in row['cells']]}
        for evidence in analysis['evidence']:
            mid = evidence['materialId']
            if mid not in originals or evidence['sourceSha256'] != originals[mid]['sha256']:
                raise ValueError('evidence source mismatch')
            if mid != source['formMaterialId'] and (mid, canon(evidence['locator']), evidence['quote']) not in located:
                raise ValueError('evidence locator mismatch')
        return analysis, documents

    def view(self, db, version, projection):
        unavailable = {'contractVersion': CONTRACT, 'status': 'unavailable',
                       'adoptionStatus': 'not_applied', 'rows': [], 'reasonCode': 'SOURCE_UNAVAILABLE'}
        inputs = projection.get('sourceInputs', [])
        # A recheck may not expose another activity from a shared multi-group run.
        if projection.get('sourceAvailability', {}).get('status') != 'available' or len(inputs) != 1:
            return unavailable
        source_view = inputs[0]
        analyses=source_view.get('analyses', [])
        primary=version.get('snapshot',{}).get('primaryRecognitionBinding') or {}
        merged=len(analyses)>1 and set(primary.get('candidateIds',[]))=={a['candidateId'] for a in analyses} and all(a['runId']==primary.get('runId') for a in analyses)
        if len(analyses)!=1 and not merged:
            return unavailable | {'reasonCode':'GROUP_BINDING_UNAVAILABLE'}
        if not self.guard.acquire(blocking=False):
            return unavailable | {'reasonCode': 'RECOGNITION_BUSY'}
        try:
            if not merged:
                return with_manual_products(self._view(db,version,source_view,unavailable),version,analyses[0]['candidateId'])
            results=[self._view(db,version,source_view|{'analyses':[item]},unavailable) for item in analyses]
            if any(r.get('status')!='ready' for r in results):return unavailable|{'reasonCode':'GROUP_BINDING_UNAVAILABLE'}
            first=results[0];rows=[row for result in results for row in result['rows']]
            if len({(r['candidateId'],r['rowId']) for r in rows})!=len(rows):return unavailable|{'reasonCode':'GROUP_BINDING_UNAVAILABLE'}
            binding={k:v for k,v in first['binding'].items() if k!='originalCandidateId'}
            binding.update(originalCandidateIds=primary['candidateIds'],primaryRecognitionDigest=primary['recognitionDigest'])
            return first|{'rows':rows,'binding':binding,'summary':{key:sum(r['summary'][key] for r in results) for key in first['summary']}}

        except (OSError, ValueError, KeyError, TypeError, AttributeError, subprocess.SubprocessError, TimeoutError):
            return unavailable | {'reasonCode': 'RECOGNITION_UNAVAILABLE'}
        finally:
            self.guard.release()

    def submission_view(self, db, actor, submission_id, auth, *, master_catalog=None):
        """Read the current submission's company matches without creating an activity."""
        from account_primitives import APIError
        from b_domain import (owns_submission, can_material, latest_run_id,
                              verify_declaration_binding)
        sub = owns_submission(db, actor, submission_id, auth)
        unavailable = {'contractVersion': 'submission-product-recognition/1.0',
                       'status': 'unavailable', 'adoptionStatus': 'not_applied',
                       'groups': [], 'reasonCode': 'SOURCE_UNAVAILABLE'}
        run_id = latest_run_id(db, sub['id'], sub['revision'])
        run = db.execute('SELECT * FROM analysis_runs WHERE id=?', (run_id,)).fetchone()
        if not run or run['state'] not in ('succeeded', 'partial'):
            return unavailable | {'reasonCode': 'CURRENT_ANALYSIS_UNAVAILABLE'}
        if not self.guard.acquire(blocking=False):
            return unavailable | {'reasonCode': 'RECOGNITION_BUSY'}
        try:
            revision = db.execute('SELECT * FROM submission_revisions WHERE submission_id=? AND revision=?',
                                  (sub['id'], sub['revision'])).fetchone()
            source = json.loads(revision['payload'])
            if not all(can_material(db, actor, mid, auth) for mid in source['allMaterialIds']):
                return unavailable | {'reasonCode': 'MATERIAL_READ_FORBIDDEN'}
            runtime = json.loads(run['payload'])
            analysis = runtime['result']
            # Pending projections reuse the saved, validated analysis. Opening a
            # result page must never start a worker, retry, or create business data.
            if analysis.get('engineVersion') != '1.0.8' and analysis.get('engine') != 'deepseek_model_first_v1':
                return unavailable | {'reasonCode': 'SAVED_ANALYSIS_UNSUPPORTED'}
            originals = verify_declaration_binding(db, sub, run, analysis, runtime['analysisDigest'], source)
            material_names = {mid: item['filename'] for mid, item in originals.items()}
            methods = {item['candidateId']: item.get('activityMethod')
                       for item in (runtime.get('agentRecognition') or {}).get('candidateMetadata', [])}
            master_catalog = master_catalog or {}
            master_brands = master_catalog.get('brands', ())
            master_products = master_catalog.get('products', ())
            if master_brands and master_products:
                # Check uniqueness against all persisted products, even if a duplicate is outside the actor's scope.
                unique_skus = {tuple(row) for row in db.execute("""
                    SELECT json_extract(body, '$.internalCode'), json_extract(body, '$.upc')
                    FROM products GROUP BY 1, 2 HAVING COUNT(*) = 1
                """)}
                master_products = [product for product in master_products
                                   if (product.get('internalCode'), product.get('upc')) in unique_skus]
            groups = []
            for candidate in analysis['candidates']:
                projection = {'submissionId': sub['id'], 'inputRevision': sub['revision'],
                    'inputDigest': run['input_digest'],
                    'materials': [{'materialId': mid, 'available': True} for mid in source['allMaterialIds']],
                    'analyses': [{'runId': run['id'], 'candidateId': candidate['candidateId'],
                                  'analysisDigest': runtime['analysisDigest']}]}
                group_unavailable = {'contractVersion': CONTRACT, 'status': 'unavailable',
                                     'adoptionStatus': 'not_applied', 'rows': [], 'reasonCode': 'SOURCE_UNAVAILABLE'}
                result = self._view(db, None, projection, group_unavailable)
                if result['status'] == 'ready':
                    result = result | {'productBrandEvidence': product_brand_evidence(
                        result['rows'], products=master_products, brands=master_brands)}
                    if (analysis.get('engine') == 'deepseek_model_first_v1' and len(analysis['candidates']) == 1
                            and candidate['fields']['rulesText']['status'] == 'missing'):
                        observed = child_workbook_rule(analysis['materialResults'], candidate, analysis['evidence'],
                                                       method=methods.get(candidate['candidateId']),
                                                       material_names=material_names)
                        if observed:
                            result['observedRules'] = observed
                groups.append({'candidateId': candidate['candidateId'], **result})
            return {'contractVersion': unavailable['contractVersion'],
                'status': 'ready' if all(g['status'] == 'ready' for g in groups) else 'partial',
                'adoptionStatus': 'not_applied',
                'binding': {'submissionId': sub['id'], 'inputRevision': sub['revision'],
                            'inputDigest': run['input_digest'], 'runId': run['id'],
                            'analysisDigest': runtime['analysisDigest']}, 'groups': groups}
        except (APIError, OSError, ValueError, KeyError, TypeError, AttributeError,
                subprocess.SubprocessError, TimeoutError):
            return unavailable | {'reasonCode': 'RECOGNITION_UNAVAILABLE'}
        finally:
            self.guard.release()

    def _view(self, db, version, source_view, unavailable):
        record = company.read_cache(self.cache)
        source_check = source_view['analyses'][0]
        run = db.execute('SELECT * FROM analysis_runs WHERE id=?', (source_check['runId'],)).fetchone()
        source_row = db.execute('SELECT * FROM submission_revisions WHERE submission_id=? AND revision=?',
            (source_view['submissionId'], source_view['inputRevision'])).fetchone()
        if (not run or not source_row or run['input_digest'] != source_view['inputDigest']
                or source_row['input_digest'] != run['input_digest']
                or run['submission_id'] != source_view['submissionId']
                or run['input_revision'] != source_view['inputRevision']):
            return unavailable
        source = json.loads(source_row['payload'])
        visible = {m['materialId'] for m in source_view['materials'] if m.get('available')}
        if not set(source['allMaterialIds']) <= visible:
            return unavailable | {'reasonCode': 'MATERIAL_READ_FORBIDDEN'}
        originals = {mid: db.execute('SELECT * FROM material_blobs WHERE id=?', (mid,)).fetchone() for mid in source['allMaterialIds']}
        if any(not m or hashlib.sha256(m['content']).hexdigest() != m['sha256'] for m in originals.values()):
            return unavailable | {'reasonCode': 'MATERIAL_HASH_MISMATCH'}
        old = json.loads(run['payload'])['result']
        model_group = old.get('engine') == 'deepseek_model_first_v1' and old.get('modelUsed') is True
        current_group = old.get('engineVersion') == '1.0.8' or model_group
        if not current_group and len(old['candidates']) != 1:
            return unavailable | {'reasonCode': 'MULTI_GROUP_RECHECK_REQUIRED'}
        worker_sha, price_sha = self.worker_sha, self.price_sha
        if current_group:
            runtime = json.loads(run['payload'])
            if model_group:
                from b_model_intake import source_for_hash
                worker=source_for_hash(runtime.get('engineSourceSha256'))
                if worker is None:return unavailable | {'reasonCode':'GROUP_BINDING_UNAVAILABLE'}
            else:worker=RELEASE/'business_agent/activity_intake_108'
            paths = [((p.relative_to(worker).as_posix() if model_group else 'business_agent/activity_intake/' + p.relative_to(worker).as_posix()),
                      hashlib.sha256(p.read_bytes()).hexdigest()) for p in sorted(worker.rglob('*.py'))]
            if ((runtime.get('engineSourceSha256') != fingerprint(paths) and not matches_upstream(worker, runtime.get('engineSourceSha256')))
                    or runtime.get('analysisDigest') != 'sha256:' + fingerprint(old)
                    or old['inputDigest'] != run['input_digest']
                    or old['inputRevision'] != run['input_revision']):
                return unavailable | {'reasonCode': 'GROUP_BINDING_UNAVAILABLE'}
            check(old, json.loads((worker / 'schemas/analysis.schema.json').read_text()))
            if sum(c['candidateId'] == source_check['candidateId'] for c in old['candidates']) != 1:
                return unavailable | {'reasonCode': 'GROUP_BINDING_UNAVAILABLE'}
            worker_sha = hashlib.sha256((worker / ('SHA256SUMS' if model_group else 'MANIFEST.json')).read_bytes()).hexdigest()
            price_sha = hashlib.sha256((RELEASE / 'business_agent/activity_price_evidence_108/evidence.py').read_bytes()).hexdigest()
        binding = {**({'activityId': version['activityId'], 'versionId': version['id']} if version else {}),
            'submissionId': run['submission_id'], 'inputRevision': run['input_revision'],
            'inputDigest': run['input_digest'], 'originalAnalysisRunId': run['id'],
            'originalCandidateId': source_check['candidateId'], 'workerSha256': worker_sha, 'priceModuleSha256': price_sha,
            'catalogExportSha256': record['exportSha256'],
            'materials': [{'materialId': mid, 'sha256': m['sha256']} for mid,m in originals.items()]}
        # Keep the existing business binding intact: saved registration drafts
        # depend on it. The additive projection has an independent cache version.
        cache_rule = {'catalogIdentityRuleVersion': self.identity_rule, 'catalogModuleSha256': self.catalog_sha,
                      'originalAnalysisDigest': 'sha256:' + fingerprint(old),
                      'agentProductContract': 'jiatan-data-agent-upc/1.1'}
        if model_group:
            cache_rule['modelPriceEvidenceSha256'] = self.model_price_sha
        key = fingerprint({'binding': binding, **cache_rule})
        cached = self.directory / ('identity-recognition-' + key + '.json')
        if cached.is_file() and not cached.is_symlink():
            saved = json.loads(cached.read_text())
            if (saved.get('binding') == binding and saved.get('cacheRule') == cache_rule
                    and saved.get('resultDigest') == fingerprint(saved.get('result'))):
                result = saved['result']
                result['catalog']['lastFetchedAt'] = record['fetchedAt']
                result['catalog']['syncDay'] = record['syncDay']
                result['catalog']['syncStale'] = record['syncDay'] < datetime.now(company.CHINA).date().isoformat()
                return result
        if current_group:
            analysis, documents = old, old['materialResults']
            located = {(d['materialId'], canon(c['locator']), c['text'][:500])
                       for d in documents for c in d['textBlocks'] + [c for r in d['tableRows'] for c in r['cells']]}
            if any(d['materialId'] not in originals or d['sha256'] != originals[d['materialId']]['sha256'] for d in documents):
                return unavailable | {'reasonCode': 'MATERIAL_HASH_MISMATCH'}
            for evidence in analysis['evidence']:
                mid = evidence['materialId']
                if (mid not in originals or evidence['sourceSha256'] != originals[mid]['sha256']
                        or mid != source['formMaterialId'] and not (any(d['materialId']==mid and c['locator']==evidence['locator'] and isinstance(evidence['quote'],str) and bool(evidence['quote']) and evidence['quote'] in c['text'] for d in documents for c in d['textBlocks']+[cell for rr in d['tableRows'] for cell in rr['cells']]) if model_group else (mid,canon(evidence['locator']),evidence['quote']) in located)):
                    return unavailable | {'reasonCode': 'GROUP_BINDING_UNAVAILABLE'}
        else:
            analysis, documents = self._reanalyze(db, source, originals, run['input_digest'], run['input_revision'], time.monotonic()+8)
            if len(analysis['candidates']) != 1:
                return unavailable | {'reasonCode': 'GROUP_RECHECK_REQUIRED'}
        selected_candidates = ([c for c in analysis['candidates'] if c['candidateId'] == source_check['candidateId']]
                               if current_group else analysis['candidates'])
        # Match from one pinned cache snapshot, even if the daily writer swaps its file.
        pinned = self.directory / ('snapshot-' + record['exportSha256'] + '.json')
        if not pinned.exists():
            company._replace_cache(pinned, record)
        companion = company.match_analysis_standardized(pinned, analysis, verify_catalog_identity=True)
        if companion['inputDigest'] != analysis['inputDigest'] or companion['catalog']['exportSha256'] != record['exportSha256']:
            raise ValueError('catalog analysis binding mismatch')
        matches = validate_product_output(companion, analysis, record['exportSha256'])
        # An optional price failure cannot erase an authorized product recheck.
        try:
            extractor = extract_price_evidence
            if model_group:
                extractor = model_prices.extract_model_price_evidence
            elif current_group:
                from business_agent.activity_price_evidence_108.evidence import extract_price_evidence as extractor
            prices = extractor(documents, analysis)
            if prices['inputDigest'] != analysis['inputDigest'] or prices['inputRevision'] != analysis['inputRevision']:
                raise ValueError('price input binding mismatch')
            price_rows = {(r['candidateId'], r['rowId']): r for r in prices['rows']}
        except (ValueError, KeyError, TypeError, AttributeError):
            price_rows = {}
        evidence = {e['evidenceId']: e for e in analysis['evidence']}
        rows = []
        for candidate in selected_candidates:
            for row in candidate['productRows']:
                match = matches[(candidate['candidateId'], row['rowId'])]
                refs = [evidence[eid] for eid in row['evidenceIds']]
                price = price_rows.get((candidate['candidateId'], row['rowId']))
                signup = price.get('signupPrice') if price else None
                activity_price = ({key: value for key, value in price.items() if key != 'signupPrice'}
                                  if price else {'status': 'unavailable', 'amountDecimal': None,
                                                'issues': ['PRICE_EVIDENCE_UNAVAILABLE'], 'observations': []})
                if signup is not None:
                    activity_price['modelPriceEvidenceSha256'] = self.model_price_sha
                    signup = signup | {'modelPriceEvidenceSha256': self.model_price_sha}
                rows.append({'rowId': row['rowId'], 'candidateId': candidate['candidateId'],
                    'rawUpc': row['rawUpc'], 'rawInternalCode': row['rawInternalCode'],
                    'rawName': row['rawName'], 'rawSpec': row.get('rawSpec'),
                    'materialMatchKind': row['matchKind'], 'directoryStatus': match['status'],
                    'matchedInternalCode': match['internalCode'], 'candidates': match['candidates'],
                    **({'catalogIdentity': match['catalogIdentity']} if 'catalogIdentity' in match else {}),
                    'uniqueUpcHit': match['uniqueUpcHit'],
                    'sourceReferences': refs, 'adoptionStatus': 'not_applied',
                    'activityPriceCandidate': activity_price,
                    **({'signupPriceCandidate': signup} if signup is not None else {})})
        counts = Counter(r['directoryStatus'] for r in rows)
        result = {'contractVersion': CONTRACT, 'status': 'ready', 'adoptionStatus': 'not_applied',
            'binding': binding, 'engineVersion': analysis['engineVersion'],
            'derivedAnalysisDigest': 'sha256:' + fingerprint(analysis),
            'catalogIdentityRuleVersion': companion['catalogIdentityRuleVersion'],
            'generatedAt': datetime.now(company.CHINA).isoformat(),
            'catalog': companion['catalog'] | {'syncStale': record['syncDay'] < datetime.now(company.CHINA).date().isoformat()},
            'summary': {'sourceRows': len(rows), 'uniqueUpcHits': sum(r['uniqueUpcHit'] for r in rows),
                'confirmedIdentityCount': sum(r.get('catalogIdentity', {}).get('status') == 'exact_internal_code_upc' for r in rows),
                'confirmedMatches': counts['exact_upc'], 'needsReview': len(rows)-counts['exact_upc'],
                'unmatched': counts['unmatched'], 'materialConflicts': sum(r['materialMatchKind']=='conflict' for r in rows)},
            'rows': rows, 'warnings': analysis['warnings']}
        if version is not None:
            company._replace_cache(cached, {'binding': binding, 'cacheRule': cache_rule, 'resultDigest': fingerprint(result), 'result': result})
        return result

    def remove_submission_cache(self, submission_id):
        for path in self.directory.glob('identity-recognition-*.json'):
            if not path.is_file() or path.is_symlink():
                continue
            try:
                binding = json.loads(path.read_text()).get('binding', {})
                if binding.get('submissionId') == submission_id and not binding.get('versionId'):
                    path.unlink(missing_ok=True)
            except (OSError, ValueError, AttributeError):
                # The deleted content is inaccessible through the API regardless.
                continue
