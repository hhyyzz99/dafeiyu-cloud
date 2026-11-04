import hashlib
import unittest
from prepare_release import RELEASE, rebuild_installer, repository_name


class PublicReleaseTests(unittest.TestCase):
    def test_rebuild_matches_deployed_installer(self):
        _,files,installer=rebuild_installer()
        self.assertEqual(hashlib.sha256(installer).hexdigest(),RELEASE['installer_sha256'])
        self.assertIn('plugin/main.py',files)
        self.assertNotIn('cmd_config.json',files)
        self.assertNotIn('owner.hash',files)

    def test_repository_rejects_shell_and_url_injection(self):
        self.assertEqual(repository_name('owner/dafeiyu-cloud'),'owner/dafeiyu-cloud')
        for name in ['https://github.com/a/b','a/b;id','a/b?x=1','a/b\nnext','a/b/../../x',"a/b'",'a/b$(id)']:
            with self.assertRaises(ValueError,msg=name):repository_name(name)

    def test_template_pins_installer_checksum(self):
        module,_,_=rebuild_installer()
        url='https://github.com/owner/dafeiyu-cloud/releases/download/v'+RELEASE['version']+'/install.run'
        template=module.template(url,RELEASE['installer_sha256'])
        script=template['Resources']['InstallDafeiyu']['Properties']['CommandContent']['Fn::Join'][1][0]
        self.assertIn(url,script)
        self.assertIn(RELEASE['installer_sha256'],script)
        self.assertLess(script.index('sha256sum -c'),script.index('bash "$work/install.run"'))
        self.assertNotIn('curl -k',script)


if __name__=='__main__':unittest.main()
