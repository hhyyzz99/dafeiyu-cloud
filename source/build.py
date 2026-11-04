"""Build from an explicit allowlist; never archive the deployment directory."""
import base64
import gzip
import hashlib
import io
import json
import tarfile
import zipfile
from pathlib import Path

VERSION = '1.0.0-rc2'
HERE = Path(__file__).resolve().parent
PROJECT = HERE if (HERE / 'plugin/main.py').is_file() else HERE.parent
FILES = ['install.sh', 'settings.py', 'configure.py', 'manage.py', 'README.md',
         'dafeiyu-kit.service', 'dafeiyu-kit-backup.service', 'dafeiyu-kit-backup.timer',
         'THIRD_PARTY.md', 'test_installer.py', 'build.py']


def payload_files():
    files = {name: (HERE / name).read_bytes() for name in FILES}
    for name in ['main.py', 'store.py', 'metadata.yaml']:
        files['plugin/' + name] = (PROJECT / 'plugin' / name).read_bytes()
    files['tests/test_store.py'] = (PROJECT / 'tests/test_store.py').read_bytes()
    files['tests/test_plugin_behavior.py'] = (PROJECT / 'tests/test_plugin_behavior.py').read_bytes()
    persona = (PROJECT / 'persona.md').read_text(encoding='utf-8')
    files['persona.md'] = persona.replace('TOM 学长鱼塘里的', '这片鱼塘里的').encode('utf-8')
    files['licenses/AstrBot-AGPL-3.0.txt'] = (HERE / 'licenses/AstrBot-AGPL-3.0.txt').read_bytes()
    for name, content in files.items():
        files[name] = content.replace(b'\r\n', b'\n')
    return files


def archive(files):
    buffer = io.BytesIO()
    with gzip.GzipFile(fileobj=buffer, mode='wb', mtime=0, filename='') as compressed:
        with tarfile.open(fileobj=compressed, mode='w') as tar:
            for name, data in sorted(files.items()):
                info = tarfile.TarInfo('dafeiyu-kit/' + name)
                info.size = len(data)
                info.mode = 0o644
                info.mtime = 0
                tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


HEADER = r'''#!/usr/bin/env bash
set -euo pipefail
umask 077
if [[ ${1:-} == --help ]]; then
  echo '大肥鱼安装包：sudo bash dafeiyu-oneclick.run [--public-ip 公网IPv4] [--port 6186] [--local-only] [--env-file /root/fish.env]'
  echo '仅解包查看源码：bash dafeiyu-oneclick.run --extract-only 空目录'
  exit 0
fi
EXTRACT_ONLY=0
if [[ ${1:-} == --extract-only ]]; then
  [[ $# == 2 ]] || { echo '请指定一个空目录。'; exit 1; }
  EXTRACT_ONLY=1
  TARGET=$2
else
  [[ $EUID -eq 0 && $(uname -s) == Linux ]] || { echo '请在 Linux 服务器上使用 sudo bash 运行。'; exit 1; }
  if ! command -v python3 >/dev/null; then
    if command -v dnf >/dev/null; then dnf install -y python3
    elif command -v apt-get >/dev/null; then apt-get update; apt-get install -y python3
    else echo '请先安装 Python 3。'; exit 1
    fi
  fi
  TARGET=$(mktemp -d /tmp/dafeiyu-kit.XXXXXXXX)
  trap 'rm -rf -- "$TARGET"' EXIT
fi
python3 - "$0" "$TARGET" '__SHA256__' <<'PY'
import base64, hashlib, io, sys, tarfile
from pathlib import Path, PurePosixPath
file, destination, expected = sys.argv[1:]
source = Path(file).read_bytes().replace(b'\r\n', b'\n')
encoded = source.rsplit(b'\n' + b'__FISH_PAYLOAD__' + b'\n', 1)[1]
raw = base64.b64decode(b''.join(encoded.splitlines()), validate=True)
if hashlib.sha256(raw).hexdigest() != expected:
    raise SystemExit('安装包校验失败，请重新下载。')
target = Path(destination).resolve()
if target.exists() and any(target.iterdir()):
    raise SystemExit('解包目录必须为空，未覆盖任何文件。')
with tarfile.open(fileobj=io.BytesIO(raw), mode='r:gz') as tar:
    members = tar.getmembers()
    for member in members:
        parts = PurePosixPath(member.name).parts
        if not member.isfile() or not parts or parts[0] != 'dafeiyu-kit' or '..' in parts or '\\' in member.name:
            raise SystemExit('安装包路径校验失败。')
    for member in members:
        output = target / member.name
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(tar.extractfile(member).read())
print('安装包内容校验通过。')
PY
if [[ $EXTRACT_ONLY == 1 ]]; then
  echo '解包完成，尚未安装任何服务。'
else
  bash "$TARGET/dafeiyu-kit/install.sh" "$@"
fi
exit 0
__FISH_PAYLOAD__
'''


def main():
    files = payload_files()
    manifest = {name: hashlib.sha256(content).hexdigest() for name, content in sorted(files.items())}
    files['MANIFEST.json'] = json.dumps({'version': VERSION, 'files': manifest}, ensure_ascii=False, indent=2).encode()
    raw = archive(files)
    out = PROJECT / 'dist'
    out.mkdir(exist_ok=True)
    tar_name = f'dafeiyu-starter-{VERSION}.tar.gz'
    (out / tar_name).write_bytes(raw)
    wrapper = HEADER.replace('__SHA256__', hashlib.sha256(raw).hexdigest()).encode()
    run_name = f'dafeiyu-oneclick-{VERSION}.run'
    (out / run_name).write_bytes(wrapper + base64.encodebytes(raw))
    zip_name = f'大肥鱼一键安装包-{VERSION}.zip'
    with zipfile.ZipFile(out / zip_name, 'w', zipfile.ZIP_DEFLATED) as package:
        package.writestr(run_name, wrapper + base64.encodebytes(raw))
        for name, data in sorted(files.items()):
            package.writestr('source/' + name, data)
    hashes = ''.join(hashlib.sha256((out / name).read_bytes()).hexdigest() + '  ' + name + '\n'
                     for name in (tar_name, run_name, zip_name))
    (out / 'SHA256SUMS.txt').write_text(hashes, encoding='utf-8')
    print(json.dumps({'version': VERSION, 'source_files': len(files),
                      'installer_bytes': (out / run_name).stat().st_size, 'output': str(out)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
