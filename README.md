# Cyber Accountant

- [TASK.md](docs/TASK.md) - описание задачи.
- [DESIGN.md](docs/DESIGN.md) - описание дизайна системы.
- [OCR.md](docs/OCR.md) - локальная конвертация документов в Markdown и координаты символов.

## Document OCR CLI

The standalone `document_ocr.py` CLI supports PDF, scans/images, DOCX, XLSX,
TXT and Markdown. It writes Markdown, a JSON character index with normalized
bounding boxes, and PDF/PNG previews. Office conversion and formula recalculation
run in a restricted local Linux Docker container. PDF extraction and OCR run locally.

Install Python dependencies and Tesseract 5 with the `rus` and `eng` language data:

```sh
python -m pip install -r requirements.txt
# Debian/Ubuntu: sudo apt install tesseract-ocr tesseract-ocr-rus fonts-dejavu-core
# Windows: python setup_ocr_windows.py
python document_ocr.py input.pdf output/result.md
```

For XLSX, openpyxl reads worksheet structure from the recalculated workbook;
tables are not reconstructed from PDF borders. Calc supplies displayed values and
cell positions, tabulate renders Markdown tables, and PyMuPDF supplies the actual
character boxes in the PDF preview. Scans use Tesseract with OpenCV preprocessing.
Docling is not a runtime dependency. See [the comparison](docs/OCR_BACKENDS.md).

For DOCX/XLSX, start a local Linux Docker engine and build the office image first:

```sh
python document_ocr.py sandbox-build
python document_ocr.py input.xlsx output/workbook.md
```

This CLI is independent of the server's simulated OCR pipeline. See
[docs/OCR.md](docs/OCR.md) for the JSON schema, highlighting, and sandbox details.

## Run with Docker

Requires Docker with its daemon running. Build and start from the project root:

```sh
docker build -t cyber-accountant .
docker run --rm --name cyber-accountant -p 8000:8000 cyber-accountant
```

Open <http://localhost:8000>. The starter page checks the server connection.
The REST API health check is at <http://localhost:8000/api/health>, and interactive
API documentation is at <http://localhost:8000/docs>.

## Local development

Requires Python 3.13+ and Node.js 24 LTS with npm. Run these commands from the
project root in two separate terminals.

Server:

```sh
python3 -m venv server/.venv
source server/.venv/bin/activate
python -m pip install -r server/requirements.txt
uvicorn app.main:app --app-dir server --reload
```

Client:

```sh
cd client
npm ci
npm run dev
```

Open the URL printed by Vite. Vite proxies `/api` requests to the server at `127.0.0.1:8000`.

To type-check and build the client:

```sh
cd client
npm run build
```
