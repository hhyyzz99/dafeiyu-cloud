"""Root-only installation helper; no privileged entry point is exposed over HTTP."""
import argparse
import ipaddress
import json
import os
import secrets
import socket
import time
import urllib.request
from pathlib import Path

from controller import issue_ticket, read_json
from settings import write_private

BASE = Path('/opt/dafeiyu-onboard')


def nginx_config(ip, tls, external_http=False):
    text = '''user dafeiyu-onboard;
worker_processes auto;
pid /opt/dafeiyu-onboard/nginx.pid;
error_log /opt/dafeiyu-onboard/logs/nginx-error.log warn;
events { worker_connections 512; }
http {
  include /etc/nginx/mime.types;
  default_type application/octet-stream;
  server_tokens off;
  access_log off;
  client_body_temp_path /opt/dafeiyu-onboard/nginx-tmp/client;
  proxy_temp_path /opt/dafeiyu-onboard/nginx-tmp/proxy;
  client_max_body_size 2m;
  limit_req_zone $binary_remote_addr zone=fish_login:10m rate=10r/m;
  map $http_upgrade $connection_upgrade { default upgrade; '' close; }
'''
    if not external_http:
        text += '''  server {
    listen 80;
    server_name __IP__;
    location ^~ /.well-known/acme-challenge/ { root /opt/dafeiyu-onboard/public; }
    location / { return 301 https://__IP__$request_uri; }
  }
'''
    if tls:
        text += '''  server {
    listen 443 ssl;
    server_name __IP__;
    ssl_certificate /opt/dafeiyu-onboard/acme/live/dafeiyu-web/fullchain.pem;
    ssl_certificate_key /opt/dafeiyu-onboard/acme/live/dafeiyu-web/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    add_header Referrer-Policy no-referrer always;
    add_header X-Content-Type-Options nosniff always;
    location = /setup { return 302 /setup/; }
    location = /setup/api/login {
      limit_req zone=fish_login burst=5 nodelay;
      client_max_body_size 4k;
      proxy_pass http://127.0.0.1:6187;
      proxy_set_header Host $host;
    }
    location /setup/ {
      client_max_body_size 4k;
      proxy_pass http://127.0.0.1:6187;
      proxy_set_header Host $host;
      proxy_read_timeout 120s;
    }
    location /downloads/ {
      alias /opt/dafeiyu-onboard/public/downloads/;
      autoindex off;
      add_header Access-Control-Allow-Origin "*" always;
      add_header X-Content-Type-Options nosniff always;
    }
    location /start/ { alias /opt/dafeiyu-onboard/public/start/; index index.html; }
    location / {
      proxy_pass http://127.0.0.1:6190;
      proxy_http_version 1.1;
      proxy_set_header Host $host;
      proxy_set_header Upgrade $http_upgrade;
      proxy_set_header Connection $connection_upgrade;
      proxy_read_timeout 180s;
      proxy_buffering off;
      error_page 502 503 =302 /setup/;
    }
  }
'''
    return text.replace('__IP__', str(ipaddress.IPv4Address(ip))) + '}\n'


def detect_http_root(ip):
    """Hermes ships nginx on port 80. Reuse its standard webroot after an exact probe."""
    try:
        with socket.create_connection(('127.0.0.1',80), timeout=2):
            pass
    except OSError:
        return str(BASE / 'public'), False
    root = Path('/usr/share/nginx/html')
    if not root.is_dir() or root.is_symlink():
        raise SystemExit('80 端口已有服务，但没有识别到同款镜像的标准网页目录，未改动原服务。')
    folder = root / '.well-known/acme-challenge'
    folder.mkdir(parents=True, exist_ok=True, mode=0o755)
    folder.parent.chmod(0o755)
    folder.chmod(0o755)
    token = secrets.token_urlsafe(24)
    probe = folder / ('dafeiyu-probe-' + secrets.token_hex(8))
    try:
        probe.write_text(token, encoding='ascii'); probe.chmod(0o644)
        req = urllib.request.Request('http://127.0.0.1/.well-known/acme-challenge/' + probe.name, headers={'Host':ip})
        with urllib.request.urlopen(req, timeout=5) as response:
            if response.read(256).decode() != token:
                raise ValueError('wrong webroot')
    except Exception:
        raise SystemExit('80 端口已有网页未通过证书目录检查。原网页未被覆盖，请管理员确认 ACME 路径。') from None
    finally:
        probe.unlink(missing_ok=True)
    return str(root), True


def main():
    p = argparse.ArgumentParser()
    p.add_argument('action', choices=['init','webroot','proxy-http','proxy-https','verify','result','reset-ticket'])
    p.add_argument('ip')
    args = p.parse_args()
    ip = str(ipaddress.IPv4Address(args.ip))
    if not ipaddress.ip_address(ip).is_global:
        raise SystemExit('需要公网 IPv4。')
    state = BASE / 'state'
    if args.action == 'init':
        current = read_json(state / 'web-settings.json', {})
        if current and current.get('public_ip') != ip:
            raise SystemExit('已有安装的公网地址不同，已停止；需要管理员先处理 IP 变更。')
        if not current:
            webroot, external_http = detect_http_root(ip)
            write_private(state / 'web-settings.json', {'public_ip': ip, 'webroot': webroot, 'external_http': external_http})
        if not (state / 'owner.hash').exists() and not (state / 'ticket.json').exists():
            issue_ticket(state)
    elif args.action == 'webroot':
        print(read_json(state / 'web-settings.json')['webroot'])
    elif args.action.startswith('proxy-'):
        # A repeat install must not take down a working HTTPS listener.
        tls = args.action == 'proxy-https' or (BASE / 'acme/live/dafeiyu-web/fullchain.pem').exists()
        external_http = read_json(state / 'web-settings.json').get('external_http',False)
        (BASE / 'nginx.conf').write_text(nginx_config(ip, tls, external_http), encoding='utf-8')
    elif args.action == 'verify':
        for _ in range(30):
            try:
                # Connect locally while using the intended origin. TLS trust is checked below by openssl.
                req = urllib.request.Request('http://127.0.0.1:6187/setup/api/public', headers={'Host': ip})
                with urllib.request.urlopen(req, timeout=3) as r:
                    assert r.status == 200
                break
            except Exception:
                time.sleep(2)
        else:
            raise SystemExit('网页服务未就绪，请查看 dafeiyu-onboard 服务日志。')
        import ssl
        ctx = ssl.create_default_context()
        with socket.create_connection(('127.0.0.1',443), timeout=10) as sock:
            with ctx.wrap_socket(sock, server_hostname=ip) as secure:
                secure.getpeercert()
        print('网页服务与可信证书验证通过。')
    elif args.action in ('result','reset-ticket'):
        if args.action == 'reset-ticket':
            if (state / 'owner.hash').exists():
                raise SystemExit('鱼塘已经认领，不能重新发放开通码覆盖管理密码。')
            issue_ticket(state)
            import pwd
            uid = pwd.getpwnam('dafeiyu-onboard').pw_uid
            os.chown(state / 'ticket.json', uid, -1)
        ticket = read_json(state / 'ticket.json', {})
        result = {'status': '安装成功', '开通页面': f'https://{ip}/setup/', 'owner_url': f'https://{ip}/setup/'}
        if not (state / 'owner.hash').exists():
            if ticket.get('expires',0) <= time.time():
                result['提示'] = '开通码已过期，请由管理员执行 provision.py reset-ticket 公网IP。'
            else:
                result['专属开通链接'] = result['开通页面'] + '#setup=' + ticket['token']
                result['owner_url'] = result['专属开通链接']
                result['提示'] = '此链接含一次性开通码，24 小时内有效，仅自己使用，不要放到公开教程。'
        print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
