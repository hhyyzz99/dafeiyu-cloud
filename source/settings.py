"""Credential discovery and installation settings; standard library only."""
import getpass
import hashlib
import ipaddress
import json
import os
import re
import secrets
import socket
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

BASE_URL = 'https://maas-api.simple-server.cn/v1'
MODEL = 'deepseek-v4.1-flash-1017'


def read_env(path):
    """Read literal KEY=value pairs. Never source/execute an environment file."""
    result = {}
    for line in Path(path).read_text(encoding='utf-8-sig').splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('export '):
            line = line[7:].strip()
        key, sep, value = line.partition('=')
        if not sep or not re.fullmatch(r'[A-Z][A-Z0-9_]*', key):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        result[key] = value
    return result


def bundled_key(values):
    # A generic OpenAI key is accepted only alongside the exact package endpoint.
    # This prevents accidentally using an unrelated paid provider's credentials.
    for prefix in ('DAFEIYU', 'MODEL', 'OPENAI'):
        key = values.get(prefix + '_API_KEY', '').strip()
        endpoint = values.get(prefix + '_BASE_URL', '').rstrip('/')
        if prefix == 'DAFEIYU' and not endpoint:
            endpoint = BASE_URL
        if key and endpoint == BASE_URL:
            return key
    return None


def discover_key(env_file=None, environ=None, candidates=None):
    if env_file:
        path = Path(env_file)
        if not path.is_file():
            raise ValueError('指定的配置文件不存在。')
        key = bundled_key(read_env(path))
        if key:
            return key, '明确指定的本机套餐配置'
        raise ValueError('配置文件没有匹配套餐地址的 API Key；未读取其他服务的密钥。')
    values = dict(os.environ if environ is None else environ)
    key = bundled_key(values)
    if key:
        return key, '指定的环境变量'
    paths = [Path(p) for p in (
        candidates if candidates is not None else ('/etc/environment', '/root/.hermes/.env')
    )]
    for path in paths:
        if not path.is_file():
            continue
        key = bundled_key(read_env(path))
        if key:
            return key, '本机已有的套餐配置'
    return None, None


def prompt(text, hidden=False):
    try:
        # A terminal is not seekable: buffered r+ mode cannot open /dev/tty.
        # Separate streams also work when install.sh redirects stdout to tee.
        with open('/dev/tty', 'w', encoding='utf-8') as terminal:
            if hidden:
                return getpass.getpass(text, stream=terminal).strip()
            terminal.write(text)
            terminal.flush()
            with open('/dev/tty', 'r', encoding='utf-8') as terminal_input:
                return terminal_input.readline().strip()
    except OSError:
        raise ValueError('当前窗口不能交互输入。请在远程连接终端运行，或使用 --env-file 和 --public-ip。') from None


def validate_key(key):
    if not isinstance(key, str) or not 8 <= len(key) <= 1024 or any(c.isspace() for c in key):
        raise ValueError('API Key 格式不正确。请复制本实例的套餐 API Key。')
    return key


def password_hash(password):
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), 600000).hex()
    return f'pbkdf2_sha256$600000${salt}${digest}'


def write_private(path, data):
    path = Path(path)
    temporary = path.with_name(path.name + '.pending')
    with open(temporary, 'w', encoding='utf-8') as stream:
        os.chmod(temporary, 0o600)
        json.dump(data, stream, ensure_ascii=False, indent=2)
    temporary.replace(path)


def check_port(port, host):
    with socket.socket() as sock:
        try:
            sock.bind((host, port))
        except OSError:
            raise ValueError(f'端口 {port} 已占用。请使用 --port 选择空闲端口。') from None


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def check_model(key, model, opener=None):
    """Small real request on the target instance; never echo response/error secrets."""
    body = json.dumps({'model': model, 'messages': [{'role': 'user', 'content': '只回复 OK'}],
                       'max_tokens': 16, 'stream': False, 'thinking': {'type': 'disabled'}}).encode()
    request = urllib.request.Request(BASE_URL + '/chat/completions', data=body,
        headers={'Authorization': 'Bearer ' + validate_key(key), 'Content-Type': 'application/json'})
    opener = opener or urllib.request.build_opener(NoRedirect)
    try:
        with opener.open(request, timeout=90) as response:
            result = json.loads(response.read(1048576))
        if not result.get('choices') or not result['choices'][0].get('message', {}).get('content'):
            raise ValueError('模型未返回有效文字。请核对套餐可用模型和额度。')
    except urllib.error.HTTPError as exc:
        raise ValueError(f'套餐模型验证失败（HTTP {exc.code}）。请检查密钥、额度和模型名称。') from None
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, KeyError, TypeError):
        raise ValueError('套餐模型连接失败。请检查服务器网络，稍后重新运行。') from None


def prepare(base, args):
    state = base / 'install-settings.json'
    if state.exists():
        settings = json.loads(state.read_text(encoding='utf-8'))
        if (args.local_only != settings['local_only'] or args.port != settings['port']
                or (args.public_ip and args.public_ip != settings['public_ip'])
                or args.model != settings['model']):
            raise ValueError('已有安装设置。恢复安装时请使用首次的端口、模式及模型参数。')
        if args.env_file:
            key, _ = discover_key(args.env_file)
            settings['api_key'] = validate_key(key)
    else:
        check_port(args.port, '127.0.0.1' if args.local_only else '0.0.0.0')
        key, source = discover_key(args.env_file)
        if not key:
            key = prompt('粘贴本实例套餐 API Key（输入不显示）：', hidden=True)
            source = '首次输入'
        validate_key(key)
        if not re.fullmatch(r'deepseek-[A-Za-z0-9_.-]+', args.model):
            raise ValueError('此包只配置 DeepSeek；模型名称必须以 deepseek- 开头。')
        public_ip = ''
        if not args.local_only:
            public_ip = args.public_ip or prompt('服务器公网 IPv4（见阿里云实例首页）：')
            address = ipaddress.IPv4Address(public_ip)
            if not address.is_global:
                raise ValueError('请填写服务器的公网 IPv4 地址。')
        settings = {'api_key': key, 'model': args.model, 'port': args.port,
                    'public_ip': public_ip, 'local_only': args.local_only}
        print('套餐配置来源：' + source)
    if not (base / 'configured.ready').exists():
        check_model(settings['api_key'], settings['model'])
        print('DeepSeek 连通检查通过（本次使用少量套餐额度）。')
    write_private(state, settings)
    owner_path = base / 'owner-login.json'
    if not owner_path.exists():
        password = 'Fish1' + secrets.token_urlsafe(24)
        address = f"http://127.0.0.1:{settings['port']}/" if args.local_only else f"https://{settings['public_ip']}:{settings['port']}/"
        write_private(owner_path, {'url': address, 'username': 'fish', 'password': password})
        (base / 'owner.hash').write_text(password_hash(password), encoding='utf-8')
        (base / 'owner.hash').chmod(0o600)
    elif not (base / 'owner.hash').exists():
        password = json.loads(owner_path.read_text(encoding='utf-8'))['password']
        (base / 'owner.hash').write_text(password_hash(password), encoding='utf-8')
        (base / 'owner.hash').chmod(0o600)
    return settings


def certificate(base, settings):
    if settings['local_only']:
        return
    tls = base / 'instance' / 'tls'
    tls.mkdir(parents=True, exist_ok=True, mode=0o700)
    key, cert = tls / 'key.pem', tls / 'cert.pem'
    if cert.exists() and key.exists():
        return
    result = subprocess.run(['openssl', 'req', '-x509', '-nodes', '-newkey', 'rsa:3072',
        '-sha256', '-days', '365', '-subj', '/CN=Dafeiyu',
        '-addext', 'subjectAltName=IP:' + settings['public_ip'] + ',IP:127.0.0.1',
        '-keyout', str(key), '-out', str(cert)], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    if result.returncode:
        raise ValueError('生成控制台证书失败，请检查 OpenSSL 版本。')
    key.chmod(0o600)
    cert.chmod(0o600)
