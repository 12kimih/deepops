#!/bin/bash
# Read-only security inventory of one node: listeners, exposure and tunnel tools,
# lingering users and crontabs, sshd settings, root keys, setuid and world-writable
# files on the root filesystem, NFS exports, firewall, containers, secret file modes,
# home modes, pending updates, auth failures, NOPASSWD rules, kernel hardening.
#
# Prints no secrets, key material or users' file contents, and never walks /home or
# other network filesystems (find stays on / with -xdev). Run on every host and
# compare (playbooks/utilities/security-audit.yml).
t() { timeout 20 "$@" 2>/dev/null; }
h() { echo "### $*"; }

h "1 listeners on non-loopback addresses (proto addr:port user process)"
t ss -Hltnup | awk '$5 !~ /^(127\.|\[::1\]|::1)/ {print $1, $5, $7}' | sed 's/users:((//; s/))//' | sort -u

h "2 non-system users' processes listening anywhere"
t ss -Hltnupe | grep -oE 'uid:[0-9]+' | cut -d: -f2 | sort -u | while read -r u; do
  [ "$u" -ge 1000 ] 2>/dev/null && [ "$u" -lt 60000 ] && echo "uid $u ($(getent passwd "$u" | cut -d: -f1))"; done

h "3 tunnel / remote-access / exposure tools running (user pid start cmd)"
ps -eo user:20,pid,lstart,args --no-headers | grep -E -i \
  'cloudflared|ngrok|frpc|frps|chisel|bore |localtunnel|zerotier|tailscaled|autossh|sshuttle|rathole|ssh .*-[A-Za-z]*R |ssh .*-R[0-9]|jupyter.*(--ip[= ](0\.0\.0\.0|\*))|code-server|nc -l|ncat -l|socat .*LISTEN|python3? -m http\.server' \
  | grep -v -E 'grep -E' | cut -c1-200

h "4 lingering users (systemd --user services survive logout)"
ls /var/lib/systemd/linger 2>/dev/null | tr '\n' ' '; echo

h "5 user crontabs present"
ls /var/spool/cron/crontabs 2>/dev/null | tr '\n' ' '; echo

h "6 uid 0 accounts other than root / accounts with empty password field (counts and names only)"
awk -F: '$3==0 && $1!="root"{print "uid0:", $1}' /etc/passwd
awk -F: '$2=="" {print "empty-password:", $1}' /etc/shadow

h "7 sshd effective settings"
t sshd -T | grep -E '^(port|permitrootlogin|passwordauthentication|pubkeyauthentication|kbdinteractiveauthentication|allowtcpforwarding|gatewayports|x11forwarding|permittunnel|allowagentforwarding|maxauthtries|permitemptypasswords|usepam) ' | tr '\n' ';'; echo

h "8 root authorized_keys (fingerprint options)"
if [ -f /root/.ssh/authorized_keys ]; then
  while IFS= read -r line; do
    case "$line" in ''|\#*) continue;; esac
    opts=$(printf '%s\n' "$line" | grep -oE '^[^ ]*(from|command|restrict)[^ ]*' || true)
    fp=$(printf '%s\n' "$line" | ssh-keygen -lf - 2>/dev/null | awk '{print $2}')
    echo "$fp ${opts:-no-options}"
  done < /root/.ssh/authorized_keys
fi

h "9 setuid/setgid files on the root filesystem outside the usual places"
t find / -xdev \( -perm -4000 -o -perm -2000 \) -type f 2>/dev/null \
  | grep -v -E '^/(usr/(bin|sbin|lib|libexec)|bin|sbin|snap)/' | head -30

h "10 world-writable files under /etc /usr /opt /var/lib (not symlinks), and world-writable dirs without sticky bit"
t find /etc /usr /opt /var/lib -xdev -type f -perm -0002 2>/dev/null | head -20
t find / -xdev -type d -perm -0002 ! -perm -1000 2>/dev/null | head -20

h "11 NFS exports (server side)"
t exportfs -v | tr -s ' \t' ' ' | paste -sd ';' -; echo

h "12 firewall: ufw status / nftables tables"
t ufw status | head -30 | tr '\n' ';'; echo
t nft list tables | tr '\n' ';'; echo

h "13 docker: running containers (name image user privileged network) and socket"
if command -v docker >/dev/null; then
  for c in $(t docker ps -q); do
    t docker inspect -f '{{.Name}} {{.Config.Image}} user={{.Config.User}} priv={{.HostConfig.Privileged}} net={{.HostConfig.NetworkMode}} pid={{.HostConfig.PidMode}} caps={{.HostConfig.CapAdd}}' "$c"
  done
  ls -l /var/run/docker.sock 2>/dev/null | awk '{print "socket", $1, $3, $4}'
fi

h "14 munge / slurm secret file modes"
for f in /etc/munge/munge.key /etc/slurm/slurmdbd.conf /etc/slurm/slurm.conf /etc/slurm/cgroup.conf; do
  [ -e "$f" ] && stat -c '%n %a %U:%G' "$f"
done

h "15 /home top-level directory modes (stat only; group- or world-readable homes)"
if mountpoint -q /home || [ -d /home ]; then
  stat -c '%a %U %n' /home/* 2>/dev/null | awk '$1 ~ /[1-7][0-7]$|[0-7][1-7][0-7]$/ {c++} END {print (c+0) " homes readable by group or others"}'
fi

h "16 pending updates: security / total, reboot required, kernel"
t apt-get -s -o Debug::NoLocking=1 upgrade | grep -c '^Inst.*-security' | sed 's/^/security: /'
t apt-get -s -o Debug::NoLocking=1 upgrade | grep -c '^Inst' | sed 's/^/total: /'
[ -f /var/run/reboot-required ] && echo "reboot-required: $(tr '\n' ' ' < /var/run/reboot-required.pkgs 2>/dev/null)"
uname -r

h "17 auth failures last 24h (sshd) and fail2ban"
t journalctl -u ssh -u sshd --since -24h --no-pager | grep -c -E 'Failed password|Invalid user' | sed 's/^/failed-ssh-24h: /'
t fail2ban-client status 2>/dev/null | tr '\n' ';'; echo

h "18 sudo rules granting NOPASSWD (file: rule)"
grep -rhE 'NOPASSWD' /etc/sudoers /etc/sudoers.d 2>/dev/null | grep -vE '^\s*#' | sort -u

h "19 kernel hardening sysctls"
for k in kernel.unprivileged_bpf_disabled kernel.kptr_restrict kernel.dmesg_restrict kernel.yama.ptrace_scope kernel.apparmor_restrict_unprivileged_userns net.ipv4.ip_forward fs.protected_symlinks fs.protected_hardlinks; do
  printf '%s=%s ' "$k" "$(sysctl -n "$k" 2>/dev/null)"; done; echo
