#!/usr/bin/python3
"""Install the application bundle without resetting an existing connection."""
import json
import hashlib
import os
from pathlib import Path
import platform
import pwd
import shutil
import subprocess

BASE = Path(__file__).resolve().parent
APP, CONFIG, STATE = Path('/opt/wb-vpn'), Path('/etc/wb-vpn'), Path('/var/lib/wb-vpn')
MODULES = ['appcore.py','browser_auth.py','setup_backend.py','remote_install.py','manager.py',
           'preferences.py','dashboard.py','supervisor.py','network.py','check-disk.py']
UNITS = ['wb-vpn.target','wb-vpn-guard.service','wb-vpn-joiner.service','wb-vpn-tun.service',
         'wb-vpn-uplink.service','wb-vpn-uplink.timer']


def install(source,dest,mode=0o644):
    if source.resolve() != dest.resolve():
        temporary = dest.with_name(dest.name+'.installing')
        shutil.copyfile(source,temporary)
        temporary.chmod(mode)
        temporary.replace(dest)


def main():
    if os.geteuid() != 0:
        raise SystemExit('Запустите install.sh: нужны права администратора')
    if platform.machine() != 'x86_64':
        raise SystemExit('Эта сборка предназначена для Linux x86_64')
    manifest = BASE/'SHA256SUMS.json'
    if manifest.exists():
        for name,digest in json.loads(manifest.read_text()).items():
            path = (BASE/name).resolve()
            if not path.is_relative_to(BASE) or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise SystemExit('Повреждён файл комплекта: '+name)
    for name in ('ip','iptables','nft','resolvectl','systemctl','curl','ssh','pkexec'):
        if not shutil.which(name):
            raise SystemExit('Не найдена зависимость '+name+'; запустите install.sh')
    subprocess.run(['/usr/bin/python3','-c',"import gi; gi.require_version('Gtk','4.0'); gi.require_version('Adw','1'); from gi.repository import Gtk,Adw; import cairo"],check=True)
    try:
        user = pwd.getpwnam('wbtransport')
    except KeyError:
        subprocess.run(['useradd','--system','--home',str(STATE),'--shell','/usr/sbin/nologin','wbtransport'],check=True)
        user = pwd.getpwnam('wbtransport')
    for directory, mode in ((APP,0o755),(CONFIG,0o750),(STATE,0o700),(APP/'bin',0o755)):
        directory.mkdir(parents=True,exist_ok=True)
        directory.chmod(mode)
    os.chown(CONFIG,0,user.pw_gid)
    os.chown(STATE,user.pw_uid,user.pw_gid)
    for name in MODULES+['wb-telemost.service']:
        install(BASE/name,APP/name)
    install(BASE/'ssh-askpass',APP/'ssh-askpass',0o755)
    for name in ('creator','joiner','tun2socks'):
        source = BASE/'bin'/name
        if not source.exists():
            source = BASE/(name+'-optimized') if name != 'tun2socks' else APP/'tun2socks'
        if not source.exists():
            raise SystemExit('В комплекте отсутствует бинарник '+name)
        install(source,APP/'bin'/name,0o755)
        if name != 'creator':
            install(APP/'bin'/name,APP/name,0o755)
    for name in UNITS:
        install(BASE/name,Path('/etc/systemd/system')/name)
    for name in ('transport-resolv.conf','transport-nsswitch.conf'):
        if not (CONFIG/name).exists():
            install(BASE/name,CONFIG/name)
    if not (CONFIG/'transport.json').exists():
        (CONFIG/'transport.json').write_text(json.dumps({'fps':60,'batch':120,'dual_track':False}))
        os.chown(CONFIG/'transport.json',0,user.pw_gid)
        (CONFIG/'transport.json').chmod(0o640)
    for name in ('server.json','transport.json'):
        if (CONFIG/name).exists():
            install(CONFIG/name,APP/name)
    ready = all((CONFIG/name).exists() for name in ('disk-token','call.txt','server.json'))
    (APP/'setup.json').write_text(json.dumps({'ready':ready}))
    (APP/'setup.json').chmod(0o644)
    for source,dest in [('wb-vpn-ui','/usr/local/bin/wb-vpn-ui'),('wb-vpn-control','/usr/local/sbin/wb-vpn-control')]:
        Path(dest).parent.mkdir(parents=True,exist_ok=True)
        install(BASE/source,Path(dest),0o755)
    for name in ('telemost-vpn.desktop','telemost-vpn-off.desktop'):
        install(BASE/name,Path('/usr/share/applications')/name)
    subprocess.run(['systemctl','daemon-reload'],check=True)
    subprocess.run(['systemd-analyze','verify',*[str(Path('/etc/systemd/system')/name) for name in UNITS]],check=True)
    print('Приложение установлено. Откройте «Телемост VPN» в меню приложений.')


if __name__ == '__main__':
    main()
