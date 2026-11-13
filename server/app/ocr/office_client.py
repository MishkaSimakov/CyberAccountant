"""HTTP client for the Office conversion service on the Compose network."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tarfile
import tempfile
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

DEFAULT_URL = 'http://office_sandbox:8001'
MAX_INPUT = 50 * 1024 * 1024
MAX_OUTPUT = 128 * 1024 * 1024
# Match the service's maximum slot wait, in addition to conversion time.
QUEUE_TIMEOUT = 180


class SandboxError(RuntimeError):
    pass


def unpack_result(archive_path, destination, *, xlsx):
    expected = {'preview.pdf', 'report.json'} | ({'recalculated.xlsx'} if xlsx else set())
    result = {}
    try:
        with tarfile.open(archive_path, 'r:') as archive:
            for member in archive:
                if member.name not in expected or member.name in result or not member.isfile():
                    raise SandboxError('Sandbox returned an unexpected file or link.')
                # XLSX metadata includes per-cell display strings and geometry.
                limit = MAX_OUTPUT // 2
                if member.size < 0 or member.size > limit:
                    raise SandboxError('Sandbox returned an oversized file.')
                data = archive.extractfile(member).read(limit + 1)
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
    return report


def render(source, destination, *, url=None, timeout=180):
    """Send DOCX/XLSX bytes and validate every returned artifact before saving."""
    source = Path(source)
    extension = source.suffix.lower()
    if extension not in ('.docx', '.xlsx'):
        raise SandboxError('Only DOCX and XLSX are supported by the office sandbox.')
    with source.open('rb') as handle:
        content = handle.read(MAX_INPUT + 1)
    if len(content) > MAX_INPUT:
        raise SandboxError('Office input exceeds the 50 MiB limit.')
    url = (url or os.environ.get('OFFICE_SANDBOX_URL', DEFAULT_URL)).rstrip('/')
    request = Request(f'{url}/convert/{extension[1:]}', data=content,
                      headers={'Content-Type': 'application/octet-stream'}, method='POST')
    # Internal document traffic must not pass through an HTTP proxy.
    opener = build_opener(ProxyHandler({}))
    try:
        with opener.open(request, timeout=timeout + QUEUE_TIMEOUT) as response:
            if response.headers.get_content_type() != 'application/x-tar':
                raise SandboxError('Office sandbox returned an unexpected content type.')
            with tempfile.TemporaryDirectory(prefix='office-response-') as temporary:
                archive = Path(temporary) / 'result.tar'
                total = 0
                with archive.open('wb') as output:
                    while chunk := response.read(1024 * 1024):
                        total += len(chunk)
                        if total > MAX_OUTPUT:
                            raise SandboxError('Office sandbox exceeded its output limit.')
                        output.write(chunk)
                report = unpack_result(archive, destination, xlsx=extension == '.xlsx')
    except HTTPError as exc:
        message = exc.read(2048).decode('utf-8', errors='replace')
        raise SandboxError(f'Office sandbox rejected the conversion ({exc.code}): {message}') from exc
    except (OSError, URLError) as exc:
        raise SandboxError(f'Office sandbox unavailable: {exc}') from exc
    report['isolation'] = {'type': 'docker_service', 'transport': 'http'}
    return report
