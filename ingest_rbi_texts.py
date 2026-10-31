from __future__ import annotations

import re
import sys
from pathlib import Path

from dotenv import load_dotenv

from semantic_splitter import write_chunks


PROJECT_ROOT = Path(__file__).resolve().parent
TEXT_DIR = PROJECT_ROOT / "data" / "rbi_text"


def main() -> None:
    load_dotenv(PROJECT_ROOT / "backend" / ".env", override=False)

    source_files = sorted(
        path
        for path in TEXT_DIR.glob("rbi_*.txt")
        if re.fullmatch(r"rbi_\d+\.txt", path.name, re.IGNORECASE)
    )
    if not source_files:
        raise FileNotFoundError(f"No RBI source files found in {TEXT_DIR}")

    print(f"Found {len(source_files)} RBI text files.")
    chunk_files: list[Path] = []

    for source_file in source_files:
        chunks_file = source_file.with_name(f"{source_file.stem}_chunks.json")
        progress_file = source_file.with_name(f"{source_file.stem}_progress.json")
        chunks = write_chunks(source_file, chunks_file, progress_file)
        if not chunks:
            raise ValueError(f"No chunks were produced from {source_file}.")
        print(f"Prepared {len(chunks)} chunks from {source_file.name}.")
        chunk_files.append(chunks_file)

    print(f"Indexing chunks from all {len(chunk_files)} text files.")
    from embed_and_index import main as index_chunks

    index_chunks(chunk_files)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        sys.exit(1)
