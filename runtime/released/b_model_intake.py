"""Bounded model-first Agent transport and standardized receipt validation."""
from contextlib import suppress
import hashlib
import json
import os
import socket
import struct
import threading
import time
from pathlib import Path

from b_model_worker import ModelWorker, file_hash
from b_model_recognition import ProviderFailure
from b_schema import check
from b_state import canon, fingerprint
from b_deepseek_local import authentication, generation, record_authentication

CONTRACT = 'activity-model-intake/1.0'
ENGINE = 'deepseek_model_first_v1'
ENGINE_VERSION = '2.1.0'
PROMPT_VERSION = 'activity-model-first-prompt/1.1.1'


class ModelIntakeWorker(ModelWorker):
    contract = 'activity-intake/1.0'
    module = 'business_agent.activity_model_first.worker'
    prompt_version = PROMPT_VERSION
    manifest_sha256 = '4f2465ab8049a31caaf232e6fbe513b0683fd8b84ab10a1b3c0f4330ed6c37db'
    output_limit = 32 * 1024 * 1024
    output_limit_reason = 'OUTPUT_LIMIT'

    def import_root(self):
        return self.directory.parent.parent

    def pinned(self):
        try:
            manifest = self.directory / 'SHA256SUMS'
            if file_hash(manifest) != self.manifest_sha256:
                return False
            for line in manifest.read_text().splitlines():
                expected, name = line.split('  ', 1)
                path = self.directory / name
                if path.is_symlink() or not path.resolve().is_relative_to(self.directory.resolve()) or file_hash(path) != expected:
                    return False
            return True
        except (OSError, ValueError):
            return False

    def invoke(self, args, payload, stop, deadline):
        endpoint = os.environ.get('JIATAN_ACTIVITY_AGENT_SOCKET')
        if not endpoint:
            return super().invoke(args, payload, stop, deadline)
        raw = b'status' if args == ['--status'] else payload
        try:
            with socket.socket(socket.AF_UNIX) as stream:
                stream.settimeout(min(2, self.remaining(stop, deadline)))
                stream.connect(endpoint)
                stream.sendall(struct.pack('>I', len(raw)) + raw)

                def read(count):
                    result = bytearray()
                    while len(result) < count:
                        stream.settimeout(min(.25, self.remaining(stop, deadline)))
                        try:
                            chunk = stream.recv(min(65536, count - len(result)))
                        except socket.timeout:
                            continue
                        if not chunk:
                            raise ProviderFailure('UNAVAILABLE')
                        result.extend(chunk)
                    return bytes(result)

                length, = struct.unpack('>I', read(4))
                if length <= 0 or length > self.output_limit:
                    raise ProviderFailure('INVALID_OUTPUT')
                return self.decode(read(length))
        except OSError:
            raise ProviderFailure('UNAVAILABLE') from None

    def available(self):
        if not self.pinned() or not Path(self.settings).is_absolute():
            return False
        try:
            wire = self.invoke(['--status'], b'', threading.Event(), time.monotonic() + 3)
            state = wire.get('result', {})
            return wire.get('ok') is True and state.get('enabled') is True and state.get('promptVersion') == PROMPT_VERSION
        except ProviderFailure:
            return False

    def capability(self):
        configured = self.available()
        state = authentication(Path(self.settings)) if configured else 'unchecked'
        return {'contractVersion': CONTRACT, 'provider': 'deepseek',
                'enabled': configured and state != 'rejected',
                'configurationStatus': 'readable' if configured else 'unavailable',
                'authenticationStatus': state}

    def source_hash(self):
        if not self.pinned():
            raise ProviderFailure('UNAVAILABLE')
        return fingerprint([(p.relative_to(self.directory).as_posix(), file_hash(p))
                            for p in sorted(self.directory.rglob('*.py'))])

    def recognize(self, request_id, payload, stop, deadline):
        if not self.pinned():
            raise ProviderFailure('UNAVAILABLE')
        raw = canon({'contractVersion': self.contract, 'requestId': request_id,
                     'operation': 'analyze', 'payload': payload}).encode()
        if len(raw) > 32 * 1024 * 1024:
            raise ProviderFailure('INPUT_INVALID')
        path = Path(self.settings)
        current = generation(path)
        wire = self.invoke([], raw, stop, deadline)
        if wire.get('requestId') != request_id:
            raise ProviderFailure('INVALID_OUTPUT')
        if wire.get('ok') is not True:
            code = (wire.get('error') or {}).get('code')
            if code in ('PROVIDER_AUTH', 'MODEL_RECOGNITION_PROVIDER_AUTH'):
                with suppress(OSError):
                    record_authentication(path, current, 'rejected')
            failure = ProviderFailure(code if isinstance(code, str) and len(code) <= 100 else 'INVALID_OUTPUT')
            failure.details = safe_error_details((wire.get('error') or {}).get('details'))
            raise failure
        if set(wire) != {'contractVersion', 'requestId', 'ok', 'result', 'metadata'}:
            raise ProviderFailure('INVALID_OUTPUT')
        with suppress(OSError):
            record_authentication(path, current, 'authorized')
        return wire['result'], wire['metadata']

    def decode(self, output):
        def unique(pairs):
            result = dict(pairs)
            if len(result) != len(pairs):
                raise ValueError('duplicate keys')
            return result
        def invalid(_):
            raise ValueError('non-finite JSON number')
        try:
            value = json.loads(output, object_pairs_hook=unique, parse_constant=invalid)
        except (ValueError, RecursionError):
            raise ProviderFailure('INVALID_OUTPUT') from None
        if not isinstance(value, dict) or value.get('contractVersion') != self.contract:
            raise ProviderFailure('INVALID_OUTPUT')
        return value


def validate_metadata(value, result, *, prompt_version=PROMPT_VERSION, verify_shared_sources=None, planned_batches=None):
    def require(ok):
        if not ok:
            raise ValueError('invalid model Agent metadata')
    required={'provider','model','thinking','promptVersion','coverage','candidateMetadata','inputScopeSha256'}
    require(isinstance(value, dict) and required <= set(value) <= required | {'providerAttempts'})
    require(value['provider'] == 'deepseek' and isinstance(value['model'], str) and bool(value['model']))
    require(value['thinking'] in ('enabled', 'disabled', 'unknown'))
    require(value['promptVersion'] == prompt_version == result['promptVersion'])
    require(isinstance(value['inputScopeSha256'],str) and len(value['inputScopeSha256'])==64 and all(c in '0123456789abcdef' for c in value['inputScopeSha256']))
    coverage = value['coverage']
    require(isinstance(coverage, dict) and set(coverage) == {'batches', 'sourceRows', 'classifiedRows', 'productSourceRows'})
    require(all(type(v) is int and v >= 0 for v in coverage.values()))
    require(coverage['batches'] > 0 and coverage['sourceRows'] == coverage['classifiedRows'])
    if 'providerAttempts' in value:
        attempts = value['providerAttempts']
        count = coverage['batches']
        require(type(attempts) is int and 1 <= count <= 256 and count <= attempts <= 2 * count)
        if planned_batches is not None:
            from business_agent.activity_model_first import large_shenjia
            from business_agent.activity_model_first.source import EXCEL_LAYOUT
            require(len(planned_batches) == count)
            if attempts > 32:
                layouts = {batch.get('layout') for batch in planned_batches}
                require(layouts == {large_shenjia.LAYOUT} or (count > 32 and layouts == {EXCEL_LAYOUT}))
        elif attempts > 32:
            # Confirmation reuses the stored and scope-verified receipt.
            from business_agent.activity_model_first.large_shenjia import layouts
            docs = result['materialResults']
            require((count > 32 and any(d.get('kind') == 'xlsx' for d in docs)) or bool(layouts(docs)))
    require(coverage['productSourceRows'] <= coverage['sourceRows'])
    source_ids=set()
    row_ids=set()
    shared=False
    for candidate in result['candidates']:
        candidate_sources=set()
        for row in candidate['productRows']:
            group_sources=row['sourceRowIds']
            require(isinstance(group_sources,list) and bool(group_sources))
            require(all(isinstance(source_id,str) and source_id for source_id in group_sources))
            require(isinstance(row['rowId'],str) and bool(row['rowId']) and row['rowId'] not in row_ids)
            require(len(group_sources)==len(set(group_sources)) and not candidate_sources.intersection(group_sources))
            row_ids.add(row['rowId'])
            candidate_sources.update(group_sources)
        shared=shared or bool(source_ids.intersection(candidate_sources))
        source_ids.update(candidate_sources)
    require(len(source_ids)==coverage['productSourceRows'])
    if shared:
        require(verify_shared_sources is not None and verify_shared_sources(result))
    items = value['candidateMetadata']
    require(isinstance(items, list))
    expected = {c['candidateId'] for c in result['candidates']}
    seen = set()
    from b_activity_method import valid_activity_method
    for item in items:
        require(isinstance(item, dict) and set(item) == {'candidateId', 'activityMethod', 'brandNames'})
        require(isinstance(item['candidateId'], str) and item['candidateId'] in expected and item['candidateId'] not in seen)
        evidence_ids={e['evidenceId'] for e in result['evidence']}
        for key in ('activityMethod','brandNames'):
            field=item[key]
            require(isinstance(field,dict) and set(field)=={'value','status','evidenceIds'})
            require(field['status'] in ('explicit','missing','conflict') and isinstance(field['evidenceIds'],list))
            require(all(isinstance(e,str) and e in evidence_ids for e in field['evidenceIds']))
        method=item['activityMethod']['value'];brands=item['brandNames']['value']
        require(method is None or valid_activity_method(method))
        require(brands is None or isinstance(brands,list) and all(isinstance(n,str) for n in brands))
        seen.add(item['candidateId'])
    require(seen == expected)
    return value


def safe_error_details(value):
    """Keep bounded diagnostics only; never retain raw provider text or secrets."""
    if not isinstance(value, dict):
        return {}
    result = {}
    for key in ('actualRows', 'maxRows', 'actualChars', 'maxChars', 'batchIndex', 'completionTokens', 'reasoningTokens', 'missingRowCount', 'visibleContentChars', 'attempt', 'providerAttempts'):
        item = value.get(key)
        if type(item) is int and 0 <= item <= (32_000_000 if key in ('actualChars','maxChars') else 1_000_000):
            result[key] = item
    import re
    for key in ('stage', 'reason', 'sourceRowId', 'field', 'finishReason'):
        item = value.get(key)
        if isinstance(item, str) and re.fullmatch(r'[A-Za-z0-9_.:-]{1,100}', item):
            result[key] = item
    return result


def source_for_hash(source_sha):
    """Read old model runs against their pinned source, without invoking any model."""
    root = Path(__file__).resolve().parent / 'business_agent'
    current = ModelIntakeWorker(root / 'activity_model_first', None)
    if current.pinned() and current.source_hash() == source_sha:
        return current.directory
    archives = {
        '0aa302459ac37dc5f11e5ebe0e9c481c2a28e9fc33ac229590ffee0c914d3ebb':
            ('activity_model_first_pre_excel_5000', 'b378139d77a55869f90e7b502180e89f1a65d03390aa474e2eb550dba55f41cd'),
        'f08282b41279a8bdfc2aebc6787a1cd2077bfde18fbc9acbc0bd4bfd9210ab10':
            ('activity_model_first_pre_horizontal_coupons', '4df2aedb133f3c59640b2fc3c8ca79bcf09eb97feb0b4581c00a9bd54a5617a4'),
        'faedcc5d778fecaf761afa3463148f032ce5b55f397c4df8fa8269b3eb596ba9':
            ('activity_model_first_pre_platforms', '787ff7a428539783445fe2700c97dc65df7cfe6bcdda997b76497badf71a80d2'),
        '70ab0a6f204fdae27aa796b92d982e658385e72430a7fdcbd8ca2bfbf1fd5ce6':
            ('activity_model_first_pre_single_shenjia', '9a22a675594cd134c8da356c09a669ac2227cc6c4a3720a13a114ccd4069047c'),
        'fcf0fdbfd128b460a51a134e52d824776dd8b714f685da60b83eda1e191323f6':
            ('activity_model_first_pre_small_cases', 'e617c49685338d1b4191144b05e819a044c47cc591a2c9989353580d7f2153ea'),
        '6e0ec046189359897db353347743bdf956691b8140b65901685776418f33d38f':
            ('activity_model_first_pre_six_repairs', 'c3ce850ee5a29098923a628a04db43f342fc13f6ad044396c0b6010d386ebd30'),
        'e09b844c6c6a33e3310ec811a4845ff93ba5a1f9720a89ede8a80bb4d5e582dc':
            ('activity_model_first_pre_coupon_tiers', '18cd21f85b3c503406f0c593c696ecbb968f8156045ce942b69e069eb4fef810'),
        '257c4d68801df58845fa60a7915424bcb0b1d42c1633087d75c7d78b78dc6c43':
            ('activity_model_first_pre_child_title', '936b6c076971c9c0bc2d4c299e652a6656175e836faca775a5b56bdf9aef3a76'),
        'ffedcb711b52f26d8b0dc41f89b9fa2951e9551a27d661b8b3f24e2fb291aacc':
            ('activity_model_first_pre_store_price', '513d4c42c6e06c91ad90caab0e69f79a1ffe1804f138c84a917f7e6289db9597'),
        '6c8b4de1a948d7d71c966dab5566613f381f0d942e52b73f68dcf9c54cbaace8':
            ('activity_model_first_pre_source_rule', 'f53cfb75261775e627e0d16dd57170d4bb806e95aefb58626da72f29721ade63'),
        '0de7e848ad6dda54170c5cfcaa56a5e4a9a15764785bc0824c06091539e9ac7e':
            ('activity_model_first_pre_recovery', '0f69f5dbc0eb3ff95d2bb67e2a33cfd1701901e9134c9bde9651a7913bb2acfe'),
        '33e2fdd141af70fada6aeb9247a9d6e98d3db7cec539c4e2a64afd7c54cf9007':
            ('activity_model_first_pre_case_sync', 'b020ee0d2f559c2fa28429374d61eac9514e3c2f6c6a0b9fd1cd01bc29b3afce'),
        'ad567f42d95177fcfffce7cb6d4ebab62e7a65b3f1fbe9beda6fe77b97c0c05c':
            ('activity_model_first_pre_subsidy_group', '3d52f80a6aa3ff8bda62bfe379e44107ae00ff5d1fc924b1f62d8e552acb6f2b'),
        'a0e5637910663044a83d8160af852ced3f9ff690beab1bada00d283914ba8561':
            ('activity_model_first_pre_submission_review', '1d6cce391c6eff0ff9bfd020b04d8da41dae2f43542a57d013a8bddfafa1ddac'),
        '126c4297e7b7b11f652e1e9f85def7b3ca56fe6d380c8ede40b74bd180729fa9':
            ('activity_model_first_pre_coupon', 'c3a0e64ecb4c4591ebaf693a77ea760c3418d8aa468cf65f3ce69dc42cc97c40'),
        'cddb8a58b2230315b4e689518d9834e815feae50c12f6cdf9f7a44abedbdf08b':
            ('activity_model_first_r3', '8620588cc71144ed2cad59be47b42ee6aeb135e014e1884b64b0f492ad3c668c'),
        '61f082d2f1b9bad8fc64b1e798b9dfec4d29628b25e575a5fd06f295646d9f5c':
            ('activity_model_first_r5', 'f91d5e150247b9c769807b51a1524ca8945a4e2f3df125affb1785eb9972670d'),
        '759d8ac3b2fbd32c8b5b96f9b9133dd6079bc95d269a0e1ee48c1b2f63f8abcb':
            ('activity_model_first_r6', '1df1b8fec1973505b88b414641fbcb3d4bdb1d5964fae48a2cc152cb4be6bdec'),
        '8a9c1ba8720071645d3627b40d0cb6c5d6b2081b810a1b82c5f7e94e87c8ff32':
            ('activity_model_first_r7', 'bc33447eb0bf1364bc010e8186ed022a0f5122c11a418f4b644d7712516cddcb'),
    }
    record = archives.get(source_sha)
    if record:
        name, pin = record
        previous = ModelIntakeWorker(root / name, None)
        previous.manifest_sha256 = pin
        if previous.pinned() and previous.source_hash() == source_sha:
            return previous.directory
    return None
