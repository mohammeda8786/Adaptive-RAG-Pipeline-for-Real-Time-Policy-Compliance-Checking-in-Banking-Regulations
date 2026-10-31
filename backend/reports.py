from __future__ import annotations

import csv
import io
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from groq import Groq
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from .retrieval import retriever
from .prompts import REGULAMIND_SYSTEM_PROMPT
from .storage import REPORT_DIR


REPORT_TYPES = {
    "regulatory_summary": "Regulatory Summary Report",
    "regulation_comparison": "RBI Regulation Comparison Report",
    "policy_compliance": "Bank Policy Compliance Report",
    "gap_analysis": "Compliance Gap Analysis Report",
    "ingestion_retrieval": "Document Ingestion and Retrieval Report",
}
GAP_FORMATS = {"pdf", "csv"}
PDF_FORMATS = {"pdf"}


def _groq_client() -> Groq | None:
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return None
    return Groq(api_key=api_key)


def _parse_model_json(content: str) -> dict[str, Any]:
    content = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", content.strip())
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        start, end = content.find("{"), content.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("The report model returned invalid structured output.")
        parsed = json.loads(content[start:end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("The report model output must be an object.")
    return parsed


def analyze_evidence(
    *,
    report_type: str,
    query: str,
    sources: list[dict[str, Any]],
    policy_text: str | None,
) -> dict[str, Any]:
    citations = [
        {
            "id": item["id"],
            "document": item["document"],
            "title": item["title"],
            "page": item["page"],
            "excerpt": item["excerpt"],
        }
        for item in sources
    ]
    client = _groq_client()
    if client is None:
        return {
            "executive_summary": (
                f"Retrieved {len(citations)} regulatory excerpts for this request. "
                "A configured LLM was unavailable, so substantive conclusions "
                "are marked inconclusive and require manual review."
            ),
            "requirements": [{
                "citation_id": item["id"],
                "requirement": item["excerpt"],
                "title": item["title"],
                "document": item["document"],
                "page": item["page"],
            } for item in citations],
            "findings": [],
            "recommendations": [
                "Review each cited RBI passage against the applicable policy and effective date."
            ],
            "limitations": [
                "Automated analysis was not run because GROQ_API_KEY is not configured.",
                "Retrieved excerpts are evidence for review, not a complete legal opinion.",
            ],
            "analysis_mode": "retrieval_only",
        }

    evidence = "\n\n".join(
        f"[{item['id']}] {item['title']} | {item['document']} | page {item['page']}\n"
        f"{item['excerpt']}"
        for item in citations
    )
    allowed_ids = ", ".join(item["id"] for item in citations) or "none"
    policy_section = (
        f"\nBANK POLICY TEXT (untrusted source; cite only as policy evidence):\n{policy_text[:22000]}"
        if policy_text else "\nNo bank policy document was provided. Do not claim policy compliance or gaps."
    )
    schema = {
        "executive_summary": "string grounded only in evidence",
        "requirements": [{"citation_id": "S1", "requirement": "verbatim or faithful requirement"}],
        "comparison": [{
            "earlier_requirement": "string",
            "updated_requirement": "string",
            "change": "string",
            "potential_impact": "string",
            "citation_ids": ["S1"],
        }],
        "findings": [{
            "requirement_id": "string",
            "clause": "only if explicitly visible in evidence; else Inconclusive",
            "policy_evidence": "exact policy excerpt or Not provided",
            "status": "Compliant, Potential gap, Conflict, or Inconclusive",
            "supporting_evidence": "string",
            "citation_ids": ["S1"],
            "next_action": "string",
        }],
        "recommendations": ["string"],
        "limitations": ["string"],
    }
    prompt = (
        "Create a structured RBI regulatory report using only the evidence below. "
        "Never invent clauses, dates, versions, page numbers, quotations, or conclusions. "
        "Use citation IDs only from this allowlist: " + allowed_ids + ". "
        "If support is incomplete, mark the item Inconclusive and state what needs manual review. "
        "Do not assign severity scores. For comparisons, state when an earlier version is absent. "
        "Return JSON only matching this shape: " + json.dumps(schema) + "\n\n"
        f"REPORT TYPE: {REPORT_TYPES[report_type]}\nREQUEST: {query}\n"
        f"RBI EVIDENCE:\n{evidence}{policy_section}"
    )
    response = client.chat.completions.create(
        model=os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile"),
        messages=[
            {
                "role": "system",
                "content": REGULAMIND_SYSTEM_PROMPT,
            },
            {"role": "user", "content": prompt},
        ],
        temperature=0.1,
        response_format={"type": "json_object"},
    )
    content = response.choices[0].message.content or ""
    result = _parse_model_json(content)

    valid_ids = {item["id"] for item in citations}
    for key in ("requirements", "comparison", "findings"):
        rows = result.get(key, [])
        if not isinstance(rows, list):
            result[key] = []
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            ids = row.get("citation_ids")
            if isinstance(ids, list):
                row["citation_ids"] = [item for item in ids if item in valid_ids]
            if key == "findings" and not row.get("citation_ids"):
                row["status"] = "Inconclusive"
                row["next_action"] = "Manually verify this item against the source documents."
    for key in ("recommendations", "limitations"):
        if not isinstance(result.get(key), list):
            result[key] = []
    result["requirements"] = result.get("requirements", [])
    result["findings"] = result.get("findings", [])
    result["analysis_mode"] = "llm_with_citations"
    return result


def _para(text: Any, style: ParagraphStyle) -> Paragraph:
    escaped = str(text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return Paragraph(escaped.replace("\n", "<br/>"), style)


def build_pdf(report: dict[str, Any], path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(
        name="ReportTitle", parent=styles["Title"], fontName="Helvetica-Bold",
        fontSize=25, leading=31, textColor=colors.HexColor("#102d4f"), alignment=TA_CENTER,
    ))
    styles.add(ParagraphStyle(
        name="SectionHeading", parent=styles["Heading2"], fontName="Helvetica-Bold",
        fontSize=12, leading=16, textColor=colors.HexColor("#153d64"),
        spaceBefore=12, spaceAfter=6,
    ))
    styles.add(ParagraphStyle(
        name="BodyCopy", parent=styles["BodyText"], fontName="Helvetica",
        fontSize=9, leading=13, spaceAfter=5,
    ))
    styles.add(ParagraphStyle(
        name="FinePrint", parent=styles["BodyText"], fontSize=8,
        leading=11, textColor=colors.HexColor("#617184"),
    ))

    doc = SimpleDocTemplate(
        str(path), pagesize=A4, rightMargin=19 * mm, leftMargin=19 * mm,
        topMargin=19 * mm, bottomMargin=18 * mm,
        title=report["title"], author="RegulaMind AI",
    )
    story: list[Any] = [Spacer(1, 45 * mm), _para("REGULAMIND AI", styles["FinePrint"]), Spacer(1, 7 * mm)]
    story.extend([
        _para(report["title"], styles["ReportTitle"]),
        Spacer(1, 8 * mm),
        _para(report["report_type"], styles["BodyCopy"]),
        _para(f"Generated {report['generated_at']}", styles["FinePrint"]),
        Spacer(1, 8 * mm),
        _para("Prepared from retrieved RBI source passages. Findings are evidence-led and may require manual review.", styles["FinePrint"]),
        PageBreak(),
    ])
    story.append(_para("Report metadata", styles["SectionHeading"]))
    metadata = [
        ["Report type", report["report_type"]],
        ["Generated", report["generated_at"]],
        ["Request", report["query"]],
        ["Analysis mode", report["analysis_mode"]],
    ]
    if report.get("source_documents"):
        metadata.append(["Source documents", ", ".join(report["source_documents"])])
    table = Table(metadata, colWidths=[38 * mm, 130 * mm], repeatRows=0, hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#eef3f7")),
        ("TEXTCOLOR", (0, 0), (-1, -1), colors.HexColor("#15283b")),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTNAME", (1, 0), (1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#d8e0e8")),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.extend([table, _para("Executive summary", styles["SectionHeading"]), _para(report["executive_summary"], styles["BodyCopy"])])

    if report["report_type_key"] == "regulation_comparison":
        story.append(_para("Regulatory comparison", styles["SectionHeading"]))
        comparisons = report.get("comparison", [])
        if comparisons:
            rows = [["Earlier requirement", "Updated requirement", "Change and potential impact", "Citation"]]
            for row in comparisons:
                rows.append([
                    _para(row.get("earlier_requirement", "Not established"), styles["FinePrint"]),
                    _para(row.get("updated_requirement", "Not established"), styles["FinePrint"]),
                    _para(f"{row.get('change', '')}<br/><br/>{row.get('potential_impact', '')}", styles["FinePrint"]),
                    _para(", ".join(row.get("citation_ids", [])) or "Review required", styles["FinePrint"]),
                ])
            story.append(_styled_table(rows, [42 * mm, 42 * mm, 58 * mm, 25 * mm]))
        else:
            story.append(_para("The retrieved evidence did not support a version-by-version comparison. Confirm that both versions are in scope.", styles["BodyCopy"]))

    if report["report_type_key"] in {"policy_compliance", "gap_analysis"}:
        story.append(_para("Compliance findings", styles["SectionHeading"]))
        findings = report.get("findings", [])
        if findings:
            rows = [["Requirement", "RBI clause", "Bank policy evidence", "Finding", "Supporting evidence and next action"]]
            for item in findings:
                citation_text = ", ".join(item.get("citation_ids", [])) or "Review required"
                rows.append([
                    _para(item.get("requirement_id", "Requirement"), styles["FinePrint"]),
                    _para(item.get("clause", "Inconclusive"), styles["FinePrint"]),
                    _para(item.get("policy_evidence", "Not provided"), styles["FinePrint"]),
                    _para(item.get("status", "Inconclusive"), styles["FinePrint"]),
                    _para(f"{item.get('supporting_evidence', '')}<br/><br/>Next: {item.get('next_action', '')}<br/>Sources: {citation_text}", styles["FinePrint"]),
                ])
            story.append(_styled_table(rows, [31 * mm, 25 * mm, 39 * mm, 25 * mm, 47 * mm]))
        else:
            story.append(_para("No policy findings were generated. Items require manual comparison against the selected policy.", styles["BodyCopy"]))

    story.append(_para("Applicable requirements and evidence", styles["SectionHeading"]))
    for item in report.get("requirements", []):
        citation = item.get("citation_id", "")
        requirement = item.get("requirement", item.get("excerpt", ""))
        story.append(KeepTogether([
            _para(f"{citation} | {item.get('title', item.get('document', 'RBI source'))} | page {item.get('page', 'not stated')}", styles["FinePrint"]),
            _para(requirement, styles["BodyCopy"]),
            Spacer(1, 2 * mm),
        ]))

    _append_list(story, "Recommendations for manual review", report.get("recommendations", []), styles)
    _append_list(story, "Limitations and inconclusive findings", report.get("limitations", []), styles)
    story.append(_para("Source references", styles["SectionHeading"]))
    for item in report.get("sources", []):
        story.append(_para(
            f"[{item['id']}] {item['title']} | {item['document']} | page {item['page']}",
            styles["BodyCopy"],
        ))

    doc.build(story, onFirstPage=_draw_page, onLaterPages=_draw_page)
    return path.stat().st_size


def _styled_table(rows: list[list[Any]], widths: list[float]) -> Table:
    table = Table(rows, colWidths=widths, repeatRows=1, hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#153d64")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 7),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#cfd9e3")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f5f8fb")]),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    return table


def _append_list(story: list[Any], title: str, items: list[str], styles: Any) -> None:
    story.append(_para(title, styles["SectionHeading"]))
    if not items:
        story.append(_para("None stated.", styles["BodyCopy"]))
        return
    for item in items:
        story.append(_para(f"• {item}", styles["BodyCopy"]))


def _draw_page(canvas: Any, doc: Any) -> None:
    canvas.saveState()
    width, _ = A4
    canvas.setStrokeColor(colors.HexColor("#d8e0e8"))
    canvas.line(19 * mm, 14 * mm, width - 19 * mm, 14 * mm)
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(colors.HexColor("#657487"))
    canvas.drawString(19 * mm, 9 * mm, "RegulaMind AI | Evidence-led review")
    canvas.drawRightString(width - 19 * mm, 9 * mm, f"{doc.page}")
    canvas.restoreState()


def build_csv(report: dict[str, Any], path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=[
            "requirement_id", "clause", "policy_evidence", "status",
            "supporting_evidence", "citation_ids", "next_action",
        ])
        writer.writeheader()
        for finding in report.get("findings", []):
            writer.writerow({
                **{key: finding.get(key, "") for key in (
                    "requirement_id", "clause", "policy_evidence", "status",
                    "supporting_evidence", "next_action",
                )},
                "citation_ids": ", ".join(finding.get("citation_ids", [])),
            })
    return path.stat().st_size


def preview_html(report: dict[str, Any]) -> str:
    """Render a safe, minimal HTML preview from the structured report."""
    from html import escape

    def safe(value: Any) -> str:
        return escape(str(value or ""))

    summary = escape(str(report.get("executive_summary", "")))
    sections = [
        f"<h1>{safe(report['title'])}</h1><p>{safe(report['report_type'])}</p>",
        f"<p>Generated {safe(report.get('generated_at'))} · {safe(report.get('analysis_mode'))}</p>",
        f"<h2>Executive summary</h2><p>{summary}</p>",
        f"<h2>Scope and source documents</h2><p>{safe(report.get('query'))}</p>",
        "<ul>" + "".join(f"<li>{safe(name)}</li>" for name in report.get("source_documents", [])) + "</ul>",
    ]
    if report.get("policy_document"):
        sections.append(f"<p>Bank policy: {safe(report['policy_document'])}</p>")

    if report.get("comparison"):
        sections.append("<h2>Regulation comparison</h2><table><thead><tr><th>Earlier requirement</th><th>Updated requirement</th><th>Change</th><th>Potential impact</th><th>Citations</th></tr></thead><tbody>")
        for item in report["comparison"]:
            sections.append(
                "<tr>" + "".join(
                    f"<td>{safe(value)}</td>" for value in (
                        item.get("earlier_requirement"), item.get("updated_requirement"),
                        item.get("change"), item.get("potential_impact"),
                        ", ".join(item.get("citation_ids", [])),
                    )
                ) + "</tr>"
            )
        sections.append("</tbody></table>")

    if report.get("findings"):
        sections.append("<h2>Compliance findings</h2><table><thead><tr><th>Requirement</th><th>Clause</th><th>Policy evidence</th><th>Finding</th><th>Supporting evidence</th><th>Next action</th></tr></thead><tbody>")
        for finding in report["findings"]:
            sections.append(
                "<tr>" + "".join(
                    f"<td>{safe(value)}</td>" for value in (
                        finding.get("requirement_id"), finding.get("clause"),
                        finding.get("policy_evidence"), finding.get("status"),
                        finding.get("supporting_evidence"), finding.get("next_action"),
                    )
                ) + "</tr>"
            )
        sections.append("</tbody></table>")

    sections.append("<h2>Applicable requirements and evidence</h2><ol>")
    for requirement in report.get("requirements", []):
        sections.append(
            f"<li><b>[{safe(requirement.get('citation_id'))}] "
            f"{safe(requirement.get('title', requirement.get('document')))}</b> "
            f"(page {safe(requirement.get('page'))})<p>{safe(requirement.get('requirement'))}</p></li>"
        )
    sections.append("</ol>")
    sections.append("<h2>Source excerpts</h2><ol>")
    for source in report.get("sources", []):
        sections.append(
            f"<li><b>[{safe(source['id'])}] {safe(source['title'])}</b> "
            f"({safe(source['document'])}, page {int(source['page'])})<p>"
            f"{safe(source['excerpt'])}</p></li>"
        )
    sections.append("</ol>")
    for heading, key in (
        ("Recommendations for manual review", "recommendations"),
        ("Limitations and inconclusive findings", "limitations"),
    ):
        sections.append(f"<h2>{heading}</h2><ul>")
        sections.extend(f"<li>{safe(item)}</li>" for item in report.get(key, []))
        if not report.get(key):
            sections.append("<li>None stated.</li>")
        sections.append("</ul>")
    return "".join(sections)