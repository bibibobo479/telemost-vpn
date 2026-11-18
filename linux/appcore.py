"""Validated, unprivileged configuration and Yandex Disk client."""
import ipaddress
import base64
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_PROFILE = {'fps': 60, 'batch': 120, 'dual_track': False}
DEFAULT_CLIENT_ID = '5fb84eb237df41ed9044fc4869795e4e'
PUBLIC = Path('/opt/wb-vpn')
USER_CONFIG = Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home()/'.config')))/'wb-telemost'


def profile(value):
    if not isinstance(value, dict):
        raise ValueError('Некорректные настройки транспорта')
    for key, maximum in [('fps', 240), ('batch', 256)]:
        if type(value.get(key)) is not int or not 1 <= value[key] <= maximum:
            raise ValueError(f'{key}: допустимо от 1 до {maximum}')
    if type(value.get('dual_track')) is not bool:
        raise ValueError('dual-track должен быть включён или выключен')
    return {key: value[key] for key in DEFAULT_PROFILE}


def server(value):
    address = str(ipaddress.ip_address(value['address'].strip()))
    port = value.get('port', 22)
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError('Порт SSH: от 1 до 65535')
    return {'address': address, 'port': port}


def folder(value):
    if value == 'disk:/Телемост VPN':
        return value
    if not isinstance(value, str) or not re.fullmatch(r'disk:/Телемост VPN/[a-f0-9]{32}', value):
        raise ValueError('Некорректная папка обмена')
    return value


def cookies(value):
    if isinstance(value, dict):
        value = value.get('cookies')
    if not isinstance(value, list) or not 1 <= len(value) <= 512:
        raise ValueError('Нужен JSON-файл cookies Яндекса')
    result = []
    for cookie in value:
        if not isinstance(cookie, dict):
            raise ValueError('Некорректная запись cookies')
        name, val = cookie.get('name'), cookie.get('value')
        if not isinstance(name, str) or not isinstance(val, str) or not name or len(name)+len(val) > 32768:
            raise ValueError('Некорректная запись cookies')
        if any(c in name+val for c in '\r\n\0'):
            raise ValueError('Некорректная запись cookies')
        domain = cookie.get('domain', '').lstrip('.')
        if domain and not any(domain == d or domain.endswith('.'+d) for d in ('yandex.ru', 'yandex.com', 'ya.ru')):
            continue
        result.append({'name': name, 'value': val})
    if not any(c['name'] == 'Session_id' and c['value'] for c in result):
        raise ValueError('Сначала войдите в аккаунт Яндекса: в cookies нет Session_id')
    return result


def atomic(path, value, mode=0o600):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.'+path.name, dir=path.parent)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, 'wb' if isinstance(value,bytes) else 'w') as stream:
            stream.write(value)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return default


def valid_link(value):
    if not isinstance(value, str) or any(c.isspace() for c in value):
        return False
    u = urllib.parse.urlparse(value)
    return u.scheme == 'https' and u.netloc == 'telemost.yandex.ru' and u.path.startswith('/j/') and len(u.path) > 3


class Disk:
    def __init__(self, token, namespace):
        if not isinstance(token, str) or not 16 <= len(token) <= 4096 or any(c.isspace() for c in token):
            raise ValueError('Вставьте OAuth-токен Яндекс Диска')
        self.token, self.folder = token, folder(namespace)

    def api(self, suffix='', method='GET', **query):
        req = urllib.request.Request('https://cloud-api.yandex.net/v1/disk/resources'+suffix+'?'+urllib.parse.urlencode(query),
            method=method, headers={'Authorization': 'OAuth '+self.token, 'Accept': 'application/json'})
        with urllib.request.urlopen(req, timeout=20) as response:
            raw = response.read(65536)
            return json.loads(raw) if raw else {}

    def transfer(self, name, value=None):
        if name not in ('room.json', 'refresh.json', 'metrics.json', 'check.json'):
            raise ValueError('Некорректное имя файла обмена')
        href = self.api('/download' if value is None else '/upload', path=self.folder+'/'+name, overwrite='true')['href']
        parsed = urllib.parse.urlparse(href)
        if parsed.scheme != 'https' or not parsed.hostname or not any(parsed.hostname.endswith(d) for d in ('.yandex.net', '.yandex.ru')):
            raise ValueError('Неожиданный адрес передачи Яндекс Диска')
        request = urllib.request.Request(href, data=None if value is None else json.dumps(value).encode(),
            method='GET' if value is None else 'PUT', headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(request, timeout=25) as response:
            return json.loads(response.read(65536)) if value is None else None

    def validate(self):
        for path in dict.fromkeys(('disk:/Телемост VPN', self.folder)):
            try:
                self.api(method='PUT', path=path)
            except urllib.error.HTTPError as exc:
                if exc.code != 409:
                    raise
        import uuid
        nonce = uuid.uuid4().hex
        self.transfer('check.json', {'nonce': nonce})
        if self.transfer('check.json') != {'nonce': nonce}:
            raise ValueError('Не удалось проверить чтение Яндекс Диска')
        self.api(method='DELETE', path=self.folder+'/check.json', permanently='true')


def oauth_url(client_id):
    if not re.fullmatch('[a-fA-F0-9]{32}', client_id):
        raise ValueError('Client ID должен содержать 32 шестнадцатеричных символа')
    return 'https://oauth.yandex.ru/authorize?'+urllib.parse.urlencode({
        'response_type': 'token', 'client_id': client_id, 'force_confirm': 'yes'})


def share_payload(value):
    if not isinstance(value, dict) or value.get('version') != 1:
        raise ValueError('Неподдерживаемая версия кода подключения')
    disk = Disk(value.get('token'), value.get('folder'))
    return {'version':1, 'server':server(value['server']), 'profile':profile(value['profile']),
            'folder':disk.folder, 'token':disk.token}


def encode_share(value):
    raw = json.dumps(share_payload(value),ensure_ascii=False,separators=(',',':')).encode()
    packed = base64.urlsafe_b64encode(gzip.compress(raw,mtime=0)).decode().rstrip('=')
    return 'TMVPN1.'+packed+'.'+hashlib.sha256(packed.encode()).hexdigest()[:12]


def decode_share(code):
    import io
    if not isinstance(code,str) or len(code)>32768:
        raise ValueError('Код подключения слишком большой')
    code = ''.join(code.split())
    match = re.fullmatch(r'TMVPN1\.([A-Za-z0-9_-]+)\.([a-f0-9]{12})',code)
    if not match or hashlib.sha256(match[1].encode()).hexdigest()[:12] != match[2]:
        raise ValueError('Код подключения повреждён или скопирован не полностью')
    try:
        packed = base64.urlsafe_b64decode(match[1]+'='*(-len(match[1])%4))
        with gzip.GzipFile(fileobj=io.BytesIO(packed)) as stream:
            raw = stream.read(32769)
        if len(raw)>32768:
            raise ValueError('Код подключения слишком большой')
        return share_payload(json.loads(raw))
    except (OSError,ValueError,KeyError,TypeError) as exc:
        raise ValueError('Некорректный код подключения') from None
