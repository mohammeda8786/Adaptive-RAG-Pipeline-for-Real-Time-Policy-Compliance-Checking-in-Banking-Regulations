import argparse
import re
from collections import Counter
from pathlib import Path

import pymupdf


def clean_page_text(
    raw_text: str,
    page_number: int,
    repeated_edge_lines: set[str],
) -> str:
    lines = [
        re.sub(r"\s+", " ", line).strip()
        for line in raw_text.replace("\x00", " ")
        .replace("\ufffd", " ")
        .replace("\ufeff", " ")
        .replace("\u00ad", "")
        .splitlines()
    ]
    lines = [line for line in lines if line]
    edge_indices = set(range(min(4, len(lines))))
    edge_indices.update(range(max(0, len(lines) - 4), len(lines)))

    cleaned_lines = []
    for index, line in enumerate(lines):
        if index in edge_indices and (
            line == str(page_number) or line in repeated_edge_lines
        ):
            continue
        if re.fullmatch(r"[-_*＝=]{3,}", line):
            continue
        cleaned_lines.append(line)

    return "\n".join(cleaned_lines)


def find_repeated_edge_lines(page_lines: list[list[str]]) -> set[str]:
    edge_counts = Counter()
    for lines in page_lines:
        edge_indices = set(range(min(4, len(lines))))
        edge_indices.update(range(max(0, len(lines) - 4), len(lines)))
        edge_counts.update(lines[index] for index in edge_indices)

    threshold = max(2, (len(page_lines) + 4) // 5)
    return {
        line
        for line, count in edge_counts.items()
        if count >= threshold
    }


def extract_pdf(pdf_path: Path, source_dir: Path, output_dir: Path) -> tuple[Path, int, int]:
    relative_path = pdf_path.relative_to(source_dir)
    text_path = (output_dir / relative_path).with_suffix(".txt")
    text_path.parent.mkdir(parents=True, exist_ok=True)

    with pymupdf.open(pdf_path) as document:
        raw_pages = [page.get_text("text") for page in document]
        page_lines = [
            [re.sub(r"\s+", " ", line).strip() for line in text.splitlines() if line.strip()]
            for text in raw_pages
        ]
        repeated_edge_lines = find_repeated_edge_lines(page_lines)
        page_texts = []

        for page_number, raw_text in enumerate(raw_pages, start=1):
            page_text = clean_page_text(
                raw_text, page_number, repeated_edge_lines
            )
            page_texts.append(f"--- Page {page_number} ---\n{page_text}")

        page_count = len(document)

    text_path.write_text("\n\n".join(page_texts), encoding="utf-8")
    return text_path, page_count, sum(len(text) for text in page_texts)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Extract searchable text from RBI policy PDFs."
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=Path("data/rbi_pdfs"),
        help="Folder containing PDF files (default: data/rbi_pdfs)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/rbi_text"),
        help="Folder for extracted .txt files (default: data/rbi_text)",
    )
    args = parser.parse_args()

    if not args.input_dir.is_dir():
        problem = "exists but is not a folder" if args.input_dir.exists() else "not found"
        print(f"PDF input path {problem}: {args.input_dir.resolve()}")
        print("Download the RBI PDFs into this folder, or pass --input-dir.")
        return 1

    pdf_paths = sorted(args.input_dir.rglob("*.pdf"))
    if not pdf_paths:
        print(f"No PDF files found in {args.input_dir.resolve()}")
        return 1

    print(f"Extracting text from {len(pdf_paths)} PDF(s)...")
    for pdf_path in pdf_paths:
        try:
            text_path, page_count, character_count = extract_pdf(
                pdf_path, args.input_dir, args.output_dir
            )
        except Exception as exc:
            print(f"ERROR {pdf_path}: {type(exc).__name__}: {exc}")
            continue

        status = "OCR may be required" if character_count == 0 else "OK"
        print(
            f"{status}: {pdf_path.name} -> {text_path} "
            f"({page_count} pages, {character_count} characters)"
        )

    print(f"\nText files saved under {args.output_dir.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())