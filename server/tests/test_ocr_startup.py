"""Required OCR languages must be available before the API starts."""

import unittest
from subprocess import CompletedProcess
from unittest.mock import patch

import pytest

from server.app import main
from server.app.ocr import converter as ocr


@pytest.mark.parametrize("languages, missing", [
    (["eng", "rus", "osd"], None),
    (["eng", "osd"], "rus"),
    (["rus"], "eng"),
    (["osd"], "eng, rus"),
])
def test_required_languages(languages, missing):
    output = 'List of available languages in "/tessdata/":\n' + '\n'.join(languages) + '\n'
    with patch.object(ocr, "find_tesseract", return_value="/test/tesseract"), patch.object(
            ocr, "run", return_value=CompletedProcess([], 0, stdout=output.encode())):
        if missing:
            with pytest.raises(ocr.ConversionError, match=f"Missing required Tesseract languages: {missing}"):
                ocr.check_ocr_languages()
        else:
            ocr.check_ocr_languages()


class OcrStartupTests(unittest.IsolatedAsyncioTestCase):
    async def test_startup_checks_languages(self):
        with patch.object(main, "check_ocr_languages") as check:
            async with main.app.router.lifespan_context(main.app):
                check.assert_called_once()

    async def test_startup_fails_if_languages_cannot_be_loaded(self):
        with patch.object(main, "check_ocr_languages", side_effect=ocr.ConversionError("Missing rus")):
            with self.assertRaisesRegex(ocr.ConversionError, "Missing rus"):
                async with main.app.router.lifespan_context(main.app):
                    self.fail("The API must not start without required OCR languages")
