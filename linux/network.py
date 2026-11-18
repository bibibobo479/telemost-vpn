#!/usr/bin/python3
"""Own only wb-tun, routing tables 10888/10889, rules 10080/10081 and nft wb_vpn."""
import json
import ipaddress
from pathlib import Path
import pwd
import subprocess
import sys

TUN = 'wb-tun'


def run(*args, check=True, data=None):
    return subprocess.run(args, input=data, text=True, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, check=check)


def refresh():
    routes = json.loads(run('ip', '-j', '-4', 'route', 'show', 'table', 'main', 'default').stdout)
    routes = [r for r in routes if r.get('gateway') and Path('/sys/class/net', r.get('dev', ''), 'device').exists()]
    if not routes:
        raise RuntimeError('No physical uplink with a default gateway')
    route = min(routes, key=lambda r: r.get('metric', 0))
    run('ip', '-4', 'route', 'replace', 'table', '10889', 'default', 'via', route['gateway'],
        'dev', route['dev'], 'onlink', 'metric', '100')
    # Use the uplink's DNS for signalling even when public DNS is blocked.
    raw_dns = run('resolvectl', 'dns', route['dev'], check=False).stdout
    servers = []
    for value in raw_dns.split():
        try:
            address = ipaddress.ip_address(value)
            if address.version == 4:
                servers.append(str(address))
        except ValueError:
            pass
    servers = list(dict.fromkeys(servers + ['77.88.8.8', '1.1.1.1']))[:3]
    content = ''.join('nameserver ' + value + '\n' for value in servers) + 'options timeout:3 attempts:2\n'
    resolver = Path('/etc/wb-vpn/transport-resolv.conf')
    if resolver.read_text() != content:
        # Keep the inode: it is bind-mounted inside the transport service.
        resolver.write_text(content)


def up():
    uid = str(pwd.getpwnam('wbtransport').pw_uid)
    if run('nft', 'list', 'table', 'inet', 'wb_vpn', check=False).returncode:
        rules = '''table inet wb_vpn {
 chain output {
  type filter hook output priority -50; policy accept;
  oifname "lo" accept
  meta skuid UID accept
  meta nfproto ipv4 oifname "wb-tun" accept
  meta nfproto ipv4 udp sport 68 udp dport 67 accept
  counter reject with icmpx type admin-prohibited
 }
}
'''.replace('UID', uid)
        run('nft', '-f', '-', data=rules)
    # Amnezia has an independent iptables OUTPUT chain. These narrow rules
    # bypass it only for our transport UID and tunnel; our nft guard still applies.
    for rule in compatibility_rules(uid):
        if run('iptables', '-w', '-C', 'OUTPUT', *rule, check=False).returncode:
            run('iptables', '-w', '-I', 'OUTPUT', '1', *rule)
    if run('ip', 'link', 'show', TUN, check=False).returncode:
        run('ip', 'tuntap', 'add', 'dev', TUN, 'mode', 'tun')
    run('ip', 'addr', 'replace', '198.18.8.1/30', 'dev', TUN)
    run('ip', 'link', 'set', TUN, 'mtu', '1400', 'up')
    run('ip', '-4', 'route', 'replace', 'table', '10888', 'default', 'dev', TUN)
    run('ip', '-4', 'route', 'replace', 'blackhole', 'default', 'table', '10889', 'metric', '32760')
    refresh()
    rules = json.loads(run('ip', '-j', '-4', 'rule', 'show').stdout)
    priorities = {r.get('priority') for r in rules}
    if 10080 not in priorities:
        run('ip', '-4', 'rule', 'add', 'priority', '10080', 'uidrange', uid + '-' + uid, 'lookup', '10889')
    if 10081 not in priorities:
        run('ip', '-4', 'rule', 'add', 'priority', '10081', 'lookup', '10888')
    run('resolvectl', 'dns', TUN, '1.1.1.1', '9.9.9.9')
    run('resolvectl', 'domain', TUN, '~.')
    run('resolvectl', 'default-route', TUN, 'yes')
    run('resolvectl', 'flush-caches')


def down():
    run('resolvectl', 'revert', TUN, check=False)
    for p in ('10080', '10081'):
        run('ip', '-4', 'rule', 'del', 'priority', p, check=False)
    for t in ('10888', '10889'):
        run('ip', '-4', 'route', 'flush', 'table', t, check=False)
    run('ip', 'link', 'del', TUN, check=False)
    uid = str(pwd.getpwnam('wbtransport').pw_uid)
    for rule in compatibility_rules(uid):
        run('iptables', '-w', '-D', 'OUTPUT', *rule, check=False)
    run('nft', 'delete', 'table', 'inet', 'wb_vpn', check=False)
    run('resolvectl', 'flush-caches', check=False)


def compatibility_rules(uid):
    return [('-m', 'owner', '--uid-owner', uid, '-m', 'comment', '--comment', 'wb-vpn-transport', '-j', 'ACCEPT'),
            ('-o', TUN, '-m', 'comment', '--comment', 'wb-vpn-tunnel', '-j', 'ACCEPT')]


if __name__ == '__main__':
    mode = sys.argv[1] if len(sys.argv) == 2 else ''
    if mode not in ('up', 'down', 'refresh'):
        raise SystemExit('Expected up, down or refresh')
    try:
        {'up': up, 'down': down, 'refresh': refresh}[mode]()
    except Exception as exc:
        print('VPN network configuration failed: ' + str(exc), file=sys.stderr)
        raise SystemExit(1)
