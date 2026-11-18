"""Runs on the VPS via SSH stdin; accepts only our fixed file manifest."""
import base64
import json
import os
from pathlib import Path
import platform
import pwd
import shutil
import subprocess


def probe():
    distro = {}
    for line in Path('/etc/os-release').read_text().splitlines():
        if '=' in line:
            key, val = line.split('=', 1)
            distro[key] = val.strip('"')
    if os.geteuid() != 0:
        raise ValueError('Войдите по SSH как root')
    if distro.get('ID') not in ('ubuntu', 'debian') or platform.machine() != 'x86_64':
        raise ValueError('Поддерживаются Ubuntu/Debian x86_64 с systemd')
    if not Path('/run/systemd/system').exists():
        raise ValueError('На сервере требуется systemd')
    if shutil.disk_usage('/').free < 300*1024*1024:
        raise ValueError('На сервере нужно не менее 300 МБ свободного места')
    configured = all(Path(p).is_file() for p in ('/opt/wb-telemost/creator',
        '/var/lib/wb-telemost/cookies.json','/var/lib/wb-telemost/disk-token',
        '/etc/systemd/system/wb-telemost.service'))
    return {'os': distro.get('PRETTY_NAME'), 'arch': platform.machine(), 'free': shutil.disk_usage('/').free,
            'configured':configured}


def cmd(*args, check=True):
    return subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=check)


def connection():
    if not probe()['configured']:
        raise ValueError('Сервер ещё не настроен')
    if cmd('systemctl','is-active','--quiet','wb-telemost',check=False).returncode:
        cmd('systemctl','start','wb-telemost')
    state = Path('/var/lib/wb-telemost')
    discovery = json.loads((state/'discovery.json').read_text()) if (state/'discovery.json').exists() else {'folder':'disk:/Телемост VPN'}
    return {'token':(state/'disk-token').read_text().strip(), 'folder':discovery['folder']}


def deploy(payload):
    probe()
    os.umask(0o077)
    if not shutil.which('curl'):
        cmd('apt-get', 'update', '-qq')
        cmd('apt-get', 'install', '-y', '-qq', 'curl', 'ca-certificates')
    try:
        account = pwd.getpwnam('wb-telemost')
    except KeyError:
        cmd('useradd', '--system', '--home', '/var/lib/wb-telemost', '--shell', '/usr/sbin/nologin', 'wb-telemost')
        account = pwd.getpwnam('wb-telemost')
    app, state = Path('/opt/wb-telemost'), Path('/var/lib/wb-telemost')
    app.mkdir(parents=True, exist_ok=True)
    state.mkdir(parents=True, exist_ok=True)
    state.chmod(0o700)
    os.chown(state, account.pw_uid, account.pw_gid)
    files = {
        app/'creator': (base64.b64decode(payload['files']['creator']), 0o755, False),
        app/'supervisor.py': (base64.b64decode(payload['files']['supervisor.py']), 0o644, False),
        Path('/etc/systemd/system/wb-telemost.service'): (base64.b64decode(payload['files']['wb-telemost.service']), 0o644, False),
        state/'cookies.json': (json.dumps(payload['cookies']).encode(), 0o600, True),
        state/'disk-token': (payload['token'].encode(), 0o600, True),
        state/'discovery.json': (json.dumps({'folder': payload['folder']}).encode(), 0o600, True),
    }
    active = cmd('systemctl', 'is-active', '--quiet', 'wb-telemost', check=False).returncode == 0
    enabled = cmd('systemctl', 'is-enabled', '--quiet', 'wb-telemost', check=False).returncode == 0
    backups = {path: (path.read_bytes(), path.stat().st_mode & 0o777, path.stat().st_uid, path.stat().st_gid)
               if path.exists() else None for path in [*files, state/'call.txt']}
    cmd('systemctl', 'stop', 'wb-telemost', check=False)
    try:
        for path, (data, mode, private) in files.items():
            temporary = path.with_name(path.name+'.installing')
            temporary.write_bytes(data)
            temporary.chmod(mode)
            if private:
                os.chown(temporary, account.pw_uid, account.pw_gid)
            temporary.replace(path)
        # A new setup owns its own discovery folder; do not reuse a previous room.
        if backups.get(state/'discovery.json') is None or backups[state/'discovery.json'][0] != files[state/'discovery.json'][0]:
            (state/'call.txt').unlink(missing_ok=True)
        cmd('systemctl', 'daemon-reload')
        cmd('systemctl', 'enable', '--now', 'wb-telemost')
        cmd('systemctl', 'is-active', '--quiet', 'wb-telemost')
    except Exception:
        cmd('systemctl', 'stop', 'wb-telemost', check=False)
        for path, previous in backups.items():
            if previous is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(previous[0])
                path.chmod(previous[1])
                os.chown(path, previous[2], previous[3])
        cmd('systemctl', 'daemon-reload', check=False)
        cmd('systemctl', 'enable' if enabled else 'disable', 'wb-telemost', check=False)
        if active:
            cmd('systemctl', 'start', 'wb-telemost', check=False)
        raise
    return {'installed': True, 'service': 'wb-telemost'}
