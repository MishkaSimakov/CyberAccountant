"""Exercise the upload-stage HTTP contract without external services or test dependencies."""

import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch

from server.app import documents as store
from server.app import entities
from server.app.main import Report, app, report_store


class DocumentApiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        report_store[:] = [Report(id=1, name="Test report"), Report(id=2, name="Other report")]
        store.documents.clear()
        store.originals.clear()
        store.revisions.clear()
        store.next_document_id = 1
        entities.next_entity_id = 1
        self.sleep_patch = patch.object(store.asyncio, "sleep", new_callable=AsyncMock)
        self.sleep_patch.start()
        self.addCleanup(self.sleep_patch.stop)

    async def request(self, method, path, body=b"", content_type="application/json"):
        messages = []

        async def receive():
            return {"type": "http.request", "body": body, "more_body": False}

        async def send(message):
            messages.append(message)

        await app(
            {
                "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
                "method": method, "path": f"/api/v1{path}", "raw_path": f"/api/v1{path}".encode(),
                "query_string": b"", "root_path": "", "scheme": "http",
                "headers": [(b"content-type", content_type.encode()), (b"content-length", str(len(body)).encode())],
                "client": ("127.0.0.1", 1234), "server": ("test", 80),
            },
            receive,
            send,
        )
        response = b"".join(message.get("body", b"") for message in messages if message["type"] == "http.response.body")
        return messages[0]["status"], response

    async def json_request(self, method, path, payload=None):
        status, body = await self.request(method, path, json.dumps(payload).encode() if payload is not None else b"")
        return status, json.loads(body) if body else None

    async def upload(self, role="supporting", name="invoice.pdf", content=b"%PDF-1.4\nexample\n%%EOF", report_id=1):
        body = (
            f'--boundary\r\nContent-Disposition: form-data; name="role"\r\n\r\n{role}\r\n'
            f'--boundary\r\nContent-Disposition: form-data; name="file"; filename="{name}"\r\n'
            'Content-Type: application/octet-stream\r\n\r\n'
        ).encode() + content + b"\r\n--boundary--\r\n"
        status, response = await self.request("POST", f"/reports/{report_id}/documents", body, "multipart/form-data; boundary=boundary")
        return status, json.loads(response)

    async def test_upload_original_and_processing(self):
        status, uploaded = await self.upload()
        self.assertEqual(status, 201)
        self.assertEqual(uploaded["ocr_status"], "queued")
        status, details = await self.json_request("GET", "/reports/1")
        self.assertEqual(status, 200)
        self.assertEqual(details["documents"][0]["ocr_status"], "completed")
        self.assertEqual(details["progress"], {"completed": 1, "total": 1})
        status, original = await self.request("GET", f'/reports/1/documents/{uploaded["id"]}/original')
        self.assertEqual(status, 200)
        self.assertEqual(original, b"%PDF-1.4\nexample\n%%EOF")

    async def test_upload_validation(self):
        for name, content, expected in [("file.exe", b"x", 415), ("empty.pdf", b"", 422)]:
            status, _ = await self.upload(name=name, content=content)
            self.assertEqual(status, expected)
        with patch.object(store, "MAX_FILE_SIZE", 4):
            status, _ = await self.upload(content=b"12345")
            self.assertEqual(status, 413)
        status, _ = await self.upload(role="unknown")
        self.assertEqual(status, 422)
        self.assertFalse(store.documents)

    async def test_polling_exposes_queued_running_and_completed(self):
        first_wait = asyncio.Event()
        second_wait = asyncio.Event()
        release_first = asyncio.Event()
        release_second = asyncio.Event()

        async def wait_for_test(delay):
            if delay == 1:
                first_wait.set()
                await release_first.wait()
            else:
                second_wait.set()
                await release_second.wait()

        with patch.object(store.asyncio, "sleep", side_effect=wait_for_test):
            upload_task = asyncio.create_task(self.upload())
            try:
                await asyncio.wait_for(first_wait.wait(), timeout=2)
                _, details = await self.json_request("GET", "/reports/1")
                self.assertEqual(details["documents"][0]["ocr_status"], "queued")
                self.assertFalse(details["available_actions"])
                release_first.set()
                await asyncio.wait_for(second_wait.wait(), timeout=2)
                _, details = await self.json_request("GET", "/reports/1")
                self.assertEqual(details["documents"][0]["ocr_status"], "running")
                self.assertEqual(details["progress"]["completed"], 0)
                release_second.set()
                await asyncio.wait_for(upload_task, timeout=2)
                _, details = await self.json_request("GET", "/reports/1")
                self.assertEqual(details["documents"][0]["ocr_status"], "completed")
            finally:
                release_first.set()
                release_second.set()
                await upload_task

    async def test_failed_document_blocks_until_deleted_and_reuploaded(self):
        await self.upload(role="final_report", name="report.pdf")
        _, failed = await self.upload(content=b"damaged")
        _, details = await self.json_request("GET", "/reports/1")
        self.assertEqual(details["processing_status"], "failed")
        self.assertFalse(details["available_actions"])
        self.assertTrue(details["documents"][1]["error"])
        status, _ = await self.json_request("POST", "/reports/1/run-entity-extraction")
        self.assertEqual(status, 409)
        status, body = await self.request("DELETE", f'/reports/1/documents/{failed["id"]}')
        self.assertEqual((status, body), (204, b""))
        self.assertNotIn(failed["id"], store.originals)
        self.assertNotIn(failed["id"], store.revisions)
        await self.upload()
        _, details = await self.json_request("GET", "/reports/1")
        self.assertEqual(details["available_actions"], ["run_entity_extraction"])

    async def test_markdown_revisions_conflicts_and_empty_edits(self):
        _, uploaded = await self.upload()
        path = f'/reports/1/documents/{uploaded["id"]}/markdown'
        status, markdown = await self.json_request("GET", path)
        self.assertEqual(status, 200)
        self.assertEqual(markdown["revision"], 1)
        status, saved = await self.json_request("POST", path, {"content": "# Исправленный текст", "base_revision": 1})
        self.assertEqual((status, saved["revision"]), (200, 2))
        status, _ = await self.json_request("POST", path, {"content": "Stale edit", "base_revision": 1})
        self.assertEqual(status, 409)
        status, _ = await self.json_request("POST", path, {"content": "   ", "base_revision": 2})
        self.assertEqual(status, 422)
        self.assertEqual(len(store.revisions[uploaded["id"]]), 2)
        self.assertTrue(store.documents[1][uploaded["id"]].user_edited)

    async def test_final_report_is_required_and_unique(self):
        await self.upload()
        status, _ = await self.json_request("POST", "/reports/1/run-entity-extraction")
        self.assertEqual(status, 409)
        await self.upload(role="final_report", name="report.pdf")
        status, _ = await self.upload(role="final_report", name="second.pdf")
        self.assertEqual(status, 409)

    async def test_transition_and_stage_permissions(self):
        await self.upload(role="final_report")
        _, uploaded = await self.upload()
        status, report = await self.json_request("POST", "/reports/1/run-entity-extraction")
        self.assertEqual((status, report["stage"]), (200, "entities"))
        self.assertFalse(report["available_actions"])
        status, _ = await self.upload()
        self.assertEqual(status, 409)
        status, _ = await self.json_request("DELETE", f'/reports/1/documents/{uploaded["id"]}')
        self.assertEqual(status, 409)
        status, _ = await self.json_request("POST", f'/reports/1/documents/{uploaded["id"]}/markdown', {"content": "edit", "base_revision": 1})
        self.assertEqual(status, 200)
        status, _ = await self.json_request("GET", f'/reports/1/documents/{uploaded["id"]}/markdown')
        self.assertEqual(status, 200)

    async def test_documents_cannot_be_accessed_from_another_report(self):
        _, uploaded = await self.upload()
        for suffix in ("", "/original", "/markdown"):
            status, _ = await self.request("GET", f'/reports/2/documents/{uploaded["id"]}{suffix}')
            self.assertEqual(status, 404)
        status, _ = await self.json_request("GET", "/reports/999")
        self.assertEqual(status, 404)

    async def test_entity_creation_editing_and_server_ids(self):
        import re

        _, uploaded = await self.upload()
        path = f'/reports/1/documents/{uploaded["id"]}/markdown'
        _, original = await self.json_request("GET", path)
        initial_id = re.search(r'id="(\d+)"', original["content"])[1]
        content = original["content"].replace('value="ВЕД-ЗП-2026-02"', 'value="corrected &amp; quoted &quot;value&quot;"', 1)
        content += '\n<entity type="number" value="12.50" unit="euros">12,50 €</entity>'
        status, saved = await self.json_request("POST", path, {"content": content, "base_revision": 1})
        self.assertEqual(status, 200)
        self.assertIn(f'id="{initial_id}"', saved["content"])
        self.assertIn('value="corrected &amp; quoted &quot;value&quot;"', saved["content"])
        ids = re.findall(r'\bid="(\d+)"', saved["content"])
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(len(ids), 9)
        self.assertTrue(store.documents[1][uploaded["id"]].user_edited)
        self.assertEqual(len(store.revisions[uploaded["id"]]), 2)
        status, _ = await self.json_request("POST", path, {"content": content, "base_revision": 1})
        self.assertEqual(status, 409)
        # Re-saving existing markup preserves every assigned ID.
        status, again = await self.json_request("POST", path, {"content": saved["content"], "base_revision": 2})
        self.assertEqual(status, 200)
        self.assertEqual(again["content"], saved["content"])

    async def test_entity_validation_and_id_protection(self):
        _, uploaded = await self.upload()
        path = f'/reports/1/documents/{uploaded["id"]}/markdown'
        _, original = await self.json_request("GET", path)
        for markup in [
            original["content"] + original["content"],
            '<entity type="unknown" value="x" unit="string">x</entity>',
            '<entity type="id" value="   " unit="string">x</entity>',
            '<entity type="id" value="x" unit="kg">x</entity>',
            '<entity type="number" value="1" unit="dollars">1</entity>',
            '<entity type="number" value="NaN" unit="kg">1</entity>',
            '<entity type="number" value="1,25" unit="kg">1</entity>',
            '<entity type="date" value="2026-02-30" unit="date">date</entity>',
            '<entity type="date" value="2026-13" unit="date">date</entity>',
            '<entity type="id" value="x" unit="string" id="999999">x</entity>',
            '<entity type="id" value="x" unit="string" extra="x">x</entity>',
            '<entity type="id" value="x" unit="string">unclosed',
            '<entity type="id" value="x" unit="string"><entity type="id" value="y" unit="string">y</entity></entity>',
        ]:
            with self.subTest(markup=markup):
                status, _ = await self.json_request("POST", path, {"content": markup, "base_revision": 1})
                self.assertEqual(status, 422)
                self.assertEqual(len(store.revisions[uploaded["id"]]), 1)

    async def test_entity_ids_unique_across_documents_and_types(self):
        import re

        _, first = await self.upload(role="final_report")
        _, second = await self.upload()
        path = f'/reports/1/documents/{second["id"]}/markdown'
        _, a = await self.json_request("GET", f'/reports/1/documents/{first["id"]}/markdown')
        _, b = await self.json_request("GET", path)
        self.assertTrue(set(re.findall(r'\bid="(\d+)"', a["content"])).isdisjoint(re.findall(r'\bid="(\d+)"', b["content"])))
        await self.json_request("POST", "/reports/1/run-entity-extraction")
        content = '\n'.join([
            '<entity type="id" value="code" unit="string">code</entity>',
            '<entity type="date" value="2026-02" unit="date">Февраль</entity>',
            '<entity type="date" value="2024-02-29" unit="date">29 февраля</entity>',
            '<entity type="number" value="-12.5" unit="kg">-12,5</entity>',
        ])
        status, saved = await self.json_request("POST", path, {"content": content, "base_revision": 1})
        self.assertEqual(status, 200)
        self.assertEqual(len(re.findall(r'\bid="(\d+)"', saved["content"])), 4)
        report_store[0].stage = "verification"
        status, _ = await self.json_request("POST", path, {"content": content, "base_revision": 2})
        self.assertEqual(status, 409)

    async def test_deletion_during_ocr_does_not_recreate_document(self):
        from fastapi import BackgroundTasks, UploadFile
        from io import BytesIO

        tasks = BackgroundTasks()
        document = await store.upload_document(1, tasks, UploadFile(BytesIO(b"%PDF-1.4\n%%EOF"), filename="report.pdf"), "final_report")
        await store.delete_document(1, document.id)
        await tasks()
        self.assertFalse(store.documents[1])
        self.assertNotIn(document.id, store.revisions)


if __name__ == "__main__":
    unittest.main()
