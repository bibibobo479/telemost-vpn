#!/usr/bin/python3
"""Fixed privileged actions. No shell commands or arbitrary paths from the GUI."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import fcntl
import ipaddress
import json
import os
from pathlib import Path
import pwd
import re
import signal
import statistics
import subprocess as sp
import sys
import time
import uuid
from appcore import DEFAULT_PROFILE, Disk, atomic, folder, profile, read_json, server, valid_link, encode_share, decode_share

APP, CONFIG, STATE = Path('/opt/wb-vpn'), Path('/etc/wb-vpn'), Path('/var/lib/wb-vpn')
RUN = Path('/run/wb-vpn-probe')
MARKER = Path('/run/wb-vpn-testing')
cancel_path = None
stopping = False


def emit(message, **fields):
    print(json.dumps(dict(message=message, **fields), ensure_ascii=False), flush=True)


def check_cancel():
    if stopping or (cancel_path and cancel_path.exists()):
        raise InterruptedError('Операция отменена')


def run(*args, check=True, timeout=45):
    return sp.run(args, text=True, stdout=sp.PIPE, stderr=sp.PIPE, timeout=timeout, check=check)


def active():
    return run('systemctl', 'is-active', '--quiet', 'wb-vpn.target', check=False).returncode == 0


def reconnect(was_active):
    if was_active:
        run('/usr/local/sbin/wb-vpn-control', 'connect', timeout=90)


def write_private(name, value):
    path = CONFIG/name
    atomic(path, value, 0o640)
    os.chown(path, 0, pwd.getpwnam('wbtransport').pw_gid)


def save_profile(value):
    value = profile(value)
    write_private('transport.json', json.dumps(value))
    atomic(APP/'transport.json', json.dumps(value), 0o644)


def disk_client():
    return Disk((CONFIG/'disk-token').read_text().strip(),
                read_json(CONFIG/'discovery.json', {'folder':'disk:/Телемост VPN'})['folder'])


def refresh_room(disk, cancellable=True):
    old = disk.transfer('room.json').get('link')
    disk.transfer('refresh.json', {'id':uuid.uuid4().hex, 'time':time.time()})
    deadline = time.monotonic()+180
    while time.monotonic() < deadline:
        if cancellable:
            check_cancel()
        try:
            link = disk.transfer('room.json').get('link')
            if valid_link(link) and link != old:
                account = pwd.getpwnam('wbtransport')
                atomic(STATE/'call.txt', link+'\n')
                os.chown(STATE/'call.txt', account.pw_uid, account.pw_gid)
                return link
        except (OSError, ValueError, KeyError):
            pass
        time.sleep(2)
    raise ValueError('Сервер не обновил комнату. Настройки сохранены прежними; проверьте доступ к Яндексу')


def configure(data):
    address, selected = server(data['server']), profile(data['profile'])
    disk = Disk(data['token'], data['folder'])
    if not valid_link(data['link']):
        raise ValueError('Некорректная ссылка комнаты')
    paths = [CONFIG/name for name in ('disk-token','discovery.json','call.txt','server.json','transport.json')]
    paths += [STATE/'call.txt',APP/'transport.json',APP/'server.json',APP/'setup.json']
    previous = {}
    for path in paths:
        if path.exists():
            stat = path.stat()
            previous[path] = (path.read_bytes(),stat.st_mode & 0o777,stat.st_uid,stat.st_gid)
        else:
            previous[path] = None
    check_cancel()
    was_active = active()
    if was_active:
        run('/usr/local/sbin/wb-vpn-control', 'disconnect', timeout=90)
    try:
        write_private('disk-token', disk.token+'\n')
        write_private('discovery.json', json.dumps({'folder':disk.folder}))
        write_private('call.txt', data['link']+'\n')
        write_private('server.json', json.dumps(address))
        account = pwd.getpwnam('wbtransport')
        atomic(STATE/'call.txt', data['link']+'\n')
        os.chown(STATE/'call.txt', account.pw_uid, account.pw_gid)
        save_profile(selected)
        atomic(APP/'server.json', json.dumps(address), 0o644)
        atomic(APP/'setup.json', json.dumps({'ready':True, 'folder':disk.folder}), 0o644)
    except Exception:
        for path,backup in previous.items():
            if backup is None:
                path.unlink(missing_ok=True)
            else:
                contents,mode,uid,gid = backup
                atomic(path,contents,mode)
                os.chown(path,uid,gid)
        raise
    finally:
        reconnect(was_active)
    return {'message':'Сервер настроен. Можно подключаться', 'profile':selected}


def settings(data):
    selected = profile(data['profile'])
    previous = profile(read_json(CONFIG/'transport.json', DEFAULT_PROFILE))
    was_active = active()
    if was_active:
        run('/usr/local/sbin/wb-vpn-control', 'disconnect', timeout=90)
    try:
        if selected['dual_track'] != previous['dual_track']:
            emit('Обновляем комнату для переключения видеоканалов…')
            refresh_room(disk_client())
        check_cancel()
        save_profile(selected)
    finally:
        reconnect(was_active)
    return {'message':'Настройки сохранены и будут переданы серверу при подключении', 'profile':selected}


def export_connection(data):
    disk = disk_client()
    code = encode_share({'version':1,'server':read_json(CONFIG/'server.json'),
        'profile':read_json(CONFIG/'transport.json',DEFAULT_PROFILE),'token':disk.token,'folder':disk.folder})
    return {'message':'Код готов. Передавайте только своим устройствам','code':code}


def import_connection(data):
    value = decode_share(data.get('code'))
    disk = Disk(value['token'],value['folder'])
    emit('Проверяем код и получаем актуальную комнату…')
    link = disk.transfer('room.json').get('link')
    if not valid_link(link):
        raise ValueError('Сервер не опубликовал комнату')
    check_cancel()
    return configure(dict(value,link=link))


@contextmanager
def isolated_route():
    uid = str(pwd.getpwnam('wbtransport').pw_uid)
    route_added = rule_added = firewall_added = False
    firewall = ['-m','owner','--uid-owner',uid,'-m','comment','--comment','wb-vpn-autotune','-j','ACCEPT']
    try:
        rules = json.loads(run('ip','-j','-4','rule','show').stdout)
        if any(r.get('priority') == 10079 for r in rules) or json.loads(run('ip','-j','-4','route','show','table','10989',check=False).stdout or '[]'):
            raise ValueError('Маршрут тестирования уже занят')
        routes = json.loads(run('ip','-j','-4','route','show','table','main','default').stdout)
        routes = [r for r in routes if r.get('gateway') and Path('/sys/class/net',r.get('dev',''),'device').exists()]
        if not routes:
            raise ValueError('Не найдено подключение Wi-Fi/Ethernet')
        route = min(routes,key=lambda r:r.get('metric',0))
        RUN.mkdir(mode=0o750, exist_ok=True)
        os.chown(RUN, 0, pwd.getpwnam('wbtransport').pw_gid)
        raw = run('resolvectl','dns',route['dev']).stdout.split(':',1)[-1].split()
        dns = []
        for address in raw:
            try:
                if ipaddress.ip_address(address).version == 4:
                    dns.append(address)
            except ValueError:
                pass
        atomic(RUN/'resolv.conf',''.join('nameserver '+v+'\n' for v in (dns+['77.88.8.8'])[:3]),0o644)
        run('ip','-4','route','add','table','10989','default','via',route['gateway'],'dev',route['dev'],'onlink')
        route_added = True
        run('ip','-4','rule','add','priority','10079','uidrange',uid+'-'+uid,'lookup','10989')
        rule_added = True
        run('iptables','-w','-I','OUTPUT','1',*firewall)
        firewall_added = True
        yield
    finally:
        run('systemctl','stop','wb-vpn-probe.service',check=False)
        if firewall_added:
            run('iptables','-w','-D','OUTPUT',*firewall,check=False)
        if rule_added:
            run('ip','-4','rule','del','priority','10079','uidrange',uid+'-'+uid,'lookup','10989',check=False)
        if route_added:
            run('ip','-4','route','flush','table','10989',check=False)
        (RUN/'call.txt').unlink(missing_ok=True)


def start_probe(link, selected):
    run('systemctl','stop','wb-vpn-probe.service',check=False)
    run('systemctl','reset-failed','wb-vpn-probe.service',check=False)
    for name, content in [('call.txt',link),('profile.json',json.dumps(selected))]:
        atomic(RUN/name,content,0o640)
        os.chown(RUN/name,0,pwd.getpwnam('wbtransport').pw_gid)
    atomic(STATE/'probe.log','',0o600)
    run('systemd-run','--quiet','--collect','--unit=wb-vpn-probe', '--property=User=wbtransport',
        '--property=RuntimeMaxSec=240','--property=Environment=GODEBUG=netdns=go',
        '--property=BindReadOnlyPaths=/run/wb-vpn-probe/resolv.conf:/etc/resolv.conf',
        '--property=BindReadOnlyPaths=/etc/wb-vpn/transport-nsswitch.conf:/etc/nsswitch.conf',
        '--property=RestrictAddressFamilies=AF_INET AF_UNIX AF_NETLINK',
        '--property=StandardOutput=append:/var/lib/wb-vpn/probe.log',
        '--property=StandardError=append:/var/lib/wb-vpn/probe.log',
        '/usr/bin/python3',str(APP/'manager.py'),'worker')
    for _ in range(10):
        check_cancel()
        time.sleep(1)


def download(amount=1048576):
    args = ['curl','--noproxy','','--proxy','socks5h://127.0.0.1:1089','-sS','-o','/dev/null',
            '--max-time','35','-w','%{json}',f'https://speed.cloudflare.com/__down?bytes={amount}']
    child = sp.Popen(args,stdout=sp.PIPE,stderr=sp.DEVNULL,text=True)
    try:
        while child.poll() is None:
            check_cancel()
            time.sleep(.2)
        raw = child.stdout.read()
        data = json.loads(raw)
        return {'ok':child.returncode == 0 and data.get('http_code') == 200 and data.get('size_download') == amount,
                'speed':data.get('speed_download',0), 'latency':data.get('time_starttransfer',35), 'bytes':data.get('size_download',0)}
    finally:
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=5)
        child.stdout.close()


def measure(selected, link):
    start_probe(link,selected)
    try:
        warm = download(1024)
        singles = [download(),download()]
        started = time.monotonic()
        with ThreadPoolExecutor(max_workers=2) as pool:
            parallel = list(pool.map(lambda _:download(),range(2)))
        elapsed = time.monotonic()-started
        errors = sum(not item['ok'] for item in [warm]+singles+parallel)
        single = statistics.median(item['speed'] for item in singles)
        aggregate = sum(item['bytes'] for item in parallel)/elapsed
        return {'profile':selected,'ok':errors == 0,'errors':errors,'single':single,'aggregate':aggregate,
                'latency':statistics.median(item['latency'] for item in singles),
                'score':.6*single+.4*aggregate}
    finally:
        run('systemctl','stop','wb-vpn-probe.service',check=False)


def choose(rows):
    return sorted((r for r in rows if r.get('score',0)>0),key=lambda r:r['score'],reverse=True)


def tune(data):
    previous = profile(read_json(CONFIG/'transport.json',DEFAULT_PROFILE))
    disk = disk_client()
    was_active = active()
    selected, rows = None, []
    committed = False
    dual_used = False
    marker = MARKER
    atomic(marker,'autotune\n',0o644)
    candidates = [previous]+[{'fps':f,'batch':b,'dual_track':d} for f,b,d in (
        (24,30,False),(48,60,False),(60,120,False),(90,120,False),(24,30,True),(60,120,True))]
    candidates = list({(p['fps'],p['batch'],p['dual_track']):p for p in candidates}.values())
    try:
        if was_active:
            run('/usr/local/sbin/wb-vpn-control','disconnect',timeout=90)
        with isolated_route():
            link = disk.transfer('room.json').get('link')
            if not valid_link(link):
                raise ValueError('Нет актуальной комнаты; повторите настройку сервера')
            previous_dual = previous['dual_track']
            for index, candidate in enumerate(candidates):
                check_cancel()
                emit(f"Профиль {index+1}/{len(candidates)}: FPS {candidate['fps']}, batch {candidate['batch']}, каналов {2 if candidate['dual_track'] else 1}", progress=index/len(candidates))
                if candidate['dual_track'] or previous_dual:
                    emit('Обновляем комнату для независимого замера…')
                    link = refresh_room(disk)
                dual_used = dual_used or candidate['dual_track']
                previous_dual = candidate['dual_track']
                row = measure(candidate,link)
                rows.append(row)
                emit('Замер завершён' if row['ok'] else 'Замер завершён с ошибками; скорость учтена', type='measurement', measurement=row)
            winners = choose(rows)
            if not winners:
                raise ValueError('Не удалось измерить положительную скорость. Прежние настройки сохранены')
            for winner in winners:
                check_cancel()
                emit('Повторно проверяем лучший профиль…')
                link = refresh_room(disk)
                verified = measure(winner['profile'],link)
                if verified.get('score',0)>0:
                    selected = winner['profile']
                    break
            if selected is None:
                raise ValueError('При повторном замере скорость нулевая. Прежние настройки сохранены')
        check_cancel()
        save_profile(selected)
        committed = True
        report = {'time':time.time(),'selected':selected,'results':rows,'validation':verified}
        atomic(APP/'autotune.json',json.dumps(report,ensure_ascii=False),0o644)
    finally:
        # A failed/cancelled dual-track experiment must not poison the old session.
        try:
            if not committed and dual_used:
                emit('Восстанавливаем комнату после теста…')
                refresh_room(disk,cancellable=False)
        finally:
            marker.unlink(missing_ok=True)
            reconnect(was_active)
    return {'message':'Автоподбор завершён. Лучший проверенный профиль сохранён', 'profile':selected, 'report':report}


def worker():
    selected = profile(read_json(RUN/'profile.json'))
    link = (RUN/'call.txt').read_text().strip()
    if not valid_link(link):
        raise ValueError('Invalid room')
    args = [str(APP/'joiner'),'--tm-link',link,'--socks-host','127.0.0.1','--socks-port','1089','--reliable',
            '--vp8-fps',str(selected['fps']),'--vp8-batch',str(selected['batch'])]
    if selected['dual_track']:
        args.append('--dual-track')
    os.execv(args[0],args)


def main():
    global cancel_path, stopping
    if sys.argv[1:] == ['worker']:
        worker()
        return
    actions = {'configure':configure,'settings':settings,'tune':tune,'export':export_connection,'import':import_connection}
    if os.geteuid() != 0 or len(sys.argv) != 2 or sys.argv[1] not in actions:
        raise SystemExit('Use the application to configure this service')
    os.umask(0o077)
    def stop(*_):
        global stopping
        stopping = True
    signal.signal(signal.SIGTERM,stop)
    signal.signal(signal.SIGINT,stop)
    with open('/run/wb-vpn-operation.lock','w') as lock:
        try:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            data = json.loads(sys.stdin.readline(65537))
            operation = data.get('operation','')
            if not re.fullmatch('[a-f0-9]{32}',operation):
                raise ValueError('Некорректный идентификатор операции')
            uid = int(os.environ.get('PKEXEC_UID',os.getuid()))
            cancel_path = Path(f'/run/user/{uid}/wb-vpn-cancel-{operation}')
            check_cancel()
            result = actions[sys.argv[1]](data)
            emit(result.pop('message'),type='result',ok=True,**result)
        except BlockingIOError:
            emit('Другая операция уже выполняется',type='result',ok=False)
        except Exception as exc:
            message = str(exc) if isinstance(exc,(ValueError,InterruptedError)) else 'Операция не завершена: '+type(exc).__name__
            emit(message,type='result',ok=False)


if __name__ == '__main__':
    main()
