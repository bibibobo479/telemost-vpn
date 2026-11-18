"""SSH setup and streaming calls to the fixed privileged manager."""
import base64
import gzip
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import uuid
from appcore import Disk, USER_CONFIG, PUBLIC, atomic, cookies, profile, server, valid_link

BASE = Path(__file__).resolve().parent


class Cancelled(InterruptedError):
    pass


class Job:
    def __init__(self, emit):
        self.emit = emit
        self.cancelled = threading.Event()
        self.id = uuid.uuid4().hex
        self.cancel_path = Path(f'/run/user/{os.getuid()}/wb-vpn-cancel-{self.id}')

    def cancel(self):
        self.cancelled.set()
        if self.cancel_path.parent.exists():
            atomic(self.cancel_path, '')

    def check(self):
        if self.cancelled.is_set():
            raise Cancelled('Операция отменена')

    def close(self):
        self.cancel_path.unlink(missing_ok=True)


class SSH:
    def __init__(self, address, password):
        self.server, self.password = server(address), password

    def run(self, script, timeout=600):
        USER_CONFIG.mkdir(parents=True, mode=0o700, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='.ssh-', dir=USER_CONFIG) as temporary:
            secret = Path(temporary)/'password'
            atomic(secret, self.password+'\n')
            env = dict(os.environ, SSH_ASKPASS=str(BASE/'ssh-askpass'), SSH_ASKPASS_REQUIRE='force',
                       DISPLAY=os.environ.get('DISPLAY', ':0'), WB_SSH_PASSWORD_FILE=str(secret))
            args = ['ssh', '-F', '/dev/null', '-o', 'ConnectTimeout=15', '-o', 'ConnectionAttempts=1',
                    '-o', 'NumberOfPasswordPrompts=1', '-o', 'StrictHostKeyChecking=accept-new',
                    '-o', 'UserKnownHostsFile='+str(USER_CONFIG/'known_hosts'),
                    '-p', str(self.server['port']), 'root@'+self.server['address'], 'python3 -']
            result = subprocess.run(args, input=script, text=True, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, env=env, timeout=timeout, start_new_session=True)
            if result.returncode:
                if 'REMOTE HOST IDENTIFICATION HAS CHANGED' in result.stderr:
                    raise ValueError('Ключ SSH сервера изменился. Проверьте сервер; сохранённый ключ не заменён')
                if 'Permission denied' in result.stderr:
                    raise ValueError('SSH: неверный пароль или вход root по паролю запрещён')
                raise ValueError('Не удалось выполнить настройку по SSH. Проверьте IP, порт, доступ root и наличие Python 3')
            try:
                data = json.loads(result.stdout.strip().splitlines()[-1])
            except (ValueError, IndexError):
                raise ValueError('Сервер вернул неожиданный ответ') from None
            if 'error' in data:
                raise ValueError(data['error'])
            return data

    def probe(self):
        return self.run((BASE/'remote_install.py').read_text()+
            '\ntry:\n print(json.dumps(probe()))\nexcept Exception as exc:\n print(json.dumps({"error": str(exc) if isinstance(exc, ValueError) else "Проверка сервера не пройдена"}))\n', timeout=45)

    def connection(self):
        return self.run((BASE/'remote_install.py').read_text()+
            '\ntry:\n print(json.dumps(connection()))\nexcept Exception as exc:\n print(json.dumps({"error": str(exc) if isinstance(exc, ValueError) else "Не удалось прочитать настройку существующего сервера"}))\n',timeout=60)

    def deploy(self, token, cookie_data, namespace):
        creator = BASE/'bin/creator'
        if not creator.exists():
            creator = BASE/'creator-optimized'
        files = {name: base64.b64encode(path.read_bytes()).decode() for name, path in (
            ('creator', creator), ('supervisor.py', BASE/'supervisor.py'), ('wb-telemost.service', BASE/'wb-telemost.service'))}
        payload = {'files': files, 'token': token, 'cookies': cookies(cookie_data), 'folder': namespace}
        packed = base64.b64encode(gzip.compress(json.dumps(payload).encode())).decode()
        script = (BASE/'remote_install.py').read_text()+ '\nimport gzip\n'
        script += '\ntry:\n result = deploy(json.loads(gzip.decompress(base64.b64decode('+repr(packed)+'))))\n print(json.dumps(result))\n'
        script += 'except Exception as exc:\n print(json.dumps({"error": str(exc) if isinstance(exc, ValueError) else "Установка не завершена; прежние файлы сервиса восстановлены. Проверьте доступ сервера к apt и systemd"}))\n'
        return self.run(script)


def manager(action, payload, job):
    request = dict(payload, operation=job.id)
    command = ['pkexec', '/usr/bin/python3', '/opt/wb-vpn/manager.py', action]
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True)
    process.stdin.write(json.dumps(request)+'\n')
    process.stdin.close()
    result = None
    for line in process.stdout:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get('type') == 'result':
            result = event
        job.emit(event)
    stderr = process.stderr.read()
    code = process.wait()
    if code or result is None:
        raise ValueError('Операция не выполнена. '+('Авторизация отменена.' if code in (126,127) else 'Смотрите журнал операции.'))
    if not result.get('ok'):
        raise ValueError(result.get('message', 'Операция не выполнена'))
    return result


def provision(address, password, token, cookie_data, selected_profile, namespace, job):
    job.check()
    job.emit({'message': 'Проверяем доступ к серверу…'})
    ssh = SSH(address, password)
    if ssh.probe().get('configured'):
        return reuse_server(address,password,selected_profile,job)
    disk = Disk(token, namespace)
    job.check()
    job.emit({'message': 'Проверяем чтение и запись Яндекс Диска…'})
    disk.validate()
    job.check()
    job.emit({'message': 'Передаём ядро и устанавливаем службу на сервере. Это может занять несколько минут…'})
    ssh.deploy(token, cookie_data, namespace)
    ssh.password = ''
    job.emit({'message': 'Ожидаем, пока сервер создаст комнату Телемоста…'})
    deadline = time.monotonic()+240
    link = ''
    while time.monotonic() < deadline:
        job.check()
        try:
            value = disk.transfer('room.json').get('link')
            if valid_link(value):
                link = value
                break
        except Exception:
            pass
        if job.cancelled.wait(3):
            job.check()
    if not link:
        raise ValueError('Служба установлена, но комната не появилась. Повторите вход в Яндекс и настройку; проверьте доступ VPS к Телемосту')
    job.check()
    job.emit({'message': 'Сохраняем подключение на этом устройстве…'})
    result = manager('configure', {'server': server(address), 'profile': profile(selected_profile),
        'token': token, 'folder': namespace, 'link': link}, job)
    return result


def reuse_server(address,password,selected,job):
    job.check()
    job.emit({'message':'Читаем существующую настройку сервера. Повторная установка не выполняется…'})
    value = SSH(address,password).connection()
    disk = Disk(value['token'],value['folder'])
    deadline = time.monotonic()+120
    while time.monotonic()<deadline:
        job.check()
        try:
            link=disk.transfer('room.json').get('link')
        except (OSError,ValueError,KeyError):
            link = None
        if valid_link(link):
            return manager('configure',{'server':address,'profile':selected,'token':disk.token,
                'folder':disk.folder,'link':link},job)
        time.sleep(3)
    raise ValueError('Сервер уже настроен, но комната недоступна. Проверьте службу или обновите авторизацию Яндекса')
