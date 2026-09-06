from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LOCAL_PDF_DEPS = ROOT / "tmp" / "pdf_deps"
if LOCAL_PDF_DEPS.exists():
    sys.path.insert(0, str(LOCAL_PDF_DEPS))

from reportlab.lib import colors
from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Flowable,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


OUTPUT = ROOT / "docs" / "Weekly_Purple_Touch_Validation_Guide_v1_1.pdf"

INK = HexColor("#182231")
MUTED = HexColor("#5F6D80")
LINE = HexColor("#D6DEE8")
SOFT = HexColor("#F5F7FA")
PURPLE = HexColor("#B77BCB")
PURPLE_DARK = HexColor("#75408B")
BLUE = HexColor("#3976D8")
YELLOW = HexColor("#F0A500")
BROWN = HexColor("#7B2E2E")
LIGHT_GREEN = HexColor("#77BD83")
GREEN = HexColor("#16856B")
RED = HexColor("#C64747")
BLACK = HexColor("#111827")
AMBER = HexColor("#B56D00")
GREEN_BG = HexColor("#EAF7F1")
RED_BG = HexColor("#FDEEEE")
BLUE_BG = HexColor("#EAF2FF")
PURPLE_BG = HexColor("#F6EFF8")
AMBER_BG = HexColor("#FFF5D9")


styles = getSampleStyleSheet()
styles.add(ParagraphStyle(name="CoverTitle", parent=styles["Title"], fontName="Helvetica-Bold", fontSize=27, leading=31, textColor=INK, alignment=TA_LEFT, spaceAfter=10))
styles.add(ParagraphStyle(name="CoverSub", parent=styles["Normal"], fontName="Helvetica", fontSize=12.5, leading=18, textColor=MUTED, spaceAfter=8))
styles.add(ParagraphStyle(name="H1x", parent=styles["Heading1"], fontName="Helvetica-Bold", fontSize=19, leading=23, textColor=INK, spaceAfter=8))
styles.add(ParagraphStyle(name="H2x", parent=styles["Heading2"], fontName="Helvetica-Bold", fontSize=12.5, leading=16, textColor=INK, spaceBefore=6, spaceAfter=5))
styles.add(ParagraphStyle(name="Bodyx", parent=styles["BodyText"], fontName="Helvetica", fontSize=9.1, leading=13, textColor=INK, spaceAfter=5))
styles.add(ParagraphStyle(name="Smallx", parent=styles["BodyText"], fontName="Helvetica", fontSize=7.4, leading=9.7, textColor=MUTED))
styles.add(ParagraphStyle(name="CaseTitle", parent=styles["BodyText"], fontName="Helvetica-Bold", fontSize=8.5, leading=10.5, textColor=INK))
styles.add(ParagraphStyle(name="CaseVerdict", parent=styles["BodyText"], fontName="Helvetica-Bold", fontSize=6.9, leading=8.1, textColor=INK, alignment=TA_CENTER))
styles.add(ParagraphStyle(name="CaseBody", parent=styles["BodyText"], fontName="Helvetica", fontSize=7.1, leading=9.4, textColor=INK))
styles.add(ParagraphStyle(name="BoxTitle", parent=styles["BodyText"], fontName="Helvetica-Bold", fontSize=8.5, leading=10.5, textColor=INK, alignment=TA_CENTER))
styles.add(ParagraphStyle(name="BoxBody", parent=styles["BodyText"], fontName="Helvetica", fontSize=7.1, leading=9.3, textColor=MUTED, alignment=TA_CENTER))
styles.add(ParagraphStyle(name="TableHead", parent=styles["BodyText"], fontName="Helvetica-Bold", fontSize=7.3, leading=9.2, textColor=colors.white))
styles.add(ParagraphStyle(name="TableCell", parent=styles["BodyText"], fontName="Helvetica", fontSize=7.1, leading=9.5, textColor=INK))
styles.add(ParagraphStyle(name="Callout", parent=styles["BodyText"], fontName="Helvetica", fontSize=8.3, leading=11.6, textColor=INK))
styles.add(ParagraphStyle(name="Codex", parent=styles["Code"], fontName="Courier", fontSize=7.3, leading=10, textColor=INK))


def p(text: str, style: str = "Bodyx") -> Paragraph:
    return Paragraph(text, styles[style])


def heading(number: str, title: str) -> Paragraph:
    return p(f"<font color='#75408B'>{number}</font>  {title}", "H1x")


def callout(title: str, body: str, background=BLUE_BG, accent=BLUE) -> Table:
    box = Table([[p(f"<b>{title}</b><br/>{body}", "Callout")]], colWidths=[174 * mm])
    box.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), background),
        ("BOX", (0, 0), (-1, -1), 0.7, accent),
        ("LINEBEFORE", (0, 0), (0, -1), 4, accent),
        ("LEFTPADDING", (0, 0), (-1, -1), 9),
        ("RIGHTPADDING", (0, 0), (-1, -1), 9),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    return box


def data_table(headers: list[str], rows: list[list[str]], widths: list[float]) -> Table:
    values = [[p(value, "TableHead") for value in headers]]
    values.extend([[p(value, "TableCell") for value in row] for row in rows])
    table = Table(values, colWidths=[value * mm for value in widths], repeatRows=1, hAlign="LEFT")
    commands = [
        ("BACKGROUND", (0, 0), (-1, 0), INK),
        ("GRID", (0, 0), (-1, -1), 0.35, LINE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4.5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4.5),
        ("TOPPADDING", (0, 0), (-1, -1), 4.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4.5),
    ]
    for index in range(1, len(values)):
        if index % 2 == 0:
            commands.append(("BACKGROUND", (0, index), (-1, index), SOFT))
    table.setStyle(TableStyle(commands))
    return table


def horizontal_flow(items: list[tuple[str, str, colors.Color]]) -> Table:
    cells: list[object] = []
    widths: list[float] = []
    item_width = 36 * mm
    for index, (title, body, accent) in enumerate(items):
        box = Table([[p(title, "BoxTitle")], [p(body, "BoxBody")]], colWidths=[item_width])
        box.setStyle(TableStyle([
            ("BOX", (0, 0), (-1, -1), 1, accent),
            ("BACKGROUND", (0, 0), (-1, 0), accent.clone(alpha=0.13)),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]))
        cells.append(box)
        widths.append(item_width)
        if index < len(items) - 1:
            cells.append(p("&gt;", "BoxTitle"))
            widths.append(7 * mm)
    table = Table([cells], colWidths=widths, hAlign="CENTER")
    table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
    return table


class MiniChart(Flowable):
    def __init__(
        self,
        candles: list[tuple[float, float, float, float]],
        lines: list[tuple[str, colors.Color, list[float], float]],
        markers: list[tuple[str, float, float, colors.Color]] | None = None,
        width: float = 80 * mm,
        height: float = 40 * mm,
    ) -> None:
        super().__init__()
        self.candles = candles
        self.lines = lines
        self.markers = markers or []
        self.width = width
        self.height = height

    def draw(self) -> None:
        c = self.canv
        c.saveState()
        left = 5 * mm
        right = self.width - 5 * mm
        bottom = 5 * mm
        top = self.height - 5 * mm
        c.setFillColor(colors.white)
        c.setStrokeColor(LINE)
        c.roundRect(0, 0, self.width, self.height, 2 * mm, fill=1, stroke=1)
        c.setLineWidth(0.3)
        for index in range(1, 4):
            y = bottom + (top - bottom) * index / 4
            c.setStrokeColor(HexColor("#E9EDF2"))
            c.line(left, y, right, y)

        values: list[float] = []
        for open_, high, low, close in self.candles:
            values.extend([open_, high, low, close])
        for _name, _color, points, _line_width in self.lines:
            values.extend(points)
        if not values:
            values = [0, 1]
        minimum = min(values)
        maximum = max(values)
        padding = max((maximum - minimum) * 0.13, 1)
        minimum -= padding
        maximum += padding

        def py(value: float) -> float:
            return bottom + (value - minimum) / (maximum - minimum) * (top - bottom)

        count = max(len(self.candles), max((len(points) for _n, _c, points, _w in self.lines), default=1))

        def px(index: int) -> float:
            return left + (right - left) * (index + 0.5) / max(count, 1)

        for _name, color, points, line_width in self.lines:
            c.setStrokeColor(color)
            c.setLineWidth(line_width)
            if len(points) == 1:
                c.line(left, py(points[0]), right, py(points[0]))
            for index in range(len(points) - 1):
                c.line(px(index), py(points[index]), px(index + 1), py(points[index + 1]))

        body_width = min(5 * mm, (right - left) / max(count, 1) * 0.42)
        for index, (open_, high, low, close) in enumerate(self.candles):
            color = GREEN if close >= open_ else RED
            x = px(index)
            c.setStrokeColor(color)
            c.setFillColor(color)
            c.setLineWidth(0.8)
            c.line(x, py(low), x, py(high))
            y = min(py(open_), py(close))
            height = max(abs(py(close) - py(open_)), 1.2 * mm)
            c.rect(x - body_width / 2, y, body_width, height, fill=1, stroke=0)

        for label, x_index, value, color in self.markers:
            x = px(int(x_index))
            y = py(value)
            c.setStrokeColor(color)
            c.setFillColor(color)
            c.setLineWidth(1.1)
            c.circle(x, y, 1.2 * mm, fill=0, stroke=1)
            c.setFont("Helvetica-Bold", 5.8)
            c.drawString(min(x + 2 * mm, right - 11 * mm), min(y + 1.5 * mm, top - 2 * mm), label)

        legend_x = left
        c.setFont("Helvetica", 5.4)
        for name, color, _points, _line_width in self.lines:
            c.setStrokeColor(color)
            c.setLineWidth(1.4)
            c.line(legend_x, 2.7 * mm, legend_x + 5 * mm, 2.7 * mm)
            c.setFillColor(MUTED)
            c.drawString(legend_x + 6 * mm, 1.4 * mm, name)
            legend_x += (len(name) * 1.2 + 12) * mm
        c.restoreState()


def chart(
    candles: list[tuple[float, float, float, float]],
    purple: list[float] | None = None,
    blue: list[float] | None = None,
    yellow: list[float] | None = None,
    brown: list[float] | None = None,
    ema26: list[float] | None = None,
    ema89: list[float] | None = None,
    markers: list[tuple[str, float, float, colors.Color]] | None = None,
) -> MiniChart:
    lines = []
    for name, color, points, width in [
        ("Purple", PURPLE, purple, 2.0),
        ("Blue", BLUE, blue, 1.5),
        ("Yellow", YELLOW, yellow, 1.5),
        ("Brown", BROWN, brown, 1.5),
        ("EMA26", LIGHT_GREEN, ema26, 1.6),
        ("EMA89", BLACK, ema89, 1.8),
    ]:
        if points:
            lines.append((name, color, points, width))
    return MiniChart(candles, lines, markers=markers)


def case_card(title: str, verdict: str, picture: MiniChart, body: str) -> Table:
    passed = verdict.startswith("PASS") or verdict.startswith("VALID") or verdict.startswith("WAIT") or verdict.startswith("OPEN")
    accent = GREEN if passed else RED
    background = GREEN_BG if passed else RED_BG
    header = Table([[p(title, "CaseTitle"), p(verdict, "CaseVerdict")]], colWidths=[54 * mm, 24 * mm])
    header.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), background),
        ("TEXTCOLOR", (1, 0), (1, 0), accent),
        ("ALIGN", (1, 0), (1, 0), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    card = Table([[header], [picture], [p(body, "CaseBody")]], colWidths=[80 * mm])
    card.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.7, LINE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, 1), 0),
        ("LEFTPADDING", (0, 2), (-1, 2), 5),
        ("RIGHTPADDING", (0, 2), (-1, 2), 5),
        ("TOPPADDING", (0, 2), (-1, 2), 4),
        ("BOTTOMPADDING", (0, 2), (-1, 2), 5),
    ]))
    return card


def case_grid(cards: list[Table]) -> Table:
    rows: list[list[object]] = []
    for index in range(0, len(cards), 2):
        row: list[object] = [cards[index]]
        row.append(cards[index + 1] if index + 1 < len(cards) else "")
        rows.append(row)
    grid = Table(rows, colWidths=[84 * mm, 84 * mm], hAlign="CENTER")
    grid.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 2),
        ("RIGHTPADDING", (0, 0), (-1, -1), 2),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    return grid


def page_header_footer(canvas, doc) -> None:
    canvas.saveState()
    width, height = A4
    canvas.setStrokeColor(LINE)
    canvas.setLineWidth(0.5)
    canvas.line(18 * mm, height - 14 * mm, width - 18 * mm, height - 14 * mm)
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(MUTED)
    canvas.drawString(18 * mm, height - 10.8 * mm, "Weekly Purple Touch Validation Guide")
    canvas.drawRightString(width - 18 * mm, 10 * mm, f"Page {doc.page}")
    canvas.restoreState()


def build_story() -> list[object]:
    story: list[object] = []

    story.extend([
        Spacer(1, 20 * mm),
        p("WEEKLY-ONLY VALIDATION MODE | VERSION 1.1", "Smallx"),
        p("Weekly Purple Touch<br/>Visual Audit Guide", "CoverTitle"),
        p("Valid, invalid, and boundary examples for Step 1, all-filter setups, entry signals, and exit signals", "CoverSub"),
        Spacer(1, 7 * mm),
        horizontal_flow([
            ("STEP 1", "Weekly candle within purple EMA9 zone", PURPLE),
            ("STRICT", "Trend, direction, approach, Monthly confirmation", BLUE),
            ("ENTRY", "30-minute early or 2-hour final", GREEN),
            ("EXIT", "30-minute close below yellow", RED),
        ]),
        Spacer(1, 10 * mm),
        callout(
            "Purpose",
            "Use this guide to compare Krishna's chart interpretation with the exact rules currently executed by the application. A PASS drawing means the illustrated rule passes; it does not promise a trade result or place an order.",
            PURPLE_BG,
            PURPLE_DARK,
        ),
        Spacer(1, 6 * mm),
        data_table(
            ["Scope", "Current behavior"],
            [
                ["Live scan", "Weekly only. Monthly and Daily live scans are paused."],
                ["Required candles", "Weekly touch + Monthly confirmation + 30-minute early/exit + 2-hour final."],
                ["History", "Existing Monthly and Daily rows remain stored but are not newly scanned or exit-monitored while paused."],
                ["Version", "Version 1.1. The original v1.0 PDF is retained for comparison."],
            ],
            [42, 132],
        ),
        Spacer(1, 7 * mm),
        p("Color key", "H2x"),
        data_table(
            ["Color", "Technical"],
            [
                ["<font color='#B77BCB'><b>Purple</b></font>", "EMA9"],
                ["<font color='#3976D8'><b>Blue</b></font>", "Upper Chande Kroll line"],
                ["<font color='#F0A500'><b>Yellow</b></font>", "Lower Chande Kroll line"],
                ["<font color='#7B2E2E'><b>Brown</b></font>", "VWMA20"],
                ["<font color='#77BD83'><b>Light green</b></font>", "EMA26"],
                ["<font color='#111827'><b>Black</b></font>", "EMA89"],
            ],
            [42, 132],
        ),
        PageBreak(),
    ])

    story.extend([
        heading("1", "Weekly data path and decision order"),
        p("Every Weekly live run refreshes and analyzes four supporting candle streams. The stages are sequential for a candidate; entry and exit checks use separate timeframes."),
        horizontal_flow([
            ("Weekly", "Touch and strict setup", PURPLE),
            ("Monthly", "Open OR close above blue", BLUE),
            ("30-minute", "Early entry and all Weekly exits", GREEN),
            ("2-hour", "Final entry", YELLOW),
        ]),
        Spacer(1, 6 * mm),
        data_table(
            ["Order", "Gate", "Pass requirement", "If it fails"],
            [
                ["1", "Step 1", "Latest Weekly wick/range physically touches purple; close is 0.00%-3.00% above it.", "No Step 1 row."],
                ["2", "All-filter setup", "All mandatory Weekly/Monthly trend and direction checks pass.", "Row stays Step 1 only."],
                ["3", "Entry snapshot", "C1 or C2 entry rules pass, or a later candle passes while the touch remains valid.", "Entry stays waiting, or touch later expires."],
                ["4", "Signal gate", "NSE market hours, or Force enabled; no duplicate trade row.", "No new trade ID or Telegram event."],
                ["5", "Exit", "Latest 30-minute candle closes strictly below yellow.", "Matching Weekly trade remains open."],
            ],
            [14, 34, 86, 40],
        ),
        Spacer(1, 6 * mm),
        callout(
            "Counts do not tally stage-for-stage",
            "Step 1 is the widest shortlist. Weekly Setups Passing All Filters is a stricter subset. Trades Triggered contains one open/closed row for each created entry signal. Exit Alerts apply only to open Weekly trade IDs.",
            AMBER_BG,
            AMBER,
        ),
        Spacer(1, 6 * mm),
        p("Minimum history used by the code", "H2x"),
        data_table(
            ["Check", "Minimum / practical requirement", "Why"],
            [
                ["Step 1", "9 Weekly candles", "Enough to calculate purple EMA9."],
                ["Strict Weekly", "Effectively 89 Weekly candles", "Configured minimum is 52, but black EMA89 is mandatory and unavailable before 89."],
                ["Monthly confirmation", "At least 19 Monthly candles", "Chande Kroll blue needs ATR10 plus stop-period history."],
                ["Early/final/exit", "At least 30 candles on each entry/exit timeframe", "The snapshot functions return insufficient before 30."],
            ],
            [43, 62, 69],
        ),
        PageBreak(),
    ])

    step1_cases = [
        case_card(
            "Exact range touch",
            "PASS",
            chart([(108, 112, 105, 110), (110, 114, 103, 106), (101, 103, 99, 102)], purple=[100, 100, 100], markers=[("wick touch", 2, 100, PURPLE_DARK)]),
            "Purple EMA9 lies inside the latest Weekly high/low range and the close is 2.00% above purple. Physical contact plus bullish close passes.",
        ),
        case_card(
            "Near touch above purple",
            "BLOCK",
            chart([(109, 113, 106, 111), (110, 112, 104, 106), (105, 109, 102, 104)], purple=[100, 100, 100], markers=[("nearest low", 2, 102, GREEN)]),
            "Invalid because neither the wick/range nor close touches purple. Being only 2.00% away does not count.",
        ),
        case_card(
            "Near touch below purple",
            "BLOCK",
            chart([(97, 99, 92, 94), (95, 99, 91, 97), (96, 98, 92, 94)], purple=[100, 100, 100], markers=[("nearest high", 2, 98, GREEN)]),
            "Invalid. The bullish Weekly setup does not accept a candle whose range and close remain below purple.",
        ),
        case_card(
            "Wick touches; close at 3%",
            "PASS BOUNDARY",
            chart([(108, 112, 105, 110), (106, 109, 102, 105), (101, 104, 99, 103)], purple=[100, 100, 100], markers=[("touch", 2, 100, PURPLE_DARK), ("close 3%", 2, 103, GREEN)]),
            "The wick physically crosses purple and the close is exactly 3.00% above it. Equality at the maximum is accepted.",
        ),
    ]
    story.extend([
        heading("2", "Step 1: valid Weekly purple-touch shapes"),
        p("Implemented test: <font name='Courier'>low &lt;= EMA9 &lt;= high AND EMA9 &lt;= close &lt;= EMA9 x 1.03</font>. The candle must physically touch/cross purple and close from 0.00% to 3.00% above it."),
        case_grid(step1_cases),
        Spacer(1, 4 * mm),
        callout("Important", "Step 1 requires physical contact and a bullish close, but it is still only the first gate. Black EMA89, EMA26, Weekly blue/purple direction, Monthly confirmation, structure, and entry readiness do not decide whether the Step 1 row exists.", PURPLE_BG, PURPLE_DARK),
        PageBreak(),
    ])

    step1_boundary_cases = [
        case_card(
            "Close exactly 3% above",
            "PASS 3.00%",
            chart([(108, 112, 106, 110), (105, 108, 102, 104), (101, 104, 99, 103)], purple=[100, 100, 100], markers=[("wick", 2, 100, PURPLE_DARK), ("close 3.00%", 2, 103, GREEN)]),
            "The wick touches purple and close = EMA9 x 1.03. Equality at the close-distance limit passes.",
        ),
        case_card(
            "Close just beyond 3%",
            "BLOCK 3.01%",
            chart([(108, 112, 106, 110), (105, 108, 102, 104), (101, 104, 99, 103.01)], purple=[100, 100, 100], markers=[("wick", 2, 100, PURPLE_DARK), ("close 3.01%", 2, 103.01, RED)]),
            "Physical wick contact is present, but a close even slightly more than 3.00% above purple is excluded.",
        ),
        case_card(
            "Wick touches; close below purple",
            "BLOCK",
            chart([(102, 105, 99, 103), (101, 104, 98, 100), (100, 102, 96, 98)], purple=[100, 100, 100], markers=[("touch", 2, 100, PURPLE_DARK), ("close below", 2, 98, RED)]),
            "The wick/range contains purple, but the close is below it. Bullish Step 1 requires close >= purple.",
        ),
        case_card(
            "Not enough Weekly history",
            "BLOCK",
            chart([(98, 103, 96, 101), (101, 104, 99, 100), (100, 102, 97, 99)], purple=[100, 100, 100]),
            "Fewer than 9 Weekly candles means EMA9/Step 1 cannot be evaluated, even if the visible latest candle appears to touch.",
        ),
    ]
    story.extend([
        heading("3", "Step 1: invalid and exact boundary cases"),
        case_grid(step1_boundary_cases),
        Spacer(1, 5 * mm),
        data_table(
            ["Displayed distance", "Included in Step 1?", "Meaning"],
            [
                ["Close 0.00%", "Yes, if range touches", "Close is exactly on purple and the candle range contains purple."],
                ["Close 0.01% to 3.00% above", "Yes, if range touches", "Wick/range physically contains purple; close remains inside the bullish distance limit."],
                ["Close > 3.00% above", "No", "Too far above purple even when the wick touches."],
                ["Close below purple", "No", "Below-purple cases are excluded from the bullish scan."],
                ["Range does not contain purple", "No", "Near touch is not a touch, regardless of small distance."],
            ],
            [48, 43, 83],
        ),
        PageBreak(),
    ])

    story.extend([
        heading("4", "Weekly Setups Passing All Filters: mandatory gates"),
        p("This UI section was called Latest Scan Candidates in v1.0. A row has passed every mandatory Weekly and Monthly setup check, but Early and Final may still show wait. A setup row is not yet an entry signal."),
        horizontal_flow([
            ("Touch", "Physical Weekly touch; close 0%-3% above", PURPLE),
            ("Direction", "Blue > purple and approach from blue", BLUE),
            ("Monthly", "Open OR close > Monthly blue", YELLOW),
            ("Trend", "Close >= EMA26; close > EMA89; not downtrend", GREEN),
        ]),
        Spacer(1, 7 * mm),
        case_grid([
            case_card(
                "Weekly setup checks pass",
                "PASS",
                chart(
                    [(118, 124, 114, 121), (121, 125, 112, 116), (103, 106, 101, 105)],
                    purple=[100, 101, 102], blue=[119, 117, 115], ema26=[92, 94, 96], ema89=[76, 78, 80],
                    markers=[("wick touch", 2, 102, GREEN)],
                ),
                "Physical Weekly touch, close within 3% above purple, blue above purple, approach from blue, close >= EMA26, close > EMA89, and structure not downtrend.",
            ),
            case_card(
                "Monthly confirmation also passes",
                "PASS",
                chart([(111, 116, 94, 99), (99, 114, 96, 112)], blue=[110, 110], markers=[("open > blue", 0, 111, GREEN), ("close > blue", 1, 112, GREEN)]),
                "The latest Monthly candle passes when its open OR close is strictly above its own blue Chande Kroll line. Only one of the two must be above.",
            ),
        ]),
        Spacer(1, 7 * mm),
        data_table(
            ["Mandatory check", "Exact implemented boundary", "Result when false/unavailable"],
            [
                ["Weekly touch", "Physical range touch; close from 0.00% to 3.00% above purple", "No all-filter setup"],
                ["Blue vs purple", "Blue > purple; equality fails", "No all-filter setup"],
                ["Approach", "Prior close >= 97% of current blue AND latest moves toward/within 3% of purple", "No all-filter setup"],
                ["Monthly confirmation", "Latest Monthly open > blue OR close > blue; equality fails", "No all-filter setup"],
                ["Close vs EMA26", "Close >= EMA26; equality passes", "No all-filter setup"],
                ["Close vs EMA89", "Close > EMA89; equality fails", "No all-filter setup"],
                ["Structure", "Only explicit downtrend blocks", "No all-filter setup"],
            ],
            [52, 79, 43],
        ),
        Spacer(1, 4 * mm),
        callout("What 'No strict candidate' meant in v1.0", "The clearer v1.1 wording is 'No all-filter setup.' It means at least one mandatory Weekly/Monthly setup check failed or lacked data. The stock may still appear in Step 1 with a blocker, but it is excluded from the all-filter list and cannot create an entry signal in that scan. It does not mean bearish, sell, or exit.", BLUE_BG, BLUE),
        PageBreak(),
    ])

    strict_invalid = [
        case_card(
            "Blue equals purple",
            "BLOCK",
            chart([(108, 112, 103, 109), (109, 111, 102, 105), (105, 108, 99, 102)], purple=[100, 100, 100], blue=[100, 100, 100], ema26=[92, 93, 94], ema89=[78, 79, 80]),
            "Blue must be strictly above purple. Equality does not pass.",
        ),
        case_card(
            "No blue-to-purple approach",
            "BLOCK",
            chart([(103, 108, 100, 106), (105, 110, 102, 108), (101, 104, 99, 102)], purple=[100, 100, 100], blue=[118, 118, 118], ema26=[91, 93, 95], ema89=[77, 79, 81]),
            "The latest candle physically touches and closes within 3% above purple, but no prior close in the 2-3 candle window came from at least 97% of the blue-line area.",
        ),
        case_card(
            "Close below EMA26",
            "BLOCK",
            chart([(108, 111, 103, 106), (106, 109, 100, 103), (101, 104, 99, 100.5)], purple=[100, 100, 100], blue=[115, 113, 111], ema26=[101, 101.5, 102], ema89=[80, 81, 82]),
            "The touch can pass, but latest Weekly close below EMA26 blocks strict qualification.",
        ),
        case_card(
            "Close equals EMA89",
            "BLOCK",
            chart([(106, 109, 102, 105), (104, 107, 100, 102), (102, 104, 96, 100)], purple=[98, 99, 100], blue=[112, 110, 108], ema26=[94, 95, 96], ema89=[100, 100, 100]),
            "Black EMA89 is strict: close must be greater than EMA89. Equality fails.",
        ),
    ]
    story.extend([
        heading("5", "Weekly Setups Passing All Filters: common blockers"),
        case_grid(strict_invalid),
        Spacer(1, 5 * mm),
        callout("Data blocker", "Although strict scan configuration says 52 Weekly candles, black EMA89 is mandatory. With 52-88 Weekly candles EMA89 is unavailable, so no Latest Scan Candidate is returned.", AMBER_BG, AMBER),
        PageBreak(),
    ])

    strict_boundaries = [
        case_card(
            "Monthly open above blue",
            "PASS",
            chart([(111, 116, 94, 99)], blue=[110], markers=[("open 111", 0, 111, GREEN)]),
            "Weekly confirmation passes when Monthly open > Monthly blue, even if Monthly close finishes below blue.",
        ),
        case_card(
            "Monthly close equals blue",
            "BLOCK",
            chart([(98, 112, 95, 110)], blue=[110], markers=[("equal", 0, 110, RED)]),
            "Open and close must not both be at/below blue. Equality alone is not above.",
        ),
        case_card(
            "Weekly close equals EMA26",
            "PASS",
            chart([(107, 110, 101, 103), (105, 107, 100, 102), (102, 104, 98, 100)], purple=[99, 99.5, 100], blue=[113, 111, 109], ema26=[98, 99, 100], ema89=[80, 81, 82]),
            "EMA26 uses >=, so close exactly at EMA26 passes this mandatory check.",
        ),
        case_card(
            "EMA9 not above EMA26",
            "PASS WITH WARNING",
            chart([(106, 109, 102, 105), (104, 107, 100, 102), (102, 104, 98, 101)], purple=[101, 101, 101], blue=[113, 111, 109], ema26=[102, 102, 101], ema89=[80, 81, 82]),
            "Purple EMA9 > EMA26 improves score but is not mandatory. The row may still qualify if latest close >= EMA26 and every mandatory gate passes.",
        ),
    ]
    story.extend([
        heading("6", "Strict boundaries and non-blocking context"),
        case_grid(strict_boundaries),
        Spacer(1, 5 * mm),
        data_table(
            ["Context shown in UI", "Mandatory?", "Current effect"],
            [
                ["EMA9 above EMA26", "No", "Adds score when true; warning when false."],
                ["Yellow below EMA26", "No", "Adds score when true; warning when false."],
                ["Brown VWMA20 vs EMA26", "No", "Descriptive context only at strict stage."],
                ["RSI bullish divergence", "No", "Entry quality context only."],
                ["Close above blue", "No", "Rare/stronger context only."],
                ["Ichimoku, VWAP, Donchian", "Ignored", "Not used by this Purple Touch setup."],
            ],
            [63, 32, 79],
        ),
        PageBreak(),
    ])

    lifecycle_cases = [
        case_card(
            "Candle 1 only",
            "ENTRY ELIGIBLE",
            chart([(101, 104, 98, 102)], purple=[100], markers=[("C1", 0, 100, PURPLE_DARK)]),
            "C1 is the mapped entry-timeframe touch candle. It can create Early/Final entry immediately when that timeframe's entry technicals pass; C2 is not required first.",
        ),
        case_card(
            "Candle 2 closed",
            "ENTRY ELIGIBLE",
            chart([(101, 104, 98, 102), (102, 106, 99, 105)], purple=[100, 100], markers=[("C1 low 98", 0, 98, PURPLE_DARK), ("C2 low 99", 1, 99, BLUE)]),
            "C2 can also create entry immediately when its technicals pass. After it closes, min(C1 low, C2 low) = 98 becomes the validity level for C3 onward.",
        ),
        case_card(
            "C3 low equals reference low",
            "STILL VALID",
            chart([(101, 104, 98, 102), (102, 106, 99, 105), (104, 107, 98, 106)], purple=[100, 100, 100], markers=[("equal 98", 2, 98, GREEN)]),
            "From C3 onward, low < reference low ends touch validity. Equality does not expire the touch.",
        ),
        case_card(
            "Any C3+ low breaks reference",
            "DISCARDED",
            chart([(101, 104, 98, 102), (102, 106, 99, 105), (104, 107, 97.99, 106), (106, 109, 103, 108)], purple=[100, 100, 100, 100], markers=[("break", 2, 97.99, RED)]),
            "Every candle from C3 onward is checked. The first low below min(C1, C2) ends that touch's validity for future entries; valid C1/C2 entry signals already created are not deleted.",
        ),
    ]
    story.extend([
        heading("7", "Entry validity: mapping C1, C2, and C3 onward"),
        case_grid(lifecycle_cases),
        Spacer(1, 5 * mm),
        callout("C1/C2 correction in v1.1", "C1 and C2 are both entry-eligible. The C3+ lower-low rule controls how long the purple touch can produce future entry signals; it does not erase C1/C2 signals or close trade IDs already created.", GREEN_BG, GREEN),
        PageBreak(),
    ])

    early_cases = [
        case_card(
            "Candle 1 close above yellow",
            "ENTRY CANDIDATE",
            chart([(101, 105, 99, 104)], yellow=[102], markers=[("C1 close > yellow", 0, 104, GREEN)]),
            "C1 can create an Early Entry candidate immediately. On 30-minute, close > yellow is the only entry technical; blue/purple and yellow/brown are ignored.",
        ),
        case_card(
            "Close equals yellow",
            "WAIT",
            chart([(101, 104, 98, 102), (102, 106, 99, 105), (105, 108, 103, 106)], yellow=[101, 103, 106]),
            "Entry requires close strictly above yellow. Equality does not create an Early candidate.",
        ),
        case_card(
            "Wick above yellow, close below",
            "WAIT",
            chart([(101, 104, 98, 102), (102, 106, 99, 105), (104, 109, 102, 103)], yellow=[101, 103, 105]),
            "Only the 30-minute close is tested. A wick above yellow is not enough.",
        ),
        case_card(
            "Candle 2 close above yellow",
            "ENTRY CANDIDATE",
            chart([(101, 103, 99, 101), (101, 106, 100, 105)], yellow=[102, 102], markers=[("C2 close > yellow", 1, 105, GREEN)]),
            "C2 is also entry-eligible. Its close above yellow can create Early Entry before any C3 validity check is needed.",
        ),
    ]
    story.extend([
        heading("8", "Weekly Early Entry Alert technicals (30-minute)"),
        case_grid(early_cases),
        Spacer(1, 5 * mm),
        callout("Early Entry rule", "Mandatory: 30-minute close > yellow while the Weekly touch is valid. Ignore 30-minute blue > purple and yellow > brown. C1 and C2 may each qualify immediately.", BLUE_BG, BLUE),
        PageBreak(),
    ])

    final_cases = [
        case_card(
            "Candle 1 final technicals pass",
            "ENTRY CANDIDATE",
            chart([(105, 111, 103, 110)], yellow=[107], brown=[105], markers=[("close > yellow", 0, 110, GREEN)]),
            "C1 can create Final Entry immediately when 2-hour close > yellow and yellow > brown. Blue/purple is ignored.",
        ),
        case_card(
            "Yellow equals brown",
            "WAIT",
            chart([(101, 104, 98, 102), (102, 106, 99, 105), (105, 111, 103, 110)], yellow=[101, 104, 107], brown=[101, 104, 107]),
            "Final requires yellow strictly above brown VWMA20. Equality fails.",
        ),
        case_card(
            "Close above yellow; yellow below brown",
            "WAIT",
            chart([(101, 104, 98, 102), (102, 106, 99, 105), (105, 111, 103, 110)], yellow=[101, 103, 106], brown=[102, 105, 108]),
            "A 2-hour close above yellow is not enough for Final while yellow remains below brown.",
        ),
        case_card(
            "Candle 2 final technicals pass",
            "ENTRY CANDIDATE",
            chart([(101, 104, 99, 102), (103, 109, 101, 108)], yellow=[101, 105], brown=[99, 103], markers=[("C2 close > yellow", 1, 108, GREEN)]),
            "C2 can also create Final Entry before C3. It must close above yellow while yellow remains above brown.",
        ),
    ]
    story.extend([
        heading("9", "Weekly Final Entry Alert technicals (2-hour)"),
        case_grid(final_cases),
        Spacer(1, 5 * mm),
        data_table(
            ["Rule", "Early 30-minute", "Final 2-hour"],
            [
                ["C1 or C2 may qualify", "Yes", "Yes"],
                ["From C3: touch still valid", "Mandatory", "Mandatory"],
                ["Close > yellow", "Mandatory", "Mandatory"],
                ["Blue > purple EMA9", "Ignored", "Ignored"],
                ["Yellow > brown VWMA20", "Ignored", "Mandatory"],
                ["RSI bullish divergence", "Optional", "Optional"],
                ["Close > blue", "Optional stronger context", "Optional stronger context"],
            ],
            [70, 52, 52],
        ),
        PageBreak(),
    ])

    story.extend([
        heading("10", "From entry candidate to a triggered trade"),
        p("A technical entry candidate becomes a stored signal and trade row only after the operational gates pass. This is why a setup can show entry_candidate while the Trades Triggered count does not increase."),
        horizontal_flow([
            ("Setup row", "All mandatory filters pass", PURPLE),
            ("Entry ready", "Early or Final snapshot passes", BLUE),
            ("Signal gate", "Market open OR Force enabled", YELLOW),
            ("Trade record", "No duplicate/suppression rule", GREEN),
        ]),
        Spacer(1, 7 * mm),
        data_table(
            ["Situation", "New alert?", "Implemented result"],
            [
                ["Market closed; Force off", "No", "Analysis rows update, but trade IDs, entry/exit events, and Telegram are skipped."],
                ["Market closed; Force on", "Possible", "Cached-data test may create trade rows and optionally Telegram alerts."],
                ["Same symbol/profile/kind/touch already recorded", "No", "Duplicate is suppressed even when the earlier trade record is closed."],
                ["Final already recorded for same setup", "No later Early", "Final suppresses a later Early for the same mapped touch."],
                ["Early exists, Final appears later", "Yes", "Early may remain open while a separate Final trade record is created."],
                ["Early and Final first appear in same run", "Final only", "Final is evaluated first; its creation suppresses the subsequent Early."],
                ["Telegram disabled/unconfigured", "Web alert may still exist", "Persistence and Telegram delivery are separate."],
            ],
            [76, 28, 70],
        ),
        Spacer(1, 6 * mm),
        callout("Weekly maximum open trades", "For one stock in Weekly-only mode, at most one Early and one Final Weekly trade can be open. A new mapped touch cannot open another trade of the same kind while that kind is already open.", GREEN_BG, GREEN),
        PageBreak(),
    ])

    story.extend([
        heading("11", "What to track while the live Weekly scan runs"),
        p("Read the UI from current scan state to historical trade outcome. The first three sections explain what the current scan found; the later sections prove which signals were actually stored and what happened afterward."),
        data_table(
            ["UI section", "What to watch", "What it tells a nontechnical user"],
            [
                ["Scan Progress & Data Used", "Status, candles refreshed, failures, last start, last end, next trigger", "Whether the Weekly scan is running, completed, delayed, or missing candle data."],
                ["Step 1: Purple EMA9 Touch", "Symbol, touch price/time in IST, close distance, blocker", "Which Weekly candles physically touched purple and closed no more than 3.00% above it."],
                ["Weekly Setups Passing All Filters", "Monthly confirmation, Weekly direction/trend, Early and Final status", "Which Step 1 rows passed every mandatory setup filter and are waiting for, or already meet, an entry condition."],
                ["Live Scan Alert Summary", "Signals created/skipped, Telegram sent/failed, reason", "Whether a technically ready entry was allowed through market-hours, duplicate, persistence, and Telegram gates."],
                ["Trades Triggered: Open & Closed", "Trade ID, Early/Final, entry time, open/closed status, exit time, duration", "One row for every created entry signal. An open row is watched for exit; a closed row keeps the linked exit and duration."],
                ["Exit Alerts", "Exit time, symbol, trade ID, 30-minute close and yellow", "Proof that an open Weekly trade received its strict close-below-yellow exit signal."],
            ],
            [50, 66, 58],
        ),
        Spacer(1, 6 * mm),
        callout("Why there is one trade section", "Every created entry signal immediately creates one trade ID, so separate entry-signal and trade tables repeated the same event. Trades Triggered is now the single user-facing view. The immutable signal event remains stored internally for duplicate prevention, Telegram audit, and troubleshooting.", BLUE_BG, BLUE),
        Spacer(1, 5 * mm),
        callout("Trades Triggered: Open & Closed", "Open means the exit scanner is still monitoring that trade ID. Closed means an Exit Alert was linked to it; entry time, exit time, and time open show the complete duration. It is not a broker position or an automatic order. All displayed timestamps are IST.", GREEN_BG, GREEN),
        Spacer(1, 5 * mm),
        callout("Live review habit", "For an unexpected result, first note the scan end timestamp, then capture the same symbol's Step 1 row, all-filter setup row, Live Scan Alert Summary, and matching trade ID. Compare charts only at that same timestamp.", AMBER_BG, AMBER),
        Spacer(1, 5 * mm),
        callout("Daily monitor operation", "On an NSE trading day, click Start market monitor at 9:15-9:20 AM IST. Keep the browser tab open and the laptop awake through the session. After 3:30 PM IST, wait for any active scan to finish and click Stop. The application currently recognizes weekdays but does not automatically exclude NSE holidays.", PURPLE_BG, PURPLE_DARK),
        PageBreak(),
    ])

    exit_cases = [
        case_card(
            "30-minute close below yellow",
            "EXIT ALERT",
            chart([(110, 113, 107, 111), (111, 114, 106, 108), (108, 110, 101, 102)], yellow=[105, 105, 105], markers=[("close below", 2, 102, RED)]),
            "Latest 30-minute close < yellow triggers exit for each matching open Weekly trade ID.",
        ),
        case_card(
            "Close equals yellow",
            "OPEN",
            chart([(110, 113, 107, 111), (111, 114, 106, 108), (108, 110, 102, 105)], yellow=[105, 105, 105], markers=[("equal", 2, 105, GREEN)]),
            "Exit comparison is strict. Equality does not trigger an exit.",
        ),
        case_card(
            "Wick below; close above",
            "OPEN",
            chart([(110, 113, 107, 111), (111, 114, 106, 108), (108, 110, 101, 107)], yellow=[105, 105, 105], markers=[("wick only", 2, 102, GREEN)]),
            "A wick below yellow is ignored. The 30-minute candle must close below yellow.",
        ),
        case_card(
            "No open Weekly trade ID",
            "NO ALERT",
            chart([(110, 113, 107, 111), (111, 114, 106, 108), (108, 110, 101, 102)], yellow=[105, 105, 105]),
            "The exit technical may be true, but exit scanning applies only to existing open Weekly trade rows.",
        ),
    ]
    story.extend([
        heading("12", "Weekly Exit Alert technicals (30-minute)"),
        case_grid(exit_cases),
        Spacer(1, 5 * mm),
        callout("Paused-profile behavior", "Existing Monthly and Daily open trades are preserved but not exit-monitored while Weekly-only validation mode is active. Re-enabling those profiles must also resume their own exit scans.", AMBER_BG, AMBER),
        PageBreak(),
    ])

    outcome_cases = [
        case_card(
            "Step 1 only",
            "EXPECTED",
            chart([(110, 114, 106, 112), (111, 113, 104, 106), (106, 109, 100, 103)], purple=[100, 100, 100], blue=[98, 99, 100]),
            "Touch passes but blue is not strictly above purple. Appears in Step 1, not Weekly Setups Passing All Filters, and creates no entry signal.",
        ),
        case_card(
            "Strict candidate waiting",
            "EXPECTED",
            chart([(118, 124, 114, 121), (121, 125, 112, 116), (116, 120, 101, 105)], purple=[100, 101, 102], blue=[119, 117, 115], yellow=[108, 108, 108]),
            "Weekly/Monthly setup passes, but the current entry close is not above yellow. Appears in Weekly Setups Passing All Filters, but no new Entry Signal is created.",
        ),
        case_card(
            "Entry candidate, operationally skipped",
            "EXPECTED",
            chart([(101, 104, 98, 102), (102, 106, 99, 105), (105, 110, 103, 109)], purple=[100, 101, 102], blue=[106, 107, 108], yellow=[99, 101, 104]),
            "Technicals pass outside market hours with Force off. UI analysis updates; no trade ID, Entry Signal, Exit Alert, or Telegram message is created.",
        ),
        case_card(
            "Entry and later exit",
            "FULL LIFECYCLE",
            chart([(101, 106, 99, 105), (105, 110, 103, 109), (109, 111, 104, 106), (106, 108, 100, 102)], yellow=[101, 104, 105, 105], markers=[("entry", 1, 109, GREEN), ("exit", 3, 102, RED)]),
            "A new Entry Signal creates a trade ID. A later 30-minute close below yellow creates one Exit Alert and closes that same trade ID.",
        ),
    ]
    story.extend([
        heading("13", "End-to-end examples: where a stock should appear"),
        case_grid(outcome_cases),
        Spacer(1, 5 * mm),
        data_table(
            ["UI section", "What one row means", "Must be a subset of"],
            [
                ["Step 1", "Current physical Weekly touch audit row", "Analyzed Weekly symbols"],
                ["Weekly Setups Passing All Filters", "Current setup passed every mandatory gate; entry may wait", "Current Step 1 rows"],
                ["Trades Triggered: Open & Closed", "Created entry event plus current state and duration for its trade ID", "Not a direct subset of current setups"],
                ["Exit Alerts", "Historical close event for a matching open trade ID", "Triggered trades"],
            ],
            [56, 76, 42],
        ),
        PageBreak(),
    ])

    story.extend([
        heading("14", "Audit checklist for a real Weekly stock"),
        p("Use one stock at a time and record the values below from the UI and Krishna's chart. A disagreement in the first failing row identifies the rule to revisit."),
        data_table(
            ["Stage", "Record from chart/UI", "Expected decision"],
            [
                ["Weekly Step 1", "Latest Weekly H/L/C, purple EMA9, close distance, Weekly candle count", "Low <= purple <= high; purple <= close <= purple x 1.03; at least 9 candles"],
                ["Weekly direction", "Blue, purple, prior 2-3 Weekly closes, latest Weekly close", "Blue > purple; prior close reaches 97% blue area; latest moves toward purple"],
                ["Monthly confirmation", "Latest Monthly O/C and Monthly blue", "Monthly O > blue OR C > blue"],
                ["Trend", "Weekly close, EMA26, EMA89, structure", "Close >= EMA26; close > EMA89; structure is not downtrend"],
                ["30-minute validity", "Mapped C1/C2 lows and every C3+ low", "C1/C2 may enter; from C3 no low < min(C1, C2)"],
                ["Early", "30-minute close and yellow", "Close > yellow; entry blue/purple and yellow/brown ignored"],
                ["2-hour validity", "Mapped C1/C2 lows and every C3+ low", "C1/C2 may enter; from C3 no low < min(C1, C2)"],
                ["Final", "2-hour close, yellow, and brown", "Close > yellow; yellow > brown; entry blue/purple ignored"],
                ["Signal gate", "Market status, Force, prior touch trade record, Telegram toggle", "Operational gate passes and event is not duplicate/suppressed"],
                ["Exit", "Open trade ID, latest 30-minute close and yellow", "Open trade exists; close < yellow"],
            ],
            [40, 78, 56],
        ),
        Spacer(1, 6 * mm),
        callout("Best evidence to share", "For any unexpected stock, capture the Weekly, Monthly, 30-minute, and 2-hour chart at the same scan timestamp plus its Step 1 and Weekly Setups Passing All Filters row. Without aligned timestamps, indicator values can legitimately differ.", BLUE_BG, BLUE),
        Spacer(1, 6 * mm),
        p("Implementation trace", "H2x"),
        data_table(
            ["Area", "Source"],
            [
                ["Step 1 and strict rules", "trading_analysis/analysis/krishna_setup.py"],
                ["Live profile selection and alert orchestration", "trading_analysis/web_services.py"],
                ["Duplicate and lifecycle precedence", "trading_analysis/storage/sqlite.py"],
                ["Weekly-only controls and progress", "web/index.html and web/app.js"],
            ],
            [64, 110],
        ),
        PageBreak(),
    ])

    story.extend([
        heading("15", "Remaining points that may need Krishna's confirmation"),
        callout(
            "1. Does any physical wick crossing count?",
            "v1.1 accepts low <= purple <= high when the close is from purple through 3.00% above it. Confirm that a deep intraperiod wick below purple remains valid as long as the close is still inside that bullish close zone.",
            AMBER_BG,
            AMBER,
        ),
        Spacer(1, 5 * mm),
        callout(
            "2. How should 'approach from blue' be measured?",
            "Current code uses any prior close in the latest 2-3 Weekly candles at or above 97% of the current blue value, plus a latest close moving down or within 3% of purple. Confirm whether candle bodies, highs/lows, or historical blue values should be used instead.",
            AMBER_BG,
            AMBER,
        ),
        Spacer(1, 5 * mm),
        callout(
            "3. How should C1 and C2 map into each entry stream?",
            "C1 and C2 are now entry-eligible immediately. Current code still maps them independently inside the 30-minute and 2-hour streams from the Weekly touch timestamp. Confirm that this mapping, rather than the first two Weekly candles, is intended.",
            AMBER_BG,
            AMBER,
        ),
        Spacer(1, 5 * mm),
        callout(
            "4. Should incomplete Weekly/Monthly candles be evaluated?",
            "The scanner uses the latest cached candle. During an active week/month that candle may still be forming. Confirm whether Step 1 and Monthly confirmation should use live incomplete candles, only completed candles, or clearly distinguish both modes.",
            AMBER_BG,
            AMBER,
        ),
        Spacer(1, 8 * mm),
        p("Review outcome", "H2x"),
        data_table(
            ["Decision", "Krishna's answer / requested change"],
            [["Physical wick depth", ""], ["Blue-to-purple approach", ""], ["C1/C2 timeframe mapping", ""], ["Incomplete candle handling", ""], ["Other rule difference", ""]],
            [58, 116],
        ),
        Spacer(1, 8 * mm),
        callout("Document status", "This guide describes the current implementation for validation. Update both code and this document after the reviewed definitions are confirmed.", PURPLE_BG, PURPLE_DARK),
    ])
    return story


def main() -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(OUTPUT),
        pagesize=A4,
        rightMargin=18 * mm,
        leftMargin=18 * mm,
        topMargin=19 * mm,
        bottomMargin=17 * mm,
        title="Weekly Purple Touch Visual Audit Guide",
        author="TradingDataAnalysis",
        subject="Weekly-only Purple Touch scanner validation",
    )
    doc.build(build_story(), onFirstPage=page_header_footer, onLaterPages=page_header_footer)
    print(OUTPUT)


if __name__ == "__main__":
    main()
