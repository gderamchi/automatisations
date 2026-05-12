from __future__ import annotations

import html
import re
from pathlib import Path

from reportlab import rl_config
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Flowable,
    HRFlowable,
    KeepTogether,
    PageBreak,
    Paragraph,
    Preformatted,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "docs" / "manuel-utilisateur-automatisation.md"
OUTPUT = ROOT / "output" / "pdf" / "manuel-utilisateur-automatisation.pdf"

rl_config.invariant = True

PAGE_WIDTH, PAGE_HEIGHT = A4
MARGIN_X = 18 * mm
MARGIN_TOP = 17 * mm
MARGIN_BOTTOM = 16 * mm

NAVY = colors.HexColor("#102A43")
BLUE = colors.HexColor("#1F6FEB")
LIGHT_BLUE = colors.HexColor("#EAF4FF")
GREEN = colors.HexColor("#0F766E")
LIGHT_GREEN = colors.HexColor("#E6F7F4")
AMBER = colors.HexColor("#B7791F")
LIGHT_AMBER = colors.HexColor("#FFF6D7")
INK = colors.HexColor("#1F2937")
MUTED = colors.HexColor("#64748B")
LINE = colors.HexColor("#D9E2EC")


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "cover_title": ParagraphStyle(
            "CoverTitle",
            parent=base["Title"],
            fontName="Helvetica-Bold",
            fontSize=26,
            leading=31,
            textColor=NAVY,
            alignment=TA_CENTER,
            spaceAfter=8,
        ),
        "cover_subtitle": ParagraphStyle(
            "CoverSubtitle",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=12.5,
            leading=18,
            textColor=INK,
            alignment=TA_CENTER,
            spaceAfter=20,
        ),
        "h1": ParagraphStyle(
            "H1",
            parent=base["Heading1"],
            fontName="Helvetica-Bold",
            fontSize=20,
            leading=25,
            textColor=NAVY,
            spaceBefore=4,
            spaceAfter=10,
        ),
        "h2": ParagraphStyle(
            "H2",
            parent=base["Heading2"],
            fontName="Helvetica-Bold",
            fontSize=15,
            leading=20,
            textColor=NAVY,
            spaceBefore=10,
            spaceAfter=6,
        ),
        "h3": ParagraphStyle(
            "H3",
            parent=base["Heading3"],
            fontName="Helvetica-Bold",
            fontSize=11.6,
            leading=15,
            textColor=GREEN,
            spaceBefore=8,
            spaceAfter=4,
        ),
        "normal": ParagraphStyle(
            "NormalBody",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=9.8,
            leading=14.2,
            textColor=INK,
            spaceAfter=6,
        ),
        "small": ParagraphStyle(
            "Small",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=8.3,
            leading=11.5,
            textColor=MUTED,
        ),
        "bullet": ParagraphStyle(
            "BulletBody",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=9.5,
            leading=13.4,
            textColor=INK,
            leftIndent=11,
            firstLineIndent=0,
            bulletIndent=0,
            spaceAfter=3.5,
        ),
        "code": ParagraphStyle(
            "Code",
            parent=base["Code"],
            fontName="Courier",
            fontSize=8.3,
            leading=11.4,
            textColor=colors.HexColor("#334155"),
            backColor=colors.HexColor("#F1F5F9"),
            borderColor=LINE,
            borderWidth=0.4,
            borderPadding=7,
            leftIndent=2,
            rightIndent=2,
            spaceBefore=3,
            spaceAfter=7,
        ),
        "callout": ParagraphStyle(
            "Callout",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=9.2,
            leading=13.2,
            textColor=INK,
        ),
        "caption": ParagraphStyle(
            "Caption",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=8,
            leading=10.5,
            textColor=MUTED,
            alignment=TA_CENTER,
        ),
        "table_header": ParagraphStyle(
            "TableHeader",
            parent=base["Normal"],
            fontName="Helvetica-Bold",
            fontSize=9,
            leading=12,
            textColor=colors.white,
        ),
    }


STYLES = _styles()


def inline_markdown(text: str) -> str:
    escaped = html.escape(text.strip())
    escaped = re.sub(r"`([^`]+)`", r'<font name="Courier">\1</font>', escaped)
    escaped = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", escaped)
    return escaped


def paragraph(text: str, style: str = "normal") -> Paragraph:
    return Paragraph(inline_markdown(text), STYLES[style])


class FlowDiagram(Flowable):
    def __init__(self, width: float):
        super().__init__()
        self.width = width
        self.height = 35 * mm
        self.steps = [
            ("1", "Envoyer", "Mail avec pieces jointes"),
            ("2", "Lire", "OCR et extraction"),
            ("3", "Verifier", "Champs facture"),
            ("4", "Confirmer", "Chantier et Excel"),
            ("5", "Classer", "NAS, Excel, InterFast"),
        ]

    def wrap(self, avail_width: float, avail_height: float) -> tuple[float, float]:
        self.width = min(self.width, avail_width)
        return self.width, self.height

    def draw(self) -> None:
        c = self.canv
        gap = 3.2 * mm
        box_w = (self.width - gap * (len(self.steps) - 1)) / len(self.steps)
        box_h = 25 * mm
        y = 6 * mm
        for index, (num, title, subtitle) in enumerate(self.steps):
            x = index * (box_w + gap)
            c.setStrokeColor(LINE)
            c.setFillColor(colors.white)
            c.roundRect(x, y, box_w, box_h, 4, stroke=1, fill=1)
            c.setFillColor(BLUE if index < 4 else GREEN)
            c.circle(x + 8 * mm, y + box_h - 8 * mm, 4.2 * mm, stroke=0, fill=1)
            c.setFillColor(colors.white)
            c.setFont("Helvetica-Bold", 8)
            c.drawCentredString(x + 8 * mm, y + box_h - 10.5 * mm, num)
            c.setFillColor(NAVY)
            c.setFont("Helvetica-Bold", 9.2)
            c.drawString(x + 4 * mm, y + 9.8 * mm, title)
            c.setFillColor(MUTED)
            c.setFont("Helvetica", 7.1)
            c.drawString(x + 4 * mm, y + 5 * mm, subtitle)
            if index < len(self.steps) - 1:
                c.setStrokeColor(BLUE)
                start_x = x + box_w + 0.8 * mm
                arrow_y = y + box_h / 2
                end_x = x + box_w + gap - 0.8 * mm
                c.line(start_x, arrow_y, end_x, arrow_y)
                c.line(end_x, arrow_y, end_x - 1.8 * mm, arrow_y + 1.4 * mm)
                c.line(end_x, arrow_y, end_x - 1.8 * mm, arrow_y - 1.4 * mm)


def callout(text: str, tone: str = "blue") -> Table:
    bg = LIGHT_BLUE
    accent = BLUE
    if tone == "green":
        bg = LIGHT_GREEN
        accent = GREEN
    elif tone == "amber":
        bg = LIGHT_AMBER
        accent = AMBER
    content = Paragraph(inline_markdown(text), STYLES["callout"])
    table = Table([[content]], colWidths=[PAGE_WIDTH - 2 * MARGIN_X - 10])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), bg),
                ("BOX", (0, 0), (-1, -1), 0.4, accent),
                ("LEFTPADDING", (0, 0), (-1, -1), 9),
                ("RIGHTPADDING", (0, 0), (-1, -1), 9),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ]
        )
    )
    return table


def cover_story() -> list:
    width = PAGE_WIDTH - 2 * MARGIN_X
    return [
        Spacer(1, 18 * mm),
        Paragraph("Manuel utilisateur", STYLES["cover_title"]),
        Paragraph(
            "Automatisation documentaire - reception email, verification OCR, routage chantier, Excel, NAS et InterFast",
            STYLES["cover_subtitle"],
        ),
        callout(
            "**Pour qui ?** Ce guide est fait pour un client non technique. Il explique quoi faire, dans quel ordre, et comment reagir quand une validation ou un blocage apparait.",
            "green",
        ),
        Spacer(1, 13 * mm),
        FlowDiagram(width),
        Spacer(1, 6 * mm),
        Paragraph(
            "Le principe : vous envoyez les documents, l'automatisation prepare le travail, puis vous confirmez uniquement les points qui meritent un controle humain.",
            STYLES["caption"],
        ),
        Spacer(1, 12 * mm),
        quick_reference_table(),
        PageBreak(),
    ]


def quick_reference_table() -> Table:
    data = [
        [
            Paragraph("Situation", STYLES["table_header"]),
            Paragraph("Action a faire", STYLES["table_header"]),
        ],
        [
            Paragraph("La reponse indique <b>A VERIFIER</b>.", STYLES["normal"]),
            paragraph("Ouvrir le lien et controler les champs OCR.", "normal"),
        ],
        [
            paragraph("Le chantier est incertain.", "normal"),
            paragraph("Choisir le chantier dans la page de routage.", "normal"),
        ],
        [
            Paragraph("Excel indique <b>A choisir</b>.", STYLES["normal"]),
            paragraph("Selectionner le bon classeur dans la liste.", "normal"),
        ],
        [
            Paragraph("Excel indique <b>Manquant</b>.", STYLES["normal"]),
            paragraph("Ne pas forcer. Signaler le blocage avec le nom du document.", "normal"),
        ],
        [
            paragraph("Je cherche l'original recu.", "normal"),
            paragraph("File Station > Professionnel_CCM > 12_AUTOMATISATION > documents > archive > originals.", "normal"),
        ],
    ]
    table = Table(data, colWidths=[58 * mm, 96 * mm], hAlign="CENTER")
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), NAVY),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("BACKGROUND", (0, 1), (-1, -1), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.35, LINE),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 7),
                ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    return table


def flush_paragraph(story: list, buffer: list[str]) -> None:
    if not buffer:
        return
    text = " ".join(part.strip() for part in buffer if part.strip())
    if text:
        story.append(paragraph(text))
    buffer.clear()


def render_markdown(source: Path) -> list:
    story: list = []
    buffer: list[str] = []
    quote_buffer: list[str] = []
    code_buffer: list[str] = []
    in_code = False

    for raw_line in source.read_text(encoding="utf-8").splitlines():
        line = raw_line.rstrip()

        if line.startswith("```"):
            if in_code:
                story.append(Preformatted("\n".join(code_buffer), STYLES["code"]))
                code_buffer.clear()
                in_code = False
            else:
                flush_paragraph(story, buffer)
                in_code = True
            continue

        if in_code:
            code_buffer.append(line)
            continue

        if line.startswith("> "):
            flush_paragraph(story, buffer)
            quote_buffer.append(line[2:])
            continue

        if quote_buffer:
            tone = "amber" if any("Important" in item or "Regle" in item for item in quote_buffer) else "blue"
            story.append(callout(" ".join(quote_buffer), tone=tone))
            story.append(Spacer(1, 4))
            quote_buffer.clear()

        if not line.strip():
            flush_paragraph(story, buffer)
            continue

        if line == "---":
            flush_paragraph(story, buffer)
            story.append(PageBreak())
            continue

        if line.startswith("# "):
            flush_paragraph(story, buffer)
            title = line[2:].strip()
            if title != "Manuel utilisateur - Automatisation documentaire":
                story.append(Paragraph(inline_markdown(title), STYLES["h1"]))
            continue

        if line.startswith("## "):
            flush_paragraph(story, buffer)
            story.append(Paragraph(inline_markdown(line[3:].strip()), STYLES["h2"]))
            story.append(HRFlowable(width="100%", thickness=0.45, color=LINE, spaceAfter=6))
            continue

        if line.startswith("### "):
            flush_paragraph(story, buffer)
            story.append(Paragraph(inline_markdown(line[4:].strip()), STYLES["h3"]))
            continue

        bullet = re.match(r"^\s*-\s+(.*)", line)
        ordered = re.match(r"^\s*(\d+)\.\s+(.*)", line)
        if bullet or ordered:
            flush_paragraph(story, buffer)
            text = bullet.group(1) if bullet else ordered.group(2)
            marker = "-" if bullet else f"{ordered.group(1)}."
            story.append(Paragraph(inline_markdown(text), STYLES["bullet"], bulletText=marker))
            continue

        buffer.append(line)

    flush_paragraph(story, buffer)
    if quote_buffer:
        story.append(callout(" ".join(quote_buffer)))
    return story


def on_page(canvas, doc) -> None:
    canvas.saveState()
    page = canvas.getPageNumber()
    canvas.setStrokeColor(LINE)
    canvas.line(MARGIN_X, PAGE_HEIGHT - 12 * mm, PAGE_WIDTH - MARGIN_X, PAGE_HEIGHT - 12 * mm)
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(MUTED)
    canvas.drawString(MARGIN_X, PAGE_HEIGHT - 9 * mm, "Automatisation documentaire - manuel utilisateur")
    canvas.drawRightString(PAGE_WIDTH - MARGIN_X, 9 * mm, f"Page {page}")
    canvas.restoreState()


def build_pdf() -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(OUTPUT),
        pagesize=A4,
        rightMargin=MARGIN_X,
        leftMargin=MARGIN_X,
        topMargin=MARGIN_TOP,
        bottomMargin=MARGIN_BOTTOM,
        title="Manuel utilisateur - Automatisation documentaire",
        author="Codex",
    )
    frame_width = PAGE_WIDTH - 2 * MARGIN_X
    story = cover_story()
    story.extend(render_markdown(SOURCE))

    # Keep short adjacent items together where possible without risking oversized blocks.
    polished: list = []
    for item in story:
        if isinstance(item, Paragraph) and item.style.name in {"H2", "H3"}:
            polished.append(KeepTogether([item]))
        else:
            polished.append(item)

    doc.build(polished, onFirstPage=on_page, onLaterPages=on_page)
    print(OUTPUT.relative_to(ROOT))
    print(f"{OUTPUT.stat().st_size} bytes")


if __name__ == "__main__":
    build_pdf()
