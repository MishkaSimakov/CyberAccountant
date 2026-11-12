import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tarfile
import zipfile

import pytest

import office_sandbox as sandbox
import document_ocr

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('office_worker', ROOT/'office_sandbox/office_worker.py')
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)


def package(path, formula='SUM(A1:A2)', cache='0', extra=None):
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('xl/worksheets/sheet1.xml',
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<sheetData><row r="3"><c r="A3"><f>'+formula+'</f><v>'+cache+'</v></c></row></sheetData></worksheet>')
        for name, content in (extra or {}).items():
            archive.writestr(name, content)


def test_formula_cache_is_not_treated_as_a_calculated_result(tmp_path, monkeypatch):
    source = tmp_path/'stale.xlsx'
    package(source)
    policy = worker.inspect_package(source)
    assert policy['formula_count'] == 1
    assert policy['formulas_before'][0]['cached_before'] == '0'

    class FakeDocument:
        value = 0
        calls = []

        def enableAutomaticCalculation(self, enabled):
            self.calls.append(('automatic', enabled))

        def calculateAll(self):
            self.calls.append(('calculateAll',))
            self.value = 150

    document = FakeDocument()
    monkeypatch.setattr(worker, 'formula_results', lambda doc: [{'numeric_value': doc.value}])
    result = worker.recalculate_document(document, policy['formula_count'])
    assert result == [{'numeric_value': 150}]
    assert document.calls == [('automatic', True), ('calculateAll',), ('automatic', False)]


@pytest.mark.parametrize('formula', [
    'WEBSERVICE("https://example.org")', '_xlfn.WEBSERVICE("https://example.org")',
    'DDE("cmd","/C example","A0")', 'CALL("library","function")',
    'RTD("server",,"topic")', 'EXEC("example")', 'PY("print(1)")',
    "'cmd'|'argument'!A0",
])
def test_active_formulas_are_rejected_before_open(tmp_path, formula):
    source = tmp_path/'untrusted.xlsx'
    package(source, formula)
    with pytest.raises(worker.DocumentRejected, match='Unsafe formula'):
        worker.inspect_package(source)


@pytest.mark.parametrize('name', [
    'xl/vbaProject.bin', 'xl/macrosheets/sheet1.xml', 'xl/embeddings/oleObject1.bin',
    'xl/activeX/activeX1.xml', 'xl/externalLinks/externalLink1.xml', 'xl/connections.xml',
])
def test_active_package_parts_are_rejected(tmp_path, name):
    source = tmp_path/'renamed.xlsx'
    package(source, extra={name: b'active content'})
    with pytest.raises(worker.DocumentRejected):
        worker.inspect_package(source)


def test_external_data_relationship_rejected_but_normal_hyperlink_allowed(tmp_path):
    source = tmp_path/'external.xlsx'
    relationships = '<Relationships><Relationship Type="https://example.test/image" TargetMode="External" Target="file:///secret"/></Relationships>'
    package(source, extra={'xl/_rels/workbook.xml.rels': relationships})
    with pytest.raises(worker.DocumentRejected, match='External file/data'):
        worker.inspect_package(source)
    package(source, extra={'xl/_rels/workbook.xml.rels': relationships.replace('/image', '/hyperlink')})
    assert worker.inspect_package(source)['formula_count'] == 1


def test_container_does_not_get_network_host_workspace_or_write_mounts(tmp_path):
    command = sandbox.container_arguments('docker', 'trusted:1', 'job', 'ocr-input-test')
    for flag, value in [('--network', 'none'), ('--user', '10001:10001'),
                        ('--cap-drop', 'ALL'), ('--pids-limit', '128'),
                        ('--security-opt', 'no-new-privileges:true'), ('--memory', '1g'),
                        ('--pull', 'never')]:
        assert command[command.index(flag)+1] == value
    assert '--read-only' in command
    assert command.count('--mount') == 1
    assert command[command.index('--mount')+1].endswith('target=/input,readonly')
    assert command[command.index('--mount')+1].startswith('type=volume,')
    assert '--privileged' not in command
    assert '--env-file' not in command


@pytest.mark.parametrize('endpoint', ['tcp://remote.example:2376', 'ssh://remote.example',
                                      'npipe:////remote.example/pipe/docker_engine'])
def test_remote_docker_context_is_rejected(monkeypatch, endpoint):
    monkeypatch.delenv('DOCKER_CONTEXT', raising=False)
    monkeypatch.setenv('DOCKER_HOST', endpoint)
    def unexpected(*args):
        pytest.fail('Remote daemon must not be contacted')
    monkeypatch.setattr(sandbox, '_query', unexpected)
    with pytest.raises(sandbox.SandboxError, match='local Docker'):
        sandbox.check_engine('docker')


@pytest.mark.parametrize('tampered', [False, True])
def test_loopback_vm_requires_its_pinned_tls_context(tmp_path, monkeypatch, tampered):
    for name in ('DOCKER_HOST', 'DOCKER_CONTEXT', 'DOCKER_TLS_VERIFY', 'DOCKER_CERT_PATH'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(sandbox.Path, 'home', lambda: tmp_path)
    state_dir = tmp_path/'.docker/ocr-vm'
    state_dir.mkdir(parents=True)
    context_dir = tmp_path/'context-tls/docker'
    context_dir.mkdir(parents=True)
    hashes = {}
    for name in ('ca.pem', 'cert.pem', 'key.pem'):
        payload = ('test material '+name).encode()
        (context_dir/name).write_bytes(payload)
        hashes[name] = hashlib.sha256(payload).hexdigest()
    (state_dir/'installed.json').write_text(json.dumps({'endpoint': sandbox.VM_ENDPOINT, 'tls_sha256': hashes}))
    if tampered:
        (context_dir/'ca.pem').write_bytes(b'wrong CA')
    context = {'Name': 'ocr-local-vm', 'Endpoints': {'docker': {
        'Host': sandbox.VM_ENDPOINT, 'SkipTLSVerify': False}},
        'Storage': {'TLSPath': str(context_dir.parent)}}
    def query(docker, arguments, timeout=20):
        if '--format' in arguments and arguments[-1] == '{{.Endpoints.docker.Host}}':
            return sandbox.VM_ENDPOINT
        if arguments == ['context', 'inspect']:
            return json.dumps([context])
        if arguments == ['info', '--format', '{{.OSType}}']:
            return 'linux'
        pytest.fail('Unexpected Docker query')
    monkeypatch.setattr(sandbox, '_query', query)
    if tampered:
        with pytest.raises(sandbox.SandboxError, match='local Docker'):
            sandbox.check_engine('docker')
    else:
        assert sandbox.check_engine('docker') is True


def test_no_docker_fails_closed_without_opening_host_office(tmp_path, monkeypatch):
    source = tmp_path/'source.xlsx'
    package(source)
    monkeypatch.setattr(sandbox.shutil, 'which', lambda _: None)
    monkeypatch.setattr(sandbox, '_bundled_docker', lambda: None)
    args = document_ocr.parser().parse_args([str(source), str(tmp_path/'output.md')])
    with pytest.raises(document_ocr.ConversionError, match='No document was opened on the host'):
        document_ocr.convert(source, args.output, args)
    assert not args.output.exists()


def make_tar(path, entries):
    with tarfile.open(path, 'w') as archive:
        for name, value in entries.items():
            info = tarfile.TarInfo(name)
            if value is None:
                info.type = tarfile.SYMTYPE
                info.linkname = '/host/secret'
                archive.addfile(info)
            else:
                info.size = len(value)
                archive.addfile(info, io.BytesIO(value))


@pytest.mark.parametrize('name,value', [('../escape', b'x'), ('preview.pdf', None)])
def test_result_archive_does_not_extract_paths_or_links(tmp_path, name, value):
    archive = tmp_path/'result.tar'
    make_tar(archive, {name: value})
    with pytest.raises(sandbox.SandboxError, match='unexpected file or link'):
        sandbox.unpack_result(archive, tmp_path/'preview.pdf', xlsx=False)


def test_xlsx_requires_successful_calculation_metadata(tmp_path):
    archive = tmp_path/'result.tar'
    make_tar(archive, {'preview.pdf': b'%PDF-1.7\n',
                      'report.json': json.dumps({'engine': 'libreoffice', 'recalculated': False}).encode(),
                      'recalculated.xlsx': b'not relevant'})
    with pytest.raises(sandbox.SandboxError, match='not explicitly recalculated'):
        sandbox.unpack_result(archive, tmp_path/'preview.pdf', xlsx=True)
    assert not (tmp_path/'preview.pdf').exists()


@pytest.mark.skipif(os.environ.get('OCR_SANDBOX_TESTS') != '1', reason='Requires local Docker and the built office sandbox image')
def test_real_sandbox_recalculates_stale_zero_preserves_source_and_geometry(tmp_path):
    import openpyxl
    source = tmp_path/'stale.xlsx'
    book = openpyxl.Workbook()
    sheet = book.active
    sheet['A1'], sheet['A2'], sheet['A3'] = 100.25, 49.75, '=SUM(A1:A2)'
    book.calculation.calcMode = 'manual'
    book.save(source)
    with zipfile.ZipFile(source) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    entries['xl/worksheets/sheet1.xml'] = entries['xl/worksheets/sheet1.xml'].replace(b'<f>SUM(A1:A2)</f><v></v>', b'<f>SUM(A1:A2)</f><v>0</v>')
    with zipfile.ZipFile(source, 'w') as archive:
        for name, value in entries.items():
            archive.writestr(name, value)
    assert openpyxl.load_workbook(source, data_only=True).active['A3'].value == 0
    original_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    output = tmp_path/'result.md'
    args = document_ocr.parser().parse_args([str(source), str(output)])
    data = document_ocr.convert(source, output, args)
    assert original_hash == hashlib.sha256(source.read_bytes()).hexdigest()
    assert openpyxl.load_workbook(tmp_path/'result.assets/recalculated.xlsx', data_only=True).active['A3'].value == 150
    markdown = output.read_text(encoding='utf-8')
    start = markdown.index('150')
    ids = document_ocr.characters_for_span(data, start, start+3)
    assert ''.join(c['text'] for c in data['characters'] if c['id'] in ids) == '150'
    assert data['office_processing']['recalculated'] is True
    report = json.loads((tmp_path/'result.assets/office-report.json').read_text(encoding='utf-8'))
    assert report['formula_results'][0]['numeric_value'] == 150
    assert report['formulas_before'][0]['cached_before'] == '0'
    markdown = output.read_text(encoding='utf-8')
    for char in data['characters']:
        if char['markdown_span'] is not None:
            start, end = char['markdown_span']
            assert markdown[start:end] in (char['text'], '\\' + char['text'])
        assert 'bbox' not in char
        assert all(0 <= value <= 1 for value in char['bbox_normalized'])
