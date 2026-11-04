"""Narrow, reversible fix for QQ empty mentions in AstrBot 4.29.4."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile

ORIGINAL_SHA256 = '3874fac08eaa87398d1211080e064d0aa5e4988170cc17710cb266cfc00617ac'
OLD = '''            messages = event.get_messages()
            cfg = self.context.get_config(umo=event.unified_msg_origin)
'''
NEW = '''            messages = event.get_messages()
            # QQ may include an empty Plain after At; ignore it for this check.
            if event.get_platform_name() == "qq_official":
                messages = [
                    part for part in messages
                    if not (isinstance(part, Plain) and not part.text.strip())
                ]
            cfg = self.context.get_config(umo=event.unified_msg_origin)
'''


def digest(data):
    return hashlib.sha256(data).hexdigest()


def patched_bytes(original):
    if digest(original) != ORIGINAL_SHA256:
        raise ValueError('Unrecognized source; refusing to overwrite existing changes.')
    source = original.decode('utf-8')
    if source.count(OLD) != 1:
        raise ValueError('Expected one empty-mention handler.')
    changed = source.replace(OLD, NEW, 1)
    compile(changed, 'astrbot/builtin_stars/astrbot/main.py', 'exec')
    return changed.encode('utf-8')


def apply(target, write=False):
    target = Path(target).resolve(strict=True)
    data = target.read_bytes()
    backup = target.with_name(target.name + '.before-dafeiyu-empty-mention')
    if NEW.encode() in data:
        restored = data.replace(NEW.encode(), OLD.encode(), 1)
        if digest(restored) != ORIGINAL_SHA256:
            raise ValueError('Patch marker exists alongside unrecognized changes.')
        return {'status': 'already_patched', 'sha256': digest(data)}
    changed = patched_bytes(data)
    result = {'status': 'ready', 'before': digest(data), 'after': digest(changed)}
    if not write:
        return result
    if backup.exists():
        if backup.read_bytes() != data:
            raise ValueError('Existing backup differs; refusing to replace it.')
    else:
        shutil.copy2(target, backup)
    stat = target.stat()
    fd, temp_name = tempfile.mkstemp(prefix=target.name + '.', dir=target.parent)
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(changed)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_name, stat.st_mode)
        if hasattr(os, 'chown'):
            os.chown(temp_name, stat.st_uid, stat.st_gid)
        os.replace(temp_name, target)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
    if target.read_bytes() != changed:
        raise RuntimeError('Written file did not match patch.')
    result.update(status='patched', backup=str(backup))
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('target')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    print(json.dumps(apply(args.target, args.apply)))
