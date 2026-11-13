"""HTTP boundary for Office conversion; each job runs in a bounded subprocess."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time

from anyio import CapacityLimiter, to_thread
from fastapi import FastAPI, HTTPException, Request
from starlette.background import BackgroundTask
from starlette.responses import FileResponse

MAX_INPUT = 50 * 1024 * 1024
MAX_OUTPUT = 128 * 1024 * 1024
MAX_LOG = 2 * 1024 * 1024
CONVERSION_TIMEOUT = 180
QUEUE_TIMEOUT = 180
OFFICE_WORKERS = int(os.environ.get('OFFICE_WORKERS', '1'))
if OFFICE_WORKERS < 1:
    raise ValueError('OFFICE_WORKERS must be a positive integer.')

app = FastAPI(title='Office sandbox')
conversion_slots = asyncio.Semaphore(OFFICE_WORKERS)
# Dedicated dispatcher capacity; do not inherit the shared HTTP thread limit.
conversion_threads = CapacityLimiter(OFFICE_WORKERS)


def convert_document(source, directory):
    archive = directory / 'result.tar'
    log = directory / 'worker.log'
    worker = Path(__file__).with_name('office_worker.py')
    with archive.open('wb') as output, log.open('wb') as errors:
        process = subprocess.Popen(
            [sys.executable, str(worker), str(source), str(directory / 'job')],
            stdin=subprocess.DEVNULL, stdout=output, stderr=errors, start_new_session=True)
        try:
            deadline = time.monotonic() + CONVERSION_TIMEOUT
            while process.poll() is None:
                if time.monotonic() > deadline:
                    raise HTTPException(504, 'Office conversion timed out.')
                if archive.stat().st_size > MAX_OUTPUT or log.stat().st_size > MAX_LOG:
                    raise HTTPException(422, 'Office conversion exceeded its output limit.')
                time.sleep(.1)
            if archive.stat().st_size > MAX_OUTPUT or log.stat().st_size > MAX_LOG:
                raise HTTPException(422, 'Office conversion exceeded its output limit.')
            if process.returncode:
                raise HTTPException(422, 'Office conversion rejected: ' + log.read_bytes()[-2000:].decode(
                    'utf-8', errors='replace'))
        finally:
            # Kill the whole job, including LibreOffice, on success or failure.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
    return archive


@app.get('/health')
def health():
    return {'status': 'ok'}


@app.post('/convert/{extension}')
async def convert(extension: str, request: Request):
    if extension not in ('docx', 'xlsx'):
        raise HTTPException(415, 'Only DOCX and XLSX are supported.')
    try:
        await asyncio.wait_for(conversion_slots.acquire(), timeout=QUEUE_TIMEOUT)
    except TimeoutError:
        raise HTTPException(503, 'Office sandbox queue wait timed out.') from None
    try:
        temporary = tempfile.TemporaryDirectory(prefix='office-', dir=os.environ.get('OFFICE_WORK_DIR'))
        try:
            directory = Path(temporary.name)
            source = directory / ('document.' + extension)
            total = 0
            with source.open('wb') as output:
                async for chunk in request.stream():
                    total += len(chunk)
                    if total > MAX_INPUT:
                        raise HTTPException(413, 'Office input exceeds the 50 MiB limit.')
                    output.write(chunk)
            if not total:
                raise HTTPException(422, 'Office input is empty.')
            archive = await to_thread.run_sync(convert_document, source, directory, limiter=conversion_threads)
            return FileResponse(archive, media_type='application/x-tar', filename='result.tar',
                                background=BackgroundTask(temporary.cleanup))
        except BaseException:
            temporary.cleanup()
            raise
    finally:
        conversion_slots.release()
