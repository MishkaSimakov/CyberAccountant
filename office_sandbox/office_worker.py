"""Untrusted Office processing. This entrypoint must run only in its container."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import time
import xml.etree.ElementTree as ET
import zipfile

NS = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
FORBIDDEN_FUNCTION = re.compile(
    r'(?i)(?<![A-Z0-9_])(?:_xlfn\.)?(?:WEBSERVICE|RTD|DDE|CALL|REGISTER(?:\.ID)?|EXEC|EVALUATE|RUN|PY|PYTHON)\s*\(')


class DocumentRejected(RuntimeError):
    pass


def inspect_package(source):
    """Reject active parts and external data before the office engine sees them."""
    formulas = []
    with zipfile.ZipFile(source) as archive:
        entries = archive.infolist()
        if len(entries) > 10000 or sum(info.file_size for info in entries) > 256*1024*1024:
            raise DocumentRejected('OOXML package exceeds its expanded size/entry limit.')
        for info in entries:
            name = info.filename
            lower = name.lower()
            if (lower.endswith('vbaproject.bin') or any(part in lower for part in (
                '/macrosheets/', '/dialogsheets/', '/activex/', '/embeddings/',
                '/externallinks/', '/querytables/')) or lower in ('xl/connections.xml',)):
                raise DocumentRejected('Active content or external data is prohibited: ' + name)
            if info.flag_bits & 1:
                raise DocumentRejected('Encrypted OOXML entries are not supported.')
            if lower == '[content_types].xml':
                types = archive.read(info).lower()
                if b'macroenabled' in types or b'macrosheet' in types or b'vbaproject' in types:
                    raise DocumentRejected('Macro-enabled document types are prohibited.')
            if lower.endswith('.rels'):
                root = ET.fromstring(archive.read(info))
                for relationship in root:
                    kind = relationship.get('Type', '').rsplit('/', 1)[-1].lower()
                    if kind in ('vbaproject', 'oleobject', 'externallink', 'attachedtemplate', 'control'):
                        raise DocumentRejected('Active/external relationship is prohibited: ' + kind)
                    if relationship.get('TargetMode', '').lower() == 'external' and kind != 'hyperlink':
                        raise DocumentRejected('External file/data relationship is prohibited.')
            if source.suffix == '.xlsx' and lower.startswith('xl/worksheets/') and lower.endswith('.xml'):
                root = ET.fromstring(archive.read(info))
                for cell in root.iter(NS+'c'):
                    formula = cell.find(NS+'f')
                    if formula is not None:
                        text = formula.text or ''
                        if FORBIDDEN_FUNCTION.search(text) or '|' in text:
                            raise DocumentRejected('Unsafe formula in ' + cell.get('r', '?'))
                        cached = cell.find(NS+'v')
                        formulas.append({'sheet_part': name, 'cell': cell.get('r'),
                                         'formula': text, 'cached_present': cached is not None,
                                         'cached_before': cached.text if cached is not None else None})
            if source.suffix == '.xlsx' and lower == 'xl/workbook.xml':
                root = ET.fromstring(archive.read(info))
                for node in root.iter(NS+'definedName'):
                    text = node.text or ''
                    if FORBIDDEN_FUNCTION.search(text) or '|' in text:
                        raise DocumentRejected('Unsafe defined name.')
    return {'formula_count': len(formulas), 'formulas_before': formulas}


def column_name(index):
    result = ''
    while index >= 0:
        index, digit = divmod(index, 26)
        result = chr(65+digit) + result
        index -= 1
    return result


def formula_results(document):
    results = []
    sheets = document.getSheets()
    for sheet_name in sheets.getElementNames():
        sheet = sheets.getByName(sheet_name)
        for region in sheet.queryContentCells(16).getRangeAddresses():
            size = (region.EndRow-region.StartRow+1)*(region.EndColumn-region.StartColumn+1)
            if size > 100000 or len(results)+size > 100000:
                raise DocumentRejected('Too many formula cells.')
            for row in range(region.StartRow, region.EndRow+1):
                for col in range(region.StartColumn, region.EndColumn+1):
                    cell = sheet.getCellByPosition(col, row)
                    error = cell.getError()
                    address = column_name(col) + str(row+1)
                    if error:
                        raise DocumentRejected(f'Formula calculation failed: {sheet_name}!{address}, Calc error {error}. '
                                               'Cached values will not be substituted.')
                    results.append({'sheet': sheet_name, 'cell': address,
                                    'formula': cell.getFormula(), 'numeric_value': cell.getValue(),
                                    'display_text': cell.getString()})
    return results


def recalculate_document(document, expected_count):
    document.enableAutomaticCalculation(True)
    document.calculateAll()
    # Freeze volatile results so the cached XLSX and its PDF agree.
    document.enableAutomaticCalculation(False)
    results = formula_results(document)
    if len(results) != expected_count:
        raise DocumentRejected('Formula count changed during import; refusing an incomplete calculation.')
    return results


def property_value(uno, name, value):
    prop = uno.createUnoStruct('com.sun.star.beans.PropertyValue')
    prop.Name, prop.Value = name, value
    return prop


def process_document(source, work):
    policy = inspect_package(source)
    # Import only inside the container; unit tests for policy need no UNO runtime.
    import uno
    work.mkdir(parents=True, exist_ok=True)
    (work/'home').mkdir(exist_ok=True)
    local_copy = work/('source' + source.suffix)
    shutil.copyfile(source, local_copy)
    local_copy.chmod(0o600)
    profile = work/'profile'
    user_profile = profile/'user'
    user_profile.mkdir(parents=True)
    (user_profile/'registrymodifications.xcu').write_text(
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<oor:items xmlns:oor="http://openoffice.org/2001/registry">'
        '<item oor:path="/org.openoffice.Office.Common/Security/Scripting">'
        '<prop oor:name="MacroSecurityLevel" oor:op="fuse"><value>3</value></prop>'
        '</item></oor:items>', encoding='utf-8')
    pipe = 'document_ocr_private'
    engine = subprocess.Popen([
        'soffice', '-env:UserInstallation=' + profile.as_uri(), '--headless', '--nologo',
        '--nodefault', '--norestore', '--nolockcheck',
        '--accept=pipe,name=' + pipe + ';urp;StarOffice.ComponentContext'],
        stdout=subprocess.DEVNULL, stderr=sys.stderr,
        env={**os.environ, 'SAL_USE_VCLPLUGIN': 'svp'})
    document = None
    try:
        context = uno.getComponentContext()
        resolver = context.ServiceManager.createInstanceWithContext('com.sun.star.bridge.UnoUrlResolver', context)
        remote = None
        for _ in range(300):
            if engine.poll() is not None:
                raise DocumentRejected('LibreOffice failed to start.')
            try:
                remote = resolver.resolve('uno:pipe,name=' + pipe + ';urp;StarOffice.ComponentContext')
                break
            except Exception:
                time.sleep(.1)
        if remote is None:
            raise DocumentRejected('LibreOffice connection timed out.')
        desktop = remote.ServiceManager.createInstanceWithContext('com.sun.star.frame.Desktop', remote)
        pv = lambda name, value: property_value(uno, name, value)
        options = (pv('Hidden', True), pv('ReadOnly', False),
                   pv('MacroExecutionMode', uno.getConstantByName('com.sun.star.document.MacroExecMode.NEVER_EXECUTE')),
                   pv('UpdateDocMode', uno.getConstantByName('com.sun.star.document.UpdateDocMode.NO_UPDATE')))
        document = desktop.loadComponentFromURL(local_copy.as_uri(), '_blank', 0, options)
        if document is None:
            raise DocumentRejected('Office engine could not open the document.')
        report = {'engine': 'libreoffice', 'recalculated': False, 'macro_execution': 'never',
                  'external_updates': 'never', **policy}
        files = []
        if source.suffix == '.xlsx':
            # Explicit rebuild even when XLSX calculation mode is manual or the
            # cache says zero. Never rely on recalculation as a side effect of export.
            results = recalculate_document(document, policy['formula_count'])
            report.update(recalculated=True, formula_results=results,
                          recalculated_formula_count=len(results),
                          calculation_mode='explicit_full_rebuild_then_frozen')
            recalculated = work/'recalculated.xlsx'
            document.storeAsURL(recalculated.as_uri(),
                                (pv('FilterName', 'Calc MS Excel 2007 XML'), pv('Overwrite', True)))
            files.append(recalculated)
            filter_name = 'calc_pdf_Export'
            filter_data = (pv('SinglePageSheets', True),)
        else:
            filter_name, filter_data = 'writer_pdf_Export', ()
        pdf = work/'preview.pdf'
        document.storeToURL(pdf.as_uri(), (pv('FilterName', filter_name),
                            pv('FilterData', uno.Any('[]com.sun.star.beans.PropertyValue', filter_data)),
                            pv('Overwrite', True)))
        # Verify that the saved workbook really contains a new cache. Formula
        # errors or missing caches must not silently become zero in Markdown.
        if source.suffix == '.xlsx':
            cached = inspect_package(recalculated)
            if cached['formula_count'] != policy['formula_count']:
                raise DocumentRejected('Formula cells were lost during save.')
            if any(not item['cached_present'] for item in cached['formulas_before']):
                raise DocumentRejected('Office engine did not persist formula results.')
        report_path = work/'report.json'
        report_path.write_text(json.dumps(report, ensure_ascii=False), encoding='utf-8')
        files.extend([pdf, report_path])
        return files
    finally:
        if document is not None:
            try:
                document.close(True)
            except Exception:
                pass
        engine.terminate()
        try:
            engine.wait(timeout=5)
        except subprocess.TimeoutExpired:
            engine.kill()
            engine.wait()


def main():
    sources = list(Path('/input').glob('document.*'))
    if len(sources) != 1 or sources[0].suffix not in ('.docx', '.xlsx'):
        raise DocumentRejected('Exactly one DOCX/XLSX input is required.')
    files = process_document(sources[0], Path('/work/job'))
    # Binary stdout is the only output channel. No writable host folders.
    with tarfile.open(fileobj=sys.stdout.buffer, mode='w|') as archive:
        for file in files:
            archive.add(file, arcname=file.name, recursive=False)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(f'Office sandbox error: {exc}', file=sys.stderr)
        raise SystemExit(1)
