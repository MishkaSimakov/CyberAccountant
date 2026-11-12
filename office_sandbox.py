"""Run the office engine in a local, restricted Linux Docker container.

There is deliberately no host Office/LibreOffice fallback.
"""
from __future__ import annotations

import io
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import time
import uuid

DEFAULT_IMAGE = 'document-ocr-office:1'
MAX_INPUT = 50 * 1024 * 1024
MAX_OUTPUT = 128 * 1024 * 1024
MAX_LOG = 2 * 1024 * 1024
VM_ENDPOINT = 'tcp://127.0.0.1:49179'
UPLOAD_CODE = '''import os,sys
from pathlib import Path
data = sys.stdin.buffer.read(50*1024*1024+1)
if len(data) > 50*1024*1024: raise SystemExit("Input exceeds limit")
path = Path("/input") / sys.argv[1]
path.write_bytes(data)
path.chmod(0o444)
'''


class SandboxError(RuntimeError):
    pass


def _bundled_docker():
    candidate = Path(__file__).parent / '.tools/docker/docker.exe'
    return str(candidate) if os.name == 'nt' and candidate.is_file() else None


def docker_command(explicit=None):
    executable = explicit or shutil.which('docker') or _bundled_docker()
    if not executable:
        raise SandboxError('Office sandbox requires local Docker with Linux containers. '
                           'No document was opened on the host. Install/start Docker, then run '
                           'python document_ocr.py sandbox-build.')
    return str(executable)


def _query(docker, arguments, timeout=20):
    try:
        result = subprocess.run([docker, *arguments], capture_output=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SandboxError(f'Docker unavailable: {exc}') from exc
    if result.returncode:
        raise SandboxError('Docker failed: ' + result.stderr.decode('utf-8', errors='replace')[-2000:])
    return result.stdout.decode('utf-8', errors='replace').strip()


def check_engine(docker):
    # Never transmit confidential documents to a remote Docker daemon.
    # DOCKER_CONTEXT takes precedence over DOCKER_HOST in the Docker CLI.
    endpoint = None if os.environ.get('DOCKER_CONTEXT') else os.environ.get('DOCKER_HOST')
    if not endpoint:
        endpoint = _query(docker, ['context', 'inspect', '--format', '{{.Endpoints.docker.Host}}'])
    local_endpoint = endpoint.startswith('unix:///') or endpoint.startswith('npipe:////./pipe/')
    managed_vm = endpoint == VM_ENDPOINT and managed_vm_context(docker)
    if not local_endpoint and not managed_vm:
        raise SandboxError('Only local Docker unix/npipe endpoints are allowed; remote/TCP/SSH contexts are rejected.')
    if _query(docker, ['info', '--format', '{{.OSType}}']) != 'linux':
        raise SandboxError('The office sandbox requires a Linux Docker engine.')
    return managed_vm


def managed_vm_context(docker):
    """Allow only our loopback VM context, with TLS keys pinned at setup."""
    if any(os.environ.get(key) for key in ('DOCKER_HOST', 'DOCKER_TLS_VERIFY', 'DOCKER_CERT_PATH')):
        return False
    state_path = Path.home()/'.docker/ocr-vm/installed.json'
    try:
        state = json.loads(state_path.read_text(encoding='utf-8'))
        if state.get('endpoint') != VM_ENDPOINT:
            return False
        context = json.loads(_query(docker, ['context', 'inspect']))[0]
        endpoint = context['Endpoints']['docker']
        if context['Name'] != 'ocr-local-vm' or endpoint['Host'] != VM_ENDPOINT or endpoint.get('SkipTLSVerify'):
            return False
        tls = Path(context['Storage']['TLSPath'])/'docker'
        for name in ('ca.pem', 'cert.pem', 'key.pem'):
            if hashlib.sha256((tls/name).read_bytes()).hexdigest() != state['tls_sha256'][name]:
                return False
        return True
    except (OSError, ValueError, KeyError, IndexError, TypeError):
        return False


def build_image(image=DEFAULT_IMAGE, docker=None):
    docker = docker_command(docker)
    check_engine(docker)
    context = Path(__file__).parent / 'office_sandbox'
    result = subprocess.run([docker, 'build', '--tag', image, str(context)], check=False)
    if result.returncode:
        raise SandboxError('Office sandbox image build failed.')


def container_arguments(docker, image, name, input_volume):
    return [docker, 'run', '--rm', '--pull', 'never', '--name', name,
            '--network', 'none', '--ipc', 'none', '--read-only',
            '--user', '10001:10001', '--cap-drop', 'ALL',
            '--security-opt', 'no-new-privileges:true',
            '--pids-limit', '128', '--memory', '1g', '--memory-swap', '1g', '--cpus', '2',
            '--tmpfs', '/tmp:rw,noexec,nosuid,nodev,size=128m,mode=1777',
            '--tmpfs', '/work:rw,noexec,nosuid,nodev,size=512m,mode=1777',
            '--mount', f'type=volume,source={input_volume},target=/input,readonly',
            '--env', 'HOME=/work/home', image]


def upload_input(docker, image, volume, source, name, timeout):
    command = [docker, 'run', '--rm', '--pull', 'never', '--name', name, '-i',
               '--network', 'none', '--read-only', '--user', '0:0', '--cap-drop', 'ALL',
               '--security-opt', 'no-new-privileges:true', '--pids-limit', '32', '--memory', '128m',
               '--mount', f'type=volume,source={volume},target=/input',
               '--entrypoint', '/usr/bin/python3', image, '-c', UPLOAD_CODE, 'document'+source.suffix.lower()]
    try:
        with source.open('rb') as handle:
            result = subprocess.run(command, stdin=handle, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.PIPE, timeout=timeout)
        if result.returncode:
            raise SandboxError('Cannot stage sandbox input: ' + result.stderr.decode('utf-8', errors='replace')[-2000:])
    except subprocess.TimeoutExpired as exc:
        raise SandboxError('Sandbox input upload timed out.') from exc
    finally:
        try:
            subprocess.run([docker, 'rm', '--force', name], capture_output=True, timeout=15)
        except (OSError, subprocess.TimeoutExpired):
            pass


def _execute(docker, command, name, directory, timeout):
    stdout_path, stderr_path = directory/'result.tar', directory/'worker.log'
    process = None
    try:
        with stdout_path.open('wb') as stdout, stderr_path.open('wb') as stderr:
            process = subprocess.Popen(command, stdout=stdout, stderr=stderr, stdin=subprocess.DEVNULL)
            deadline = time.monotonic() + timeout
            while process.poll() is None:
                if time.monotonic() > deadline:
                    raise SandboxError(f'Office sandbox exceeded its {timeout}s time limit.')
                if stdout_path.stat().st_size > MAX_OUTPUT or stderr_path.stat().st_size > MAX_LOG:
                    raise SandboxError('Office sandbox exceeded its output limit.')
                time.sleep(.1)
            if stdout_path.stat().st_size > MAX_OUTPUT or stderr_path.stat().st_size > MAX_LOG:
                raise SandboxError('Office sandbox exceeded its output limit.')
        if process.returncode:
            message = stderr_path.read_bytes()[-5000:].decode('utf-8', errors='replace')
            raise SandboxError('Office sandbox failed; no host fallback: ' + message)
    finally:
        # Killing docker.exe alone does not necessarily stop its container.
        try:
            subprocess.run([docker, 'rm', '--force', name], capture_output=True, timeout=15)
        except (OSError, subprocess.TimeoutExpired):
            pass
        if process is not None and process.poll() is None:
            process.kill()
            process.wait(timeout=10)
    return stdout_path


def unpack_result(archive_path, destination, *, xlsx):
    expected = {'preview.pdf', 'report.json'} | ({'recalculated.xlsx'} if xlsx else set())
    result = {}
    try:
        with tarfile.open(archive_path, 'r:') as archive:
            for member in archive:
                if member.name not in expected or member.name in result or not member.isfile():
                    raise SandboxError('Sandbox returned an unexpected file or link.')
                limit = 2*1024*1024 if member.name == 'report.json' else MAX_OUTPUT//2
                if member.size < 0 or member.size > limit:
                    raise SandboxError('Sandbox returned an oversized file.')
                data = archive.extractfile(member).read(limit+1)
                if len(data) != member.size:
                    raise SandboxError('Sandbox returned a truncated file.')
                result[member.name] = data
    except (tarfile.TarError, OSError) as exc:
        raise SandboxError(f'Invalid sandbox output: {exc}') from exc
    if set(result) != expected or not result['preview.pdf'].startswith(b'%PDF-'):
        raise SandboxError('Sandbox did not return all expected artifacts.')
    try:
        report = json.loads(result['report.json'])
    except (ValueError, UnicodeError) as exc:
        raise SandboxError('Sandbox returned invalid calculation metadata.') from exc
    if not isinstance(report, dict) or report.get('engine') != 'libreoffice':
        raise SandboxError('Sandbox returned invalid calculation metadata.')
    if xlsx and report.get('recalculated') is not True:
        raise SandboxError('Spreadsheet was not explicitly recalculated.')
    # Do not extract the tar: names, links and permissions are untrusted data.
    destination.write_bytes(result['preview.pdf'])
    if xlsx:
        destination.with_name('recalculated.xlsx').write_bytes(result['recalculated.xlsx'])
    report['isolation'] = {'type': 'docker', 'network': 'none', 'user': '10001:10001',
                           'read_only_root': True, 'input_read_only': True,
                           'memory_limit': '1g', 'cpu_limit': 2, 'pids_limit': 128}
    return report


def render(source, destination, *, image=DEFAULT_IMAGE, docker=None, timeout=180):
    docker = docker_command(docker)
    managed_vm = check_engine(docker)
    try:
        _query(docker, ['image', 'inspect', image])
    except SandboxError as exc:
        raise SandboxError('Office sandbox image is not available locally. Run '
                           f'python document_ocr.py sandbox-build --sandbox-image {image}') from exc
    if source.stat().st_size > MAX_INPUT:
        raise SandboxError('Office input exceeds the 50 MiB limit.')
    if source.suffix.lower() not in ('.docx', '.xlsx'):
        raise SandboxError('Only DOCX and XLSX are supported by the office sandbox.')
    with tempfile.TemporaryDirectory(prefix='office-sandbox-') as temporary:
        directory = Path(temporary)
        staged = directory/'input'
        staged.mkdir(mode=0o755)
        staged_file = staged/('document' + source.suffix.lower())
        shutil.copyfile(source, staged_file)
        staged_file.chmod(0o444)
        name = 'document-ocr-' + uuid.uuid4().hex
        volume = 'ocr-input-' + uuid.uuid4().hex
        _query(docker, ['volume', 'create', volume])
        try:
            upload_input(docker, image, volume, staged_file, name+'-upload', timeout)
            command = container_arguments(docker, image, name, volume)
            archive = _execute(docker, command, name, directory, timeout)
            report = unpack_result(archive, destination, xlsx=source.suffix.lower() == '.xlsx')
        finally:
            try:
                subprocess.run([docker, 'volume', 'rm', volume], capture_output=True, timeout=20)
            except (OSError, subprocess.TimeoutExpired):
                pass
        report['isolation']['image'] = image
        report['isolation']['engine_location'] = 'local_qemu_vm' if managed_vm else 'local_engine'
        report['isolation']['input_transport'] = 'temporary_docker_volume'
        report['isolation']['host_directory_mounts'] = False
        report['isolation']['timeout_seconds'] = timeout
        return report
