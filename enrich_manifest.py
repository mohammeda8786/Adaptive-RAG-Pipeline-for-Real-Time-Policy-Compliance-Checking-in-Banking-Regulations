
import csv
import re
from pathlib import Path

import fitz  # PyMuPDF

PDF_DIR = Path("data/rbi_pdfs")
INPUT_CSV = Path("rbi_manifest.csv")
OUTPUT_CSV = Path("rbi_manifest_enriched.csv")

MONTHS = (
    "January February March April May June July August "
    "September October November December"
)

DATE_PATTERN = re.compile(
    rf"\b\d{{1,2}}(?:st|nd|rd|th)?[\s/-]+"
    rf"(?:{MONTHS})[\s,/-]+\d{{4}}\b"
    r"|\b\d{1,2}[/-]\d{1,2}[/-]\d{4}\b"
    r"|\b\d{4}-\d{2}-\d{2}\b",
    re.IGNORECASE,
)


def clean_line(line):
    return re.sub(r"\s+", " ", line).strip(" \t|:-")


def extract_pdf_details(pdf_path):
    result = {
        "pdf_title": "",
        "publication_date": "",
        "page_count": 0,
        "text_chars": 0,
        "first_page_preview": "",
        "extraction_status": "",
    }

    try:
        with fitz.open(pdf_path) as doc:
            result["page_count"] = len(doc)

            if len(doc) == 0:
                result["extraction_status"] = "EMPTY_PDF"
                return result

            metadata = doc.metadata or {}
            metadata_title = clean_line(metadata.get("title") or "")

            first_page_text = doc[0].get_text("text")
            all_text_parts = []

            # Read the first pages to find the document heading.
            for page in doc[: min(len(doc), 3)]:
                all_text_parts.append(page.get_text("text"))

            combined_text = "\n".join(all_text_parts)
            result["text_chars"] = len(combined_text)

            lines = [
                clean_line(line)
                for line in first_page_text.splitlines()
            ]
            lines = [line for line in lines if line]

            # Identify a useful heading from the first page.
            ignored_exact = {
                "reserve bank of india",
                "master directions",
                "master direction",
                "notification",
                "government of india",
                "भारतीय रिज़र्व बैंक",
            }

            candidates = []

            for index, line in enumerate(lines[:35]):
                lower = line.lower()

                if lower in ignored_exact:
                    continue

                if len(line) < 8 or len(line) > 220:
                    continue

                if lower.startswith(("www.", "http://", "https://")):
                    continue

                if lower.startswith(("rbi/", "dbod.", "dos.", "dpSS.")):
                    continue

                # Prefer specific regulatory headings.
                score = 0

                if "master direction" in lower:
                    score += 8

                if any(word in lower for word in [
                    "know your customer",
                    "fraud risk",
                    "digital payment",
                    "information technology",
                    "outsourcing",
                    "internal ombudsman",
                    "credit card",
                    "deposit",
                    "lending",
                    "prudential",
                    "bank",
                    "non-banking",
                ]):
                    score += 4

                if len(line) >= 20:
                    score += 1

                if line.isupper() and len(line) > 25:
                    score += 1

                # Prefer headings near the top of the page.
                score += max(0, 5 - index // 3)

                candidates.append((score, index, line))

            candidates.sort(key=lambda item: (-item[0], item[1]))

            extracted_title = (
                candidates[0][2] if candidates else ""
            )

            # PDF metadata can be generic, so prefer a useful
            # heading extracted from the document itself.
            generic_titles = {
                "",
                "master direction",
                "master directions",
                "reserve bank of india",
                "document",
            }

            if (
                extracted_title
                and extracted_title.lower() not in generic_titles
            ):
                result["pdf_title"] = extracted_title
            elif metadata_title.lower() not in generic_titles:
                result["pdf_title"] = metadata_title
            else:
                result["pdf_title"] = "TITLE_REVIEW_REQUIRED"

            date_match = DATE_PATTERN.search(combined_text)
            if date_match:
                result["publication_date"] = date_match.group(0)

            result["first_page_preview"] = " ".join(lines[:12])[:1000]

            if not combined_text.strip():
                result["extraction_status"] = (
                    "NO_TEXT_OCR_MAY_BE_REQUIRED"
                )
            elif result["pdf_title"] == "TITLE_REVIEW_REQUIRED":
                result["extraction_status"] = "TITLE_REVIEW_REQUIRED"
            else:
                result["extraction_status"] = "OK"

    except Exception as exc:
        result["extraction_status"] = (
            f"ERROR: {type(exc).__name__}: {exc}"
        )

    return result


def main():
    if not INPUT_CSV.exists():
        raise FileNotFoundError(
            f"Manifest not found: {INPUT_CSV.resolve()}"
        )

    with INPUT_CSV.open(
        "r", newline="", encoding="utf-8-sig"
    ) as file:
        records = list(csv.DictReader(file))

    if not records:
        print("The input manifest contains no records.")
        return

    output_records = []

    for number, record in enumerate(records, start=1):
        filename = record.get("filename", "").strip()
        pdf_path = PDF_DIR / filename

        print(f"[{number}/{len(records)}] {filename}")

        if not pdf_path.exists():
            details = {
                "pdf_title": "",
                "publication_date": "",
                "page_count": 0,
                "text_chars": 0,
                "first_page_preview": "",
                "extraction_status": "PDF_FILE_NOT_FOUND",
            }
        else:
            details = extract_pdf_details(pdf_path)

        output_records.append({**record, **details})

        print("  Title:", details["pdf_title"])
        print("  Status:", details["extraction_status"])

    fields = list(dict.fromkeys(
        key for record in output_records for key in record
    ))

    with OUTPUT_CSV.open(
        "w", newline="", encoding="utf-8-sig"
    ) as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(output_records)

    print("\nManifest enrichment complete.")
    print("Output:", OUTPUT_CSV.resolve())
    print("PDFs downloaded again: 0")
    print("Review TITLE_REVIEW_REQUIRED and OCR statuses manually.")


if __name__ == "__main__":
    main()