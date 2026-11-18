"""Dedicated browser login using private CDP pipes (no listening debug port)."""
import fcntl
import json
import os
from pathlib import Path
import select
import shutil
import subprocess
import sys
import threading
import time
from appcore import USER_CONFIG, cookies


class BrowserLogin:
    def __init__(self):
        self.process = None
        self.lock = threading.Lock()
        self.counter = 0
        self.buffer = b''

    def open(self):
        if self.process and self.process.poll() is None:
            return
        if self.process:
            self.close()
        self.buffer = b''
        browser = next((shutil.which(x) for x in ('google-chrome-stable', 'google-chrome', 'chromium', 'chromium-browser', 'brave') if shutil.which(x)), None)
        if not browser:
            raise ValueError('Установите Chrome/Chromium или импортируйте JSON cookies из Creator')
        profile = USER_CONFIG/'browser-login'
        profile.mkdir(parents=True, mode=0o700, exist_ok=True)
        os.chmod(profile, 0o700)
        child_read, self.write_fd = os.pipe()
        self.read_fd, child_write = os.pipe()
        try:
            self.process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--launch',
                str(child_read), str(child_write), browser, str(profile)], pass_fds=(child_read, child_write),
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            os.close(self.write_fd)
            os.close(self.read_fd)
            raise
        finally:
            os.close(child_read)
            os.close(child_write)

    def request(self, method):
        with self.lock:
            if not self.process or self.process.poll() is not None:
                raise ValueError('Откройте окно входа в Яндекс')
            self.counter += 1
            os.write(self.write_fd, json.dumps({'id': self.counter, 'method': method}).encode()+b'\0')
            deadline = time.monotonic()+15
            while time.monotonic() < deadline:
                while b'\0' in self.buffer:
                    message, self.buffer = self.buffer.split(b'\0', 1)
                    if not message:
                        continue
                    response = json.loads(message)
                    if response.get('id') == self.counter:
                        if 'error' in response:
                            raise ValueError('Браузер не предоставил данные входа')
                        return response.get('result', {})
                if select.select([self.read_fd], [], [], .25)[0]:
                    chunk = os.read(self.read_fd, 65536)
                    if not chunk:
                        break
                    self.buffer += chunk
            raise ValueError('Браузер не отвечает. Закройте окно входа и откройте снова')

    def collect(self):
        return cookies(self.request('Storage.getCookies').get('cookies', []))

    def close(self):
        if self.process:
            try:
                if self.process.poll() is None:
                    self.process.terminate()
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
            for fd in (self.read_fd, self.write_fd):
                os.close(fd)
            self.process = None


if __name__ == '__main__' and sys.argv[1] == '--launch':
    # Duplicate above 9 before moving to CDP's fixed descriptors, avoiding collisions.
    reader = fcntl.fcntl(int(sys.argv[2]), fcntl.F_DUPFD, 10)
    writer = fcntl.fcntl(int(sys.argv[3]), fcntl.F_DUPFD, 10)
    os.dup2(reader, 3, inheritable=True)
    os.dup2(writer, 4, inheritable=True)
    for fd in {reader, writer, int(sys.argv[2]), int(sys.argv[3])}-{3, 4}:
        os.close(fd)
    os.execv(sys.argv[4], [sys.argv[4], '--remote-debugging-pipe', '--user-data-dir='+sys.argv[5],
        '--no-first-run', '--no-default-browser-check', '--new-window', 'https://telemost.yandex.ru'])
