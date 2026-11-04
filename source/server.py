"""Loopback-only onboarding HTTP service. Production TLS and limits are provided by nginx."""
import argparse
import json
import signal
import threading
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from controller import Controller, Runtime, UserError


def handler_for(controller, origin, static, dev=False):
    allowed_host = urlsplit(origin).netloc
    class Handler(BaseHTTPRequestHandler):
        server_version = 'Dafeiyu'
        sys_version = ''

        def log_message(self, *args):
            pass  # No request paths, tickets, passwords, QR payloads, or model keys in logs.

        def setup(self):
            super().setup()
            self.connection.settimeout(110)

        def send(self, code, content, mime='application/json; charset=utf-8', cookie=None):
            body = json.dumps(content, ensure_ascii=False).encode() if isinstance(content, dict) else content
            self.send_response(code)
            for k, v in {'Content-Type': mime, 'Content-Length': str(len(body)), 'Cache-Control': 'no-store',
                         'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer',
                         'Content-Security-Policy': "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"}.items():
                self.send_header(k, v)
            if cookie:
                self.send_header('Set-Cookie', cookie)
            self.end_headers()
            self.wfile.write(body)

        def token(self):
            try:
                cookies = SimpleCookie(self.headers.get('Cookie', ''))
                return cookies['fish_session'].value if 'fish_session' in cookies else ''
            except Exception:
                return ''

        def cookie(self, token, age=7200):
            return f'fish_session={token}; Path=/setup/; HttpOnly; SameSite=Strict; Max-Age={age}' + ('' if dev else '; Secure')

        def check_host(self):
            if self.headers.get('Host') != allowed_host:
                raise UserError('访问地址不匹配。', 403)

        def do_GET(self):
            try:
                self.check_host()
                path = urlsplit(self.path).path
                if path == '/setup/api/public':
                    return self.send(200, controller.public_state())
                if path == '/setup/api/status':
                    session = controller.authenticate(self.token())
                    return self.send(200, {**controller.status(), 'csrf': session['csrf']})
                files = {'/setup/': ('index.html', 'text/html; charset=utf-8'),
                         '/setup/app.js': ('app.js', 'text/javascript; charset=utf-8'),
                         '/setup/style.css': ('style.css', 'text/css; charset=utf-8')}
                if path not in files:
                    raise UserError('页面不存在。', 404)
                name, mime = files[path]
                content = (static / name).read_bytes()
                if dev and name == 'index.html':
                    content = content.replace(b'<body>', '<body><div class="preview-banner">本地界面预览 · 尚未连接云服务器，请勿输入真实密钥</div>'.encode())
                self.send(200, content, mime)
            except UserError as exc:
                self.send(exc.status, {'error': str(exc)})

        def do_POST(self):
            try:
                self.check_host()
                if self.headers.get('Origin') != origin:
                    raise UserError('请在大肥鱼页面内操作。', 403)
                if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                    raise UserError('请求格式不正确。', 415)
                size = int(self.headers.get('Content-Length', '0'))
                if size < 2 or size > 4096 or self.headers.get('Transfer-Encoding'):
                    raise UserError('请求内容过大或不完整。', 413)
                payload = json.loads(self.rfile.read(size))
                if not isinstance(payload, dict):
                    raise UserError('请求格式不正确。')
                path = urlsplit(self.path).path
                if path == '/setup/api/login':
                    token, csrf = controller.login(payload, self.client_address[0])
                    return self.send(200, {'csrf': csrf}, cookie=self.cookie(token))
                controller.authenticate(self.token(), self.headers.get('X-Fish-CSRF', ''))
                if path == '/setup/api/logout':
                    controller.logout(self.token())
                    return self.send(200, {'ok': True}, cookie=self.cookie('', 0))
                if path == '/setup/api/activate':
                    result = controller.activate(payload.get('key'))
                elif path == '/setup/api/qq/start':
                    result = controller.start_qq()
                elif path == '/setup/api/qq/poll':
                    result = controller.poll_qq()
                elif path == '/setup/api/confirm':
                    result = controller.confirm_reply()
                else:
                    raise UserError('操作不存在。', 404)
                self.send(200, result)
            except UserError as exc:
                self.send(exc.status, {'error': str(exc)})
            except (ValueError, TypeError):
                self.send(400, {'error': '请求格式不正确，请刷新页面重试。'})
            except Exception:
                self.send(500, {'error': '本次操作未完成，请稍后重试；已有资料会保留。'})
    return Handler


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--base', type=Path, default=Path('/opt/dafeiyu-onboard'))
    p.add_argument('--port', type=int, default=6187)
    p.add_argument('--dev', action='store_true')
    args = p.parse_args()
    state = args.base / 'state'
    cfg = json.loads((state / 'web-settings.json').read_text())
    origin = f'http://127.0.0.1:{args.port}' if args.dev else 'https://' + cfg['public_ip']
    controller = Controller(state)
    runtime = None if args.dev else Runtime(state, args.base / 'venv/bin/astrbot')
    server = ThreadingHTTPServer(('127.0.0.1', args.port), handler_for(controller, origin, Path(__file__).parent / 'web', args.dev))
    server.daemon_threads = True
    def stop(*_):
        threading.Thread(target=server.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    if runtime:
        threading.Thread(target=runtime.supervise, daemon=True).start()
    try:
        server.serve_forever()
    finally:
        if runtime:
            runtime.close()
        server.server_close()


if __name__ == '__main__':
    main()
