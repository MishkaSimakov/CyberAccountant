"""Policy and recalculation tests need no LibreOffice installation."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from office_sandbox import office_worker as worker


def package(path, formula='SUM(A1:A2)', extra=None):
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('xl/worksheets/sheet1.xml',
                         '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                         '<sheetData><row r="3"><c r="A3"><f>' + formula + '</f><v>0</v></c></row></sheetData></worksheet>')
        for name, content in (extra or {}).items():
            archive.writestr(name, content)


class OfficeWorkerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.source = Path(temporary.name) / 'source.xlsx'

    def test_recalculation_does_not_trust_stale_formula_cache(self):
        package(self.source)
        policy = worker.inspect_package(self.source)
        self.assertEqual(policy['formulas_before'][0]['cached_before'], '0')

        class Document:
            value = 0

            def enableAutomaticCalculation(self, enabled):
                pass

            def calculateAll(self):
                self.value = 150

        with patch.object(worker, 'formula_results', lambda doc: [{'numeric_value': doc.value}]):
            self.assertEqual(worker.recalculate_document(Document(), policy['formula_count']), [{'numeric_value': 150}])

    def test_active_formulas_are_rejected(self):
        for formula in ('WEBSERVICE("https://example.org")', '_xlfn.WEBSERVICE("https://example.org")',
                        'DDE("cmd","/C example","A0")', 'CALL("library","function")',
                        'RTD("server",,"topic")', 'EXEC("example")', 'PY("print(1)")', "'cmd'|'argument'!A0"):
            with self.subTest(formula=formula):
                package(self.source, formula)
                with self.assertRaises(worker.DocumentRejected):
                    worker.inspect_package(self.source)

    def test_active_package_parts_are_rejected(self):
        for name in ('xl/vbaProject.bin', 'xl/macrosheets/sheet1.xml', 'xl/embeddings/oleObject1.bin',
                     'xl/activeX/activeX1.xml', 'xl/externalLinks/externalLink1.xml', 'xl/connections.xml'):
            with self.subTest(name=name):
                package(self.source, extra={name: b'active content'})
                with self.assertRaises(worker.DocumentRejected):
                    worker.inspect_package(self.source)

    def test_external_data_rejected_but_hyperlinks_allowed(self):
        relationships = '<Relationships><Relationship Type="https://example.test/image" TargetMode="External" Target="file:///secret"/></Relationships>'
        package(self.source, extra={'xl/_rels/workbook.xml.rels': relationships})
        with self.assertRaises(worker.DocumentRejected):
            worker.inspect_package(self.source)
        package(self.source, extra={'xl/_rels/workbook.xml.rels': relationships.replace('/image', '/hyperlink')})
        self.assertEqual(worker.inspect_package(self.source)['formula_count'], 1)
