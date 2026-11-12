"""Manage the local Docker Linux VM without Docker Desktop or Hyper-V.

Runtime files are private to the current user in ~/.docker/ocr-vm.
QEMU uses TCG software emulation. It exposes only loopback ports.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import time
import textwrap
import uuid

ROOT = Path.home() / '.docker/ocr-vm'
SSH_PORT = 49177
CONSOLE_PORT = 49178
DOCKER_PORT = 49179
CONTEXT = 'ocr-local-vm'


class Console:
    """Bootstrap console, bound by QEMU to loopback only; not used for documents."""
    def __init__(self):
        self.socket = socket.create_connection(('127.0.0.1', CONSOLE_PORT), timeout=5)
        self.socket.settimeout(1)

    def close(self):
        self.socket.close()

    def read_until(self, pattern, timeout=180):
        data = ''
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                chunk = self.socket.recv(65536)
                if not chunk:
                    raise RuntimeError('VM console disconnected')
                data += chunk.decode('utf-8', errors='replace')
                if re.search(pattern, data):
                    return data
                if len(data) > 8*1024*1024:
                    raise RuntimeError('VM console output too large')
            except socket.timeout:
                pass
        raise TimeoutError('VM console timeout; last output: ' + data[-1500:])

    def login(self):
        self.socket.sendall(b'\r\n')
        data = self.read_until(r'(?:login:|[~:/\w-]+\s*# )', timeout=60)
        if 'login:' in data:
            self.socket.sendall(b'root\r\n')
            self.read_until(r'# ', timeout=20)

    def run(self, command, timeout=180):
        marker = '__OCR_' + uuid.uuid4().hex + '__'
        line = f"( {command} ); _ocr_rc=$?; printf '\\n{marker}:%s\\n' \"$_ocr_rc\""
        self.socket.sendall(line.encode('utf-8') + b'\r\n')
        output = self.read_until(re.escape(marker) + r':\d+\r?\n', timeout)
        status = int(re.search(re.escape(marker) + r':(\d+)', output).group(1))
        if status:
            raise RuntimeError(f'Guest command exited {status}: ' + output[-3000:])
        return output

    def upload(self, data, path):
        delimiter = 'OCR_UPLOAD_' + uuid.uuid4().hex
        encoded = '\n'.join(textwrap.wrap(base64.b64encode(data).decode('ascii'), 72))
        return self.run(f"base64 -d > '{path}' <<'{delimiter}'\n{encoded}\n{delimiter}\n")


def qemu_command():
    command = [str(ROOT/'qemu/qemu-system-x86_64.exe'),
               '-accel', 'tcg,thread=multi', '-machine', 'q35', '-cpu', 'max',
               '-m', '3072', '-smp', '2', '-display', 'none', '-monitor', 'none',
               '-serial', f'tcp:127.0.0.1:{CONSOLE_PORT},server=on,wait=off',
               '-netdev', f'user,id=net0,hostfwd=tcp:127.0.0.1:{SSH_PORT}-:22,hostfwd=tcp:127.0.0.1:{DOCKER_PORT}-:2376',
               '-device', 'virtio-net-pci,netdev=net0',
               '-drive', 'file=' + str(ROOT/'disk.qcow2') + ',format=qcow2,if=virtio']
    return command


def engine_ready():
    try:
        docker = Path(__file__).parent/'.tools/docker/docker.exe'
        command = [str(docker), '--tlsverify', '--tlscacert', str(ROOT/'tls/ca.pem'),
                   '--tlscert', str(ROOT/'tls/cert.pem'), '--tlskey', str(ROOT/'tls/key.pem'),
                   '-H', f'tcp://127.0.0.1:{DOCKER_PORT}', 'version', '--format', '{{.Server.Version}}']
        environment = os.environ.copy()
        for key in ('DOCKER_HOST', 'DOCKER_CONTEXT', 'DOCKER_TLS_VERIFY', 'DOCKER_CERT_PATH'):
            environment.pop(key, None)
        result = subprocess.run(command, capture_output=True, timeout=5, env=environment)
        return result.returncode == 0 and bool(result.stdout.strip())
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return False


def vm_listening():
    try:
        with socket.create_connection(('127.0.0.1', CONSOLE_PORT), timeout=1):
            return True
    except OSError:
        return False


def start():
    if os.name != 'nt':
        raise RuntimeError('This helper is for the local Windows QEMU environment.')
    if not (ROOT/'installed.json').is_file():
        raise RuntimeError('The Linux VM installation has not completed.')
    if engine_ready():
        print('Docker VM is already running.')
        return
    process = None
    if not vm_listening():
        with (ROOT/'qemu.log').open('ab') as log:
            process = subprocess.Popen(qemu_command(), stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                       creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)
        (ROOT/'vm.pid').write_text(str(process.pid))
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        if process is not None and process.poll() is not None:
            raise RuntimeError('QEMU exited; see ' + str(ROOT/'qemu.log'))
        if engine_ready():
            print('Docker VM started; context: ' + CONTEXT)
            return
        time.sleep(1)
    raise TimeoutError('Docker VM has not become ready in 180 seconds.')


def ssh_arguments():
    return ['ssh', '-n', '-T', '-p', str(SSH_PORT), '-i', str(ROOT/'id_ed25519'),
            '-o', 'ConnectTimeout=5', '-o', 'ServerAliveInterval=5', '-o', 'ServerAliveCountMax=1',
            '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
            '-o', 'UserKnownHostsFile=' + str(ROOT/'known_hosts'), 'root@127.0.0.1']


def stop():
    if not vm_listening():
        print('Docker VM is already stopped.')
        return
    process = subprocess.Popen([*ssh_arguments(), '/sbin/poweroff'], stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    deadline = time.monotonic() + 60
    try:
        while time.monotonic() < deadline:
            # QEMU can exit without SSH receiving a clean TCP disconnect.
            if not vm_listening():
                print('Docker VM stopped.')
                return
            if process.poll() not in (None, 0):
                _, error = process.communicate()
                raise RuntimeError('VM shutdown failed: ' + error.decode(errors='replace'))
            time.sleep(0.5)
        raise TimeoutError('Docker VM has not stopped in 60 seconds.')
    finally:
        if process.poll() is None:
            process.terminate()
        process.communicate(timeout=10)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['start', 'stop', 'status'])
    action = parser.parse_args().action
    if action == 'start':
        start()
    elif action == 'stop':
        stop()
    else:
        print(json.dumps(json.loads((ROOT/'installed.json').read_text()), indent=2))
        subprocess.run([str(Path(__file__).parent/'.tools/docker/docker.exe'), '--context', CONTEXT, 'version'], check=True)


if __name__ == '__main__':
    main()
