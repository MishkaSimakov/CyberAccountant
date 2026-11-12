"""Exercise the service protocol without Docker or OCR libraries."""
import io
import json
from email.message import Message
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from server.app.ocr import office_client as client


def make_archive(entries):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode='w') as archive:
        for name, value in entries.items():
            member = tarfile.TarInfo(name)
            if value is None:
                member.type = tarfile.SYMTYPE
                member.linkname = '/host/secret'
                archive.addfile(member)
            else:
                member.size = len(value)
                archive.addfile(member, io.BytesIO(value))
    return output.getvalue()


class Response(io.BytesIO):
    def __init__(self, content, content_type='application/x-tar'):
        super().__init__(content)
        self.headers = Message()
        self.headers['Content-Type'] = content_type


class OfficeClientTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.source = self.directory / 'source.xlsx'
        self.source.write_bytes(b'workbook bytes')
        self.destination = self.directory / 'preview.pdf'
        self.artifacts = {
            'preview.pdf': b'%PDF-1.7\n',
            'recalculated.xlsx': b'recalculated workbook',
            'report.json': json.dumps({'engine': 'libreoffice', 'recalculated': True}).encode(),
        }

    def test_sends_bytes_to_service_and_saves_validated_artifacts(self):
        with patch.object(client, 'build_opener') as factory:
            opener = factory.return_value
            opener.open.return_value = Response(make_archive(self.artifacts))
            report = client.render(self.source, self.destination, url='http://office_sandbox:8001/')
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, 'http://office_sandbox:8001/convert/xlsx')
        self.assertEqual(request.data, b'workbook bytes')
        self.assertEqual(request.method, 'POST')
        self.assertEqual(self.destination.read_bytes(), self.artifacts['preview.pdf'])
        self.assertEqual(self.destination.with_name('recalculated.xlsx').read_bytes(),
                         self.artifacts['recalculated.xlsx'])
        self.assertTrue(report['recalculated'])
        self.assertEqual(report['isolation']['transport'], 'http')

    def test_service_url_can_be_configured_through_environment(self):
        with patch.dict(client.os.environ, {'OFFICE_SANDBOX_URL': 'http://worker:9000'}), \
                patch.object(client, 'build_opener') as factory:
            factory.return_value.open.return_value = Response(make_archive(self.artifacts))
            client.render(self.source, self.destination)
            request = factory.return_value.open.call_args.args[0]
        self.assertEqual(request.full_url, 'http://worker:9000/convert/xlsx')

    def test_service_errors_have_no_host_office_fallback(self):
        for error in (URLError('unavailable'),
                      HTTPError('http://worker', 422, 'Rejected', {}, io.BytesIO(b'bad document'))):
            with self.subTest(error=error), patch.object(client, 'build_opener') as factory:
                factory.return_value.open.side_effect = error
                with self.assertRaises(client.SandboxError):
                    client.render(self.source, self.destination)
                self.assertFalse(self.destination.exists())

    def test_paths_links_and_missing_recalculation_are_rejected(self):
        for entries in ({'../escape': b'x'}, {'preview.pdf': None}, {
            **self.artifacts, 'report.json': b'{"engine":"libreoffice","recalculated":false}',
        }):
            with self.subTest(entries=entries), patch.object(client, 'build_opener') as factory:
                factory.return_value.open.return_value = Response(make_archive(entries))
                with self.assertRaises(client.SandboxError):
                    client.render(self.source, self.destination)
                self.assertFalse(self.destination.exists())

    def test_oversized_input_and_response_are_rejected(self):
        with patch.object(client, 'MAX_INPUT', 1), patch.object(client, 'build_opener') as factory:
            with self.assertRaisesRegex(client.SandboxError, 'input exceeds'):
                client.render(self.source, self.destination)
            factory.assert_not_called()
        with patch.object(client, 'MAX_OUTPUT', 1), patch.object(client, 'build_opener') as factory:
            factory.return_value.open.return_value = Response(b'oversized')
            with self.assertRaisesRegex(client.SandboxError, 'output limit'):
                client.render(self.source, self.destination)
        self.assertFalse(self.destination.exists())

    def test_unexpected_content_type_is_rejected(self):
        with patch.object(client, 'build_opener') as factory:
            factory.return_value.open.return_value = Response(b'not an archive', 'text/html')
            with self.assertRaisesRegex(client.SandboxError, 'content type'):
                client.render(self.source, self.destination)
