import io
import json
import os
import select
import shutil
import sqlite3
import sys
import tarfile
import time
import unittest
import urllib.error
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from contextlib import closing
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build
import configure
import manage
import settings


def default_shape():
    # The installer configures these supported sections of AstrBot 4.29.4.
    return {'agent_runner': {'config': {'model': {}, 'persona': {}, 'compression': {}, 'misc': {}}},
            'platform_settings': {}, 'provider_settings': {}, 'provider_ltm_settings': {'active_reply': {}},
            'dashboard': {}, 'platform': [{'secret': 'old-test-secret'}]}


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.base = Path(__file__).parent / '.runs' / uuid.uuid4().hex
        self.base.mkdir(parents=True)
        self.args = SimpleNamespace(port=6186, local_only=True, public_ip='', model=settings.MODEL, env_file=None)
        self.sample = {'api_key': 'test-local-only-key', 'model': settings.MODEL,
                       'port': 6186, 'public_ip': '', 'local_only': True}

    def tearDown(self):
        self.assertEqual(self.base.resolve().parent, (Path(__file__).parent / '.runs').resolve())
        shutil.rmtree(self.base)

    def prepare(self):
        with patch.object(settings, 'discover_key', return_value=('test-local-only-key', 'test')), \
                patch.object(settings, 'check_port'), patch.object(settings, 'check_model'):
            return settings.prepare(self.base, self.args)

    def test_env_is_literal_and_bom_quotes_supported(self):
        file = self.base / 'model.env'
        file.write_text('\ufeff# comment\nexport MODEL_BASE_URL="' + settings.BASE_URL +
                        '"\nMODEL_API_KEY=\'test-secret\'\nIGNORED=$(touch never-run)\n', encoding='utf-8')
        data = settings.read_env(file)
        self.assertEqual(data['IGNORED'], '$(touch never-run)')
        self.assertEqual(settings.discover_key(file, environ={})[0], 'test-secret')

    def test_unrelated_provider_key_is_never_reused(self):
        key, _ = settings.discover_key(environ={'OPENAI_API_KEY': 'unrelated-secret',
            'OPENAI_BASE_URL': 'https://unrelated.example/v1'}, candidates=[])
        self.assertIsNone(key)
        self.assertIsNone(settings.bundled_key({'OPENAI_API_KEY': 'unpaired-secret'}))

    def test_explicit_config_can_replace_stale_environment_key(self):
        file = self.base / 'replacement.env'
        file.write_text('MODEL_BASE_URL=' + settings.BASE_URL + '\nMODEL_API_KEY=test-new-key', encoding='utf-8')
        key, _ = settings.discover_key(file, environ={'MODEL_BASE_URL': settings.BASE_URL,
                                                       'MODEL_API_KEY': 'test-stale-key'})
        self.assertEqual(key, 'test-new-key')

    def test_key_and_endpoint_must_come_from_same_source(self):
        file = self.base / 'test.env'
        file.write_text('OPENAI_BASE_URL=' + settings.BASE_URL, encoding='utf-8')
        key, _ = settings.discover_key(environ={'OPENAI_API_KEY': 'unrelated-secret'}, candidates=[file])
        self.assertIsNone(key)

    def test_api_key_rejects_header_injection(self):
        for key in ['', 'short', 'test-secret\nInjected: yes', 'contains spaces']:
            with self.assertRaises(ValueError):
                settings.validate_key(key)

    def test_fresh_config_has_no_qq_credentials_and_has_actual_tools(self):
        original = default_shape()
        cfg = configure.build_config(original, self.sample, 'test-hash', self.base)
        self.assertEqual(cfg['platform'], [])
        self.assertEqual(original['platform'][0]['secret'], 'old-test-secret')
        self.assertNotIn('old-test-secret', json.dumps(cfg))
        self.assertIn('tool_use', cfg['provider'][0]['modalities'])
        self.assertEqual(cfg['dashboard']['host'], '127.0.0.1')
        self.assertFalse(cfg['dashboard']['ssl']['enable'])
        self.assertEqual(cfg['admins_id'], [])
        self.assertFalse(cfg['provider_settings']['proactive_capability']['add_cron_tools'])

    def test_public_panel_requires_tls_and_unique_jwt(self):
        self.sample['local_only'] = False
        a = configure.build_config(default_shape(), self.sample, 'h', self.base)
        b = configure.build_config(default_shape(), self.sample, 'h', self.base)
        self.assertTrue(a['dashboard']['ssl']['enable'])
        self.assertNotEqual(a['dashboard']['jwt_secret'], b['dashboard']['jwt_secret'])

    def test_repeat_prepare_keeps_owner_password_and_hash(self):
        self.prepare()
        before = (self.base / 'owner-login.json').read_bytes()
        hashed = (self.base / 'owner.hash').read_bytes()
        self.prepare()
        self.assertEqual((self.base / 'owner-login.json').read_bytes(), before)
        self.assertEqual((self.base / 'owner.hash').read_bytes(), hashed)

    def test_owner_hash_interruption_can_resume(self):
        self.prepare()
        owner = (self.base / 'owner-login.json').read_bytes()
        (self.base / 'owner.hash').unlink()
        self.prepare()
        self.assertEqual((self.base / 'owner-login.json').read_bytes(), owner)
        self.assertTrue((self.base / 'owner.hash').read_text().startswith('pbkdf2_sha256$600000$'))

    def test_changed_resume_arguments_do_not_modify_settings(self):
        self.prepare()
        before = (self.base / 'install-settings.json').read_bytes()
        self.args.port = 6187
        with self.assertRaises(ValueError):
            self.prepare()
        self.assertEqual((self.base / 'install-settings.json').read_bytes(), before)

    def test_failed_model_check_does_not_commit_credentials(self):
        with patch.object(settings, 'discover_key', return_value=('test-local-only-key', 'test')), \
                patch.object(settings, 'check_port'), patch.object(settings, 'check_model', side_effect=ValueError('bad key')):
            with self.assertRaises(ValueError):
                settings.prepare(self.base, self.args)
        self.assertFalse((self.base / 'owner-login.json').exists())
        self.assertFalse((self.base / 'install-settings.json').exists())

    def test_configured_instance_is_not_overwritten(self):
        target = self.base / 'instance/data'
        target.mkdir(parents=True)
        value = b'{"platform":[{"secret":"customer-test-secret"}]}'
        (target / 'cmd_config.json').write_bytes(value)
        (self.base / 'configured.ready').touch()
        configure.configure(self.base, self.sample, self.base)
        self.assertEqual((target / 'cmd_config.json').read_bytes(), value)

    def test_unowned_config_is_rejected_without_overwrite(self):
        target = self.base / 'instance/data'
        target.mkdir(parents=True)
        (target / 'cmd_config.json').write_text('customer-data', encoding='utf-8')
        (self.base / 'owner.hash').write_text('test-hash', encoding='utf-8')
        with self.assertRaises(ValueError):
            configure.configure(self.base, self.sample, self.base)
        self.assertEqual((target / 'cmd_config.json').read_text(), 'customer-data')

    def test_error_messages_never_echo_remote_body_or_key(self):
        secret = 'do-not-print-test-secret'
        class FakeOpener:
            def open(self, request, timeout):
                raise urllib.error.HTTPError(request.full_url, 401, 'error', {}, io.BytesIO(secret.encode()))
        with self.assertRaises(ValueError) as caught:
            settings.check_model(secret, settings.MODEL, opener=FakeOpener())
        self.assertIn('401', str(caught.exception))
        self.assertNotIn(secret, str(caught.exception))
        self.assertIsNone(settings.NoRedirect().redirect_request(None, None, 302, '', {}, 'https://elsewhere.example'))

    def test_backup_keeps_seven_valid_sqlite_snapshots_and_unrelated_data(self):
        data = self.base / 'instance/data'
        data.mkdir(parents=True)
        (data / 'cmd_config.json').write_text('{}', encoding='utf-8')
        with closing(sqlite3.connect(data / 'data_v4.db')) as db:
            db.execute('CREATE TABLE test (value TEXT)')
            db.execute('INSERT INTO test VALUES (?)', ('saved-memory',))
            db.commit()
        unrelated = self.base / 'backups/keep-this'
        unrelated.mkdir(parents=True)
        original_mkdir = Path.mkdir
        def test_mkdir(path, mode=0o777, parents=False, exist_ok=False):
            # Windows sandbox ACLs do not emulate Linux mkdir(0700). Test the
            # actual backup/retention logic with inherited Windows ACLs instead.
            return original_mkdir(path, mode=0o777 if os.name == 'nt' else mode,
                                  parents=parents, exist_ok=exist_ok)
        with patch.object(Path, 'mkdir', test_mkdir):
            for _ in range(9):
                manage.backup(self.base)
        completed = [p for p in unrelated.parent.iterdir() if (p / 'complete').exists()]
        self.assertEqual(len(completed), 7)
        self.assertTrue(unrelated.exists())
        with closing(sqlite3.connect(completed[-1] / 'astrbot.db')) as db:
            self.assertEqual(db.execute('SELECT value FROM test').fetchone()[0], 'saved-memory')

    def test_archive_uses_allowlist_and_contains_no_live_deployment(self):
        files = build.payload_files()
        raw = build.archive(files)
        with tarfile.open(fileobj=io.BytesIO(raw), mode='r:gz') as tar:
            self.assertTrue(all(member.isfile() for member in tar.getmembers()))
            blob = b'\n'.join(tar.extractfile(m).read() for m in tar.getmembers())
        for name in files:
            self.assertNotIn('.owner-login', name)
            self.assertFalse(name.endswith(('.db', '.env', '.pem')))
        # Independently check the known local owner password without logging it.
        local_owner = build.PROJECT / '.owner-login.json'
        if local_owner.exists():
            owner = json.loads(local_owner.read_text(encoding='utf-8'))
            self.assertFalse(owner['password'].encode() in blob, 'Live owner credential found in distribution')
            self.assertFalse(urlsplit(owner['url']).hostname.encode() in blob, 'Live host found in distribution')


@unittest.skipUnless(os.name == 'posix', 'Requires a real POSIX controlling terminal')
class TerminalPromptTests(unittest.TestCase):
    def exercise_prompt(self, hidden):
        import pty
        pid, master = pty.fork()
        if pid == 0:
            try:
                # Match the installer: normal stdout/stderr are redirected.
                null = os.open(os.devnull, os.O_WRONLY)
                os.dup2(null, 1)
                os.dup2(null, 2)
                os.close(null)
                value = settings.prompt('PROMPT>', hidden=hidden)
                with open('/dev/tty', 'w') as terminal:
                    terminal.write('RESULT:' + value + '\n')
                os._exit(0)
            except BaseException:
                os._exit(1)
        output = b''
        sent = False
        reaped = False
        sample = b'test-secret-only' if hidden else b'101.201.1.2'
        deadline = time.monotonic() + 10
        try:
            while time.monotonic() < deadline:
                if not select.select([master], [], [], 0.2)[0]:
                    continue
                try:
                    chunk = os.read(master, 4096)
                except OSError:
                    break
                if not chunk:
                    break
                output += chunk
                if b'PROMPT>' in output and not sent:
                    os.write(master, sample + b'\n')
                    sent = True
                if b'RESULT:' + sample in output:
                    break
            self.assertTrue(sent, output)
            self.assertIn(b'RESULT:' + sample, output)
            if hidden:
                self.assertNotIn(sample, output.split(b'RESULT:')[0])
            _, status = os.waitpid(pid, 0)
            reaped = True
            self.assertEqual(status, 0)
        finally:
            os.close(master)
            if not reaped:
                try:
                    os.kill(pid, 9)
                except ProcessLookupError:
                    pass
                os.waitpid(pid, 0)

    def test_hidden_key_prompt_with_redirected_output(self):
        self.exercise_prompt(True)

    def test_public_ip_prompt_with_redirected_output(self):
        self.exercise_prompt(False)


if __name__ == '__main__':
    unittest.main()
