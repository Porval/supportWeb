"""Private original attachments and parsed caches, referenced by saved conversations."""

import base64
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat
import tempfile

from chat_attachments import MAX_BYTES, read_uploads
from conversation_store import ConversationUnavailable, actor_key, conversation_key

FILE_ID = re.compile(r'[0-9a-f]{32}')
DIGEST = re.compile(r'[0-9a-f]{64}')
MAX_PARSED_BYTES = 32 * 1024 * 1024


def _private_directory(path, create=False):
    if create:
        path.mkdir(mode=0o700, parents=False, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ConversationUnavailable('Private conversation storage is required')
    return path


def _file_id(filename, digest):
    return sha256((filename + '\0' + digest).encode('utf-8')).hexdigest()[:32]


def _reference(ref):
    if not isinstance(ref, dict):
        raise ValueError('Invalid saved attachment reference')
    fid, name, digest, size = (ref.get(key) for key in ('id', 'filename', 'sha256', 'size'))
    if (not isinstance(fid, str) or not FILE_ID.fullmatch(fid)
            or not isinstance(name, str) or not 1 <= len(name) <= 180 or not name.isprintable()
            or '/' in name or '\\' in name
            or not isinstance(digest, str) or not DIGEST.fullmatch(digest)
            or type(size) is not int or not 0 < size <= MAX_BYTES
            or fid != _file_id(name, digest)):
        raise ValueError('Invalid saved attachment reference')
    return fid


def _read(path, budget):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_mode & 0o077 or info.st_nlink != 1 or info.st_size > budget):
            raise ConversationUnavailable('Saved attachment is not readable')
        data = stream.read(budget + 1)
    if len(data) > budget:
        raise ConversationUnavailable('Saved attachment exceeds its storage budget')
    return data


def _atomic_write(path, data):
    if path.is_symlink():
        raise ConversationUnavailable('Saved attachment path is invalid')
    fd, temporary = tempfile.mkstemp(prefix='.saving-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class AttachmentStore:
    def __init__(self, root):
        self.root = Path(root).absolute()
        try:
            # The application chooses this trusted root; upload names never enter paths.
            if not self.root.exists():
                self.root.mkdir(mode=0o700, parents=True)
            _private_directory(self.root)
        except OSError:
            raise ConversationUnavailable('Attachment storage is unavailable') from None

    def _directory(self, actor, cid, create=False):
        actor_digest = sha256(actor_key(actor).encode('utf-8')).hexdigest()
        cid = conversation_key(cid)
        _private_directory(self.root)
        account = _private_directory(self.root / actor_digest, create)
        return _private_directory(account / cid, create)

    def save_uploads(self, actor, cid, uploads):
        verified = read_uploads(uploads)
        refs = []
        seen = set()
        try:
            directory = self._directory(actor, cid, create=True)
            for filename, encoded, digest in verified:
                data = base64.b64decode(encoded, validate=True)
                fid = _file_id(filename, digest)
                ref = {'id': fid, 'filename': filename, 'size': len(data), 'sha256': digest}
                path = directory / (fid + '.original')
                if path.exists() or path.is_symlink():
                    existing = _read(path, MAX_BYTES)
                    if len(existing) != len(data) or sha256(existing).hexdigest() != digest:
                        raise ConversationUnavailable('Saved attachment integrity check failed')
                else:
                    _atomic_write(path, data)
                if fid not in seen:
                    refs.append(ref)
                    seen.add(fid)
            return refs
        except OSError:
            raise ConversationUnavailable('Attachment could not be saved') from None

    def save_parsed(self, actor, cid, ref, context, receipt):
        fid = _reference(ref)
        if context is not None and not isinstance(context, dict) or not isinstance(receipt, dict):
            raise ValueError('Invalid parsed attachment')
        # Validate the persisted original before binding any parsed evidence to it.
        self.download(actor, cid, ref)
        try:
            payload = json.dumps({'context': context, 'receipt': receipt}, ensure_ascii=False,
                                 separators=(',', ':'), allow_nan=False)
            envelope = {'schemaVersion': 1, 'sha256': ref['sha256'], 'id': fid,
                        'payloadSha256': sha256(payload.encode()).hexdigest(), 'payload': payload}
            data = json.dumps(envelope, ensure_ascii=False, separators=(',', ':')).encode()
            if len(data) > MAX_PARSED_BYTES:
                raise ConversationUnavailable('Parsed attachment exceeds local storage budget')
            directory = self._directory(actor, cid)
            _atomic_write(directory / (fid + '.parsed.json'), data)
        except (OSError, TypeError, ValueError):
            raise ConversationUnavailable('Parsed attachment could not be saved') from None

    def load_parsed(self, actor, cid, ref):
        fid = _reference(ref)
        try:
            path = self._directory(actor, cid) / (fid + '.parsed.json')
            if not path.exists() and not path.is_symlink():
                return None
            envelope = json.loads(_read(path, MAX_PARSED_BYTES))
            if (envelope.get('schemaVersion') != 1 or envelope.get('sha256') != ref['sha256']
                    or envelope.get('id') != fid or not isinstance(envelope.get('payload'), str)
                    or sha256(envelope['payload'].encode()).hexdigest() != envelope.get('payloadSha256')):
                raise ConversationUnavailable('Parsed attachment integrity check failed')
            payload = json.loads(envelope['payload'])
            if (set(payload) != {'context', 'receipt'} or not isinstance(payload['receipt'], dict)
                    or payload['context'] is not None and not isinstance(payload['context'], dict)):
                raise ConversationUnavailable('Parsed attachment is not readable')
            return payload['context'], payload['receipt']
        except FileNotFoundError:
            return None
        except (OSError, ValueError, TypeError, AttributeError):
            raise ConversationUnavailable('Parsed attachment is not readable') from None

    def download(self, actor, cid, ref):
        fid = _reference(ref)
        try:
            data = _read(self._directory(actor, cid) / (fid + '.original'), MAX_BYTES)
            if len(data) != ref['size'] or sha256(data).hexdigest() != ref['sha256']:
                raise ConversationUnavailable('Saved attachment integrity check failed')
            return data
        except OSError:
            raise ConversationUnavailable('Saved attachment is not readable') from None

    def read_upload(self, actor, cid, ref):
        return {'filename': ref['filename'],
                'contentBase64': base64.b64encode(self.download(actor, cid, ref)).decode('ascii')}
