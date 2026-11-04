"""Exercise plugin handlers with isolated stores; never use live user data.

FISH_TEST_REAL_ASTRBOT=1 uses installed AstrBot decorators for server validation.
"""
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import sys
import uuid
import types
import unittest
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]


def load_plugin():
    package = types.ModuleType('fish_behavior_test_plugin')
    package.__path__ = [str(ROOT / 'plugin')]
    sys.modules[package.__name__] = package
    spec = importlib.util.spec_from_file_location(package.__name__ + '.main', ROOT / 'plugin/main.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    if os.environ.get('FISH_TEST_REAL_ASTRBOT') == '1':
        spec.loader.exec_module(module)
    else:
        modules = {name: types.ModuleType(name) for name in (
            'astrbot', 'astrbot.api', 'astrbot.api.event', 'astrbot.api.provider',
            'astrbot.api.star', 'astrbot.core', 'astrbot.core.star',
            'astrbot.core.star.filter', 'astrbot.core.star.filter.command')}
        decorators = types.SimpleNamespace(**{name: lambda *a, **kw: lambda f: f
            for name in ('command', 'on_llm_request', 'llm_tool', 'after_message_sent', 'on_llm_response')})
        modules['astrbot.api.event'].AstrMessageEvent = object
        modules['astrbot.api.event'].filter = decorators
        modules['astrbot.api.provider'].ProviderRequest = object
        for name in ('Context', 'Star', 'StarTools'):
            setattr(modules['astrbot.api.star'], name, type(name, (), {}))
        modules['astrbot.core.star.filter.command'].GreedyStr = str
        with patch.dict(sys.modules, modules):
            spec.loader.exec_module(module)
    return module


plugin = load_plugin()


class Event:
    def __init__(self, sender='alice', message='我喜欢简短回答'):
        self.sender, self.message, self.extra = sender, message, {}
        self.unified_msg_origin = 'qq:GroupMessage:group-' + sender

    def get_platform_id(self): return 'qq'
    def get_group_id(self): return 'group'
    def get_sender_id(self): return self.sender
    def get_session_id(self): return self.unified_msg_origin
    def get_sender_name(self): return '测试昵称'
    def get_message_str(self): return self.message
    def get_extra(self, key, default=None): return self.extra.get(key, default)
    def set_extra(self, key, value): self.extra[key] = value
    def plain_result(self, text): return text


class BehaviorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = ROOT / 'tests/.runs' / uuid.uuid4().hex
        self.temp.mkdir(parents=True)
        self.bot = object.__new__(plugin.Dafeiyu)
        self.bot.store = plugin.FishStore(self.temp / 'fish.db')
        self.bot.context = types.SimpleNamespace(get_using_provider_async=AsyncMock(return_value=None))
        self.event = Event()
        self.scope = self.bot.scope(self.event)

    def tearDown(self):
        for file in self.temp.iterdir(): file.unlink()
        self.temp.rmdir()

    async def status_text(self, event=None):
        return '\n'.join([text async for text in self.bot.status(event or self.event)])

    async def prepare(self, prompt='', event=None):
        request = types.SimpleNamespace(system_prompt=prompt)
        await self.bot.member_context(event or self.event, request)
        return request.system_prompt

    async def test_status_resolves_each_session_and_model_changes(self):
        models = {self.event.unified_msg_origin: 'deepseek-example', Event('bob').unified_msg_origin: 'qwen-example'}
        async def resolve(umo): return types.SimpleNamespace(get_model=lambda: models[umo])
        self.bot.context.get_using_provider_async.side_effect = resolve
        self.assertIn('配置模型：deepseek-example', await self.status_text())
        self.assertIn('配置模型：qwen-example', await self.status_text(Event('bob')))
        models[self.event.unified_msg_origin] = 'changed-model'
        self.assertIn('配置模型：changed-model', await self.status_text())
        self.assertEqual(self.bot.context.get_using_provider_async.await_count, 3)

    async def test_status_failure_never_claims_deepseek_or_exposes_error(self):
        for provider in (None, types.SimpleNamespace(get_model=lambda: ''), types.SimpleNamespace(get_model=lambda: 42)):
            self.bot.context.get_using_provider_async.return_value = provider
            self.assertIn('配置模型：未知', await self.status_text())
        self.bot.context.get_using_provider_async.side_effect = RuntimeError('SECRET-DO-NOT-EXPOSE')
        text = await self.status_text()
        self.assertIn('配置模型：未知', text)
        self.assertNotIn('SECRET', text)
        self.assertNotIn('DeepSeek', text)

    async def test_status_missing_session_does_not_fall_back_to_global(self):
        self.event.unified_msg_origin = ''
        self.assertIn('配置模型：未知', await self.status_text())
        self.bot.context.get_using_provider_async.assert_not_awaited()

    async def test_status_counts_and_read_only(self):
        self.bot.store.remember(self.scope, '称呼', '小明')
        self.bot.store.touch(self.scope)
        before = Path(self.bot.store.path).read_bytes()
        text = await self.status_text()
        self.assertIn('自动记忆：关闭', text)
        self.assertIn('1 次聊天', text)
        self.assertIn('1 条记忆', text)
        self.assertEqual(before, Path(self.bot.store.path).read_bytes())
        self.bot.store.set_auto_memory(self.scope, True)
        self.assertIn('自动记忆：开启', await self.status_text())

    async def test_legacy_rule_normalized_only_in_outgoing_prompt(self):
        persona = '我的个性：爱吐槽、喜欢蓝色。\n- ' + plugin.LEGACY_MEMORY_RULE + '\n继续保持我的自定义结尾。'
        original = persona
        result = await self.prepare(persona)
        self.assertNotIn(plugin.LEGACY_MEMORY_RULE, result)
        self.assertTrue(result.startswith(persona.replace(plugin.LEGACY_MEMORY_RULE, plugin.PERSONA_MEMORY_RULE)))
        self.assertEqual(persona, original)
        self.assertIn('自动记忆已开启": false', result)

    async def test_custom_persona_and_existing_memories_preserved(self):
        persona = '自定义人格：只用粤语。不要改动我的昵称。'
        self.bot.store.remember(self.scope, '称呼', '阿鱼')
        before = Path(self.bot.store.path).read_bytes()
        self.assertTrue((await self.prepare(persona)).startswith(persona))
        self.assertEqual(before, Path(self.bot.store.path).read_bytes())
        self.assertEqual(self.bot.store.all_memories(self.scope)[0]['value'], '阿鱼')

    async def test_auto_off_rejects_save_but_manual_save_works(self):
        await self.prepare()
        response = json.loads(await self.bot.remember_fact(self.event, '回复偏好', '简短回答', self.event.message))
        self.assertFalse(response['成功'])
        self.assertEqual(self.bot.store.all_memories(self.scope), [])
        replies = [text async for text in self.bot.remember(self.event, '称呼=小明')]
        self.assertIn('写进小本本', replies[0])
        self.assertEqual(self.bot.store.all_memories(self.scope)[0]['value'], '小明')

    async def test_auto_on_success_and_off_keeps_saved_facts(self):
        self.bot.store.set_auto_memory(self.scope, True)
        await self.prepare()
        result = json.loads(await self.bot.remember_fact(self.event, '回复偏好', '简短回答', self.event.message))
        self.assertTrue(result['成功'])
        before = self.bot.store.all_memories(self.scope)
        self.bot.store.set_auto_memory(self.scope, False)
        self.assertEqual(before, self.bot.store.all_memories(self.scope))

    async def test_save_rejects_missing_revision_stale_revision_and_bad_evidence(self):
        self.assertFalse(json.loads(await self.bot.remember_fact(self.event, '偏好', '简短', self.event.message))['成功'])
        self.bot.store.set_auto_memory(self.scope, True)
        await self.prepare()
        self.assertFalse(json.loads(await self.bot.remember_fact(self.event, '偏好', '简短', '伪造原话'))['成功'])
        self.bot.store.set_auto_memory(self.scope, False)
        self.bot.store.set_auto_memory(self.scope, True)
        self.assertFalse(json.loads(await self.bot.remember_fact(self.event, '偏好', '简短', self.event.message))['成功'])
        self.assertEqual(self.bot.store.all_memories(self.scope), [])

    async def test_save_storage_error_is_explicit_failure(self):
        await self.prepare()
        with patch.object(self.bot.store, 'auto_remember', side_effect=sqlite3.OperationalError('PRIVATE PATH')):
            response = await self.bot.remember_fact(self.event, '偏好', '简短', self.event.message)
        self.assertFalse(json.loads(response)['成功'])
        self.assertNotIn('PRIVATE', response)

    async def test_delete_success_refusal_and_storage_failure(self):
        self.bot.store.remember(self.scope, '称呼', '小明')
        self.event.message = '别忘记我的称呼'
        self.assertFalse(json.loads(await self.bot.forget_fact(self.event, '称呼', self.event.message))['成功'])
        self.event.message = '忘记我的称呼'
        with patch.object(self.bot.store, 'forget', side_effect=sqlite3.OperationalError('PRIVATE PATH')):
            response = await self.bot.forget_fact(self.event, '称呼', self.event.message)
        self.assertFalse(json.loads(response)['成功'])
        self.assertNotIn('PRIVATE', response)
        self.assertEqual(len(self.bot.store.all_memories(self.scope)), 1)
        self.assertTrue(json.loads(await self.bot.forget_fact(self.event, '称呼', self.event.message))['成功'])
        self.assertEqual(self.bot.store.all_memories(self.scope), [])


if __name__ == '__main__': unittest.main()
