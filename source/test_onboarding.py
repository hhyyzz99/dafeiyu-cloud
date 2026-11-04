import http.client
import json
import shutil
import threading
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import Mock

import controller
from controller import Controller, UserError, issue_ticket, read_json
from server import handler_for, ThreadingHTTPServer
from provision import nginx_config


class OnboardingTests(unittest.TestCase):
    def setUp(self):
        self.base = Path(__file__).parent / '.tests' / uuid.uuid4().hex
        self.base.mkdir(parents=True)
        self.bridge = Mock()
        self.bridge.call.return_value = {'platforms': []}
        self.initialize = Mock()
        self.check = Mock()
        self.c = Controller(self.base, bridge=self.bridge, initializer=self.initialize, model_checker=self.check)
        self.ticket = issue_ticket(self.base)
        self.password = 'FixtureOnlyPassword789'

    def tearDown(self):
        self.assertEqual(self.base.resolve().parent, (Path(__file__).parent / '.tests').resolve())
        shutil.rmtree(self.base)

    def login(self):
        return self.c.login({'ticket': self.ticket, 'password': self.password}, 'test-peer')

    def configured(self, qq=None):
        folder = self.base / 'instance/data'
        folder.mkdir(parents=True)
        owner = self.base / 'owner.hash'
        encoded = owner.read_text().strip() if owner.exists() else controller.settings.password_hash(self.password)
        controller.settings.write_private(folder / 'cmd_config.json', {
            'platform': [] if qq is None else [qq], 'dashboard': {'pbkdf2_password': encoded}})
        (self.base / 'configured.ready').touch()

    def test_claim_consumes_ticket_and_stores_hash_only(self):
        token, csrf = self.login()
        self.assertFalse((self.base / 'ticket.json').exists())
        self.assertNotIn(self.password, (self.base / 'owner.hash').read_text())
        self.assertEqual(self.c.authenticate(token, csrf)['csrf'], csrf)
        with self.assertRaises(UserError):
            self.c.login({'ticket': self.ticket, 'password': 'AnotherPassword987'}, 'peer-two')

    def test_expired_ticket_cannot_claim(self):
        controller.settings.write_private(self.base / 'ticket.json', {'token': self.ticket, 'expires': time.time()-1})
        with self.assertRaises(UserError): self.login()
        self.assertFalse((self.base / 'owner.hash').exists())

    def test_restarting_service_keeps_login_and_invalidates_old_sessions(self):
        token, _ = self.login()
        replacement = Controller(self.base, bridge=self.bridge)
        with self.assertRaises(UserError): replacement.authenticate(token)
        replacement.login({'password': self.password}, 'peer-two')

    def test_bad_csrf_or_expired_session_rejected(self):
        token, csrf = self.login()
        with self.assertRaises(UserError): self.c.authenticate(token, 'wrong')
        self.c.logout(token)
        with self.assertRaises(UserError): self.c.authenticate(token, csrf)

    def test_dashboard_password_change_revokes_old_password_and_session(self):
        token, csrf = self.login()
        self.configured()
        self.c.authenticate(token, csrf)
        path = self.base / 'instance/data/cmd_config.json'
        cfg = read_json(path)
        new_password = 'ChangedFixturePassword789'
        cfg['dashboard']['pbkdf2_password'] = controller.settings.password_hash(new_password)
        controller.settings.write_private(path, cfg)
        with self.assertRaises(UserError): self.c.authenticate(token, csrf)
        with self.assertRaises(UserError): self.c.login({'password': self.password}, 'old-password-peer')
        new_token, new_csrf = self.c.login({'password': new_password}, 'new-password-peer')
        self.c.authenticate(new_token, new_csrf)

    def test_login_rate_limited(self):
        for _ in range(6):
            with self.assertRaises(UserError): self.c.login({'ticket':'bad','password':self.password}, 'bad-peer')
        with self.assertRaises(UserError) as caught: self.c.login({'ticket':self.ticket,'password':self.password}, 'bad-peer')
        self.assertEqual(caught.exception.status, 429)

    def test_key_error_is_safe_and_can_retry(self):
        self.login()
        self.check.side_effect = ValueError('upstream echo test-private-model-key')
        with self.assertRaises(UserError) as caught: self.c.activate('test-private-model-key')
        self.assertNotIn('test-private-model-key', str(caught.exception))
        self.assertFalse(self.c.busy)
        self.assertFalse((self.base / 'configured.ready').exists())
        self.initialize.assert_not_called()
        self.check.side_effect = None
        self.c.activate('test-private-model-key')
        self.initialize.assert_called_once()

    def test_repeat_activation_does_not_overwrite_configuration(self):
        self.login(); self.configured({'id':'dafeiyu_qq','secret':'private-qq-secret'})
        before = (self.base / 'instance/data/cmd_config.json').read_bytes()
        self.c.activate('test-private-model-key')
        self.initialize.assert_not_called(); self.check.assert_not_called()
        self.assertEqual((self.base / 'instance/data/cmd_config.json').read_bytes(), before)

    def test_saved_is_distinct_from_running_and_real_reply(self):
        self.configured({'id':'dafeiyu_qq','appid':'123456','secret':'private-qq-secret'})
        status = self.c.status()
        self.assertTrue(status['qq_saved']); self.assertFalse(status['qq_running'])
        self.assertNotIn('private-qq-secret', json.dumps(status))
        with self.assertRaises(UserError): self.c.confirm_reply()
        self.bridge.call.return_value = {'platforms':[{'id':'dafeiyu_qq','status':'running'}]}
        self.assertFalse(self.c.status()['reply_confirmed'])
        self.c.confirm_reply()
        self.assertTrue(self.c.status()['reply_confirmed'])

    def test_qr_credentials_survive_failed_save_and_retry(self):
        self.configured()
        task = {'created':time.time(),'last_poll':0,'task_id':'task-test','bind_key':'test-bind-key'}
        controller.settings.write_private(self.base / 'qq-pending.json', task)
        saved = []
        def first(path, body=None):
            if path == '/api/platform/stats': return {'platforms':[]}
            if path.endswith('/registration/qq_official'): return {'status':'created','appid':'1234567','secret':'private-qq-secret'}
            if path.endswith('/new'):
                saved.append(body)
                if len(saved)==1: raise UserError('temporary',503)
                return {}
            raise AssertionError(path)
        self.bridge.call.side_effect = first
        with self.assertRaises(UserError): self.c.poll_qq()
        self.assertEqual(read_json(self.base / 'qq-pending.json')['credentials']['secret'], 'private-qq-secret')
        data = read_json(self.base / 'qq-pending.json'); data['last_poll']=0
        controller.settings.write_private(self.base / 'qq-pending.json', data)
        result = self.c.poll_qq()
        self.assertEqual(result, {'status':'saved'})
        self.assertEqual(saved[0],saved[1])
        self.assertFalse((self.base / 'qq-pending.json').exists())

    def test_expired_qr_returns_expired_without_exposing_bind_key(self):
        controller.settings.write_private(self.base / 'qq-pending.json', {'created':0,'bind_key':'private-bind-key'})
        self.assertEqual(self.c.poll_qq(), {'status':'expired'})

    def test_nginx_keeps_wizard_local_and_has_no_public_state_alias(self):
        cfg = nginx_config('101.201.238.73', True)
        self.assertIn('listen 443 ssl;', cfg)
        self.assertIn('proxy_pass http://127.0.0.1:6187',cfg)
        self.assertIn('/.well-known/acme-challenge/',cfg)
        self.assertNotIn('alias /opt/dafeiyu-onboard/state',cfg)
        with self.assertRaises(ValueError): nginx_config('127.0.0.1;evil',True)
        shared = nginx_config('101.201.238.73',True,external_http=True)
        self.assertNotIn('listen 80;', shared)
        self.assertIn('listen 443 ssl;',shared)

    def test_http_origin_csrf_body_limits_and_cookie(self):
        server = ThreadingHTTPServer(('127.0.0.1',0), lambda *args: None)
        port=server.server_port
        origin='https://101.201.238.73'
        server.RequestHandlerClass=handler_for(self.c,origin,Path(__file__).parent/'web')
        thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
        def request(method,path,body=None,extra=None):
            conn=http.client.HTTPConnection('127.0.0.1',port)
            headers={'Host':'101.201.238.73','Origin':origin,'Content-Type':'application/json',**(extra or {})}
            conn.request(method,'/setup/'+path,body=None if body is None else json.dumps(body),headers=headers)
            response=conn.getresponse(); code=response.status; out=response.read(); h=dict(response.getheaders()); conn.close()
            return code,out,h
        try:
            self.assertEqual(request('GET','api/status')[0],401)
            self.assertEqual(request('POST','api/login',{}, {'Origin':'https://evil.example'})[0],403)
            self.assertEqual(request('GET','api/public',extra={'Host':'evil.example'})[0],403)
            self.assertEqual(request('POST','api/login',{'a':'x'*5000})[0],413)
            code,out,headers=request('POST','api/login',{'ticket':self.ticket,'password':self.password})
            self.assertEqual(code,200)
            cookie=headers['Set-Cookie']
            for flag in ('HttpOnly','Secure','SameSite=Strict'): self.assertIn(flag,cookie)
            csrf=json.loads(out)['csrf']; cookie=cookie.split(';')[0]
            self.assertEqual(request('POST','api/activate',{'key':'test-private-model-key'},{'Cookie':cookie})[0],403)
            self.assertEqual(request('GET','../owner.hash',extra={'Cookie':cookie})[0],404)
            self.assertEqual(request('POST','api/activate',{'key':'test-private-model-key'},{'Cookie':cookie,'X-Fish-CSRF':csrf})[0],200)
        finally:
            server.shutdown();server.server_close();thread.join()


if __name__=='__main__': unittest.main()
