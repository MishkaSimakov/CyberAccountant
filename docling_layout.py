"""Local Docling PDF/OCR pipeline with an adapter for real character boxes."""
from __future__ import annotations

from functools import lru_cache
import io
import os
from pathlib import Path
import re
import shutil
from types import SimpleNamespace


def prepare_tesseract(executable, environment, languages):
    """Keep native Windows paths and CLI language discovery free of encoding issues."""
    environment = environment.copy()
    data = environment.get('TESSDATA_PREFIX')
    if os.name == 'nt' and data:
        source = Path(data)
        cache = Path.home()/'.cache/document-ocr/tessdata'
        cache.mkdir(parents=True, exist_ok=True)
        for language in [*languages, 'osd']:
            destination = cache/(language+'.traineddata')
            if not destination.exists():
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source/(language+'.traineddata'), destination)
        configs = next((p for p in [source/'configs', Path(executable).parent/'../share/tessdata/configs']
                        if p.is_dir()), None)
        if configs is None:
            raise ValueError('Tesseract configs/tsv is required by Docling; install a complete Tesseract distribution.')
        shutil.copytree(configs, cache/'configs', dirs_exist_ok=True)
        environment['TESSDATA_PREFIX'] = str(cache)
    for key in ('PATH', 'TESSDATA_PREFIX'):
        if key in environment:
            os.environ[key] = environment[key]
    return environment.get('TESSDATA_PREFIX')


@lru_cache(maxsize=4)
def converter(models, languages, ocr, psm, scale, executable, data, table_model):
    # Models are downloaded explicitly by the setup command, not with documents.
    os.environ['HF_HUB_OFFLINE'] = '1'
    from docling.backend.pypdfium2_backend import PyPdfiumDocumentBackend
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import (
        OcrMode, PdfPipelineOptions, TesseractCliOcrOptions, TableFormerMode,
        TableStructureOptions, TableStructureV2Options)
    from docling.document_converter import DocumentConverter, PdfFormatOption
    options = PdfPipelineOptions(
        artifacts_path=Path(models), enable_remote_services=False,
        do_ocr=ocr != 'never', generate_parsed_pages=False,
        ocr_options=TesseractCliOcrOptions(
            lang=list(languages), tesseract_cmd=executable or 'tesseract', path=data,
            mode=OcrMode.FULL_PAGE if ocr == 'always' else OcrMode.DEFAULT, scale=scale, psm=psm),
        table_structure_options=TableStructureV2Options() if table_model == 'v2' else TableStructureOptions(
            mode=TableFormerMode.FAST if table_model == 'v1-fast' else TableFormerMode.ACCURATE),
    )
    return DocumentConverter(allowed_formats=[InputFormat.PDF], format_options={
        InputFormat.PDF: PdfFormatOption(pipeline_options=options, backend=PyPdfiumDocumentBackend)})


class DoclingLayout:
    def __init__(self, preview, args, *, image_pixels=False, executable=None, environment=None):
        from docling.datamodel.base_models import DocumentStream
        self.languages = tuple(args.lang.split('+'))
        previous = {key: os.environ.get(key) for key in ('PATH', 'TESSDATA_PREFIX')}
        try:
            data = prepare_tesseract(executable, environment, self.languages) if executable else None
            parser = converter(str(args.models), self.languages, args.ocr,
                               (6 if image_pixels else 11) if args.psm == 'auto' else int(args.psm),
                               1.0 if image_pixels else args.dpi/72,
                               executable, data, args.docling_table_model)
            self.result = parser.convert(DocumentStream(name='preview.pdf', stream=io.BytesIO(preview.read_bytes())))
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
        self.document = self.result.document

    def write_page(self, writer, page, number, words):
        from docling_core.types.doc import TableItem
        # Line ids preserve OCR reading order without inventing per-letter boxes.
        glyphs = [(word.line, char) for word in words for char in word.characters]
        markers, edits = {}, []

        def mark(obj, box):
            text = re.sub(r'\s+', ' ', obj.text).strip()
            if not text:
                return
            chars = []
            if box is not None:
                rect = box.to_top_left_origin(page.rect.height)
                groups = {}
                for line, char in glyphs:
                    x0, y0, x1, y1 = char['bbox']
                    if rect.l-.5 <= (x0+x1)/2 <= rect.r+.5 and rect.t-.5 <= (y0+y1)/2 <= rect.b+.5:
                        groups.setdefault(line, []).append(char)
                for line in sorted(groups, key=lambda key: (
                        min(c['bbox'][1] for c in groups[key]), min(c['bbox'][0] for c in groups[key]))):
                    chars.extend(sorted(groups[line], key=lambda c: c['bbox'][0]))
            marker = f'ZQDOCLING{len(markers)}QZ'
            markers[marker] = SimpleNamespace(text=text, characters=chars)
            edits.append((obj, obj.text))
            obj.text = marker

        count = 0
        for item, _ in self.document.iterate_items():
            if not item.prov or item.prov[0].page_no != number:
                continue
            if isinstance(item, TableItem):
                count += 1
                for cell in item.data.table_cells:
                    mark(cell, cell.bbox)
            elif hasattr(item, 'text'):
                mark(item, item.prov[0].bbox)
        try:
            markdown = self.document.export_to_markdown(page_no=number)
        finally:
            for obj, text in edits:
                obj.text = text
        writer.write(f'<!-- page: {number} -->\n\n')
        position = 0
        for match in re.finditer(r'ZQDOCLING\d+QZ', markdown):
            writer.write(markdown[position:match.start()])
            writer.word(markers[match.group()], number, [page.rect.width, page.rect.height])
            position = match.end()
        writer.write(markdown[position:]+'\n\n')
        return count


def download_models(destination, table_model):
    """Explicit setup step: downloads model weights, never receives documents."""
    from docling.utils.model_downloader import download_models as fetch
    return fetch(output_dir=destination,
                 with_tableformer=table_model in ('v1', 'both'),
                 with_tableformer_v2=table_model in ('v2', 'both'),
                 with_code_formula=False, with_picture_classifier=False, with_rapidocr=False)
