import base64
from contextlib import nullcontext
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import appcore as core
import manager
import remote_install
import setup_backend


class Validation(unittest.TestCase):
    def test_share_roundtrip_excludes_administration_secrets(self):
        value={'version':1,'server':{'address':'192.0.2.1'},'profile':core.DEFAULT_PROFILE,
               'token':'fixture-token-123456','folder':'disk:/Телемост VPN','password':'private','cookies':['private']}
        code=core.encode_share(value)
        decoded=core.decode_share(' \n'+code+'\n')
        self.assertEqual(decoded['token'],'fixture-token-123456')
        self.assertNotIn('password',decoded)
        self.assertNotIn('cookies',decoded)
        with self.assertRaises(ValueError):
            core.decode_share(code[:-1]+('0' if code[-1]!='0' else '1'))

    def test_share_decompression_is_bounded(self):
        import gzip,hashlib
        data=base64.urlsafe_b64encode(gzip.compress(b' '*1000000)).decode().rstrip('=')
        code='TMVPN1.'+data+'.'+hashlib.sha256(data.encode()).hexdigest()[:12]
        with self.assertRaises(ValueError):
            core.decode_share(code)

    def test_profiles_reject_wrong_types_and_limits(self):
        for changes in ({'fps':True},{'fps':0},{'fps':241},{'batch':257},{'batch':2.5},{'dual_track':'false'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                core.profile(dict(core.DEFAULT_PROFILE,**changes))

    def test_server_cannot_inject_ssh_options(self):
        for address in ('-oProxyCommand=bad','host; bad','1.2.3.4\nbad'):
            with self.assertRaises(ValueError):
                core.server({'address':address})
        self.assertEqual(core.server({'address':'2001:db8::1'})['port'],22)

    def test_only_expected_cookie_domains(self):
        with self.assertRaises(ValueError):
            core.cookies([{'name':'Session_id','value':'test','domain':'notyandex.ru'}])
        self.assertEqual(len(core.cookies([{'name':'Session_id','value':'test','domain':'.yandex.ru'},
                                         {'name':'secret','value':'test','domain':'example.com'}])),1)

    def test_namespace_is_bounded(self):
        for name in ('disk:/other','disk:/Телемост VPN/../private','disk:/Телемост VPN/'+'a'*31):
            with self.assertRaises(ValueError):
                core.folder(name)

    def test_private_atomic_write(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'secret'
            core.atomic(path,'test')
            self.assertEqual(path.stat().st_mode & 0o777,0o600)
            self.assertEqual(list(Path(temp).iterdir()),[path])


class SSHTest(unittest.TestCase):
    def test_existing_server_is_reused_without_deployment(self):
        job=setup_backend.Job(lambda event:None)
        with patch.object(setup_backend,'SSH') as ssh, patch.object(setup_backend,'Disk') as disk, patch.object(setup_backend,'manager') as local:
            ssh.return_value.connection.return_value={'token':'fixture-token','folder':'disk:/Телемост VPN'}
            disk.return_value.transfer.return_value={'link':'https://telemost.yandex.ru/j/12345678901234'}
            with patch.object(setup_backend,'valid_link',return_value=True):
                setup_backend.reuse_server({'address':'192.0.2.1'},'fixture',core.DEFAULT_PROFILE,job)
            ssh.return_value.deploy.assert_not_called()
            local.assert_called_once()
        with patch.object(job.cancel_path.__class__,'unlink'):
            job.close()

    def test_password_is_not_in_arguments_and_temp_removed(self):
        password='fixture-password-only'
        captured=[]
        def run(args,**kwargs):
            self.assertNotIn(password,' '.join(args))
            self.assertNotIn(password,json.dumps(kwargs['env']))
            secret=Path(kwargs['env']['WB_SSH_PASSWORD_FILE'])
            self.assertEqual(secret.read_text(),password+'\n')
            self.assertEqual(secret.stat().st_mode & 0o777,0o600)
            captured.append(secret)
            return subprocess.CompletedProcess(args,0,'{"os":"fixture"}\n','')
        with tempfile.TemporaryDirectory() as temp, patch.object(setup_backend,'USER_CONFIG',Path(temp)), patch.object(setup_backend.subprocess,'run',run):
            self.assertEqual(setup_backend.SSH({'address':'192.0.2.1'},password).run('pass')['os'],'fixture')
            self.assertFalse(captured[0].exists())


class Tuning(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.config=self.root/'config'
        self.config.mkdir()
        (self.config/'transport.json').write_text(json.dumps(core.DEFAULT_PROFILE))
        self.patches=[patch.object(manager,'CONFIG',self.config),patch.object(manager,'APP',self.root),
            patch.object(manager,'MARKER',self.root/'testing'),patch.object(manager,'active',return_value=True),
            patch.object(manager,'run',return_value=SimpleNamespace(returncode=0,stdout='')),
            patch.object(manager,'emit'),patch.object(manager,'isolated_route',return_value=nullcontext()),
            patch.object(manager,'disk_client',return_value=SimpleNamespace(transfer=lambda *args:{'link':'https://telemost.yandex.ru/j/test'})),
            patch.object(manager,'refresh_room',return_value='https://telemost.yandex.ru/j/fresh'),
            patch.object(manager,'save_profile'),patch.object(manager,'stopping',False),patch.object(manager,'cancel_path',None)]
        for p in self.patches:p.start()

    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.temp.cleanup()

    def test_fast_profile_is_selected_despite_transfer_errors(self):
        def measurement(selected,link):
            return {'profile':selected,'ok':not selected['dual_track'],
                    'score':10000 if selected['dual_track'] else selected['fps']}
        with patch.object(manager,'measure',side_effect=measurement):
            result=manager.tune({})
        self.assertTrue(result['profile']['dual_track'])
        manager.save_profile.assert_called_once_with(result['profile'])
        self.assertFalse(manager.MARKER.exists())
        self.assertIn(unittest.mock.call('/usr/local/sbin/wb-vpn-control','connect',timeout=90),manager.run.call_args_list)

    def test_cancel_restores_room_and_does_not_save_profile(self):
        def measurement(selected,link):
            if selected['dual_track']:
                raise InterruptedError('cancel')
            return {'profile':selected,'ok':True,'score':10}
        with patch.object(manager,'measure',side_effect=measurement), self.assertRaises(InterruptedError):
            manager.tune({})
        manager.save_profile.assert_not_called()
        self.assertFalse(manager.MARKER.exists())
        self.assertFalse(manager.refresh_room.call_args.kwargs['cancellable'])
        self.assertIn(unittest.mock.call('/usr/local/sbin/wb-vpn-control','connect',timeout=90),manager.run.call_args_list)

    def test_failed_validation_keeps_old_profile(self):
        calls=0
        def measurement(selected,link):
            nonlocal calls
            calls+=1
            return {'profile':selected,'ok':calls<=6,'score':10 if calls<=6 else 0}
        with patch.object(manager,'measure',side_effect=measurement),self.assertRaises(ValueError):
            manager.tune({})
        manager.save_profile.assert_not_called()

    def test_failed_warmup_and_partial_transfers_still_measure_speed(self):
        sample={'ok':False,'speed':1000,'bytes':500,'latency':1}
        with patch.object(manager,'start_probe'), patch.object(manager,'download',return_value=sample) as download, patch.object(manager.time,'monotonic',side_effect=[10,12]):
            row=manager.measure(core.DEFAULT_PROFILE,'https://telemost.yandex.ru/j/test')
        self.assertEqual(download.call_count,5)
        self.assertFalse(row['ok'])
        self.assertEqual(row['errors'],5)
        self.assertEqual(row['score'],800)


class DeployRollback(unittest.TestCase):
    def test_local_import_failure_restores_all_previous_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            app,config,state=(root/name for name in ('app','config','state'))
            for directory in (app,config,state):
                directory.mkdir()
            (config/'disk-token').write_text('original-secret')
            (config/'disk-token').chmod(0o640)
            (state/'call.txt').write_text('original-room')
            def fail(*args):
                raise OSError('fixture write failure')
            with patch.multiple(manager,APP=app,CONFIG=config,STATE=state), patch.object(manager,'active',return_value=True), patch.object(manager,'run'), patch.object(manager,'reconnect') as reconnect, patch.object(manager,'save_profile',side_effect=fail), patch.object(manager.os,'chown'), patch.object(manager.pwd,'getpwnam',return_value=SimpleNamespace(pw_uid=1000,pw_gid=1000)), self.assertRaises(OSError):
                manager.configure({'server':{'address':'192.0.2.1'},'profile':core.DEFAULT_PROFILE,
                    'token':'fixture-token-123456','folder':'disk:/Телемост VPN','link':'https://telemost.yandex.ru/j/fixture'})
            self.assertEqual((config/'disk-token').read_text(),'original-secret')
            self.assertEqual((config/'disk-token').stat().st_mode & 0o777,0o640)
            self.assertEqual((state/'call.txt').read_text(),'original-room')
            self.assertFalse((config/'discovery.json').exists())
            reconnect.assert_called_once_with(True)

    def test_failed_service_start_restores_existing_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            def remote_path(value):return root/str(value).lstrip('/')
            app=remote_path('/opt/wb-telemost')
            state=remote_path('/var/lib/wb-telemost')
            app.mkdir(parents=True)
            state.mkdir(parents=True)
            remote_path('/etc/systemd/system').mkdir(parents=True)
            (app/'creator').write_bytes(b'old creator')
            (state/'cookies.json').write_bytes(b'old cookies')
            calls=[]
            def command(*args,**kwargs):
                calls.append(args)
                if args == ('systemctl','enable','--now','wb-telemost'):
                    raise subprocess.CalledProcessError(1,args)
                return SimpleNamespace(returncode=0)
            payload={'files':{n:base64.b64encode(b'new').decode() for n in ('creator','supervisor.py','wb-telemost.service')},
                     'cookies':[{'name':'Session_id','value':'fixture'}],'token':'fixture-token','folder':'disk:/Телемост VPN/'+'a'*32}
            with patch.object(remote_install,'Path',remote_path),patch.object(remote_install,'probe'),patch.object(remote_install,'cmd',side_effect=command),patch.object(remote_install.pwd,'getpwnam',return_value=SimpleNamespace(pw_uid=os.getuid(),pw_gid=os.getgid())):
                with self.assertRaises(subprocess.CalledProcessError):remote_install.deploy(payload)
            self.assertEqual((app/'creator').read_bytes(),b'old creator')
            self.assertEqual((state/'cookies.json').read_bytes(),b'old cookies')
            self.assertFalse((state/'disk-token').exists())
            self.assertIn(('systemctl','start','wb-telemost'),calls)


if __name__ == '__main__':
    unittest.main()
