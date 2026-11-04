"""Private, per-server onboarding. Never accepts an arbitrary URL or shell command."""
import base64
import hashlib
import hmac
import io
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

SOURCE = Path(__file__).resolve().parent
sys.path.insert(0, str(SOURCE if (SOURCE / 'settings.py').exists() else SOURCE.parent / 'starter'))
import settings


class UserError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def read_json(path, default=None):
    return json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else default


def verify_password(value, encoded):
    try:
        kind, rounds, salt, expected = encoded.split('$')
        if kind != 'pbkdf2_sha256' or int(rounds) != 600000:
            return False
        actual = hashlib.pbkdf2_hmac('sha256', value.encode(), bytes.fromhex(salt), int(rounds)).hex()
        return hmac.compare_digest(actual, expected)
    except (ValueError, AttributeError):
        return False


def validate_password(value):
    if not isinstance(value, str) or not 12 <= len(value) <= 128 or not re.search('[A-Za-z]', value) or not re.search('[0-9]', value):
        raise UserError('请设置 12～128 位管理密码，至少包含一个英文字母和一个数字。')


def issue_ticket(state):
    """Only the root installer/recovery command calls this, never an HTTP route."""
    token = secrets.token_urlsafe(32)
    settings.write_private(state / 'ticket.json', {'token': token, 'expires': time.time() + 86400})
    return token


class Runtime:
    def __init__(self, state, executable):
        self.state, self.executable = state, executable
        self.process = None
        self.stop_event = threading.Event()

    def supervise(self):
        while not self.stop_event.wait(3):
            if not (self.state / 'configured.ready').exists():
                continue
            if self.process is None or self.process.poll() is not None:
                env = dict(os.environ, ASTRBOT_ROOT=str(self.state / 'instance'), PYTHONUNBUFFERED='1')
                self.process = subprocess.Popen([str(self.executable), 'run'], cwd=self.state / 'instance', env=env)

    def close(self):
        self.stop_event.set()
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(15)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()


class AstrBotBridge:
    def __init__(self, state):
        self.state = state

    def call(self, path, body=None):
        cfg = read_json(self.state / 'instance/data/cmd_config.json')['dashboard']
        def b64(value):
            return base64.urlsafe_b64encode(value).rstrip(b'=').decode()
        head = b64(b'{"alg":"HS256","typ":"JWT"}')
        payload = b64(json.dumps({'username': cfg['username'], 'exp': int(time.time()) + 60}).encode())
        unsigned = head + '.' + payload
        token = unsigned + '.' + b64(hmac.new(cfg['jwt_secret'].encode(), unsigned.encode(), hashlib.sha256).digest())
        req = urllib.request.Request('http://127.0.0.1:' + str(cfg['port']) + path,
            data=None if body is None else json.dumps(body).encode(),
            headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
        try:
            with urllib.request.build_opener(settings.NoRedirect).open(req, timeout=20) as response:
                result = json.loads(response.read(2_000_000))
            if result.get('status') != 'ok':
                raise UserError('机器人暂时未完成操作，请稍后重试。', 502)
            return result.get('data') or {}
        except UserError:
            raise
        except Exception:
            raise UserError('机器人正在启动或暂时无法连接，请等待片刻再试。', 503) from None


class Controller:
    def __init__(self, state, source=SOURCE, bridge=None, model_checker=None, initializer=None):
        self.state = Path(state)
        self.state.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.source = Path(source)
        self.bridge = bridge or AstrBotBridge(self.state)
        self.model_checker = model_checker or settings.check_model
        self.initializer = initializer or self._initialize
        self.lock = threading.RLock()
        self.sessions = {}
        self.failures = {}
        self.busy = False

    def _password_hash(self):
        # The dashboard becomes the password authority after activation. Changing
        # its password must also revoke wizard access with the previous password.
        try:
            if (self.state / 'configured.ready').exists():
                cfg = read_json(self.state / 'instance/data/cmd_config.json', {})
                return cfg.get('dashboard', {}).get('pbkdf2_password', '')
            return (self.state / 'owner.hash').read_text().strip()
        except (OSError, ValueError):
            return ''

    def _new_session(self):
        now = time.time()
        self.sessions = {k: v for k, v in self.sessions.items() if v['expires'] > now}
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
        if len(self.sessions) >= 20:
            self.sessions.pop(next(iter(self.sessions)))
        self.sessions[hashlib.sha256(token.encode()).hexdigest()] = {
            'csrf': csrf, 'expires': now + 7200, 'credential': self._password_hash()}
        return token, csrf

    def authenticate(self, token, csrf=None):
        with self.lock:
            session = self.sessions.get(hashlib.sha256(token.encode()).hexdigest())
            if not session or session['expires'] < time.time() or not hmac.compare_digest(session['credential'], self._password_hash()):
                raise UserError('请先登录管理页面。', 401)
            if csrf is not None and not hmac.compare_digest(csrf, session['csrf']):
                raise UserError('页面验证已失效，请刷新后重试。', 403)
            return session

    def logout(self, token):
        with self.lock:
            self.sessions.pop(hashlib.sha256(token.encode()).hexdigest(), None)

    def login(self, payload, peer):
        with self.lock:
            now = time.time()
            self.failures = {k: [t for t in v if now-t < 600] for k, v in self.failures.items() if any(now-t < 600 for t in v)}
            if len(self.failures.get(peer, [])) >= 6 or sum(map(len, self.failures.values())) >= 60:
                raise UserError('尝试次数过多，请 10 分钟后再试。', 429)
            password = payload.get('password', '')
            owner = self.state / 'owner.hash'
            if owner.exists():
                valid = isinstance(password, str) and len(password) <= 128 and verify_password(password, self._password_hash())
            else:
                ticket = read_json(self.state / 'ticket.json', {})
                token = payload.get('ticket', '')
                valid = isinstance(token, str) and bool(ticket.get('token')) and ticket.get('expires', 0) > now and hmac.compare_digest(token, ticket['token'])
                if valid:
                    validate_password(password)
                    # Persist ownership before consuming the ticket. A retry can log in with this password.
                    pending = owner.with_name('owner.hash.pending')
                    pending.write_text(settings.password_hash(password), encoding='utf-8')
                    pending.chmod(0o600)
                    os.replace(pending, owner)
                    (self.state / 'ticket.json').unlink(missing_ok=True)
            if not valid:
                self.failures.setdefault(peer, []).append(now)
                raise UserError('管理密码或开通码不正确，或者开通码已经过期。', 401)
            self.failures.pop(peer, None)
            return self._new_session()

    def public_state(self):
        return {'claimed': (self.state / 'owner.hash').exists()}

    def status(self):
        configured = (self.state / 'configured.ready').exists()
        result = {'configured': configured, 'busy': self.busy, 'bot_ready': False,
                  'qq_saved': False, 'qq_running': False, 'reply_confirmed': False}
        if configured:
            cfg = read_json(self.state / 'instance/data/cmd_config.json', {})
            qq = next((p for p in cfg.get('platform', []) if p.get('id') == 'dafeiyu_qq'), None)
            result['qq_saved'] = bool(qq)
            if qq:
                result['app_id'] = qq.get('appid', '')
            try:
                stats = self.bridge.call('/api/platform/stats')
                result['bot_ready'] = True
                result['qq_running'] = any(p.get('id') == 'dafeiyu_qq' and p.get('status') == 'running' for p in stats.get('platforms', []))
            except UserError:
                pass
            result['reply_confirmed'] = (self.state / 'reply-confirmed.json').exists()
        return result

    def _initialize(self, key):
        import configure
        cfg = {'api_key': key, 'model': settings.MODEL, 'local_only': True, 'port': 6190, 'public_ip': ''}
        configure.configure(self.state, cfg, self.source)

    def activate(self, key):
        with self.lock:
            if self.busy:
                raise UserError('正在验证，请稍等。', 409)
            if (self.state / 'configured.ready').exists():
                return {'configured': True}
            if not (self.state / 'owner.hash').exists():
                raise UserError('请先设置管理密码。', 403)
            try:
                settings.validate_key(key)
            except (ValueError, TypeError):
                raise UserError('请粘贴这台服务器自带的模型 API Key。') from None
            self.busy = True
        try:
            self.model_checker(key, settings.MODEL)
            self.initializer(key)
            return {'configured': True}
        except ValueError as exc:
            # Only the model check's pre-defined messages are safe; do not return arbitrary upstream errors.
            allowed = ('套餐模型验证失败', '套餐模型连接失败', '模型未返回有效文字')
            message = str(exc) if str(exc).startswith(allowed) else '初始化未完成，请稍后重试；已有配置会保留。'
            raise UserError(message, 502) from None
        finally:
            with self.lock:
                self.busy = False

    def start_qq(self):
        with self.lock:
            if not (self.state / 'configured.ready').exists():
                raise UserError('请先激活 DeepSeek。', 409)
            if self.status()['qq_saved']:
                return {'status': 'saved'}
            task = self.bridge.call('/api/platform/registration/qq_official', {'action': 'start', 'platform_config': {}})
            if not all(isinstance(task.get(k), str) and task[k] for k in ('task_id', 'bind_key', 'qrcode')):
                raise UserError('未获取到二维码，请重试。', 502)
            # No image CDN: render the QR locally, keeping binding data on this server.
            import qrcode
            import qrcode.image.svg
            qr = qrcode.make(task['qrcode'], image_factory=qrcode.image.svg.SvgPathImage, box_size=8)
            buf = io.BytesIO()
            qr.save(buf)
            task.update(created=time.time(), last_poll=0)
            settings.write_private(self.state / 'qq-pending.json', task)
            return {'status': 'pending', 'image': 'data:image/svg+xml;base64,' + base64.b64encode(buf.getvalue()).decode()}

    def poll_qq(self):
        with self.lock:
            if self.status()['qq_saved']:
                (self.state / 'qq-pending.json').unlink(missing_ok=True)
                return {'status': 'saved'}
            path = self.state / 'qq-pending.json'
            task = read_json(path, {})
            if not task or time.time() - task.get('created', 0) > 300:
                path.unlink(missing_ok=True)
                return {'status': 'expired'}
            if time.time() - task.get('last_poll', 0) < 2:
                return {'status': 'pending'}
            if 'credentials' not in task:
                result = self.bridge.call('/api/platform/registration/qq_official', {
                    'action': 'poll', 'platform_config': {}, 'task_id': task['task_id'], 'bind_key': task['bind_key']})
                task['last_poll'] = time.time()
                if result.get('status') == 'created':
                    if not re.fullmatch(r'\d{5,24}', str(result.get('appid', ''))) or not result.get('secret'):
                        raise UserError('QQ 返回的绑定资料不完整，请重新扫码。', 502)
                    task['credentials'] = {'appid': str(result['appid']), 'secret': result['secret']}
                elif result.get('status') in ('expired', 'error'):
                    path.unlink(missing_ok=True)
                    return {'status': 'expired'}
                settings.write_private(path, task)
            if 'credentials' in task:
                # Credentials remain in a private pending file until save succeeds, making retry safe.
                self.bridge.call('/api/config/platform/new', {
                    'id': 'dafeiyu_qq', 'type': 'qq_official', 'enable': True,
                    'enable_group_c2c': True, 'enable_guild_direct_message': True, 'use_markdown': True,
                    **task['credentials']})
                path.unlink(missing_ok=True)
                return {'status': 'saved'}
            return {'status': 'pending'}

    def confirm_reply(self):
        if not self.status()['qq_running']:
            raise UserError('QQ 尚未显示运行中，请稍后检查。', 409)
        settings.write_private(self.state / 'reply-confirmed.json', {'confirmed_at': int(time.time()), 'source': 'owner'})
        return {'confirmed': True}
