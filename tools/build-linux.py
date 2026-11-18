#!/usr/bin/env python3
"""Build a distributable Linux installer from this checkout; no local user data."""
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'linux'))
from install_app import MODULES,UNITS

def main():
    env=dict(os.environ,GOTOOLCHAIN='go1.26.1',CGO_ENABLED='0',GOOS='linux',GOARCH='amd64')
    output=ROOT/'prebuilts'
    output.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as temporary:
        bundle=Path(temporary)/'telemost-vpn-linux-x64'
        (bundle/'bin').mkdir(parents=True)
        for name,target in [('creator','./headless/telemost'),('joiner','./headless/telemost-joiner')]:
            subprocess.run(['go','build','-trimpath','-ldflags=-s -w','-o',str(bundle/'bin'/name),target],cwd=ROOT,env=env,check=True)
        url='https://github.com/xjasonlyu/tun2socks/releases/download/v2.7.0/tun2socks-linux-amd64.zip'
        with urllib.request.urlopen(url,timeout=60) as response:
            archive=zipfile.ZipFile(io.BytesIO(response.read()))
        binary=archive.read('tun2socks-linux-amd64')
        if hashlib.sha256(binary).hexdigest()!='98d545a663b790b4a4100d0c796e1d43d82c5442efdb6e9c4d82e2e32e99c031':
            raise SystemExit('Unexpected tun2socks checksum')
        (bundle/'bin/tun2socks').write_bytes(binary)
        for p in (bundle/'bin').iterdir():p.chmod(0o755)
        names=MODULES+UNITS+['install.sh','install_app.py','ssh-askpass','wb-telemost.service',
            'wb-vpn-ui','wb-vpn-control','telemost-vpn.desktop','telemost-vpn-off.desktop',
            'transport-resolv.conf','transport-nsswitch.conf','QUICKSTART.md']
        for name in names:
            shutil.copy2(ROOT/'linux'/name,bundle/name)
        shutil.copy2(ROOT/'LICENSE',bundle/'LICENSE')
        shutil.copytree(ROOT/'licenses',bundle/'licenses')
        manifest={str(p.relative_to(bundle)):hashlib.sha256(p.read_bytes()).hexdigest() for p in bundle.rglob('*') if p.is_file()}
        (bundle/'SHA256SUMS.json').write_text(json.dumps(manifest,indent=2)+'\n')
        def clean(info):
            info.uid=info.gid=0;info.uname=info.gname='root';return info
        dest=output/'telemost-vpn-linux-x64.tar.gz'
        with tarfile.open(dest,'w:gz') as tar:tar.add(bundle,arcname=bundle.name,filter=clean)
        print(dest)

if __name__=='__main__':main()
