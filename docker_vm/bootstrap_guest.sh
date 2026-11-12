#!/bin/sh
# Only for the disposable QEMU guest created by this project.
set -eu
test "$(cat /sys/class/dmi/id/sys_vendor)" = QEMU
test "$(cat /sys/block/vda/size)" = 67108864
test -f /root/ocr_authorized_key

printf '%s\n' ocr-docker > /etc/hostname
hostname ocr-docker
cat > /etc/network/interfaces <<'EOF'
auto lo
iface lo inet loopback
auto eth0
iface eth0 inet dhcp
EOF
rc-update add networking boot
mkdir -p /root/.ssh
chmod 700 /root/.ssh
cp /root/ocr_authorized_key /root/.ssh/authorized_keys
chmod 600 /root/.ssh/authorized_keys
ssh-keygen -A
cat > /etc/ssh/sshd_config <<'EOF'
Port 22
PermitRootLogin prohibit-password
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitEmptyPasswords no
AllowUsers root
Subsystem sftp /usr/lib/ssh/sftp-server
EOF
# Lock down the initially empty console password. SSH uses the host's key.
printf 'root:%s\n' "$(openssl rand -hex 32)" | chpasswd
rc-update add sshd default
rc-update add cgroups boot

umask 077
mkdir -p /etc/docker/pki
cd /etc/docker/pki
openssl ecparam -name prime256v1 -genkey -noout -out ca-key.pem
openssl req -new -x509 -sha256 -days 3650 -key ca-key.pem -out ca.pem -subj /CN=OCR-Local-VM-CA
openssl ecparam -name prime256v1 -genkey -noout -out server-key.pem
openssl req -new -sha256 -key server-key.pem -out server.csr -subj /CN=localhost
printf '%s\n' 'subjectAltName=IP:127.0.0.1,IP:10.0.2.15,DNS:localhost' 'extendedKeyUsage=serverAuth' > server.ext
openssl x509 -req -sha256 -days 365 -in server.csr -CA ca.pem -CAkey ca-key.pem -CAcreateserial -out server-cert.pem -extfile server.ext
openssl ecparam -name prime256v1 -genkey -noout -out key.pem
openssl req -new -sha256 -key key.pem -out client.csr -subj /CN=ocr-local-client
printf '%s\n' 'extendedKeyUsage=clientAuth' > client.ext
openssl x509 -req -sha256 -days 365 -in client.csr -CA ca.pem -CAkey ca-key.pem -CAcreateserial -out cert.pem -extfile client.ext
chmod 600 *key.pem
cat > /etc/docker/daemon.json <<'EOF'
{
  "hosts": ["unix:///var/run/docker.sock", "tcp://0.0.0.0:2376"],
  "tls": true,
  "tlsverify": true,
  "tlscacert": "/etc/docker/pki/ca.pem",
  "tlscert": "/etc/docker/pki/server-cert.pem",
  "tlskey": "/etc/docker/pki/server-key.pem",
  "log-driver": "local"
}
EOF
rc-update add docker default
printf '\nexport GODEBUG=cpu.all=off\n' >> /etc/conf.d/docker

# /dev/vda is exclusively our new 32 GiB qcow2 disk, verified above.
ERASE_DISKS=/dev/vda KERNELOPTS='console=ttyS0,115200' setup-disk -m sys -s 0 /dev/vda
# setup-disk saves /etc but does not include root's SSH authorized_keys.
mount /dev/vda2 /mnt
mkdir -p /mnt/root/.ssh
cp /root/.ssh/authorized_keys /mnt/root/.ssh/
chmod 700 /mnt/root/.ssh
chmod 600 /mnt/root/.ssh/authorized_keys
umount /mnt
sync
printf '\nOCR_GUEST_INSTALLED\n'
