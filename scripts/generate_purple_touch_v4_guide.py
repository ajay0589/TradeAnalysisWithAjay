from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LOCAL_PDF_DEPS = ROOT / "tmp" / "pdf_deps"
if LOCAL_PDF_DEPS.exists():
    sys.path.insert(0, str(LOCAL_PDF_DEPS))

from reportlab.lib import colors
from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.platypus import Flowable, PageBreak, SimpleDocTemplate, Spacer, Table, TableStyle

from scripts.generate_purple_touch_guide import (
    AMBER_BG,
    BLACK_LINE,
    BLUE,
    BLUE_BG,
    BROWN,
    GREEN,
    GREEN_BG,
    INK,
    LIGHT_GREEN,
    LINE,
    MUTED,
    PURPLE,
    PURPLE_BG,
    RED,
    RED_BG,
    SOFT,
    YELLOW,
    callout,
    data_table,
    flowchart,
    heading,
    horizontal_flow,
    p,
)


OUTPUT = ROOT / "docs" / "Purple_Touch_Implementation_Guide_v1_3.pdf"
ACCENT = HexColor("#0F766E")


class TouchCases(Flowable):
    def __init__(self):
        super().__init__()
        self.width = 174 * mm
        self.height = 78 * mm

    def _case(self, x, title, low, high, close, line, valid, note):
        c = self.canv
        w = 40 * mm
        c.setStrokeColor(GREEN if valid else RED)
        c.setFillColor(GREEN_BG if valid else RED_BG)
        c.roundRect(x, 8 * mm, w, 65 * mm, 2 * mm, fill=1, stroke=1)
        c.setFillColor(INK)
        c.setFont("Helvetica-Bold", 7.4)
        c.drawCentredString(x + w / 2, 66 * mm, title)
        base = 17 * mm
        scale = 3.4 * mm
        y = lambda value: base + value * scale
        c.setStrokeColor(PURPLE)
        c.setLineWidth(2.3)
        c.line(x + 5 * mm, y(line), x + 35 * mm, y(line))
        candle_x = x + 20 * mm
        candle_color = GREEN if close >= (low + high) / 2 else RED
        c.setStrokeColor(candle_color)
        c.setFillColor(candle_color)
        c.setLineWidth(1)
        c.line(candle_x, y(low), candle_x, y(high))
        body_bottom = min(y(close), y((low + high) / 2))
        body_height = max(3 * mm, abs(y(close) - y((low + high) / 2)))
        c.rect(candle_x - 3 * mm, body_bottom, 6 * mm, body_height, fill=1, stroke=0)
        c.setFillColor(GREEN if valid else RED)
        c.setFont("Helvetica-Bold", 7.2)
        c.drawCentredString(x + w / 2, 12 * mm, "PASS" if valid else "BLOCK")
        c.setFillColor(MUTED)
        c.setFont("Helvetica", 5.8)
        c.drawCentredString(x + w / 2, 4 * mm, note)

    def draw(self):
        self.canv.saveState()
        self._case(1 * mm, "Wick touches", 2, 9, 7, 3, True, "Close is 0% to +3%")
        self._case(45 * mm, "Close at +3%", 3, 9, 8, 3, True, "Exactly +3% remains valid")
        self._case(89 * mm, "Entirely above", 5, 10, 8, 3, False, "No physical range touch")
        self._case(133 * mm, "Close below", 2, 9, 2, 3, False, "Bullish setup requires >= Purple")
        self.canv.restoreState()


class FixedThreshold(Flowable):
    def __init__(self):
        super().__init__()
        self.width = 174 * mm
        self.height = 68 * mm

    def draw(self):
        c = self.canv
        c.saveState()
        c.setFillColor(SOFT)
        c.roundRect(2 * mm, 5 * mm, 170 * mm, 58 * mm, 2 * mm, fill=1, stroke=0)
        c.setStrokeColor(PURPLE)
        c.setLineWidth(2.4)
        c.line(14 * mm, 23 * mm, 158 * mm, 23 * mm)
        c.setFillColor(PURPLE)
        c.setFont("Helvetica-Bold", 8)
        c.drawString(14 * mm, 17 * mm, "Captured Purple EMA9 = 100.00")
        c.setStrokeColor(RED)
        c.setDash(4, 3)
        c.line(14 * mm, 48 * mm, 158 * mm, 48 * mm)
        c.setDash()
        c.setFillColor(RED)
        c.drawString(14 * mm, 52 * mm, "Fixed discard level = 100.00 x 1.03 = 103.00")
        points = [(25, 29), (48, 38), (72, 46), (96, 48), (120, 50), (145, 56)]
        c.setStrokeColor(BLUE)
        c.setLineWidth(2)
        for left, right in zip(points, points[1:]):
            c.line(left[0] * mm, left[1] * mm, right[0] * mm, right[1] * mm)
        c.setFillColor(GREEN)
        c.circle(96 * mm, 48 * mm, 2.6 * mm, fill=1, stroke=0)
        c.setFillColor(INK)
        c.setFont("Helvetica", 7)
        c.drawString(99 * mm, 43 * mm, "103.00: valid")
        c.setFillColor(RED)
        c.circle(120 * mm, 50 * mm, 2.6 * mm, fill=1, stroke=0)
        c.drawString(123 * mm, 45 * mm, ">103.00: discard setup")
        c.setFillColor(MUTED)
        c.setFont("Helvetica-Oblique", 7)
        c.drawString(14 * mm, 8 * mm, "Later EMA9 movement never changes this threshold. Existing trades remain open.")
        c.restoreState()


class ParallelWorkers(Flowable):
    def __init__(self):
        super().__init__()
        self.width = 174 * mm
        self.height = 88 * mm

    def _box(self, x, y, w, h, title, body, color):
        c = self.canv
        c.setStrokeColor(color)
        c.setFillColor(color.clone(alpha=0.11))
        c.roundRect(x, y, w, h, 2 * mm, fill=1, stroke=1)
        c.setFillColor(INK)
        c.setFont("Helvetica-Bold", 7.8)
        c.drawCentredString(x + w / 2, y + h - 7 * mm, title)
        c.setFillColor(MUTED)
        c.setFont("Helvetica", 6.2)
        for index, line in enumerate(body):
            c.drawCentredString(x + w / 2, y + h - (13 + index * 4.5) * mm, line)

    def draw(self):
        c = self.canv
        c.saveState()
        self._box(48 * mm, 65 * mm, 78 * mm, 19 * mm, "Continuous Candle Data Service", ["One rate-limited priority queue", "Atomic completed cache"], ACCENT)
        boxes = [
            (2, "Monthly setup", ["Every 2 hours"]),
            (46, "Weekly setup", ["Every 30 min"]),
            (90, "Daily setup", ["Every 3 min"]),
            (134, "Entry checker", ["Early + Final", "active setups only"]),
        ]
        for x, title, body in boxes:
            self._box(x * mm, 31 * mm, 38 * mm, 25 * mm, title, body, BLUE if x < 134 else PURPLE)
            c.setStrokeColor(LINE)
            c.line(87 * mm, 65 * mm, (x + 19) * mm, 56 * mm)
        self._box(50 * mm, 2 * mm, 74 * mm, 20 * mm, "Open Trade Exit Checker", ["Open trades only", "Independent from setup and entry scans"], RED)
        c.setStrokeColor(LINE)
        c.line(87 * mm, 31 * mm, 87 * mm, 22 * mm)
        c.restoreState()


def header_footer(canvas, doc):
    canvas.saveState()
    page = canvas.getPageNumber()
    if page > 1:
        canvas.setStrokeColor(LINE)
        canvas.line(18 * mm, A4[1] - 14 * mm, A4[0] - 18 * mm, A4[1] - 14 * mm)
        canvas.setFillColor(MUTED)
        canvas.setFont("Helvetica", 7.3)
        canvas.drawString(18 * mm, A4[1] - 10 * mm, "TradeAnalysisWithAjay - Purple Touch v4 Guide")
        canvas.drawRightString(A4[0] - 18 * mm, A4[1] - 10 * mm, "scanner-audit-and-v4")
    canvas.setStrokeColor(LINE)
    canvas.line(18 * mm, 13 * mm, A4[0] - 18 * mm, 13 * mm)
    canvas.setFillColor(MUTED)
    canvas.setFont("Helvetica", 7.2)
    canvas.drawString(18 * mm, 8 * mm, "Implementation snapshot: scanner-audit-and-v4 | 14 Sep 2026 | IST")
    canvas.drawRightString(A4[0] - 18 * mm, 8 * mm, f"Page {page}")
    canvas.restoreState()


def bullets(items):
    return [p(f"<b>{index}.</b> {item}") for index, item in enumerate(items, 1)]


def story():
    pages = []
    pages += [
        Spacer(1, 18 * mm),
        p("PURPLE TOUCH v4", "CoverTitle"),
        p("Filtering, Scanning, Lifecycle, and Windows Setup Guide", "CoverTitle"),
        Spacer(1, 4 * mm),
        p("A visual implementation reference for Monthly, Weekly, and Daily profiles on branch scanner-audit-and-v4.", "CoverSub"),
        Spacer(1, 8 * mm),
        callout(
            "Purpose",
            "Explain exactly what data is downloaded, how each filter is evaluated, when a setup enters or leaves monitoring, how alerts and exits are created, and how to run v4 beside an existing v3 installation on Windows.",
            PURPLE_BG,
            PURPLE,
        ),
        Spacer(1, 10 * mm),
        flowchart([
            ("Candle service", "Continuously refreshes prioritized source candles while the monitor is running.", ACCENT),
            ("Setup scanners", "Monthly, Weekly, and Daily independently find physical Purple EMA9 touches and strict candidates.", BLUE),
            ("Persistent setup bucket", "Qualified touch lifecycles remain available for repeated Early, Final, and invalidation checks.", PURPLE),
            ("Trade lifecycle", "Entry signals create independent trade IDs; only open trades are checked for exits.", GREEN),
        ]),
        Spacer(1, 10 * mm),
        p("Read-only alerting and audit support. The application does not place orders and is not financial advice.", "CoverSub"),
        PageBreak(),
    ]

    pages += [
        heading("1", "Architecture: downloads and analysis are decoupled"),
        p("The old refresh-then-scan chain has been replaced. Cached-data analysis can run concurrently, but Zerodha downloads remain serialized and rate limited."),
        ParallelWorkers(),
        callout(
            "Key behavior",
            "Analyze fresh cache now does not download candles. Start candle service &amp; monitor starts the downloader and recurring workers. Scanners never read a partially written CSV and never create alerts from stale data.",
            AMBER_BG,
            YELLOW,
        ),
        Spacer(1, 5 * mm),
        horizontal_flow([
            ("Priority 1", "Open-trade exit timeframes", RED),
            ("Priority 2", "Active setup Early/Final timeframes", PURPLE),
            ("Priority 3", "Full-universe setup sources", BLUE),
        ]),
        Spacer(1, 6 * mm),
        p("One failed symbol or profile is recorded and skipped; it does not terminate the other workers."),
        PageBreak(),
    ]

    pages += [
        heading("2", "Timeframe matrix and source files"),
        data_table(
            ["Profile", "Setup touch", "Higher confirmation", "Early", "Final", "Exit", "Default schedule"],
            [
                ["Monthly", "Monthly", "Derived 5-month:<br/>open OR close &gt; blue", "2 hour", "Daily", "2 hour", "Every 2 hours"],
                ["Weekly", "Weekly", "Monthly:<br/>open OR close &gt; blue", "30 min", "2 hour", "30 min", "Every 30 min"],
                ["Daily", "Daily", "Weekly:<br/>open OR close &gt; blue", "10 min", "30 min", "10 min", "Every 3 min"],
            ],
            [17, 22, 42, 20, 20, 20, 33],
        ),
        Spacer(1, 8 * mm),
        callout(
            "Why only four source timeframes appear in Candle Data Service",
            "Monthly, Weekly, and derived 5-month candles are built from Daily data. Two-hour candles are built from 1-hour data. Therefore the downloaded sources are Daily, 1 hour, 30 min, and 10 min.",
        ),
        Spacer(1, 8 * mm),
        data_table(
            ["Displayed requirement", "Downloaded source", "Used by"],
            [
                ["Monthly / Weekly / Daily / 5-month", "Daily", "All setup scans and higher confirmation"],
                ["2 hour", "1 hour", "Monthly Early/Exit; Weekly Final"],
                ["30 min", "30 min", "Weekly Early/Exit; Daily Final"],
                ["10 min", "10 min", "Daily Early/Exit"],
            ],
            [55, 43, 76],
        ),
        Spacer(1, 8 * mm),
        p("All timestamps stored or displayed by the Purple Touch workflow are converted to Indian Standard Time."),
        PageBreak(),
    ]

    pages += [
        heading("3", "Step 1: exact bullish Purple EMA9 touch"),
        p("The latest setup-timeframe candle must physically contain Purple EMA9 inside its low/high range. The close must be on or above Purple and no more than 3.00% above Purple."),
        TouchCases(),
        callout(
            "Implemented expression",
            "PASS when low &lt;= Purple EMA9 &lt;= high AND Purple EMA9 &lt;= close &lt;= Purple EMA9 x 1.03. Exactly +3.00% passes. An entirely-above near touch and any close below Purple are blocked.",
            PURPLE_BG,
            PURPLE,
        ),
        Spacer(1, 6 * mm),
        data_table(
            ["Value recorded", "Meaning"],
            [
                ["Touch price", "Purple EMA9 value touched by the wick/range"],
                ["Touch timestamp", "Setup candle timestamp in IST"],
                ["Detected timestamp", "When the scanner persisted the setup"],
                ["Initial distance", "Close distance above captured Purple, ranked from 0.00% upward"],
            ],
            [48, 126],
        ),
        PageBreak(),
    ]

    pages += [
        heading("4", "Strict setup candidate filters"),
        p("Step 1 rows are audit rows. A stock enters Stocks Waiting for Entry only after all mandatory setup filters pass."),
        data_table(
            ["Strict filter", "Pass boundary", "Role"],
            [
                ["Physical touch + close zone", "Range touches Purple; close is 0.00% to 3.00% above", "Mandatory"],
                ["Black EMA89", "Touch-timeframe close strictly &gt; EMA89", "Mandatory"],
                ["Light-green EMA26", "Touch-timeframe close &gt;= EMA26", "Mandatory"],
                ["Blue vs Purple", "Touch-timeframe blue strictly &gt; Purple EMA9", "Mandatory"],
                ["Approach direction", "Prior 2-3 setup candles come from blue area toward Purple", "Mandatory"],
                ["Higher confirmation", "Latest mapped candle open OR close strictly &gt; its blue", "Mandatory"],
                ["Market structure", "Must not be downtrend", "Mandatory"],
                ["EMA9 vs EMA26", "EMA9 &gt; EMA26", "Score/context; warning if absent"],
                ["Yellow vs EMA26", "Yellow &lt; EMA26", "Score/context; warning if absent"],
                ["Brown vs EMA26, RSI divergence", "Displayed relationship / optional quality", "Non-blocking context"],
            ],
            [55, 78, 41],
        ),
        Spacer(1, 6 * mm),
        callout(
            "No strict candidate",
            "The stock passed the physical Purple touch audit but at least one mandatory setup filter blocked it. It stays visible in Step 1 for explanation, but it is not persisted into the active entry-checker bucket.",
            RED_BG,
            RED,
        ),
        PageBreak(),
    ]

    pages += [
        heading("5", "Higher-timeframe confirmation"),
        horizontal_flow([
            ("Monthly touch", "Derived 5-month open OR close &gt; blue", PURPLE),
            ("Weekly touch", "Monthly open OR close &gt; blue", BLUE),
            ("Daily touch", "Weekly open OR close &gt; blue", ACCENT),
        ]),
        Spacer(1, 10 * mm),
        data_table(
            ["Boundary case", "Result", "Reason"],
            [
                ["Open above blue; close below", "PASS", "Open OR close is sufficient"],
                ["Open below blue; close above", "PASS", "Open OR close is sufficient"],
                ["Open equals blue; close below", "BLOCK", "Comparison is strictly above"],
                ["Open below; close equals blue", "BLOCK", "Equality does not pass"],
                ["Blue unavailable", "WAIT/BLOCK", "Required candle history is insufficient"],
            ],
            [57, 30, 87],
        ),
        Spacer(1, 10 * mm),
        callout(
            "Derived 5-month candle",
            "For the Monthly profile, source Monthly candles are grouped into stable five-month calendar buckets. The latest derived bucket supplies open, close, and blue-line confirmation values.",
        ),
        Spacer(1, 8 * mm),
        p("This confirmation belongs to setup qualification. It is separate from Early and Final entry-timeframe rules."),
        PageBreak(),
    ]

    pages += [
        heading("6", "Persistent setup lifecycle"),
        flowchart([
            ("Strict setup passes", "Create or update one row identified by symbol + profile + touch candle timestamp.", GREEN),
            ("active_waiting", "Entry checker monitors only this persistent bucket using newly closed Early and Final candles.", PURPLE),
            ("Early trigger", "Create Early trade; keep checking the same setup for Final.", BLUE),
            ("Final trigger", "Create Final trade; suppress any later Early entry for this setup.", GREEN),
            ("Discard or complete", "Keep the historical setup row for audit; never delete it automatically.", RED),
        ]),
        Spacer(1, 6 * mm),
        data_table(
            ["Status", "Meaning"],
            [
                ["active_waiting", "Qualified setup is waiting for Early/Final entry rules"],
                ["early_entry_triggered", "Early trade exists; Final monitoring continues"],
                ["final_entry_triggered", "Final trade exists; later Early checks stop"],
                ["discarded_above_3_percent", "Latest entry-check price exceeded fixed captured-Purple limit"],
                ["discarded_c1_c2_low_break", "A Candle 3+ low broke the lower C1/C2 low"],
                ["completed", "Lifecycle no longer needs entry monitoring"],
            ],
            [58, 116],
        ),
        PageBreak(),
    ]

    pages += [
        heading("7", "Fixed 3% disqualification"),
        FixedThreshold(),
        data_table(
            ["Scenario", "Setup action", "Existing trade action"],
            [
                ["Price = fixed +3.00% level", "Remain active", "No change"],
                ["Price strictly above fixed +3.00%", "Discard further Early/Final checks", "Keep open; normal Exit monitoring"],
                ["EMA9 later moves", "Do not move threshold", "No change"],
                ["Exceeded during C1, C2, or C3+", "Discard setup", "No automatic exit"],
            ],
            [62, 65, 47],
        ),
        Spacer(1, 7 * mm),
        p("The entry checker uses the latest successfully refreshed price. The stored audit includes actual price, calculated distance, fixed threshold, discard time, and reason."),
        PageBreak(),
    ]

    pages += [
        heading("8", "Candle 1 / Candle 2 validity and invalidation"),
        horizontal_flow([
            ("Candle 1", "Mapped touch period. Entry is eligible immediately. Record C1 low.", PURPLE),
            ("Candle 2", "Entry remains eligible. Record C2 low when closed.", BLUE),
            ("Candle 3 onward", "Discard only if low &lt; min(C1 low, C2 low).", RED),
        ]),
        Spacer(1, 10 * mm),
        callout(
            "Important",
            "C1 and C2 are not waiting candles. Either can create Early or Final entry if the corresponding entry rules pass. Starting at C3, the same lower C1/C2 low remains the invalidation threshold for every later candle. There is no 10-candle expiry.",
            AMBER_BG,
            YELLOW,
        ),
        Spacer(1, 10 * mm),
        data_table(
            ["Example", "Result"],
            [
                ["C1 low 98; C2 low 97; C3 low 97", "Valid: equality does not break below 97"],
                ["C1 low 98; C2 low 97; C5 low 96.99", "Discard: a later low is strictly below 97"],
                ["C2 not available yet", "C1 may trigger entry; later-low invalidation waits for C2"],
            ],
            [92, 82],
        ),
        PageBreak(),
    ]

    pages += [
        heading("9", "Early and Final entry checks"),
        data_table(
            ["Rule", "Early", "Final"],
            [
                ["Required candle", "Latest completed Early timeframe", "Latest completed Final timeframe"],
                ["Close strictly above yellow", "Mandatory", "Mandatory"],
                ["Yellow strictly above brown VWMA20", "Ignored", "Mandatory"],
                ["Blue above Purple on entry timeframe", "Ignored / context only", "Ignored / context only"],
                ["RSI bullish divergence", "Optional context", "Optional context"],
                ["C1 and C2", "Both eligible", "Both eligible"],
                ["Duplicate closed candle", "Not reprocessed", "Not reprocessed"],
            ],
            [79, 47, 48],
        ),
        Spacer(1, 9 * mm),
        flowchart([
            ("Active setup + fresh closed candle", "Check fixed 3% and C3+ invalidation first.", PURPLE),
            ("Evaluate Final", "close &gt; yellow AND yellow &gt; brown. If triggered, suppress later Early.", GREEN),
            ("Evaluate Early", "close &gt; yellow. If triggered first, keep setup for Final.", BLUE),
            ("Persist and alert immediately", "Serialize database write, suppress duplicates, optionally send Telegram.", ACCENT),
        ]),
        PageBreak(),
    ]

    pages += [
        heading("10", "Exit alerts and trade history"),
        horizontal_flow([
            ("Entry signal", "Creates a unique Early or Final trade ID", GREEN),
            ("Open trade", "Exit checker reads only open trade IDs", BLUE),
            ("Exit signal", "Closed exit candle close &lt; yellow", RED),
        ]),
        Spacer(1, 10 * mm),
        data_table(
            ["UI section", "What it means"],
            [
                ["Stocks Waiting for Entry", "Persistent qualified setups still being checked, including discard history through filters"],
                ["Live Scan Alert Summary", "Counts and delivery status for the latest analysis"],
                ["Exit Alerts", "Historical exit events created from closed candles below yellow"],
                ["Trades Triggered: Open &amp; Closed", "One lifecycle row per Early/Final trade ID with entry, exit, status, and duration"],
            ],
            [61, 113],
        ),
        Spacer(1, 8 * mm),
        callout(
            "Independent lifecycles",
            "Early and Final trades for the same setup are separate. Discarding the setup stops new entry checks but does not close an existing trade. Only its configured exit-timeframe closed candle below yellow closes that trade ID.",
        ),
        PageBreak(),
    ]

    pages += [
        heading("11", "Freshness, scheduling, and UI counts"),
        data_table(
            ["Freshness state", "Meaning", "Scanner action"],
            [
                ["fresh", "Successful complete cache is within the source freshness window", "May analyze"],
                ["updating", "A queued or active replacement is in progress", "Use prior fresh atomic cache only; otherwise defer"],
                ["stale", "A prior success exists but is too old or behind required data", "Defer; never alert"],
                ["missing", "No successful refresh exists", "Defer; never alert"],
            ],
            [36, 91, 47],
        ),
        Spacer(1, 8 * mm),
        callout(
            "Why Stale and Missing can be non-zero",
            "The service refreshes a rate-limited queue, not every symbol at once. If the monitor is stopped before completion, completed sources later become stale and unfinished sources remain missing. Analyze fresh cache now does not fill these columns; restart the candle service.",
            AMBER_BG,
            YELLOW,
        ),
        Spacer(1, 8 * mm),
        data_table(
            ["Control", "Actual behavior"],
            [
                ["Start candle service &amp; monitor", "Starts continuous downloads, independent schedules, entry checker, and exit checker"],
                ["Analyze fresh cache now", "Immediately analyzes only currently fresh completed sources; may report partial/deferred"],
                ["Cancel scan", "Cooperatively stops an active manual analysis; disabled while idle"],
                ["Stop", "Stops monitor/download workers, clears queued work, preserves setup/trade history"],
            ],
            [65, 109],
        ),
        PageBreak(),
    ]

    pages += [
        heading("12", "Windows: clone v4 into a new folder"),
        p("Use a new folder so v3 remains untouched. These commands use port 8766; replace it with another free port if needed."),
        callout(
            "PowerShell commands",
            "<font name='Courier'>cd C:\\Users\\YOUR_NAME\\Documents<br/>git clone --branch scanner-audit-and-v4 --single-branch https://github.com/ajay0589/TradeAnalysisWithAjay.git TradeAnalysis-v4<br/>cd .\\TradeAnalysis-v4<br/>Copy-Item .env.example .env<br/>notepad .env<br/>python -m trading_analysis.cli env-check<br/>.\\scripts\\start_web_ui.ps1 -Port 8766</font>",
            SOFT,
            INK,
        ),
        Spacer(1, 7 * mm),
        data_table(
            ["Prerequisite", "Check"],
            [
                ["Git for Windows", "git --version"],
                ["Python 3.11 or newer", "python --version"],
                ["PowerShell execution", "If blocked: Set-ExecutionPolicy -Scope Process Bypass"],
                ["Free port", "Get-NetTCPConnection -LocalPort 8766 -ErrorAction SilentlyContinue"],
                ["Browser", "Open http://127.0.0.1:8766"],
            ],
            [58, 116],
        ),
        Spacer(1, 7 * mm),
        p("The runtime application uses Python standard-library modules. ReportLab is required only when regenerating this PDF: <font name='Courier'>python -m pip install reportlab</font>."),
        PageBreak(),
    ]

    pages += [
        heading("13", "Optional: safely carry v3 local data into v4"),
        p("Git does not copy secrets, downloaded candle caches, logs, reports, or the SQLite database. Stop v3 before copying its database."),
        flowchart([
            ("Stop v3", "Stop its server and confirm no scan/database write is active.", RED),
            ("Copy secrets", "Copy v3 .env to v4 .env. Never commit this file.", PURPLE),
            ("Copy cached data", "Copy data/raw so v4 does not begin with an empty candle cache.", BLUE),
            ("Optional history", "Copy data/db only if setup, alert, and trade history must continue. v4 migrates schema on startup.", GREEN),
        ]),
        Spacer(1, 7 * mm),
        callout(
            "PowerShell example",
            "<font name='Courier'>Copy-Item 'C:\\path\\to\\v3\\.env' '.\\.env' -Force<br/>robocopy 'C:\\path\\to\\v3\\data\\raw' '.\\data\\raw' /E<br/>robocopy 'C:\\path\\to\\v3\\data\\db' '.\\data\\db' /E</font>",
            SOFT,
            INK,
        ),
        Spacer(1, 8 * mm),
        callout(
            "Do not share one live database folder",
            "v3 and v4 may run simultaneously on different ports only when each folder has its own copied data/db. Do not point both running processes at the same SQLite file.",
            RED_BG,
            RED,
        ),
        PageBreak(),
    ]

    pages += [
        heading("14", "Daily operating checklist"),
        *bullets([
            "Before market, update the Zerodha access token in v4 .env and refresh NSE/NFO instrument masters if needed.",
            "At about 9:15-9:20 AM IST, open v4 on port 8766 and click Start candle service &amp; monitor.",
            "Expand Candle Data Service. Confirm queue activity, increasing successes, zero/understood failures, and fresh source counts.",
            "Check the separate Monthly, Weekly, and Daily profile cards for queued, actual start, completion, duration, analyzed, and deferred counts.",
            "Review Stocks Waiting for Entry for captured Purple, fixed +3% level, C1/C2 lows, Early/Final states, and discard reason.",
            "Use Analyze fresh cache now only for an immediate audit. It does not download data and can be partial while refresh continues.",
            "Keep the PC awake and server running. After 3:30 PM IST, allow active work to finish and then click Stop.",
        ]),
        Spacer(1, 8 * mm),
        data_table(
            ["Symptom", "Interpretation / action"],
            [
                ["0 rows in a few seconds", "Expand Candle Data Service. If Fresh is 0, start monitor and wait for completed sources."],
                ["Many deferred", "Normal during queue warm-up; recheck as fresh counts increase."],
                ["Failures increase", "Inspect Latest failure; verify token, instrument master, network, and rate limits."],
                ["Cancel disabled", "No manual analysis is currently running."],
                ["Success counter resets", "It is session-level; persisted per-source Last refresh remains available."],
                ["Expected candle differs", "Check market holiday or data-provider availability; holiday detection is not automatic."],
            ],
            [57, 117],
        ),
        PageBreak(),
    ]

    pages += [
        heading("15", "Review and validation worksheet"),
        p("Use several known valid and invalid symbols for each profile. Compare chart values with the row audit before judging alert quality."),
        data_table(
            ["Check", "Expected evidence", "Reviewer"],
            [
                ["Physical touch", "low &lt;= captured Purple &lt;= high; close from 0% through +3%", "Pass / Fail"],
                ["Strict setup", "Black, EMA26, blue/Purple, approach, confirmation, structure", "Pass / Fail"],
                ["Persistence", "Same touch updates same setup ID; new touch creates new lifecycle", "Pass / Fail"],
                ["Fixed threshold", "Captured EMA9 x 1.03 does not move; equality valid", "Pass / Fail"],
                ["C1/C2", "Both eligible; C3+ uses lower C1/C2 low forever", "Pass / Fail"],
                ["Early", "Closed entry candle close strictly above yellow", "Pass / Fail"],
                ["Final", "Close &gt; yellow and yellow &gt; brown", "Pass / Fail"],
                ["Exit", "Only corresponding open trade closes when exit candle close &lt; yellow", "Pass / Fail"],
                ["Freshness", "Stale/missing data creates no entry, exit, or Telegram alert", "Pass / Fail"],
                ["Concurrency", "Profiles, Entry, and Exit progress independently; downloads stay rate limited", "Pass / Fail"],
            ],
            [42, 102, 30],
        ),
        Spacer(1, 8 * mm),
        callout(
            "Implementation confidence",
            "Automated tests cover persistence, fixed 3%, C1/C2 entries and invalidation, Early-to-Final precedence, open-trade exits, stale-data blocking, duplicate candle suppression, profile mappings, scheduling priorities, concurrent analysis, cancellation, and UI/API payloads. Chart review remains essential for strategy validation.",
            GREEN_BG,
            GREEN,
        ),
    ]
    return pages


def main():
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    document = SimpleDocTemplate(
        str(OUTPUT),
        pagesize=A4,
        rightMargin=18 * mm,
        leftMargin=18 * mm,
        topMargin=19 * mm,
        bottomMargin=18 * mm,
        title="Purple Touch v4 Filtering, Scanning, Lifecycle, and Windows Setup Guide",
        author="TradeAnalysisWithAjay",
        subject="scanner-audit-and-v4 implementation and operating guide",
    )
    document.build(story(), onFirstPage=header_footer, onLaterPages=header_footer)
    print(OUTPUT)


if __name__ == "__main__":
    main()
