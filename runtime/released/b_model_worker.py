"""Run a pinned one-shot Agent in a bounded child process, with no automatic replay."""
import hashlib
import json
import os
from pathlib import Path
import selectors
import subprocess
import sys
import threading
import time

from b_model_recognition import CONTRACT, PREFIX, ProviderFailure
from b_state import canon

SETTINGS_ENV = 'JIATAN_INTAKE_DEEPSEEK_SETTINGS'
MANIFEST_SHA256 = 'ff76fe2f57b9c77251a5d98aee239b948c519f956e50a40fa85da2008c809edb'
PROMPT_VERSION = 'activity-model-recognition-prompt/1.0.0'
ERRORS = {
    'UNAVAILABLE', 'INPUT_INVALID', 'INVALID_OUTPUT', 'TIMEOUT',
    'PROVIDER_AUTH', 'PROVIDER_QUOTA', 'PROVIDER_UNAVAILABLE',
}
OUTPUT_LIMIT = 2 * 1024 * 1024
INPUT_LIMIT = 256 * 1024


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ModelWorker:
    contract=CONTRACT
    prefix=PREFIX
    manifest_sha256=MANIFEST_SHA256
    prompt_version=PROMPT_VERSION
    module="worker"
    settings_env = SETTINGS_ENV
    output_limit = OUTPUT_LIMIT
    output_limit_reason = 'INVALID_OUTPUT'

    def __init__(self, directory, settings):
        self.directory = Path(directory)
        self.settings = str(settings) if settings else ''

    def pinned(self):
        manifest = self.directory / 'MANIFEST.json'
        try:
            if file_hash(manifest) != self.manifest_sha256:
                return False
            sources = json.loads(manifest.read_bytes())['files']
            return all(file_hash(self.directory / name) == expected for name, expected in sources.items())
        except (OSError, ValueError, KeyError):
            return False

    def available(self):
        if not Path(self.settings).is_absolute() or not self.pinned():
            return False
        try:
            state = self.invoke(['--status'], b'', threading.Event(), time.monotonic() + 2)
            return (set(state) == {'enabled', 'model', 'promptVersion'} and state['enabled'] is True
                    and isinstance(state['model'], str) and state['promptVersion'] == self.prompt_version)
        except ProviderFailure:
            return False

    def recognize(self, request, stop, deadline):
        if not self.pinned():
            raise ProviderFailure('UNAVAILABLE')
        payload = canon({'contractVersion': self.contract, 'consent': True, **request}).encode()
        if len(payload) > INPUT_LIMIT:
            raise ProviderFailure('INPUT_INVALID')
        result = self.invoke([], payload, stop, deadline)
        if (set(result) != {'model', 'thinking', 'promptVersion', 'groups'}
                or result['promptVersion'] != self.prompt_version):
            raise ProviderFailure('INVALID_OUTPUT')
        metadata = {key: result[key] for key in ('model', 'thinking', 'promptVersion')}
        return metadata, {'groups': result['groups']}

    def invoke(self, args, payload, stop, deadline):
        if stop.is_set():
            raise ProviderFailure('INTERRUPTED')
        if time.monotonic() >= deadline:
            raise ProviderFailure('TIMEOUT')
        # The subprocess inherits neither credentials nor proxy/CA overrides.
        env = {self.settings_env: self.settings}
        try:
            proc = subprocess.Popen([sys.executable, '-I', '-B', '-c',
                                    'import runpy,sys; sys.path.insert(0,sys.argv.pop(1)); '
                                    'runpy.run_module(sys.argv.pop(1),run_name="__main__")',
                                    str(self.import_root()), self.module, *args],
                                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                    cwd=self.directory, env=env)
        except OSError:
            raise ProviderFailure('UNAVAILABLE') from None
        output, offset = bytearray(), 0
        try:
            with selectors.DefaultSelector() as selector:
                for stream, event in ((proc.stdin, selectors.EVENT_WRITE), (proc.stdout, selectors.EVENT_READ)):
                    os.set_blocking(stream.fileno(), False)
                    selector.register(stream, event)
                while selector.get_map():
                    remaining = self.remaining(stop, deadline)
                    for key, _ in selector.select(min(0.05, remaining)):
                        stream = key.fileobj
                        if stream is proc.stdin:
                            try:
                                offset += os.write(stream.fileno(), payload[offset:offset + 65536])
                            except BrokenPipeError:
                                offset = len(payload)
                            if offset < len(payload):
                                continue
                        else:
                            chunk = os.read(stream.fileno(), 65536)
                            output.extend(chunk)
                            if len(output) > self.output_limit:
                                raise ProviderFailure(self.output_limit_reason)
                            if chunk:
                                continue
                        selector.unregister(stream)
                        stream.close()
            while proc.poll() is None:
                time.sleep(min(0.01, self.remaining(stop, deadline)))
            if proc.returncode != 0:
                raise ProviderFailure('INVALID_OUTPUT')
            return self.decode(output)
        except (OSError, ValueError):
            raise ProviderFailure('INVALID_OUTPUT') from None
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.wait()
            proc.stdin.close()
            proc.stdout.close()

    @staticmethod
    def remaining(stop, deadline):
        if stop.is_set():
            raise ProviderFailure('INTERRUPTED')
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ProviderFailure('TIMEOUT')
        return remaining

    def import_root(self):
        return self.directory

    def decode(self, output):
        def unique(pairs):
            value = dict(pairs)
            if len(value) != len(pairs):
                raise ValueError('duplicate keys')
            return value

        def invalid_constant(_value):
            raise ValueError('invalid number')

        try:
            wire = json.loads(output, object_pairs_hook=unique, parse_constant=invalid_constant)
        except (ValueError, RecursionError):
            raise ProviderFailure('INVALID_OUTPUT') from None
        if not isinstance(wire, dict) or wire.get('contractVersion') != self.contract:
            raise ProviderFailure('INVALID_OUTPUT')
        if set(wire) == {'contractVersion', 'ok', 'result'} and wire['ok'] is True and isinstance(wire['result'], dict):
            return wire['result']
        if set(wire) == {'contractVersion', 'ok', 'error'} and wire['ok'] is False and isinstance(wire['error'], dict):
            code = wire['error'].get('code')
            if isinstance(code, str) and code.startswith(self.prefix) and code[len(self.prefix):] in ERRORS:
                raise ProviderFailure(code[len(self.prefix):])
        raise ProviderFailure('INVALID_OUTPUT')
