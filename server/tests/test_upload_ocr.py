"""Temporary converter artifacts must not outlive an upload's OCR job."""

from unittest.mock import patch

import pymupdf as fitz
import pytest

from server.app import storage
from server.app.ocr import converter as ocr
from server.app.ocr import ConversionError


@pytest.mark.parametrize("fails", [False, True])
def test_ocr_bundle_is_preserved_before_temporary_files_are_removed(fails, tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path / "storage"))
    source = tmp_path / "original.pdf"
    source.write_bytes(b"uploaded bytes")
    paths = []

    def convert(source, output, args, *, logger):
        paths.append(output.parent)
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
                with ocr.parse_document(source):
                    pytest.fail("Failed OCR must not produce artifacts")
        else:
            with ocr.parse_document(source) as parsed:
                saved = storage.save_ocr(1, 2, 1, parsed.markdown)
            markdown = storage.artifact_path(saved)
            assert markdown.read_text(encoding="utf-8") == "# Распознанный текст\n"
            assert markdown.with_suffix(".boxes.json").is_file()
            assert (markdown.parent / "document.assets" / "preview.pdf").read_bytes() == b"preview"
    assert len(paths) == 1
    assert all(not path.exists() for path in paths)
    assert source.read_bytes() == b"uploaded bytes"


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
