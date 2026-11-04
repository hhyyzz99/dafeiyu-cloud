import sys
import uuid
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'plugin'))
from store import FishStore, calculate, scope_key


class FishTests(unittest.TestCase):
    def setUp(self):
        self.temp = Path(__file__).parent / '.runs' / uuid.uuid4().hex
        self.temp.mkdir(parents=True)
        self.path = self.temp / 'fish.db'
        self.store = FishStore(self.path)
        self.scope = scope_key('qq', 'group1', 'alice')

    def tearDown(self):
        for file in self.temp.iterdir(): file.unlink()
        self.temp.rmdir()

    def test_member_group_and_private_isolation(self):
        self.store.remember(self.scope, '称呼', '小明')
        for other in [scope_key('qq', 'group1', 'bob'), scope_key('qq', 'group2', 'alice'),
                      scope_key('qq', '', 'alice'), scope_key('qq2', 'group1', 'alice')]:
            self.assertEqual(self.store.memories(other), [])

    def test_persists_and_updates_without_duplicates(self):
        self.store.remember(self.scope, '称呼', '小明')
        self.store.remember(self.scope, '称呼', '阿明')
        self.assertEqual(FishStore(self.path).memories(self.scope)[0]['value'], '阿明')
        self.assertEqual(len(self.store.memories(self.scope)), 1)

    def test_forget_only_own_record(self):
        other = scope_key('qq', 'group1', 'bob')
        self.store.remember(self.scope, '称呼', '阿明')
        self.store.remember(other, '称呼', '小红')
        self.assertTrue(self.store.forget(self.scope, '称呼'))
        self.assertFalse(self.store.forget(self.scope, '称呼'))
        self.assertEqual(self.store.memories(other)[0]['value'], '小红')

    def test_bounded_memory_allows_existing_update(self):
        for n in range(12): self.store.remember(self.scope, str(n), '普通偏好')
        with self.assertRaises(ValueError): self.store.remember(self.scope, '第十三条', '超出')
        self.store.remember(self.scope, '0', '更新')
        with self.assertRaises(ValueError): self.store.remember(self.scope, '长文本', 'a'*181)

    def test_member_count_survives_restart(self):
        self.store.touch(self.scope)
        self.store.touch(self.scope)
        self.assertEqual(FishStore(self.path).status(self.scope)['turns'], 2)

    def test_missing_identity_rejected(self):
        with self.assertRaises(ValueError): scope_key('qq', 'group', '')

    def test_private_memory_survives_new_conversation(self):
        self.assertEqual(scope_key('webchat', '', 'tom', 'conversation1'),
                         scope_key('webchat', '', 'tom', 'conversation2'))

    def test_calculator_real_arithmetic(self):
        self.assertEqual(calculate('(1250 + 750) / 4'), '500.0')
        self.assertEqual(calculate('-2**3 + 1'), '-7')

    def test_calculator_rejects_code_and_resource_abuse(self):
        for expr in ['__import__("os").getcwd()', '2**999999999', '1/0', '(-1)**0.5', 'True+1', '10**12 * 10**12']:
            with self.assertRaises(ValueError, msg=expr): calculate(expr)


if __name__ == '__main__': unittest.main()
