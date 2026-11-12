"""Temporary converter artifacts must not outlive an upload's OCR job."""

from unittest.mock import patch

import pymupdf as fitz
import pytest

from server.app import documents, ocr
from server.app.ocr import ConversionError


@pytest.mark.parametrize("fails", [False, True])
def test_temporary_artifacts_are_removed_on_success_and_failure(fails):
    paths = []

    def convert(source, output, args):
        paths.extend([source.parent, output.parent])
        assert source.suffix == ".pdf"
        assert source.read_bytes() == b"uploaded bytes"
        assets = output.parent / "document.assets"
        assets.mkdir()
        (assets / "preview.pdf").write_bytes(b"preview")
        output.with_suffix(".boxes.json").write_text("{}")
        if fails:
            raise ConversionError("Cannot convert document")
        output.write_text("# Распознанный текст\n", encoding="utf-8")
        return {"preview_pdf": "document.assets/preview.pdf"}

    with patch.object(ocr, "convert", side_effect=convert):
        if fails:
            with pytest.raises(ConversionError):
                documents.extract_markdown("../../REPORT.PDF", b"uploaded bytes")
        else:
            assert documents.extract_markdown("../../REPORT.PDF", b"uploaded bytes") == "# Распознанный текст\n"
    assert len(paths) == 2
    assert all(not path.exists() for path in paths)


def test_parse_document_returns_bundle_for_one_original(tmp_path):
    source = tmp_path / "original.pdf"
    with fitz.open() as pdf:
        pdf.new_page().insert_text((50, 50), "Total 12345.67")
        pdf.save(source)
    original = source.read_bytes()

    with ocr.parse_document(source) as parsed:
        assert "Total 12345.67" in parsed.markdown.read_text(encoding="utf-8")
        assert parsed.json.is_file()
        with fitz.open(parsed.pdf) as pdf:
            assert len(pdf) == 1
        directory = parsed.markdown.parent
    assert not directory.exists()
    assert source.read_bytes() == original


def test_parse_document_reports_missing_file(tmp_path):
    with pytest.raises(ConversionError, match="Input file does not exist"):
        with ocr.parse_document(tmp_path / "missing.pdf"):
            pytest.fail("Missing files must not produce a parsed document")


def test_parse_document_describes_unexpected_converter_errors(tmp_path):
    with patch.object(ocr, "convert", side_effect=ValueError("broken PDF")):
        with pytest.raises(ConversionError, match="Cannot parse original.pdf: broken PDF"):
            with ocr.parse_document(tmp_path / "original.pdf"):
                pytest.fail("Conversion errors must not produce a parsed document")
