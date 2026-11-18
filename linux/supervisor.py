#!/usr/bin/python3
"""Supervise a single Telemost tunnel; optional room exchange via Yandex Disk."""
import json
import os
import re
from pathlib import Path
import signal
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import uuid

SERVER = sys.argv[1:] == ['server']
STATE = Path('/var/lib/wb-telemost' if SERVER else '/var/lib/wb-vpn')
CONFIG = Path('/var/lib/wb-telemost' if SERVER else '/etc/wb-vpn')
ROOM = 'disk:/Телемост VPN/room.json'
REFRESH = 'disk:/Телемост VPN/refresh.json'
METRICS = 'disk:/Телемост VPN/metrics.json'
PUBLIC = Path('/run/wb-vpn-status/dashboard.json')
cpu_previous = None
stopping = False
child = None


def load_discovery():
    global ROOM, REFRESH, METRICS
    config = CONFIG / 'discovery.json'
    if config.exists():
        folder = json.loads(config.read_text())['folder']
        if folder != 'disk:/Телемост VPN' and not re.fullmatch(r'disk:/Телемост VPN/[a-f0-9]{32}', folder):
            raise ValueError('Invalid discovery folder')
        ROOM, REFRESH, METRICS = (folder + '/' + name + '.json' for name in ('room', 'refresh', 'metrics'))


def log(message):
    print(message, flush=True)


def server_metrics():
    global cpu_previous
    values = list(map(int, Path('/proc/stat').read_text().splitlines()[0].split()[1:9]))
    total, idle = sum(values), values[3] + values[4]
    cpu = None
    if cpu_previous and total > cpu_previous[0]:
        cpu = round(100 * (1 - (idle - cpu_previous[1]) / (total - cpu_previous[0])), 1)
    cpu_previous = total, idle
    mem = {line.split(':')[0]: int(line.split()[1]) * 1024
           for line in Path('/proc/meminfo').read_text().splitlines()}
    disk_usage = shutil.disk_usage('/')
    return {'time': time.time(), 'cpu': cpu, 'cores': os.cpu_count(),
            'memory_used': mem['MemTotal'] - mem['MemAvailable'], 'memory_total': mem['MemTotal'],
            'load': list(os.getloadavg()), 'uptime': float(Path('/proc/uptime').read_text().split()[0]),
            'disk_used': disk_usage.used, 'disk_total': disk_usage.total}


def dashboard(state, latency=None, metrics=None):
    if SERVER:
        return
    try:
        previous = json.loads(PUBLIC.read_text()) if PUBLIC.exists() else {}
        value = {'time': time.time(), 'state': state, 'latency_ms': latency,
                 'server': metrics if metrics is not None else previous.get('server')}
        tmp = PUBLIC.with_suffix('.tmp')
        tmp.write_text(json.dumps(value))
        tmp.chmod(0o644)
        tmp.replace(PUBLIC)
    except OSError:
        pass


def stop(*_):
    global stopping
    stopping = True
    if child and child.poll() is None:
        child.terminate()


def valid_link(value):
    if not isinstance(value, str) or any(c.isspace() for c in value):
        return False
    u = urllib.parse.urlparse(value)
    return u.scheme == 'https' and u.netloc == 'telemost.yandex.ru' and u.path.startswith('/j/') and len(u.path) > 3


def saved_link(path):
    if not path.exists():
        return ''
    return next((line.strip() for line in reversed(path.read_text().splitlines())
                 if valid_link(line.strip())), '')


def atomic(path, value):
    tmp = path.with_suffix('.tmp')
    tmp.write_text(value)
    tmp.chmod(0o600)
    tmp.replace(path)


def disk(path, data=None):
    token = (CONFIG / 'disk-token').read_text().strip()
    op = 'download' if data is None else 'upload'
    query = urllib.parse.urlencode({'path': path, 'overwrite': 'true'})
    req = urllib.request.Request('https://cloud-api.yandex.net/v1/disk/resources/' + op + '?' + query,
                                 headers={'Authorization': 'OAuth ' + token, 'Accept': 'application/json',
                                          'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=15) as response:
        href = json.load(response)['href']
    u = urllib.parse.urlparse(href)
    if u.scheme != 'https' or not u.hostname or not (u.hostname.endswith('.yandex.net') or u.hostname.endswith('.yandex.ru')):
        raise ValueError('Unexpected Disk transfer host')
    payload = None if data is None else json.dumps(data).encode()
    req = urllib.request.Request(href, data=payload, method='GET' if data is None else 'PUT',
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=20) as response:
        if data is None:
            return json.loads(response.read(16384))


def sync_room():
    if not (CONFIG / 'disk-token').exists():
        return
    try:
        value = disk(ROOM)['link']
        if valid_link(value):
            atomic(STATE / 'call.txt', value + '\n')
    except Exception as exc:
        log('Room sync unavailable: ' + type(exc).__name__)


def main():
    global child
    load_discovery()
    os.umask(0o077)
    STATE.mkdir(exist_ok=True)
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    if not SERVER and not (STATE / 'call.txt').exists():
        atomic(STATE / 'call.txt', (CONFIG / 'call.txt').read_text())
    failures = 0
    last_request_file = STATE / 'last-refresh'
    last_request = last_request_file.read_text().strip() if last_request_file.exists() else ''
    while not stopping:
        if not SERVER:
            dashboard('connecting')
            sync_room()
        link_file = STATE / 'call.txt'
        link = saved_link(link_file)
        if link and not valid_link(link):
            raise ValueError('Invalid Telemost room URL')
        if SERVER:
            creator_output = STATE / 'creator-room.txt'
            creator_output.unlink(missing_ok=True)
            args = ['/opt/wb-telemost/creator', '--cookies', str(STATE / 'cookies.json'),
                    '--write-file', str(creator_output)]
            if link:
                args += ['--tm-link', link]
        else:
            if not link:
                raise ValueError('No room configured')
            args = ['/opt/wb-vpn/joiner', '--tm-link', link, '--socks-host', '127.0.0.1',
                    '--socks-port', '1088', '--reliable']
            profile_path = CONFIG / 'transport.json'
            profile = json.loads(profile_path.read_text()) if profile_path.exists() else {}
            fps, batch = int(profile.get('fps', 24)), int(profile.get('batch', 30))
            if not (1 <= fps <= 240 and 1 <= batch <= 256):
                raise ValueError('Invalid transport profile')
            args += ['--vp8-fps', str(fps), '--vp8-batch', str(batch)]
            if profile.get('dual_track', False):
                args += ['--dual-track']
        started = time.monotonic()
        published = ''
        unhealthy = 0
        rotate = False
        fresh_requested = False
        received_previous = 0
        next_check = started + (10 if SERVER else 5)
        log('Starting creator' if SERVER else 'Connecting to Telemost')
        with (STATE / 'tunnel.log').open('w') as output:
            child = subprocess.Popen(args, stdout=output, stderr=subprocess.STDOUT)
            while not stopping and child.poll() is None:
                time.sleep(1)
                now = time.monotonic()
                if SERVER:
                    current_link = saved_link(creator_output)
                    if current_link and current_link != saved_link(link_file):
                        atomic(link_file, current_link + '\n')
                if not SERVER and not fresh_requested and (CONFIG / 'disk-token').exists():
                    with (STATE / 'tunnel.log').open('rb') as transport_log:
                        transport_log.seek(0, 2)
                        transport_log.seek(max(0, transport_log.tell() - 16384))
                        invalid_room = b'ERROR: get connection: status 404' in transport_log.read()
                    if invalid_room:
                        try:
                            disk(REFRESH, {'id': str(uuid.uuid4()), 'time': time.time()})
                            fresh_requested = True
                            log('Telemost rejected the saved room; requested a new room')
                            dashboard('reconnecting')
                            child.terminate()
                            break
                        except Exception as exc:
                            log('Room refresh unavailable: ' + type(exc).__name__)
                if now < next_check:
                    continue
                next_check = now + 20
                if SERVER and (CONFIG / 'disk-token').exists():
                    try:
                        disk(METRICS, server_metrics())
                    except Exception as exc:
                        log('Server metrics unavailable: ' + type(exc).__name__)
                    try:
                        current = link_file.read_text().strip()
                        if valid_link(current) and published != current:
                            disk(ROOM, {'link': current})
                            published = current
                            log('Room published to Disk')
                    except Exception as exc:
                        log('Room publication unavailable: ' + type(exc).__name__)
                    try:
                        request = disk(REFRESH)
                        request_id = request.get('id', '')
                        if request_id and request_id != last_request and time.time() - float(request.get('time', 0)) < 600:
                            last_request = request_id
                            atomic(last_request_file, last_request)
                            rotate = True
                            child.terminate()
                    except Exception:
                        pass
                elif not SERVER:
                    probe_started = time.monotonic()
                    result = subprocess.run(['/usr/bin/curl', '--silent', '--fail', '--max-time', '30',
                                             '--proxy', 'socks5h://127.0.0.1:1088',
                                             'https://www.gstatic.com/generate_204'],
                                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    latency = round((time.monotonic() - probe_started) * 1000) if result.returncode == 0 else None
                    metrics = None
                    if (CONFIG / 'disk-token').exists():
                        try:
                            metrics = disk(METRICS)
                        except Exception:
                            pass
                    try:
                        received = int(Path('/sys/class/net/wb-tun/statistics/rx_bytes').read_text())
                    except OSError:
                        received = received_previous
                    flowing = received - received_previous > 4096
                    received_previous = received
                    dashboard('connected' if result.returncode == 0 else 'degraded' if flowing else 'reconnecting', latency, metrics)
                    unhealthy = 0 if result.returncode == 0 or flowing else unhealthy + 1
                    if unhealthy == 0:
                        failures = 0
                        atomic(STATE / 'status', 'connected\n')
                    if unhealthy >= 4 and time.monotonic() - started >= 120:
                        atomic(STATE / 'status', 'reconnecting\n')
                        log('Tunnel health check failed; reconnecting')
                        child.terminate()
            try:
                child.wait(timeout=8)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
        if stopping:
            break
        failures += 1
        if SERVER:
            # Only replace a room when the shared discovery channel is configured.
            if (rotate or (failures >= 3 and time.monotonic() - started < 120)) and (CONFIG / 'disk-token').exists():
                link_file.unlink(missing_ok=True)
                failures = 0
        elif failures >= 3 and (CONFIG / 'disk-token').exists():
            try:
                disk(REFRESH, {'id': str(uuid.uuid4()), 'time': time.time()})
                log('Requested a fresh server room')
                failures = 0
            except Exception as exc:
                log('Room refresh unavailable: ' + type(exc).__name__)
        for _ in range(30 if fresh_requested else min(30, 3 * max(1, failures))):
            if stopping:
                break
            time.sleep(1)
    dashboard('disconnected')


if __name__ == '__main__':
    main()
