"""Start the application with independent Office and OCR process counts."""

import argparse
import os
from pathlib import Path
import shutil
import subprocess


def positive(value):
    try:
        number = int(value)
        if number > 0:
            return number
    except ValueError:
        pass
    raise argparse.ArgumentTypeError('Worker count must be a positive integer.')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--office-workers', type=positive, default=os.getenv('OFFICE_WORKERS'),
                        help='Office process limit (default: .env or 1)')
    parser.add_argument('--ocr-workers', type=positive, default=os.getenv('OCR_WORKERS'),
                        help='OCR process count (default: .env or 1)')
    parser.add_argument('--dev', action='store_true', help='Start the development stack with Vite')
    args = parser.parse_args(argv)
    engine = shutil.which('docker') or shutil.which('podman')
    if not engine:
        parser.error('Install Docker Compose or Podman Compose first.')
    root = Path(__file__).resolve().parents[1]
    # Optional model credentials; keep any existing file unchanged.
    (root / '.env').touch(exist_ok=True)
    environment = dict(os.environ)
    for name in ('office_workers', 'ocr_workers'):
        if (value := getattr(args, name)) is not None:
            environment[name.upper()] = str(value)
    compose = 'compose.dev.yaml' if args.dev else 'compose.yaml'
    project = os.getenv('COMPOSE_PROJECT_NAME') or ('cyberaccountant-dev' if args.dev else 'cyberaccountant')
    return subprocess.call([engine, 'compose', '-p', project, '-f', compose, 'up', '-d', '--build'],
                           cwd=root, env=environment)


if __name__ == '__main__':
    raise SystemExit(main())
