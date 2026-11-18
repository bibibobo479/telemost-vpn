#!/bin/bash
set -euo pipefail
base=$(cd -- "$(dirname -- "$0")" && pwd)
if [[ $EUID != 0 ]]; then
    exec pkexec /bin/bash "$base/install.sh"
fi
if command -v apt-get >/dev/null; then
    apt-get update -qq
    apt-get install -y python3 python3-gi python3-cairo gir1.2-gtk-4.0 gir1.2-adw-1 \
        iproute2 iptables nftables systemd-resolved curl openssh-client policykit-1 xdg-utils
elif command -v pacman >/dev/null; then
    pacman -S --needed --noconfirm python python-gobject python-cairo gtk4 libadwaita \
        iproute2 iptables nftables systemd curl openssh polkit xdg-utils
else
    echo 'Поддерживаются Ubuntu/Debian и Arch Linux. Установите GTK4/libadwaita/PyGObject, iproute2, iptables, nftables, systemd-resolved, curl, openssh, polkit.' >&2
    exit 1
fi
systemctl start systemd-resolved
exec /usr/bin/python3 "$base/install_app.py"
