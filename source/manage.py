"""Small local maintenance commands. login explicitly prints owner credentials."""
import argparse
import json
import shutil
import sqlite3
import ssl
import subprocess
import time
import urllib.error
import urllib.request
from contextlib import closing
from datetime import datetime, timezone, timedelta
from pathlib import Path


def read_config(base):
    return json.loads((base / 'instance/data/cmd_config.json').read_text(encoding='utf-8-sig'))


def backup(base):
    data = base / 'instance/data'
    destination = base / 'backups'
    destination.mkdir(mode=0o700, exist_ok=True)
    destination.chmod(0o700)
    folder = destination / datetime.now(timezone(timedelta(hours=8))).strftime('%Y%m%d-%H%M%S-%f')
    folder.mkdir(mode=0o700)
    for source, name in [(data / 'data_v4.db', 'astrbot.db'),
                         (data / 'plugin_data/astrbot_plugin_tom_fish/fish.db', 'fish.db')]:
        if source.exists():
            with closing(sqlite3.connect(source)) as original, closing(sqlite3.connect(folder / name)) as target:
                original.backup(target)
    shutil.copy2(data / 'cmd_config.json', folder / 'cmd_config.json')
    (folder / 'complete').touch()
    # Only prune snapshots created by this command, never unrelated directories.
    completed = sorted(p for p in destination.iterdir() if p.is_dir() and not p.is_symlink() and (p / 'complete').is_file())
    for old in completed[:-7]:
        if old.resolve().parent == destination.resolve():
            shutil.rmtree(old)
    print('本机备份完成：' + folder.name)


def health(base, wait=False):
    cfg = read_config(base)['dashboard']
    tls = cfg.get('ssl', {}).get('enable', False)
    scheme = 'https' if tls else 'http'
    url = f"{scheme}://127.0.0.1:{cfg['port']}"
    context = ssl.create_default_context(cafile=cfg['ssl']['cert_file']) if tls else None
    for attempt in range(60 if wait else 1):
        try:
            with urllib.request.urlopen(url + '/', context=context, timeout=3) as response:
                if response.status != 200:
                    raise OSError('控制台未就绪')
            try:
                urllib.request.urlopen(url + '/api/config/get', context=context, timeout=3).close()
                raise ValueError('配置接口未要求登录，已停止。')
            except urllib.error.HTTPError as exc:
                if exc.code != 401:
                    raise ValueError('配置接口鉴权检查未通过。') from None
            print('控制台已启动；未登录访问配置返回 401。QQ 绑定和真实回复请在扫码后验证。')
            return
        except (OSError, urllib.error.URLError):
            if not wait or attempt == 59:
                raise ValueError('控制台未就绪，请查看：journalctl -u dafeiyu-kit -n 60') from None
            time.sleep(2)


def info(base):
    cfg = read_config(base)['dashboard']
    owner = json.loads((base / 'owner-login.json').read_text(encoding='utf-8'))
    print('控制台：' + owner['url'])
    print('账号保存在服务器 /opt/dafeiyu-kit/owner-login.json（仅管理员可读）。')
    if cfg.get('ssl', {}).get('enable'):
        result = subprocess.run(['openssl', 'x509', '-in', cfg['ssl']['cert_file'],
                                 '-noout', '-fingerprint', '-sha256'], capture_output=True, text=True, check=True)
        print('本机生成的 HTTPS 证书，首次浏览器访问会提示不受信任。核对指纹后继续：')
        print(result.stdout.strip())
        print(f"在阿里云防火墙允许管理设备的 IP 访问 TCP {cfg['port']}；QQ 连接不需要开放额外入站端口。")
        print('证书有效期一年。长期公开使用请配置可信证书；本包不代开云防火墙。')
    else:
        print(f"在自己电脑建立隧道：ssh -N -L {cfg['port']}:127.0.0.1:{cfg['port']} root@服务器公网IP")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['info', 'login', 'health', 'backup'])
    parser.add_argument('--base', default='/opt/dafeiyu-kit')
    parser.add_argument('--wait', action='store_true')
    args = parser.parse_args()
    base = Path(args.base).resolve()
    if args.action == 'login':
        print((base / 'owner-login.json').read_text(encoding='utf-8'))
    elif args.action == 'health':
        health(base, args.wait)
    else:
        globals()[args.action](base)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError) as exc:
        raise SystemExit(str(exc)) from None
