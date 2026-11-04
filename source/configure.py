"""Initialize an independent AstrBot instance. No existing deployment is read."""
import argparse
import asyncio
import copy
import json
import os
import secrets
import shutil
from pathlib import Path

from settings import MODEL, BASE_URL, prepare, certificate, write_private


def build_config(defaults, settings, owner_hash, base):
    cfg = copy.deepcopy(defaults)
    cfg['provider_sources'] = [{
        'id': 'instance_deepseek', 'provider': 'openai', 'type': 'openai_chat_completion',
        'provider_type': 'chat_completion', 'enable': True, 'key': [settings['api_key']],
        'api_base': BASE_URL, 'timeout': 90, 'proxy': '', 'custom_headers': {},
    }]
    cfg['provider'] = [{
        'id': 'dafeiyu_deepseek', 'provider_source_id': 'instance_deepseek', 'enable': True,
        'model': settings['model'], 'modalities': ['text', 'tool_use'],
        'custom_extra_body': {'max_tokens': 2048, 'temperature': 0.85, 'thinking': {'type': 'disabled'}},
    }]
    runner = cfg['agent_runner']['config']
    runner['model'].update(provider_id='dafeiyu_deepseek', request_max_retries=1)
    runner['persona']['persona_id'] = '大肥鱼'
    runner['compression'].update(max_turns=12, trim_turns=4, overflow_strategy='truncate_by_turns')
    runner['misc'].update(max_steps=5, tool_call_timeout=15)
    cfg.update(admins_id=[], wake_prefix=['/'], plugin_set=['astrbot_plugin_tom_fish'], platform=[], timezone='Asia/Shanghai')
    cfg['platform_settings'].update(unique_session=True, enable_id_white_list=False,
        ignore_bot_self_message=True, ignore_at_all=True,
        rate_limit={'time': 60, 'count': 12, 'strategy': 'discard'})
    cfg['provider_settings'].update(display_reasoning_text=False, identifier=True, web_search=False,
        computer_use_runtime='none', datetime_system_prompt=True,
        proactive_capability={'add_cron_tools': False})
    cfg['provider_ltm_settings']['active_reply']['enable'] = False
    cfg['provider_ltm_settings']['group_message_history_enable'] = False
    cfg['dashboard'].update(host='127.0.0.1' if settings['local_only'] else '0.0.0.0',
        port=settings['port'], username='fish', password='', pbkdf2_password=owner_hash,
        password_storage_upgraded=True, password_change_required=False,
        jwt_secret=secrets.token_urlsafe(48), trust_proxy_headers=False,
        ssl={'enable': not settings['local_only'], 'cert_file': str(base / 'instance/tls/cert.pem'),
             'key_file': str(base / 'instance/tls/key.pem'), 'ca_certs': ''})
    return cfg


async def save_persona(data, source):
    from astrbot.core.db.sqlite import SQLiteDatabase
    db = SQLiteDatabase(str(data / 'data_v4.db'))
    try:
        await db.initialize()
        if not await db.get_persona_by_id('大肥鱼'):
            await db.insert_persona(persona_id='大肥鱼',
                system_prompt=(source / 'persona.md').read_text(encoding='utf-8'),
                tools=['fish_time', 'fish_calculate', 'fish_remember', 'fish_recall', 'fish_forget'], skills=[],
                custom_error_message='鱼这边暂时没接上模型，请稍后再试。')
    finally:
        await db.engine.dispose()


def configure(base, settings, source):
    if (base / 'configured.ready').exists():
        print('已有配置，保留账号、QQ 绑定、聊天和记忆。')
        return
    root = base / 'instance'
    data = root / 'data'
    data.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.environ['ASTRBOT_ROOT'] = str(root)
    (root / '.astrbot').touch()
    target = data / 'cmd_config.json'
    owner_hash = (base / 'owner.hash').read_text(encoding='utf-8').strip()
    # Seed before importing AstrBot: its import initializes a global config.
    # The pending marker allows interrupted initialization to resume safely.
    pending = base / 'configure.pending'
    if target.exists() and not pending.exists():
        raise ValueError('发现非本安装流程创建的配置，已停止以免覆盖。')
    pending.touch()
    write_private(target, {'dashboard': {'pbkdf2_password': owner_hash, 'password': '',
        'password_storage_upgraded': True, 'password_change_required': False}})
    from astrbot.core.config.default import DEFAULT_CONFIG
    write_private(target, build_config(DEFAULT_CONFIG, settings, owner_hash, base))
    plugin = data / 'plugins' / 'astrbot_plugin_tom_fish'
    shutil.copytree(source / 'plugin', plugin, dirs_exist_ok=True)
    (plugin / '__init__.py').touch()
    asyncio.run(save_persona(data, source))
    # Use the bundled, unmodified Dashboard at /; no dependency on another website.
    (base / 'configured.ready').touch()
    pending.unlink()
    print('人设、群友记忆和工具配置完成。')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base', default='/opt/dafeiyu-kit')
    parser.add_argument('--port', type=int, default=6186)
    parser.add_argument('--public-ip', default='')
    parser.add_argument('--local-only', action='store_true')
    parser.add_argument('--env-file')
    parser.add_argument('--model', default=MODEL)
    parser.add_argument('--prepare-only', action='store_true')
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error('端口应在 1024 到 65535 之间。')
    base = Path(args.base).resolve()
    if args.prepare_only:
        prepare(base, args)
        return
    settings = json.loads((base / 'install-settings.json').read_text(encoding='utf-8'))
    certificate(base, settings)
    configure(base, settings, Path(__file__).resolve().parent)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError) as exc:
        raise SystemExit(str(exc)) from None
