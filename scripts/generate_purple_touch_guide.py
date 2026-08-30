from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LOCAL_PDF_DEPS = ROOT / "tmp" / "pdf_deps"
if LOCAL_PDF_DEPS.exists():
    sys.path.insert(0, str(LOCAL_PDF_DEPS))

from reportlab.graphics.shapes import Drawing, Line, Polygon, Rect, String
from reportlab.lib import colors
from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.platypus import (
    Flowable,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


OUTPUT = ROOT / "docs" / "Purple_Touch_Implementation_Guide_v1_2.pdf"

INK = HexColor("#172033")
MUTED = HexColor("#5C687C")
LINE = HexColor("#D7DEE8")
SOFT = HexColor("#F4F7FA")
PURPLE = HexColor("#B78BD0")
PURPLE_DARK = HexColor("#70468A")
BLUE = HexColor("#3976D8")
YELLOW = HexColor("#F0A500")
BROWN = HexColor("#7D2E2E")
LIGHT_GREEN = HexColor("#7CC38B")
GREEN = HexColor("#16856B")
RED = HexColor("#C64747")
BLACK_LINE = HexColor("#111827")
AMBER_BG = HexColor("#FFF5D9")
GREEN_BG = HexColor("#EAF7F1")
RED_BG = HexColor("#FDEEEE")
BLUE_BG = HexColor("#EAF2FF")
PURPLE_BG = HexColor("#F5EFF8")


styles = getSampleStyleSheet()
styles.add(
    ParagraphStyle(
        name="CoverTitle",
        parent=styles["Title"],
        fontName="Helvetica-Bold",
        fontSize=28,
        leading=32,
        textColor=INK,
        alignment=TA_LEFT,
        spaceAfter=10,
    )
)
styles.add(
    ParagraphStyle(
        name="CoverSub",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=13,
        leading=19,
        textColor=MUTED,
        spaceAfter=8,
    )
)
styles.add(
    ParagraphStyle(
        name="H1x",
        parent=styles["Heading1"],
        fontName="Helvetica-Bold",
        fontSize=20,
        leading=24,
        textColor=INK,
        spaceBefore=2,
        spaceAfter=10,
    )
)
styles.add(
    ParagraphStyle(
        name="H2x",
        parent=styles["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=13,
        leading=17,
        textColor=INK,
        spaceBefore=8,
        spaceAfter=6,
    )
)
styles.add(
    ParagraphStyle(
        name="Bodyx",
        parent=styles["BodyText"],
        fontName="Helvetica",
        fontSize=9.3,
        leading=13.2,
        textColor=INK,
        spaceAfter=5,
    )
)
styles.add(
    ParagraphStyle(
        name="Smallx",
        parent=styles["BodyText"],
        fontName="Helvetica",
        fontSize=7.7,
        leading=10.5,
        textColor=MUTED,
    )
)
styles.add(
    ParagraphStyle(
        name="BoxTitle",
        parent=styles["BodyText"],
        fontName="Helvetica-Bold",
        fontSize=9.2,
        leading=12,
        textColor=INK,
        alignment=TA_CENTER,
    )
)
styles.add(
    ParagraphStyle(
        name="BoxBody",
        parent=styles["BodyText"],
        fontName="Helvetica",
        fontSize=7.5,
        leading=10,
        textColor=MUTED,
        alignment=TA_CENTER,
    )
)
styles.add(
    ParagraphStyle(
        name="TableHead",
        parent=styles["BodyText"],
        fontName="Helvetica-Bold",
        fontSize=7.7,
        leading=9.8,
        textColor=colors.white,
    )
)
styles.add(
    ParagraphStyle(
        name="TableCell",
        parent=styles["BodyText"],
        fontName="Helvetica",
        fontSize=7.5,
        leading=10.2,
        textColor=INK,
    )
)
styles.add(
    ParagraphStyle(
        name="TableCellStrong",
        parent=styles["TableCell"],
        fontName="Helvetica-Bold",
    )
)
styles.add(
    ParagraphStyle(
        name="Callout",
        parent=styles["BodyText"],
        fontName="Helvetica",
        fontSize=8.5,
        leading=12,
        textColor=INK,
    )
)
styles.add(
    ParagraphStyle(
        name="Codex",
        parent=styles["Code"],
        fontName="Courier",
        fontSize=7.3,
        leading=10,
        textColor=INK,
    )
)


def p(text: str, style: str = "Bodyx") -> Paragraph:
    return Paragraph(text, styles[style])


def heading(number: str, title: str) -> Paragraph:
    return p(f"<font color='#70468A'>{number}</font>  {title}", "H1x")


def callout(title: str, body: str, background=BLUE_BG, accent=BLUE) -> Table:
    content = p(f"<b>{title}</b><br/>{body}", "Callout")
    table = Table([[content]], colWidths=[174 * mm])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), background),
                ("BOX", (0, 0), (-1, -1), 0.7, accent),
                ("LINEBEFORE", (0, 0), (0, -1), 4, accent),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    return table


def data_table(headers: list[str], rows: list[list[str]], widths: list[float]) -> Table:
    data = [[p(item, "TableHead") for item in headers]]
    for row in rows:
        data.append([p(item, "TableCell") for item in row])
    table = Table(data, colWidths=[value * mm for value in widths], repeatRows=1, hAlign="LEFT")
    commands = [
        ("BACKGROUND", (0, 0), (-1, 0), INK),
        ("GRID", (0, 0), (-1, -1), 0.35, LINE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]
    for row_index in range(1, len(data)):
        if row_index % 2 == 0:
            commands.append(("BACKGROUND", (0, row_index), (-1, row_index), SOFT))
    table.setStyle(TableStyle(commands))
    return table


def flowchart(steps: list[tuple[str, str, colors.Color]]) -> Table:
    rows = []
    for index, (title, body, accent) in enumerate(steps):
        box = Table(
            [[p(title, "BoxTitle")], [p(body, "BoxBody")]],
            colWidths=[130 * mm],
        )
        box.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), colors.white),
                    ("BOX", (0, 0), (-1, -1), 1.2, accent),
                    ("BACKGROUND", (0, 0), (-1, 0), accent.clone(alpha=0.13)),
                    ("TOPPADDING", (0, 0), (-1, 0), 6),
                    ("BOTTOMPADDING", (0, 0), (-1, 0), 4),
                    ("TOPPADDING", (0, 1), (-1, 1), 5),
                    ("BOTTOMPADDING", (0, 1), (-1, 1), 6),
                ]
            )
        )
        rows.append([box])
        if index < len(steps) - 1:
            rows.append([p("<font color='#5C687C' size='13'>v</font>", "BoxTitle")])
    table = Table(rows, colWidths=[174 * mm], hAlign="CENTER")
    table.setStyle(TableStyle([("ALIGN", (0, 0), (-1, -1), "CENTER")]))
    return table


def horizontal_flow(items: list[tuple[str, str, colors.Color]]) -> Table:
    cells = []
    widths = []
    for index, (title, body, accent) in enumerate(items):
        box = Table([[p(title, "BoxTitle")], [p(body, "BoxBody")]], colWidths=[44 * mm])
        box.setStyle(
            TableStyle(
                [
                    ("BOX", (0, 0), (-1, -1), 1, accent),
                    ("BACKGROUND", (0, 0), (-1, 0), accent.clone(alpha=0.13)),
                    ("TOPPADDING", (0, 0), (-1, -1), 5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ]
            )
        )
        cells.append(box)
        widths.append(44 * mm)
        if index < len(items) - 1:
            cells.append(p("<font size='13' color='#5C687C'>&gt;</font>", "BoxTitle"))
            widths.append(8 * mm)
    table = Table([cells], colWidths=widths, hAlign="CENTER")
    table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
    return table


class IndicatorSketch(Flowable):
    def __init__(self, width: float = 174 * mm, height: float = 72 * mm):
        super().__init__()
        self.width = width
        self.height = height

    def draw(self) -> None:
        c = self.canv
        c.saveState()
        c.setStrokeColor(LINE)
        c.setLineWidth(0.4)
        for step in range(1, 6):
            y = 12 * mm + step * 8 * mm
            c.line(8 * mm, y, self.width - 8 * mm, y)
        candles = [
            (17, 31, 43, True),
            (31, 37, 49, True),
            (45, 41, 55, False),
            (59, 45, 58, True),
            (73, 49, 62, True),
            (87, 47, 60, False),
            (101, 43, 56, False),
            (115, 39, 51, False),
        ]
        for x_mm, low_mm, high_mm, bullish in candles:
            x = x_mm * mm
            low = low_mm * mm
            high = high_mm * mm
            color = GREEN if bullish else RED
            c.setStrokeColor(color)
            c.setFillColor(color)
            c.line(x, low - 3 * mm, x, high + 3 * mm)
            body_low = low if bullish else low + 5 * mm
            body_height = max(5 * mm, high - low - 5 * mm)
            c.rect(x - 2.5 * mm, body_low, 5 * mm, body_height, fill=1, stroke=0)
        paths = [
            (PURPLE, [(10, 29), (30, 34), (50, 39), (70, 44), (90, 46), (110, 43), (128, 40)]),
            (BLUE, [(10, 45), (30, 49), (50, 53), (70, 56), (90, 55), (110, 49), (128, 44)]),
            (LIGHT_GREEN, [(10, 25), (30, 28), (50, 31), (70, 35), (90, 37), (110, 36), (128, 34)]),
            (BROWN, [(10, 21), (30, 24), (50, 27), (70, 30), (90, 32), (110, 31), (128, 30)]),
            (YELLOW, [(10, 18), (30, 18), (50, 22), (70, 25), (90, 27), (110, 29), (128, 29)]),
            (BLACK_LINE, [(10, 13), (30, 15), (50, 17), (70, 19), (90, 21), (110, 23), (128, 25)]),
        ]
        for color, points in paths:
            c.setStrokeColor(color)
            c.setLineWidth(2.2 if color in (PURPLE, BLACK_LINE) else 1.5)
            for left, right in zip(points, points[1:]):
                c.line(left[0] * mm, left[1] * mm, right[0] * mm, right[1] * mm)
        legend = [
            (PURPLE, "Purple EMA9"),
            (BLUE, "Blue CK"),
            (LIGHT_GREEN, "Light green EMA26"),
            (BROWN, "Brown VWMA20"),
            (YELLOW, "Yellow CK"),
            (BLACK_LINE, "Black EMA89"),
        ]
        x = 134 * mm
        y = 60 * mm
        c.setFont("Helvetica", 7.2)
        for color, label in legend:
            c.setStrokeColor(color)
            c.setLineWidth(2.2)
            c.line(x, y, x + 9 * mm, y)
            c.setFillColor(INK)
            c.drawString(x + 11 * mm, y - 2.2, label)
            y -= 8 * mm
        c.setFillColor(MUTED)
        c.setFont("Helvetica-Oblique", 7)
        c.drawString(10 * mm, 7 * mm, "Concept sketch only - values are illustrative, not a market chart.")
        c.restoreState()


class ExactTouchSketch(Flowable):
    def __init__(self, width: float = 174 * mm, height: float = 55 * mm):
        super().__init__()
        self.width = width
        self.height = height

    def draw_case(self, x: float, title: str, touched: bool) -> None:
        c = self.canv
        c.setFillColor(INK)
        c.setFont("Helvetica-Bold", 9)
        c.drawCentredString(x + 34 * mm, 45 * mm, title)
        c.setStrokeColor(PURPLE)
        c.setLineWidth(3)
        line_y = 26 * mm if touched else 19 * mm
        c.line(x + 6 * mm, line_y, x + 62 * mm, line_y)
        c.setStrokeColor(GREEN)
        c.setFillColor(GREEN_BG)
        c.setLineWidth(1.5)
        c.line(x + 34 * mm, 12 * mm, x + 34 * mm, 39 * mm)
        c.rect(x + 29 * mm, 21 * mm, 10 * mm, 11 * mm, fill=1, stroke=1)
        c.setFont("Helvetica", 7.2)
        c.setFillColor(MUTED)
        if touched:
            c.drawCentredString(x + 34 * mm, 5 * mm, "EMA9 lies inside candle high/low")
            c.setFillColor(GREEN)
            c.drawString(x + 53 * mm, 35 * mm, "PASS")
        else:
            c.drawCentredString(x + 34 * mm, 5 * mm, "Nearest edge within 3.00% of EMA9")
            c.setFillColor(GREEN)
            c.drawString(x + 53 * mm, 35 * mm, "PASS")

    def draw(self) -> None:
        self.canv.saveState()
        self.draw_case(5 * mm, "Exact range touch", True)
        self.draw_case(94 * mm, "Near-touch distance zone", False)
        self.canv.restoreState()


class EntryTimelineSketch(Flowable):
    def __init__(self, width: float = 174 * mm, height: float = 58 * mm):
        super().__init__()
        self.width = width
        self.height = height

    def draw(self) -> None:
        c = self.canv
        c.saveState()
        base = 22 * mm
        c.setStrokeColor(LINE)
        c.setLineWidth(1)
        c.line(13 * mm, base, 162 * mm, base)
        nodes = [
            (24, PURPLE, "Candle 1", "Mapped purple touch"),
            (68, BLUE, "Candle 2", "Must close first"),
            (112, GREEN, "Candle 3+", "Check entry + lows"),
            (153, RED, "Discard", "Break lower C1/C2 low"),
        ]
        for x_mm, color, title, body in nodes:
            x = x_mm * mm
            c.setFillColor(color)
            c.circle(x, base, 4 * mm, fill=1, stroke=0)
            c.setFillColor(INK)
            c.setFont("Helvetica-Bold", 8)
            c.drawCentredString(x, base + 10 * mm, title)
            c.setFillColor(MUTED)
            c.setFont("Helvetica", 6.8)
            c.drawCentredString(x, base - 10 * mm, body)
        c.setStrokeColor(RED)
        c.setLineWidth(1)
        c.line(97 * mm, 9 * mm, 156 * mm, 9 * mm)
        c.setFillColor(RED)
        c.setFont("Helvetica", 6.8)
        c.drawCentredString(126 * mm, 4 * mm, "Invalidation window has no fixed candle-count expiry")
        c.restoreState()


def page_header_footer(canvas, doc) -> None:
    canvas.saveState()
    page = canvas.getPageNumber()
    if page > 1:
        canvas.setStrokeColor(LINE)
        canvas.setLineWidth(0.5)
        canvas.line(18 * mm, A4[1] - 14 * mm, A4[0] - 18 * mm, A4[1] - 14 * mm)
        canvas.setFillColor(MUTED)
        canvas.setFont("Helvetica", 7.5)
        canvas.drawString(18 * mm, A4[1] - 10 * mm, "TradeAnalysisWithAjay - Purple Touch Implementation Guide")
        canvas.drawRightString(A4[0] - 18 * mm, A4[1] - 10 * mm, "Read-only analysis support")
    canvas.setStrokeColor(LINE)
    canvas.line(18 * mm, 13 * mm, A4[0] - 18 * mm, 13 * mm)
    canvas.setFillColor(MUTED)
    canvas.setFont("Helvetica", 7.3)
    canvas.drawString(18 * mm, 8 * mm, "Implementation snapshot: scanner-audit-and-v3 | 30 Aug 2026")
    canvas.drawRightString(A4[0] - 18 * mm, 8 * mm, f"Page {page}")
    canvas.restoreState()


def build_story() -> list[Flowable]:
    story: list[Flowable] = []

    story += [
        Spacer(1, 18 * mm),
        p("PURPLE TOUCH", "CoverTitle"),
        p("Implementation and Review Guide", "CoverTitle"),
        Spacer(1, 4 * mm),
        p(
            "A visual explanation of how the scanner shortlists F&amp;O stocks, applies the confirmed Krishna rules, waits for entry confirmation, creates alerts and trade IDs, and tracks exits.",
            "CoverSub",
        ),
        Spacer(1, 8 * mm),
        IndicatorSketch(),
        Spacer(1, 7 * mm),
        callout(
            "Purpose",
            "Use this document to compare the implemented logic with Krishna's intended chart setup. It documents current code behavior and calls out assumptions that may need refinement.",
            PURPLE_BG,
            PURPLE_DARK,
        ),
        Spacer(1, 5 * mm),
        callout(
            "Scope and safety",
            "The feature identifies analysis candidates and tracks confirmation/invalidation context. It does not place orders, execute trades, or claim future performance.",
            AMBER_BG,
            YELLOW,
        ),
        Spacer(1, 10 * mm),
        p("Version 1.2 | Branch scanner-audit-and-v3 | Generated 30 Aug 2026", "Smallx"),
        PageBreak(),
    ]

    story += [
        heading("1", "The complete workflow at a glance"),
        p(
            "The system separates discovery, qualification, entry, and exit. A stock appearing in Step 1 is not automatically an alert. A strict candidate is also not automatically a new alert.",
        ),
        flowchart(
            [
                ("1. Refresh cached candles", "Download/update all required Monthly, Weekly, Daily and lower-timeframe candles for the F&amp;O list.", BLUE),
                ("2. Step 1 - purple EMA9 distance", "Latest higher-timeframe candle must touch EMA9 or come within 3.00%. Rows rank from 0.00% upward.", PURPLE_DARK),
                ("3. Strict higher-timeframe qualification", "Apply EMA89, EMA26, blue-above-purple, blue-to-purple approach, higher confirmation, and structure checks.", GREEN),
                ("4. Entry status", "Evaluate early and final entry timeframes. Status can be waiting, candidate, discarded, missing, or insufficient.", YELLOW),
                ("5. Alert + trade ID", "During NSE market hours, a fresh entry candidate creates one entry event and one open trade ID. Duplicates are suppressed.", BLUE),
                ("6. Exit tracking", "Only open trade IDs are checked. A close below yellow on the configured exit timeframe creates an exit alert and closes that ID.", RED),
            ]
        ),
        Spacer(1, 4 * mm),
        callout(
            "Core distinction",
            "Step 1 rows > strict candidates > entry-ready rows > newly created alerts. Each stage can only reduce or preserve the previous stage count.",
            GREEN_BG,
            GREEN,
        ),
        PageBreak(),
    ]

    story += [
        heading("2", "Timeframe matrix"),
        p("All three profiles are scanned independently because an opportunity can originate from Monthly, Weekly, or Daily purple touch."),
        data_table(
            ["Profile", "Touch timeframe", "Higher confirmation", "Early entry", "Final entry", "Exit"],
            [
                ["Monthly", "Monthly", "Derived 5-month candle: open or close above blue", "120 minute (2 hour)", "Daily", "120 minute"],
                ["Weekly", "Weekly", "Monthly candle: open or close above blue", "30 minute", "120 minute (2 hour)", "30 minute"],
                ["Daily", "Daily", "Weekly candle: open or close above blue", "10 minute", "30 minute", "10 minute"],
            ],
            [22, 26, 48, 26, 26, 26],
        ),
        Spacer(1, 7 * mm),
        p("Independent monitor intervals", "H2x"),
        data_table(
            ["Profile", "UI interval choices", "What the interval controls"],
            [
                ["Monthly", "15m, 30m, 1h, 2h (default)", "How often the Monthly profile refresh/check is queued during market hours."],
                ["Weekly", "5m, 10m, 15m, 30m (default)", "How often the Weekly profile refresh/check is queued during market hours."],
                ["Daily", "3m (default), 5m, 10m", "How often the Daily profile refresh/check is queued during market hours."],
            ],
            [28, 52, 94],
        ),
        Spacer(1, 7 * mm),
        callout(
            "No overlapping scan work",
            "Due profile scans are queued. Candle refresh, entry checks, and exit checks for the same run complete in sequence so concurrent scans do not overwrite UI state.",
            BLUE_BG,
            BLUE,
        ),
        Spacer(1, 5 * mm),
        callout(
            "Per-profile visibility",
            "The UI shows separate Monthly, Weekly, and Daily progress bars plus each profile's last start, last end, and next scheduled trigger. A queued profile waits for the active profile to finish.",
            GREEN_BG,
            GREEN,
        ),
        PageBreak(),
    ]

    story += [
        heading("3", "Indicator and color mapping"),
        IndicatorSketch(height=68 * mm),
        Spacer(1, 5 * mm),
        data_table(
            ["Color", "Implemented indicator", "Role in this setup", "Mandatory?"],
            [
                ["Purple", "EMA 9", "Touch line and fast trend reference.", "Yes - range distance <= 3.00%"],
                ["Blue", "Upper Chande Kroll stop (10, 1, 9)", "Must be above purple; used for approach and higher confirmation.", "Yes"],
                ["Yellow", "Lower Chande Kroll stop (10, 1, 9)", "Entry close-above trigger and exit close-below trigger.", "Yes"],
                ["Light green", "EMA 26", "Higher-timeframe trend filter; close and EMA9 relationships are displayed.", "Yes in strict scan"],
                ["Brown", "VWMA 20", "Final entry requires yellow above brown; early entry treats it as context.", "Final only"],
                ["Black", "EMA 89", "Longer trend filter; higher-timeframe close must be above it.", "Yes"],
                ["RSI", "RSI 14", "Bullish divergence can improve context but does not block entry.", "Optional"],
            ],
            [25, 48, 72, 29],
        ),
        Spacer(1, 5 * mm),
        callout(
            "Explicitly ignored for Purple Touch",
            "Ichimoku cloud, VWAP, and Donchian Channel 20 may exist elsewhere in the project but are not qualification conditions in this setup.",
            AMBER_BG,
            YELLOW,
        ),
        PageBreak(),
    ]

    story += [
        heading("4", "Step 1 - Purple EMA9 touch and distance"),
        p(
            "The latest candle on the selected touch timeframe must physically contain EMA9 within its high/low range or its nearest edge must be no more than 3.00% away. Rows are ranked from the smallest range distance upward.",
        ),
        ExactTouchSketch(),
        Spacer(1, 4 * mm),
        callout(
            "Implemented expression",
            "PASS when EMA9 is inside the candle range (0.00%) or the nearest candle edge is within 3.00% of EMA9. Close distance remains separate context. UI bands show exact 0.00%, then 0.20%, 0.50%, 1.00%, 1.50%, 2.00%, and the full 3.00% shortlist.",
            PURPLE_BG,
            PURPLE_DARK,
        ),
        Spacer(1, 6 * mm),
        p("What Step 1 does and does not mean", "H2x"),
        data_table(
            ["Step 1 result", "Meaning", "Next action"],
            [
                ["Touch row", "The latest Monthly/Weekly/Daily candle range touches EMA9 or comes within 3.00%.", "Audit the strict blockers shown in the same row."],
                ["No row", "EMA9 is more than 3.00% from the latest candle range, or required data is unavailable.", "The stock does not proceed in that profile for this scan."],
                ["Same symbol in multiple rows", "The symbol independently touched in more than one profile.", "Review each profile using its own entry/exit matrix."],
            ],
            [34, 78, 62],
        ),
        Spacer(1, 5 * mm),
        callout(
            "Confirmed distance rule",
            "The current maximum is 3.00%. Near-touch rows may proceed through strict qualification and entry alerts. UI filters narrow the view without changing the scanner result set.",
            GREEN_BG,
            GREEN,
        ),
        PageBreak(),
    ]

    story += [
        heading("5", "Strict higher-timeframe qualification"),
        p("A Step 1 touch becomes a strict candidate only when every mandatory higher-timeframe gate passes."),
        horizontal_flow(
            [
                ("Purple distance", "Range distance <= 3%", PURPLE_DARK),
                ("Trend", "Close > EMA89 and EMA26", BLACK_LINE),
                ("Direction", "Blue > purple", BLUE),
            ]
        ),
        Spacer(1, 4 * mm),
        horizontal_flow(
            [
                ("Approach", "Blue area toward purple over 2-3 candles", YELLOW),
                ("Higher confirm", "5M / Month / Week above blue", GREEN),
                ("Structure", "Not a downtrend", BROWN),
            ]
        ),
        Spacer(1, 8 * mm),
        data_table(
            ["Gate", "Pass condition", "UI result when it fails"],
            [
                ["Black EMA89", "Latest higher-timeframe close > EMA89.", "Block; Unknown when fewer than 89 candles are available."],
                ["EMA26", "Latest close >= EMA26. EMA9 > EMA26 adds constructive context.", "Block when close is below EMA26."],
                ["Blue / purple", "Upper Chande Kroll blue line > purple EMA9.", "Block when blue is not above purple or cannot be calculated."],
                ["Approach", "Within latest 3 candles, a prior close was near blue and latest price moved toward purple.", "Block when direction is not observed."],
                ["Higher confirmation", "Profile-specific confirmation candle passes blue-line rule.", "Block or Missing/Insufficient."],
                ["Market structure", "Structure is uptrend/range/neutral; explicit downtrend is rejected.", "Block when downtrend."],
            ],
            [36, 88, 50],
        ),
        Spacer(1, 5 * mm),
        callout(
            "Strict candidate is not an alert",
            "The strict table can include rows whose early and final entry status is still 'wait'. Alert creation only evaluates rows with entry_candidate status.",
            AMBER_BG,
            YELLOW,
        ),
        PageBreak(),
    ]

    story += [
        heading("6", "Higher confirmation by profile"),
        p("The blue-line confirmation is evaluated on a separate higher aggregation. This is mandatory in the current implementation."),
        data_table(
            ["Purple touch", "Confirmation data", "PASS", "BLOCK"],
            [
                ["Monthly", "Daily candles are aggregated into stable calendar 5-month buckets.", "Latest derived 5-month open OR close > its blue Chande Kroll.", "Both open and close are <= blue, or blue/data is unavailable."],
                ["Weekly", "Monthly candles.", "Latest Monthly open OR close > its blue Chande Kroll.", "Both Monthly open and close are <= blue, or blue/data is unavailable."],
                ["Daily", "Weekly candles.", "Latest Weekly open OR close > its blue Chande Kroll.", "Both Weekly open and close are <= blue, or blue/data is unavailable."],
            ],
            [30, 52, 54, 38],
        ),
        Spacer(1, 7 * mm),
        flowchart(
            [
                ("Monthly touch", "Confirm with derived 5-month open OR close above blue.", PURPLE_DARK),
                ("Weekly touch", "Confirm with Monthly open OR close above blue.", BLUE),
                ("Daily touch", "Confirm with Weekly open OR close above blue.", GREEN),
            ]
        ),
        Spacer(1, 6 * mm),
        callout(
            "Review point for Krishna",
            "The 5-month candle is derived by grouping source candles into stable calendar buckets. Confirm whether Krishna intended fixed Jan-May / Jun-Oct style buckets, a rolling 5-month candle, or the broker chart's own aggregation.",
            AMBER_BG,
            YELLOW,
        ),
        PageBreak(),
    ]

    story += [
        heading("7", "Blue-to-purple approach"),
        p(
            "This gate attempts to encode Krishna's observation that blue should be above purple and the candle should come from the blue area toward purple. It is the least visually exact part of the current rules and deserves deliberate review.",
        ),
        horizontal_flow(
            [
                ("Prior candle(s)", "At least one prior close is within 3% below blue or above it", BLUE),
                ("Latest candle", "Close moves below prior maximum OR comes within 3% above purple", PURPLE_DARK),
                ("Result", "Both conditions true = approach pass", GREEN),
            ]
        ),
        Spacer(1, 8 * mm),
        p("Current parameters", "H2x"),
        data_table(
            ["Parameter", "Current value", "Why it exists", "Potential improvement"],
            [
                ["Lookback", "Latest 3 touch-timeframe candles", "Supports the accepted 'last 2-3 candles' guidance.", "Allow 2 or 3 as a UI/config choice."],
                ["Blue proximity", "3%", "Allows a candle to be treated as coming from the blue area.", "Replace percentage with ATR or candle-range distance."],
                ["Movement toward purple", "Latest close declines from prior max OR is within 3% above EMA9", "Captures pullback toward purple.", "Use distance-to-purple that must contract candle by candle."],
            ],
            [30, 29, 57, 58],
        ),
        Spacer(1, 6 * mm),
        callout(
            "Important",
            "Both Step 1 distance and the directional approach currently use a 3% ceiling, but they measure different things: Step 1 measures candle-range distance to EMA9; approach measures movement from blue toward purple.",
            BLUE_BG,
            BLUE,
        ),
        PageBreak(),
    ]

    story += [
        heading("8", "Entry lifecycle - Candle 1, Candle 2, Candle 3+"),
        p("After a valid purple touch, the setup remains pending until lower-timeframe candles establish a reference and confirmation can be evaluated."),
        EntryTimelineSketch(),
        Spacer(1, 6 * mm),
        data_table(
            ["Stage", "Implemented behavior", "Status"],
            [
                ["Candle 1", "Map the higher-timeframe touch period onto the entry timeframe. Record Candle 1 low.", "wait"],
                ["Candle 2", "Wait for the next entry-timeframe candle to close. Record Candle 2 low.", "wait until closed"],
                ["Candle 3 onward", "Entry rules may qualify. Continuously check whether any later candle breaks the lower of Candle 1 and Candle 2 lows.", "entry_candidate or wait"],
                ["Invalidation", "Any later candle low < min(Candle 1 low, Candle 2 low).", "discarded"],
                ["Expiry", "No fixed 10-candle expiry is used.", "remains active until discarded or entered"],
            ],
            [30, 106, 38],
        ),
        Spacer(1, 6 * mm),
        callout(
            "Implemented invalidation level",
            "The UI reports min(Candle 1 low, Candle 2 low) as the effective threshold. Breaking only the higher reference low does not discard the setup. The first later candle below the lower reference low records the invalidation timestamp.",
            PURPLE_BG,
            PURPLE_DARK,
        ),
        PageBreak(),
    ]

    story += [
        heading("9", "Early entry versus final entry"),
        p("Both entry styles use the profile matrix, but final entry has one additional mandatory relationship."),
        data_table(
            ["Rule", "Early entry", "Final entry"],
            [
                ["Entry timeframe", "Monthly 2H; Weekly 30m; Daily 10m", "Monthly Daily; Weekly 2H; Daily 30m"],
                ["Candle 2 closed", "Mandatory", "Mandatory"],
                ["No C1/C2 low break", "Mandatory", "Mandatory"],
                ["Entry close above yellow", "Mandatory", "Mandatory"],
                ["Blue above purple on entry timeframe", "Mandatory", "Mandatory"],
                ["Yellow above brown VWMA20", "Optional context", "Mandatory"],
                ["RSI bullish divergence", "Optional quality context", "Optional quality context"],
                ["Close above blue", "Rare stronger context", "Rare stronger context"],
            ],
            [58, 58, 58],
        ),
        Spacer(1, 7 * mm),
        horizontal_flow(
            [
                ("Early candidate", "Close > yellow + blue > purple + lifecycle valid", BLUE),
                ("Final candidate", "Early logic + yellow > brown", GREEN),
                ("No certainty", "Candidate requires human review and risk context", YELLOW),
            ]
        ),
        Spacer(1, 7 * mm),
        callout(
            "Why a strict candidate can still show Wait",
            "Higher-timeframe qualification and lower-timeframe entry confirmation are separate. A stock can pass every strict filter while its lower-timeframe close remains below yellow or Candle 2 has not closed.",
            BLUE_BG,
            BLUE,
        ),
        PageBreak(),
    ]

    story += [
        heading("10", "Alert, trade ID, and exit lifecycle"),
        horizontal_flow(
            [
                ("Entry event", "Fresh early/final candidate during allowed hours", GREEN),
                ("Open trade ID", "KPT-date-symbol-profile-kind", BLUE),
                ("Exit event", "Exit-TF close < yellow", RED),
            ]
        ),
        Spacer(1, 8 * mm),
        data_table(
            ["Item", "Meaning", "Persistence behavior"],
            [
                ["Entry Alert History", "All historical entry events created by the scanner.", "Rows remain after exit; history is not deleted."],
                ["Open Trade IDs", "Entry events that have not yet received an exit event.", "Only these IDs are checked by the exit scan."],
                ["Exit Alerts", "Historical exit events linked to trade IDs.", "Closes the matching open ID."],
                ["Duplicate suppression", "Same symbol + touch profile + early/final kind while already open.", "Does not create another trade ID on each monitor pass."],
            ],
            [38, 76, 60],
        ),
        Spacer(1, 6 * mm),
        callout(
            "Count reconciliation",
            "Historical entry events = open trade IDs + closed trade IDs. Closed trade IDs should equal exit alerts. Example: 91 entries, 91 open, 0 exits is internally consistent.",
            GREEN_BG,
            GREEN,
        ),
        Spacer(1, 5 * mm),
        p("Exit rule", "H2x"),
        p(
            "For both early and final entries, the scanner loads the configured exit timeframe and checks the latest candle. An exit candidate is generated only when that candle closes below the yellow Chande Kroll line. It does not scan unrelated symbols for exits.",
        ),
        PageBreak(),
    ]

    story += [
        heading("11", "What happens when Run scanner now is clicked"),
        flowchart(
            [
                ("Refresh queued", "The bulk job count is unique market targets multiplied by source timeframes. 2-hour analysis uses downloaded 1-hour source candles.", BLUE),
                ("Refresh completed", "Completed/total means broker/cache refresh ended. It is not the end of setup analysis.", PURPLE_DARK),
                ("Analysis running", "The service scans all selected symbols and profiles, calculates indicators, applies gates, and checks open exits.", YELLOW),
                ("Analysis completed", "UI renders Step 1, strict candidates, early/final-ready counts, and newly created alert counts.", GREEN),
            ]
        ),
        Spacer(1, 7 * mm),
        data_table(
            ["Control/state", "Behavior"],
            [
                ["Run scanner now disabled", "A refresh or analysis request is still in flight. It re-enables only when both phases finish or fail."],
                ["Cancel current scan", "Stops the bulk refresh between broker requests. It cannot interrupt an already executing broker request or the later pure analysis phase."],
                ["Start market monitor", "Starts recurring market-hour profile scans at independent Monthly/Weekly/Daily intervals."],
                ["Force cached-data outside market hours", "Off: analyze only. On: also permits test entry/exit trade IDs and optional Telegram messages from cached candles."],
            ],
            [55, 119],
        ),
        Spacer(1, 6 * mm),
        callout(
            "Normal setting",
            "Keep Force off. Outside NSE market hours, candidate tables can refresh but new alert events, trade IDs, exit events, and Telegram messages are intentionally paused.",
            AMBER_BG,
            YELLOW,
        ),
        PageBreak(),
    ]

    story += [
        heading("12", "How to read the UI counts"),
        data_table(
            ["UI count", "Definition", "Does it create an alert?"],
            [
                ["Analyzed", "Stock-profile rows with enough source data to run strict scanning.", "No"],
                ["Step 1 touches", "Purple EMA9 range distance <=3.00% before mandatory filters.", "No"],
                ["Strict candidates", "Touch/near-touch rows that also pass all mandatory higher-timeframe gates.", "Not by itself"],
                ["Early ready", "Strict rows whose early entry snapshot is entry_candidate.", "Eligible during market hours"],
                ["Final ready", "Strict rows whose final entry snapshot is entry_candidate.", "Eligible during market hours"],
                ["New entry alerts", "Fresh eligible entries actually persisted this run.", "Yes"],
                ["New exit alerts", "Open IDs whose exit condition was persisted this run.", "Yes"],
            ],
            [39, 95, 40],
        ),
        Spacer(1, 7 * mm),
        p("Count relationship", "H2x"),
        p("Step 1 rows are the widest set. Strict candidates are a subset. Entry-ready rows are a subset of strict candidates. New alerts are only fresh, eligible entry-ready events created during allowed hours."),
        Spacer(1, 6 * mm),
        callout(
            "Counts are time-sensitive",
            "The reference numbers document one cached-data run. They will change as candles update, F&amp;O membership changes, data becomes available, and rule parameters are refined.",
            BLUE_BG,
            BLUE,
        ),
        PageBreak(),
    ]

    story += [
        heading("13", "Data requirements and lookback"),
        data_table(
            ["Data", "Why needed", "Current practical window"],
            [
                ["Monthly", "EMA89, purple distance, Chande Kroll, VWMA20, structure.", "3000 calendar days for the Monthly profile."],
                ["Weekly", "Weekly touch and Daily-profile higher confirmation.", "730 days for Weekly; all-profile refresh requests 3000 days."],
                ["Daily", "Daily touch, Monthly final entry, and derived higher aggregations.", "365 days minimum; all-profile refresh requests 3000 days."],
                ["120 minute", "Monthly early/exit and Weekly final entry.", "Broker limits may constrain historical intraday depth."],
                ["30 minute", "Weekly early/exit.", "Enough for indicators plus lifecycle mapping."],
                ["10 minute", "Daily early/final/exit.", "Enough for indicators plus lifecycle mapping."],
            ],
            [34, 84, 56],
        ),
        Spacer(1, 7 * mm),
        p("Why EMA89 can show Unknown", "H2x"),
        p(
            "The Step 1 audit can run with as few as 9 touch candles, but EMA89 needs at least 89 candles on that exact timeframe. Downloading 3000 daily days does not automatically guarantee 89 valid Monthly rows if the symbol history is shorter, CSV derivation is missing, or a timeframe file was not refreshed. Strict qualification rejects Unknown EMA89.",
        ),
        Spacer(1, 5 * mm),
        callout(
            "Data quality principle",
            "Missing or insufficient values are displayed as blockers/warnings. The scanner should not manufacture indicator values or silently treat Unknown as Pass.",
            RED_BG,
            RED,
        ),
        PageBreak(),
    ]

    story += [
        heading("14", "Worked decision examples"),
        data_table(
            ["Scenario", "Observed state", "Expected result"],
            [
                ["A - Step 1 only", "Weekly EMA9 lies inside candle range, but close is below EMA89.", "Appears in Step 1 with EMA89 blocker; absent from strict candidates; no alert."],
                ["B - Strict but waiting", "Monthly purple distance <=3%; all strict gates pass; 2H close remains below yellow.", "Appears in strict candidates; early=wait; final depends on Daily; no early alert."],
                ["C - Early ready", "Weekly strict gates pass; Candle 2 closed; no low break; 30m close > yellow; blue > purple.", "early=entry_candidate. Fresh market-hour run can create early trade ID."],
                ["D - Final blocked", "Final timeframe close > yellow but yellow <= brown VWMA20.", "final=wait because yellow-above-brown is mandatory."],
                ["E - Discarded", "From Candle 3 onward, an entry-timeframe candle breaks the lower of Candle 1 and Candle 2 lows.", "Entry status=discarded; setup cannot create an entry alert."],
                ["F - Exit", "Open trade ID exists and latest configured exit-timeframe candle closes below yellow.", "Exit alert is persisted and matching trade ID is closed."],
            ],
            [38, 85, 51],
        ),
        Spacer(1, 7 * mm),
        p("Telegram delivery", "H2x"),
        p(
            "When Telegram is configured and enabled, newly persisted entry and exit events can be sent to the configured chat. Candidate rows that do not create a fresh database event are not repeatedly sent. Market-hour gating and open-trade duplicate suppression reduce notification volume.",
        ),
        Spacer(1, 5 * mm),
        callout(
            "Audit sequence for an unexpected alert",
            "Open the trade ID -> identify profile and entry kind -> inspect Step 1 range distance -> inspect each strict gate -> inspect Candle 1/Candle 2 lifecycle -> verify entry close/yellow/blue/brown -> confirm market-hour and Telegram status.",
            PURPLE_BG,
            PURPLE_DARK,
        ),
        PageBreak(),
    ]

    story += [
        heading("15", "Review checklist for Krishna"),
        p("These questions focus on places where visual chart judgment was translated into deterministic code."),
        data_table(
            ["Topic", "Current implementation", "Review decision"],
            [
                ["Purple touch", "Range distance <=3%; UI bands through 0.00%, 0.20%, 0.50%, 1.00%, 1.50%, 2.00%, and 3.00%.", "Compare candidate quality by distance band."],
                ["Touch timing", "Only the latest higher-timeframe candle qualifies.", "Confirm whether a prior touch should remain active while lifecycle waits."],
                ["Blue approach", "Latest 3 candles; prior close within 3% of blue; latest moves toward purple.", "Confirm geometry and replace 3% if needed."],
                ["5-month candle", "Stable calendar-derived 5-month buckets.", "Confirm calendar versus rolling construction."],
                ["Higher confirmation", "Latest confirmation candle only.", "Confirm open/close rules and whether incomplete candles are allowed."],
                ["EMA26", "Strict close >= EMA26; EMA9 > EMA26 adds context.", "Confirm whether both relationships are mandatory."],
                ["Market structure", "Explicit downtrend blocks.", "Confirm whether this filter should remain mandatory."],
                ["C1/C2 invalidation", "Any later low below the lower of the two reference lows discards setup.", "Confirm wick-low versus candle-close break."],
                ["Exit", "Exit-timeframe candle close below yellow.", "Confirm whether re-entry is allowed after an exit and new touch."],
            ],
            [38, 82, 54],
        ),
        Spacer(1, 7 * mm),
        callout(
            "Recommended review method",
            "Choose 10 known charts: clear passes, clear failures, and ambiguous cases. Record Krishna's expected result for every gate, then compare the UI row field-by-field before changing thresholds.",
            GREEN_BG,
            GREEN,
        ),
        PageBreak(),
    ]

    story += [
        heading("16", "Implementation map and status glossary"),
        p("Primary code locations", "H2x"),
        data_table(
            ["Area", "File / function"],
            [
                ["Profiles and all technical rules", "trading_analysis/analysis/krishna_setup.py"],
                ["Step 1", "scan_krishna_purple_step1_candidate()"],
                ["Strict scan", "scan_krishna_purple_touch_setup()"],
                ["Entry lifecycle", "_purple_entry_snapshot()"],
                ["Higher confirmation", "_purple_higher_confirmation()"],
                ["Blue approach", "_approaches_purple_from_blue()"],
                ["Scan orchestration", "AnalysisService.scan_krishna_purple_touch_alerts()"],
                ["Trade IDs and persistence", "KrishnaPurpleAlertRepository in trading_analysis/storage/sqlite.py"],
                ["Web presentation", "web/index.html and web/app.js"],
            ],
            [57, 117],
        ),
        Spacer(1, 7 * mm),
        p("Status glossary", "H2x"),
        data_table(
            ["Status", "Meaning"],
            [
                ["step1_only", "Purple range distance is <=3%, but one or more mandatory strict gates failed/are unavailable."],
                ["qualified", "All higher-timeframe strict gates passed; entry status is evaluated separately."],
                ["wait", "Setup remains valid but entry confirmation is not yet complete."],
                ["entry_candidate", "All rules for that early/final entry snapshot currently pass."],
                ["discarded", "A candle from Candle 3 onward broke the lower of Candle 1 and Candle 2 lows."],
                ["missing", "Required cached timeframe candles do not exist."],
                ["insufficient", "Some candles exist, but not enough to calculate the required indicators."],
                ["open", "Entry event has a trade ID and no matching exit yet."],
                ["closed", "Matching exit event has been created."],
            ],
            [42, 132],
        ),
        Spacer(1, 8 * mm),
        callout(
            "Document maintenance",
            "Regenerate this PDF after material rule, timeframe, status, or alert-lifecycle changes. The generator is scripts/generate_purple_touch_guide.py and the output is docs/Purple_Touch_Implementation_Guide.pdf.",
            PURPLE_BG,
            PURPLE_DARK,
        ),
    ]
    return story


def main() -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    document = SimpleDocTemplate(
        str(OUTPUT),
        pagesize=A4,
        rightMargin=18 * mm,
        leftMargin=18 * mm,
        topMargin=19 * mm,
        bottomMargin=18 * mm,
        title="Purple Touch Implementation and Review Guide",
        author="TradeAnalysisWithAjay",
        subject="Read-only Purple Touch scanner implementation guide",
    )
    document.build(build_story(), onFirstPage=page_header_footer, onLaterPages=page_header_footer)
    print(OUTPUT)


if __name__ == "__main__":
    main()
