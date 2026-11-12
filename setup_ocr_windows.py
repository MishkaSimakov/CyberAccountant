"""Download a workspace-local Tesseract environment. Does not upload documents."""
import io
from pathlib import Path
import subprocess
import sys
import tarfile
import urllib.request


def main():
    if sys.platform != 'win32':
        raise SystemExit('This bootstrap is for Windows. On Linux install tesseract-ocr and tesseract-ocr-rus.')
    root = Path(__file__).resolve().parent / '.tools'
    root.mkdir(exist_ok=True)
    executable = root / 'micromamba.exe'
    if not executable.exists():
        print('Downloading micromamba...')
        with urllib.request.urlopen('https://micro.mamba.pm/api/micromamba/win-64/latest', timeout=120) as response:
            archive = response.read()
        with tarfile.open(fileobj=io.BytesIO(archive), mode='r:bz2') as package:
            member = next(m for m in package.getmembers() if m.name.endswith('/micromamba.exe'))
            executable.write_bytes(package.extractfile(member).read())
    environment = root / 'ocr'
    action = 'install' if (environment / 'conda-meta').exists() else 'create'
    subprocess.run([str(executable), action, '-y', '-p', str(environment), '-c', 'conda-forge',
                    'tesseract=5.5.1', 'libcurl'], check=True)
    print('Ready:', environment / 'Library/bin/tesseract.exe')


if __name__ == '__main__':
    main()
