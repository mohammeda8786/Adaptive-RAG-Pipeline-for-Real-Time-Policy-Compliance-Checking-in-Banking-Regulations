
"""
Improved rule-based RBI regulation chunker.

Features:
- No AI or API calls
- Preserves the exact input text, including whitespace and page markers
- Keeps section headings with their first clause
- Tracks chapter, section, clause, and page-number metadata
- Splits oversized sections at structural boundaries where possible
- Handles Annexure content and tables using line-aware splitting
- Validates exact reconstruction before writing output
- Does not reuse progress from the old AI splitter
"""


import json
import re
from pathlib import Path
from typing import Any


# ============================================================
# CONFIGURATION
# ============================================================

INPUT_FILE = Path("data/rbi_text/rbi_008.txt")
OUTPUT_FILE = Path("data/rbi_text/rbi_008_chunks.json")
PROGRESS_FILE = Path("data/rbi_text/rbi_008_progress.json")

TARGET_CHARS = 1100
MAX_CHARS = 1700
MIN_CHARS = 300

PIPELINE_VERSION = "rule-based-rbi-v2"


# ============================================================
# REGEX PATTERNS
# ============================================================

PAGE_RE = re.compile(
    r"^[ \t]*---\s*Page\s+(\d+)\s*---[ \t]*$",
    re.IGNORECASE,
)

CHAPTER_RE = re.compile(
    r"^[ \t]*(?:CHAPTER|PART|SECTION)\s+"
    r"[IVXLCDM\d]+\b.*$",
    re.IGNORECASE,
)

ANNEXURE_RE = re.compile(
    r"^[ \t]*(?:ANNEXURE|ANNEXURE|APPENDIX|SCHEDULE)"
    r"(?:\s+[\wIVXLCDM-]+)?(?:\s*[:.-].*)?[ \t]*$",
    re.IGNORECASE,
)

# Numbered main sections, for example:
# 1. Short Title and Commencement
# 12. Role and Responsibilities of Internal Ombudsman
SECTION_RE = re.compile(
    r"^[ \t]*(\d{1,2})\.\s+([A-Z][^\r\n]{1,180})[ \t]*$"
)

# Clause and subclause starts, for example:
# (1), (a), (iv), (A), 1), a)
CLAUSE_RE = re.compile(
    r"^[ \t]*("
    r"\(\d+\)|"
    r"\([a-z]\)|"
    r"\([A-Z]\)|"
    r"\([ivxlcdm]+\)|"
    r"\d+\)|"
    r"[a-z]\)|"
    r"[ivxlcdm]+\)"
    r")[ \t]*(.*)$",
    re.IGNORECASE,
)

TABLE_HEADER_RE = re.compile(
    r"^[ \t]*(?:S\.?\s*No\.?|Particulars|Number)[ \t]*$",
    re.IGNORECASE,
)


# ============================================================
# SOURCE READING
# ============================================================

def read_source_exactly(path: Path) -> str:
    """
    newline='' prevents Python from translating CRLF into LF.
    This allows the validation to preserve original newline characters.
    """
    with path.open("r", encoding="utf-8", newline="") as file:
        return file.read()


def get_lines_with_offsets(text):
    """Return each line with its exact start and end offsets."""
    lines = []
    offset = 0

    for line in text.splitlines(keepends=True):
        end = offset + len(line)
        lines.append({
            "text": line,
            "start": offset,
            "end": end,
        })
        offset = end

    # Handle empty input or any unusual trailing content safely.
    if offset < len(text):
        lines.append({
            "text": text[offset:],
            "start": offset,
            "end": len(text),
        })

    return lines


def strip_line_ending(line):
    return line.rstrip("\r\n").strip()


# ============================================================
# STRUCTURE DETECTION
# ============================================================

def classify_line(line_text):
    """
    Return structural information for a line.
    Classification affects boundaries and metadata only;
    it never changes the source text.
    """
    value = strip_line_ending(line_text)

    if not value:
        return {"type": "blank"}

    page_match = PAGE_RE.match(value)
    if page_match:
        return {
            "type": "page",
            "page": int(page_match.group(1)),
        }

    if CHAPTER_RE.match(value):
        return {
            "type": "chapter",
            "title": value,
        }

    if ANNEXURE_RE.match(value):
        return {
            "type": "annexure",
            "title": value,
        }

    section_match = SECTION_RE.match(value)
    if section_match:
        title = section_match.group(2).strip()

        # Avoid treating table entries or very long sentences as headings.
        if len(value) <= 200 and not value.endswith((",", ";")):
            return {
                "type": "section",
                "number": section_match.group(1),
                "title": title,
            }

    clause_match = CLAUSE_RE.match(value)
    if clause_match:
        return {
            "type": "clause",
            "number": clause_match.group(1),
            "text": clause_match.group(2).strip(),
        }

    if TABLE_HEADER_RE.match(value):
        return {
            "type": "table_header",
            "title": value,
        }

    return {"type": "text"}


def build_structural_units(text):
    """
    Build exact source slices at chapter, section, annexure,
    and clause boundaries.

    Page markers remain inside the source text and do not force
    a new chunk by themselves.
    """
    lines = get_lines_with_offsets(text)

    if not lines:
        return []

    classified = []
    for line in lines:
        classified.append({
            **line,
            "structure": classify_line(line["text"]),
        })

    # Main boundaries:
    # - chapter / part headings
    # - numbered section headings
    # - annexure headings
    # - clause starts
    boundaries = {0, len(text)}

    for item in classified:
        kind = item["structure"]["type"]

        if kind in {"chapter", "section", "annexure", "clause"}:
            boundaries.add(item["start"])

    sorted_boundaries = sorted(boundaries)
    units = []

    for start, end in zip(sorted_boundaries, sorted_boundaries[1:]):
        if start >= end:
            continue

        # Identify the structural line at the beginning of this unit.
        first_line = next(
            (
                item for item in classified
                if item["start"] == start
            ),
            None,
        )

        structure = (
            first_line["structure"]
            if first_line
            else {"type": "text"}
        )

        units.append({
            "text": text[start:end],
            "start": start,
            "end": end,
            "structure": structure,
        })

    return units


# ============================================================
# LONG UNIT SPLITTING
# ============================================================

def split_exact_at_lines(text, max_chars):
    """
    Split long text at line boundaries where possible.
    Every returned piece is an exact slice of the input.
    """
    if len(text) <= max_chars:
        return [text]

    lines = get_lines_with_offsets(text)
    pieces = []
    start = 0

    while start < len(text):
        limit = min(start + max_chars, len(text))

        if limit == len(text):
            pieces.append(text[start:])
            break

        # Prefer the last newline before the size limit.
        newline_pos = text.rfind("\n", start, limit)

        # Avoid creating a very small piece just to find a newline.
        minimum_boundary = start + int(max_chars * 0.55)

        if newline_pos >= minimum_boundary:
            end = newline_pos + 1
        else:
            # Fall back to a whitespace boundary.
            space_pos = text.rfind(" ", minimum_boundary, limit)

            if space_pos >= minimum_boundary:
                end = space_pos + 1
            else:
                # Last resort: exact character boundary.
                end = limit

        if end <= start:
            end = limit

        pieces.append(text[start:end])
        start = end

    if "".join(pieces) != text:
        raise ValueError("Long-unit splitting changed the source text.")

    return pieces


# ============================================================
# METADATA TRACKING
# ============================================================

def build_context_by_offset(text):
    """
    Record active chapter, section, and annexure context at
    each structural unit's starting offset.
    """
    context = {
        "chapter": None,
        "section_number": None,
        "section_title": None,
        "annexure": None,
    }

    snapshots = []
    lines = get_lines_with_offsets(text)

    for line in lines:
        info = classify_line(line["text"])
        kind = info["type"]

        if kind == "chapter":
            context["chapter"] = info["title"]
            context["section_number"] = None
            context["section_title"] = None

        elif kind == "section":
            context["section_number"] = info["number"]
            context["section_title"] = info["title"]

        elif kind == "annexure":
            context["annexure"] = info["title"]
            context["section_number"] = None
            context["section_title"] = None

        snapshots.append({
            "start": line["start"],
            "context": context.copy(),
        })

    return snapshots


def context_at_offset(snapshots, offset):
    result = {}

    for snapshot in snapshots:
        if snapshot["start"] > offset:
            break
        result = snapshot["context"]

    return result.copy()


def pages_in_range(text, start, end):
    """Return page markers found within the specified source range."""
    pages = []

    for line in get_lines_with_offsets(text[start:end]):
        info = classify_line(line["text"])

        if info["type"] == "page":
            pages.append(info["page"])

    return sorted(set(pages))


def extract_clause_labels(text):
    labels = []

    for line in get_lines_with_offsets(text):
        info = classify_line(line["text"])

        if info["type"] == "clause":
            label = info["number"]
            if label not in labels:
                labels.append(label)

    return labels


def meaningful_heading(text, context):
    """
    Prefer actual section metadata over a page marker or
    an arbitrary first line of body text.
    """
    if context.get("section_title"):
        number = context.get("section_number")
        if number:
            return f"{number}. {context['section_title']}"
        return context["section_title"]

    if context.get("annexure"):
        return context["annexure"]

    if context.get("chapter"):
        return context["chapter"]

    for line in text.splitlines():
        value = line.strip()

        if not value:
            continue

        info = classify_line(value)

        if info["type"] not in {"page", "blank"}:
            return value[:180]

    return ""


# ============================================================
# CHUNK CONSTRUCTION
# ============================================================

def build_chunks(text: str, source_file: str = INPUT_FILE.name) -> list[dict[str, Any]]:
    if not text:
        return []

    units = build_structural_units(text)
    context_snapshots = build_context_by_offset(text)

    grouped = []
    current = ""
    current_start = None

    def flush_current():
        nonlocal current, current_start

        if current:
            grouped.append({
                "text": current,
                "start": current_start,
                "end": current_start + len(current),
            })

        current = ""
        current_start = None

    for unit in units:
        unit_text = unit["text"]
        kind = unit["structure"]["type"]

        # A new chapter, section, or annexure should start a new chunk,
        # except that the heading must stay with the following clause.
        is_major_heading = kind in {"chapter", "section", "annexure"}

        if is_major_heading and current:
            flush_current()

        # If a single structural unit is too large, split it at
        # line boundaries. Keep exact source offsets.
        if len(unit_text) > MAX_CHARS:
            if current:
                flush_current()

            pieces = split_exact_at_lines(unit_text, MAX_CHARS)
            cursor = unit["start"]

            for piece in pieces:
                grouped.append({
                    "text": piece,
                    "start": cursor,
                    "end": cursor + len(piece),
                })
                cursor += len(piece)

            continue

        # Do not split immediately after a heading. This keeps a section
        # heading with the first clause that follows it.
        current_context = (
            context_at_offset(context_snapshots, current_start)
            if current_start is not None
            else {}
        )
        current_has_heading = (
            current
            and current_context.get("section_number")
            and current.strip().splitlines()
        )

        should_flush = (
            current
            and len(current) + len(unit_text) > MAX_CHARS
            and len(current) >= MIN_CHARS
            and not current_has_heading
        )

        if should_flush:
            flush_current()

        if not current:
            current_start = unit["start"]

        current += unit_text

        # Aim for target size without forcing a split through a clause.
        if len(current) >= TARGET_CHARS:
            flush_current()

    flush_current()

    # Merge a small trailing chunk into its predecessor if the result
    # remains under MAX_CHARS. Otherwise retain the small chunk.
    if len(grouped) > 1 and len(grouped[-1]["text"]) < MIN_CHARS:
        combined_size = (
            len(grouped[-2]["text"]) + len(grouped[-1]["text"])
        )

        if combined_size <= MAX_CHARS:
            grouped[-2]["text"] += grouped[-1]["text"]
            grouped[-2]["end"] = grouped[-1]["end"]
            grouped.pop()

    # Verify offsets and source reconstruction.
    reconstructed_parts = []
    expected_offset = 0

    for item in grouped:
        if item["start"] != expected_offset:
            raise ValueError(
                "Offset gap or overlap detected near character "
                f"{expected_offset}."
            )

        if item["end"] - item["start"] != len(item["text"]):
            raise ValueError("Chunk offsets do not match text length.")

        reconstructed_parts.append(item["text"])
        expected_offset = item["end"]

    reconstructed = "".join(reconstructed_parts)

    if reconstructed != text:
        raise ValueError(
            "Text preservation failed. No output will be written."
        )

    if expected_offset != len(text):
        raise ValueError("Final offset does not match source length.")

    # Build output with consistent metadata.
    chunks = []

    for index, item in enumerate(grouped, start=1):
        chunk_text = item["text"]
        context = context_at_offset(context_snapshots, item["start"])

        metadata = {
            "chunk_index": index,
            "source_file": source_file,
            "splitter": PIPELINE_VERSION,
            "start_char": item["start"],
            "end_char": item["end"],
            "char_count": len(chunk_text),
            "chapter": context.get("chapter"),
            "section_number": context.get("section_number"),
            "section_title": context.get("section_title"),
            "annexure": context.get("annexure"),
            "clause_labels": extract_clause_labels(chunk_text),
            "page_numbers": pages_in_range(
                text, item["start"], item["end"]
            ),
            "heading_or_clause": meaningful_heading(
                chunk_text, context
            ),
        }

        chunks.append({
            "id": f"rbi_chunk_{index:05d}",
            "text": chunk_text,
            "metadata": metadata,
        })

    return chunks


# ============================================================
# MAIN
# ============================================================

def write_chunks(
    input_file: Path,
    output_file: Path,
    progress_file: Path,
) -> list[dict[str, Any]]:
    if not input_file.exists():
        raise FileNotFoundError(
            f"Input file not found: {input_file.resolve()}"
        )

    original_text = read_source_exactly(input_file)

    print("=" * 60)
    print("RBI RULE-BASED CHUNKER")
    print("=" * 60)
    print(f"Input file       : {input_file}")
    print(f"Input characters : {len(original_text)}")

    chunks = build_chunks(original_text, source_file=input_file.name)

    # Final validation before writing.
    rebuilt_text = "".join(chunk["text"] for chunk in chunks)

    if rebuilt_text != original_text:
        raise ValueError(
            "Final source validation failed. Output was not saved."
        )

    output_file.parent.mkdir(parents=True, exist_ok=True)

    output_data = {
        "pipeline_version": PIPELINE_VERSION,
        "source_file": input_file.name,
        "total_characters": len(original_text),
        "total_chunks": len(chunks),
        "validation": "passed",
        "chunks": chunks,
    }

    output_file.write_text(
        json.dumps(output_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # Replace the old progress record with the new pipeline version.
    # This splitter does not resume batches from the AI implementation.
    progress_data = {
        "pipeline_version": PIPELINE_VERSION,
        "status": "completed",
        "source_file": input_file.name,
        "total_chunks": len(chunks),
        "text_preservation": "passed",
    }

    progress_file.parent.mkdir(parents=True, exist_ok=True)
    progress_file.write_text(
        json.dumps(progress_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"Chunks created   : {len(chunks)}")
    print(f"Output file      : {output_file}")
    print(f"Progress file    : {progress_file}")
    print("Text validation  : PASSED")

    if chunks:
        sizes = [len(chunk["text"]) for chunk in chunks]

        print(f"Smallest chunk   : {min(sizes)} characters")
        print(f"Largest chunk    : {max(sizes)} characters")
        print(
            f"Average chunk    : "
            f"{sum(sizes) / len(sizes):.0f} characters"
        )

        oversized = sum(size > MAX_CHARS for size in sizes)
        if oversized:
            print(
                f"Note: {oversized} chunks exceed MAX_CHARS because "
                "of structural grouping or a long unsplittable line."
            )

    print("=" * 60)
    return chunks


def main():
    write_chunks(INPUT_FILE, OUTPUT_FILE, PROGRESS_FILE)


if __name__ == "__main__":
    main()