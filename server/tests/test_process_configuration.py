"""The launcher validates counts and passes independent overrides to Compose."""

import pytest

from scripts import start


@pytest.fixture
def launcher(monkeypatch, tmp_path):
    monkeypatch.setattr(start, '__file__', str(tmp_path / 'scripts' / 'start.py'))
    monkeypatch.setattr(start.shutil, 'which', lambda name: '/bin/docker' if name == 'docker' else None)
    monkeypatch.delenv('OFFICE_WORKERS', raising=False)
    monkeypatch.delenv('OCR_WORKERS', raising=False)
    monkeypatch.delenv('COMPOSE_PROJECT_NAME', raising=False)
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return 0

    monkeypatch.setattr(start.subprocess, 'call', run)
    return tmp_path, calls


def test_independent_counts_preserve_existing_env(launcher):
    root, calls = launcher
    (root / '.env').write_text('EXISTING_SETTING=keep\n', encoding='utf-8')
    assert start.main(['--office-workers', '2', '--ocr-workers', '3', '--dev']) == 0
    command, options = calls[0]
    assert command == ['/bin/docker', 'compose', '-p', 'cyberaccountant-dev',
                       '-f', 'compose.dev.yaml', 'up', '-d', '--build']
    assert options['cwd'] == root
    assert options['env']['OFFICE_WORKERS'] == '2'
    assert options['env']['OCR_WORKERS'] == '3'
    assert (root / '.env').read_text(encoding='utf-8') == 'EXISTING_SETTING=keep\n'


def test_unspecified_counts_use_compose_defaults_and_env_file(launcher):
    root, calls = launcher
    assert start.main([]) == 0
    assert (root / '.env').exists()
    assert 'OFFICE_WORKERS' not in calls[0][1]['env']
    assert 'OCR_WORKERS' not in calls[0][1]['env']
    assert calls[0][0][3] == 'cyberaccountant'


@pytest.mark.parametrize('option', ['--office-workers', '--ocr-workers'])
@pytest.mark.parametrize('value', ['0', '-1', '1.5'])
def test_invalid_counts_do_not_start_containers(launcher, option, value):
    _, calls = launcher
    with pytest.raises(SystemExit) as error:
        start.main([option, value])
    assert error.value.code == 2
    assert not calls


def test_shell_settings_and_explicit_override(launcher, monkeypatch):
    _, calls = launcher
    monkeypatch.setenv('OFFICE_WORKERS', '4')
    monkeypatch.setenv('OCR_WORKERS', '5')
    start.main(['--ocr-workers', '2'])
    assert calls[0][1]['env']['OFFICE_WORKERS'] == '4'
    assert calls[0][1]['env']['OCR_WORKERS'] == '2'
