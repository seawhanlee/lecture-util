"""On-demand PDF access for Codex; never preprocess lecture materials."""
from __future__ import annotations

import argparse
import json
from contextlib import closing
from pathlib import Path

from lecture_util.errors import LectureUtilError


def page_count(path: Path) -> int:
    import pypdfium2 as pdfium

    try:
        with pdfium.PdfDocument(path) as document:
            count = len(document)
            if not count:
                raise ValueError("empty document")
            return count
    except Exception as error:
        raise LectureUtilError(f"Could not open PDF {path}: {error}") from error


def read_pages(path: Path, first: int, last: int, output: Path) -> list[dict]:
    """Render only the requested pages, preserving physical one-based numbering."""
    import pypdfium2 as pdfium

    try:
        with pdfium.PdfDocument(path) as document:
            if not 1 <= first <= last <= len(document):
                raise ValueError("page range is outside the PDF")
            output.mkdir(parents=True, exist_ok=True)
            result = []
            for number in range(first, last + 1):
                with closing(document[number - 1]) as page:
                    with closing(page.get_textpage()) as textpage:
                        text = textpage.get_text_bounded()
                    image_path = output / f"page-{number:06}.png"
                    with closing(page.render(scale=2.5)) as bitmap:
                        image = bitmap.to_pil()
                        try:
                            image.save(image_path)
                        finally:
                            image.close()
                    result.append({"page": number, "text": text, "image": str(image_path.resolve())})
            return result
    except Exception as error:
        raise LectureUtilError(f"Could not read PDF {path}: {error}") from error


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--first", type=int, required=True)
    parser.add_argument("--last", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(read_pages(args.pdf, args.first, args.last, args.output), ensure_ascii=False))
    except LectureUtilError as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
