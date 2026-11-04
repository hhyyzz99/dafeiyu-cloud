"""Exercise the shipped memory store with isolated, temporary user records."""
import importlib.util
from pathlib import Path
import unittest
import uuid

HERE = Path(__file__).resolve().parent
ROOT = HERE if (HERE/'plugin/store.py').exists() else HERE.parent
spec = importlib.util.spec_from_file_location('release_memory_store', ROOT/'plugin/store.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class MemoryReleaseTests(unittest.TestCase):
    def setUp(self):
        self.folder = HERE/'.tests'/uuid.uuid4().hex
        self.folder.mkdir(parents=True)
        self.store = module.FishStore(self.folder/'memory.db')
        self.scope = module.scope_key('qq', 'room', 'alice')

    def tearDown(self):
        self.assertEqual(self.folder.resolve().parent, (HERE/'.tests').resolve())
        for file in self.folder.iterdir():
            file.unlink()
        self.folder.rmdir()

    def save_auto(self, value='我喜欢写代码', evidence='我喜欢写代码', revision=None):
        if revision is None:
            revision = self.store.memory_settings(self.scope)['revision']
        self.store.auto_remember(self.scope, '爱好', value, evidence, '我喜欢写代码', revision)

    def test_opt_in_required_and_persists(self):
        with self.assertRaises(ValueError):
            self.save_auto()
        self.store.set_auto_memory(self.scope, True)
        self.save_auto()
        reopened = module.FishStore(self.folder/'memory.db')
        self.assertEqual(reopened.recall(self.scope, '代码')[0]['value'], '我喜欢写代码')
        self.store.set_auto_memory(self.scope, False)
        self.assertEqual(len(self.store.all_memories(self.scope)), 1)
        with self.assertRaises(ValueError):
            self.save_auto()

    def test_member_group_and_private_auto_isolation(self):
        self.store.set_auto_memory(self.scope, True)
        self.save_auto()
        for scope in [module.scope_key('qq','room','bob'), module.scope_key('qq','other','alice'),
                      module.scope_key('qq','','alice')]:
            self.assertEqual(self.store.recall(scope, '代码'), [])
            self.assertFalse(self.store.memory_settings(scope)['enabled'])

    def test_evidence_and_sensitive_values_rejected(self):
        self.store.set_auto_memory(self.scope, True)
        with self.assertRaises(ValueError):
            self.save_auto(evidence='不是用户说的话')
        with self.assertRaises(ValueError):
            self.save_auto(value='密码 abc123')
        self.assertEqual(self.store.all_memories(self.scope), [])

    def test_manual_record_cannot_be_overwritten_automatically(self):
        self.store.remember(self.scope, '爱好', '我喜欢画画')
        self.store.set_auto_memory(self.scope, True)
        with self.assertRaises(ValueError):
            self.save_auto()
        self.assertEqual(self.store.memories(self.scope)[0]['value'], '我喜欢画画')

    def test_forget_prevents_inflight_request_from_restoring_record(self):
        self.store.set_auto_memory(self.scope, True)
        self.save_auto()
        revision = self.store.memory_settings(self.scope)['revision']
        self.store.forget(self.scope, '爱好')
        with self.assertRaises(ValueError):
            self.save_auto(revision=revision)
        self.assertEqual(self.store.all_memories(self.scope), [])


if __name__ == '__main__':
    unittest.main()
