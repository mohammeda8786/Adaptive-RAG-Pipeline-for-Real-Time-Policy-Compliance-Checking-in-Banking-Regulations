
import csv
import hashlib
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse, urldefrag

import requests
from bs4 import BeautifulSoup


# ============================================================
# CONFIGURATION
# ============================================================

START_URL = (
    "https://www.rbi.org.in/Scripts/"
    "BS_ViewMasterDirections.aspx?did=338"
)

OUT_DIR = Path("data/rbi_pdfs")
MANIFEST = Path("rbi_manifest.csv")
TARGET = 50

REQUEST_DELAY = 0.8
MAX_PAGES = 500

OUT_DIR.mkdir(parents=True, exist_ok=True)

session = requests.Session()
session.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/pdf,*/*;q=0.8",
})

RBI_HOSTS = ("rbi.org.in",)


# ============================================================
# URL HELPERS
# ============================================================

def normalize_url(url):
    """Remove fragments so the same page is not crawled repeatedly."""
    url, _ = urldefrag(url)
    return url.strip()


def is_rbi_url(url):
    """Allow only RBI-owned domains, including rbidocs.rbi.org.in."""
    host = (urlparse(url).hostname or "").lower()

    return any(
        host == domain or host.endswith("." + domain)
        for domain in RBI_HOSTS
    )


def is_pdf_url(url):
    """Check the URL path for a PDF extension."""
    path = urlparse(url).path.lower()
    return path.endswith(".pdf")


# ============================================================
# RELEVANCE FILTER
# ============================================================

def is_relevant_title(title):
    """
    Preliminary title filter.
    A title match is not proof that the PDF is a regulation.
    """
    text = (title or "").lower()

    exclude_terms = [
        "accessibility statement",
        "accessibility200",
        "annual report",
        "press release",
        "recruitment",
        "holiday list",
        "tender",
        "result of",
        "minutes of meeting",
    ]

    if any(term in text for term in exclude_terms):
        return False

    include_terms = [
        "master direction",
        "master directions",
        "know your customer",
        "kyc",
        "digital payment",
        "payment system",
        "fraud risk",
        "cyber security",
        "cybersecurity",
        "information technology",
        "outsourcing",
        "internal ombudsman",
        "commercial bank",
        "co-operative bank",
        "cooperative bank",
        "non-banking financial",
        "non banking financial",
        "nbfc",
        "priority sector lending",
        "credit card",
        "debit card",
        "capital adequacy",
        "liquidity",
        "governance",
        "prudential",
        "deposit",
        "lending",
        "digital banking",
        "frauds",
        "customer protection",
        "banking regulation",
    ]

    return any(term in text for term in include_terms)


# ============================================================
# WEBPAGE EXTRACTION
# ============================================================

def get_html(url):
    response = session.get(url, timeout=40)
    response.raise_for_status()

    content_type = response.headers.get("Content-Type", "").lower()

    if "text/html" not in content_type and "<html" not in response.text[:1000].lower():
        raise ValueError(f"Expected an HTML page, got {content_type}")

    return response.text


def extract_links(page_url, html):
    """Extract RBI links and their visible anchor text."""
    soup = BeautifulSoup(html, "html.parser")
    results = []

    for anchor in soup.find_all("a", href=True):
        link = normalize_url(urljoin(page_url, anchor["href"]))
        title = anchor.get_text(" ", strip=True)

        if is_rbi_url(link):
            results.append((title, link))

    return results


def looks_like_document_page(url, title):
    """
    Identify likely RBI regulatory detail pages.
    Do not apply the PDF title filter to these pages, because
    a detail page may contain a PDF link with a generic title.
    """
    text = f"{url} {title}".lower()
    path = urlparse(url).path.lower()

    return (
        "master direction" in text
        or "masterdirections" in text
        or "notificationuser.aspx" in path
        or "bs_viewmasdirections.aspx" in path
        or "bs_viewmasterdirections.aspx" in path
    )


# ============================================================
# PDF DOWNLOAD AND VALIDATION
# ============================================================

def download_pdf(url):
    headers = {
        "Referer": "https://www.rbi.org.in/",
        "Accept": "application/pdf,*/*;q=0.8",
    }

    response = session.get(
        url,
        headers=headers,
        timeout=90,
        allow_redirects=True,
    )

    print("HTTP status:", response.status_code)
    print("Content-Type:", response.headers.get("Content-Type"))
    print("Final URL:", response.url)

    response.raise_for_status()
    data = response.content

    # PDF signature should appear near the beginning.
    signature_position = data[:1024].find(b"%PDF-")

    if signature_position == -1:
        print("Skipped: response does not contain a PDF signature.")
        print("Response preview:", repr(data[:200]))
        return None

    # Usually the signature is at byte 0. If a prefix exists,
    # remove it so the saved file begins with %PDF-.
    if signature_position > 0:
        data = data[signature_position:]

    return data


def sha256_file(data):
    return hashlib.sha256(data).hexdigest()


# ============================================================
# MAIN CRAWLER
# ============================================================

def main():
    # URL + title queue
    queue = [(START_URL, "RBI Master Directions index")]

    visited_pages = set()
    seen_pdf_urls = set()
    seen_hashes = set()
    records = []

    print("Starting RBI regulatory PDF collection...")
    print("Target:", TARGET, "unique relevant PDFs")
    print("Output directory:", OUT_DIR.resolve())

    while queue and len(records) < TARGET:
        if len(visited_pages) >= MAX_PAGES:
            print("Stopped at MAX_PAGES safety limit.")
            break

        page_url, page_title = queue.pop(0)
        page_url = normalize_url(page_url)

        if page_url in visited_pages:
            continue

        if not is_rbi_url(page_url):
            continue

        visited_pages.add(page_url)
        print(f"\nScanning page {len(visited_pages)}: {page_url}")

        try:
            html = get_html(page_url)
            links = extract_links(page_url, html)

        except (requests.RequestException, ValueError) as exc:
            print("Page error:", page_url, exc)
            continue

        for title, link in links:
            if len(records) >= TARGET:
                break

            link = normalize_url(link)

            # --------------------------------------------
            # Case 1: Direct PDF link
            # --------------------------------------------
            if is_pdf_url(link):
                if link in seen_pdf_urls:
                    continue

                seen_pdf_urls.add(link)

                effective_title = title or page_title

                # Filter unrelated PDFs before downloading.
                if not is_relevant_title(effective_title):
                    print("Skipped unrelated title:", effective_title)
                    continue

                try:
                    time.sleep(REQUEST_DELAY)
                    data = download_pdf(link)

                    if data is None:
                        continue

                    digest = sha256_file(data)

                    # Different URLs can contain identical PDFs.
                    if digest in seen_hashes:
                        print("Skipped duplicate PDF content:", effective_title)
                        continue

                    seen_hashes.add(digest)

                    filename = f"rbi_{len(records) + 1:03d}.pdf"
                    filepath = OUT_DIR / filename
                    filepath.write_bytes(data)

                    record = {
                        "filename": filename,
                        "title": effective_title,
                        "source_url": link,
                        "sha256": digest,
                        "bytes": len(data),
                    }

                    records.append(record)

                    print(
                        f"Downloaded {len(records)}/{TARGET}: "
                        f"{filename} - {effective_title}"
                    )

                except requests.RequestException as exc:
                    print("PDF download error:", link, exc)

            # --------------------------------------------
            # Case 2: Likely document detail page
            # --------------------------------------------
            elif looks_like_document_page(link, title):
                if link not in visited_pages:
                    queue.append((link, title or page_title))

        time.sleep(0.3)

    # ========================================================
    # SAVE MANIFEST
    # ========================================================

    fields = [
        "filename",
        "title",
        "source_url",
        "sha256",
        "bytes",
    ]

    with MANIFEST.open(
        "w",
        newline="",
        encoding="utf-8-sig",
    ) as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(records)

    # ========================================================
    # SUMMARY
    # ========================================================

    print("\n" + "=" * 55)
    print("RBI PDF COLLECTION SUMMARY")
    print("=" * 55)
    print("Pages scanned:", len(visited_pages))
    print("Unique PDF URLs examined:", len(seen_pdf_urls))
    print("Unique relevant PDFs downloaded:", len(records))
    print("PDF folder:", OUT_DIR.resolve())
    print("Manifest:", MANIFEST.resolve())

    if len(records) == TARGET:
        print("Target of 50 PDFs reached.")
    else:
        print(
            f"Target not reached. Found {len(records)} PDFs. "
            "The crawler may need additional RBI index/detail-page "
            "patterns or a broader document discovery strategy."
        )

    print(
        "\nBefore indexing, review the manifest and verify each "
        "document's title, regulatory scope, and effective date."
    )


if __name__ == "__main__":
    main()