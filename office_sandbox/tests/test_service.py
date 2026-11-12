import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from fastapi import HTTPException
from office_sandbox import service


class OfficeServiceTests(unittest.IsolatedAsyncioTestCase):
    async def request(self, path, body=b'', method='POST'):
        messages = []

        async def receive():
            return {'type': 'http.request', 'body': body, 'more_body': False}

        async def send(message):
            messages.append(message)

        await service.app({
            'type': 'http', 'asgi': {'version': '3.0', 'spec_version': '2.4'}, 'http_version': '1.1',
            'method': method, 'path': path, 'raw_path': path.encode(), 'query_string': b'',
            'root_path': '', 'scheme': 'http', 'headers': [],
            'client': ('127.0.0.1', 1234), 'server': ('office_sandbox', 8001),
        }, receive, send)
        content = b''.join(message.get('body', b'') for message in messages if message['type'] == 'http.response.body')
        return messages[0]['status'], content

    async def test_health(self):
        status, body = await self.request('/health', method='GET')
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {'status': 'ok'})

    async def test_conversion_streams_input_returns_archive_and_removes_job_files(self):
        directories = []

        def convert(source, directory):
            directories.append(directory)
            self.assertEqual(source.name, 'document.xlsx')
            self.assertEqual(source.read_bytes(), b'workbook')
            archive = directory / 'result.tar'
            archive.write_bytes(b'converted archive')
            return archive

        with patch.object(service, 'convert_document', convert):
            status, body = await self.request('/convert/xlsx', b'workbook')
        self.assertEqual(status, 200)
        self.assertEqual(body, b'converted archive')
        self.assertFalse(directories[0].exists())

    async def test_invalid_empty_and_oversized_uploads_do_not_start_worker(self):
        with patch.object(service, 'convert_document') as convert:
            self.assertEqual((await self.request('/convert/exe', b'bad'))[0], 415)
            self.assertEqual((await self.request('/convert/docx'))[0], 422)
            with patch.object(service, 'MAX_INPUT', 1):
                self.assertEqual((await self.request('/convert/docx', b'too big'))[0], 413)
            convert.assert_not_called()

    async def test_busy_worker_is_rejected_and_conversion_failures_are_reported(self):
        async with service.conversion_lock:
            self.assertEqual((await self.request('/convert/docx', b'document'))[0], 503)
        with patch.object(service, 'convert_document', side_effect=HTTPException(422, 'Unsafe formula')):
            status, body = await self.request('/convert/xlsx', b'workbook')
        self.assertEqual(status, 422)
        self.assertIn('Unsafe formula', json.loads(body)['detail'])


class WorkerProcessTests(unittest.TestCase):
    def test_timeout_kills_the_worker_process_group(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            source = directory / 'document.docx'
            source.write_bytes(b'document')
            process = Mock(pid=1234)
            process.poll.return_value = None
            with patch.object(service.subprocess, 'Popen', return_value=process), \
                    patch.object(service.time, 'monotonic', side_effect=[0, service.CONVERSION_TIMEOUT + 1]), \
                    patch.object(service.os, 'killpg') as kill:
                with self.assertRaises(HTTPException) as error:
                    service.convert_document(source, directory)
                self.assertEqual(error.exception.status_code, 504)
                kill.assert_called_once_with(1234, service.signal.SIGKILL)
                process.wait.assert_called_once()
