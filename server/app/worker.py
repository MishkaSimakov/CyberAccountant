"""Start one RQ worker, processing one OCR job at a time."""

from rq import Worker

from .ocr import check_ocr_languages
from .queue import ocr_queue


def main() -> None:
    check_ocr_languages()
    queue = ocr_queue()
    Worker([queue], connection=queue.connection).work()


if __name__ == "__main__":
    main()
