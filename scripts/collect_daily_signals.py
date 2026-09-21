"""Append daily live Swing v1 candidates and refresh their outcomes in Google Sheets.

This collector deliberately reuses the same scanner gates as ``backtest_v1``.
It does not place orders, generate research, or alter BACKTEST_ONLY rows.

Required environment variables:
  GOOGLE_SERVICE_ACCOUNT_JSON  Service-account JSON (the sheet must be shared with it)
  GOOGLE_SHEET_ID              Destination spreadsheet ID
"""

import importlib.util
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

UNIVERSE = [
    ("005930", "삼성전자"), ("000660", "SK하이닉스"),
    ("207940", "삼성바이오로직스"), ("373220", "LG에너지솔루션"),
    ("005380", "현대차"), ("000270", "기아"),
    ("012330", "현대모비스"), ("009150", "삼성전기"),
    ("006400", "삼성SDI"), ("028260", "삼성물산"),
    ("018260", "삼성에스디에스"), ("051910", "LG화학"),
    ("066570", "LG전자"), ("003550", "LG"), ("003670", "POSCO홀딩스"),
    ("010130", "고려아연"), ("035420", "NAVER"), ("035720", "카카오"),
    ("034730", "SK"), ("105560", "KB금융"), ("055550", "신한지주"),
    ("086790", "하나금융지주"), ("316140", "우리금융지주"), ("032830", "삼성생명"),
    ("329180", "HD현대중공업"), ("009540", "HD한국조선해양"),
    ("047810", "한국항공우주"), ("259960", "크래프톤"), ("068270", "셀트리온"),
    ("096770", "SK이노베이션"),
]

RULE_VERSION = "v1.0"
HORIZON = 10
LIVE_MARKER = "LIVE_DAILY"
SHEET_SCOPE = ["https://www.googleapis.com/auth/spreadsheets"]


def load_analysis_module():
    path = Path(__file__).resolve().parents[1] / "api" / "analysis.py"
    spec = importlib.util.spec_from_file_location("stock_analysis", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def normalize_date(value):
    digits = "".join(character for character in str(value or "") if character.isdigit())
    if len(digits) >= 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    return str(value or "")[:10]


def as_number(value, default=None):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def scanner_passed(analysis):
    indicators = analysis.get("indicators", {})
    return bool(
        analysis.get("success")
        and analysis.get("qualified")
        and analysis.get("inBuyZone")
        and analysis.get("currentPrice", 0) >= indicators.get("ma20", float("inf"))
        and (indicators.get("volumeRatio20") or 0) >= 1.2
        and 45 <= (indicators.get("rsi14") or 0) <= 65
    )


def signal_id(ticker, signal_date):
    return f"SIG-{RULE_VERSION}-{ticker}-{signal_date.replace('-', '')}"


def sheet_service():
    # Keep scanner helpers importable for local tests without the Sheets client.
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
    if not raw or not os.environ.get("GOOGLE_SHEET_ID"):
        raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON and GOOGLE_SHEET_ID are required")
    credentials = service_account.Credentials.from_service_account_info(
        json.loads(raw), scopes=SHEET_SCOPE
    )
    return build("sheets", "v4", credentials=credentials, cache_discovery=False)


def values(service, spreadsheet_id, range_name):
    return service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id, range=range_name
    ).execute().get("values", [])


def append_rows(service, spreadsheet_id, range_name, rows):
    if not rows:
        return
    service.spreadsheets().values().append(
        spreadsheetId=spreadsheet_id,
        range=range_name,
        valueInputOption="USER_ENTERED",
        insertDataOption="INSERT_ROWS",
        body={"values": rows},
    ).execute()


def write_row(service, spreadsheet_id, range_name, row):
    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=range_name,
        valueInputOption="USER_ENTERED",
        body={"values": [row]},
    ).execute()


def make_signal_row(ticker, company, analysis, run_id, signal_date):
    indicators = analysis["indicators"]
    return [
        signal_id(ticker, signal_date), run_id, signal_date, ticker, company, "KOSPI",
        RULE_VERSION, "IN_BUY_ZONE", analysis["currentPrice"], indicators["ma20"],
        indicators["rsi14"], indicators.get("volumeRatio20"), analysis["technicalScore"],
        analysis["buyZoneLow"], analysis["buyZoneHigh"], analysis["entryPrice"],
        analysis["stopLoss"], analysis["firstTargetPrice"], analysis["riskReward"],
        "자동 스캔 · 기업 리서치 미수집",
        f"https://m.stock.naver.com/domestic/stock/{ticker}/total",
        "PRICE_VERIFIED", "NOT_RESEARCHED", analysis["strategy"], "NOT_TRADED",
        f"{LIVE_MARKER} · auto daily scan",
    ]


def evaluate_outcome(bars, source_row):
    # Signal values are read from Signals so an outcome always reflects the recorded plan.
    sid, ticker, signal_date = source_row[0], source_row[3], normalize_date(source_row[2])
    entry, stop, target = (as_number(source_row[index]) for index in (15, 16, 17))
    if not signal_date or not entry or not stop or not target or entry <= stop:
        return None

    later = [bar for bar in bars if normalize_date(bar["date"]) > signal_date][:HORIZON]
    closes = [bar["close"] for bar in later]
    event, event_date = "OPEN", ""
    for bar in later:
        # Conservative convention: same-day stop and target touch is a stop first.
        if bar["low"] <= stop:
            event, event_date = "STOP_HIT", normalize_date(bar["date"])
            break
        if bar["high"] >= target:
            event, event_date = "TARGET_HIT", normalize_date(bar["date"])
            break
    if event == "OPEN" and len(later) >= HORIZON:
        event, event_date = "TIME_EXIT", normalize_date(later[-1]["date"])

    def return_at(days):
        return round(closes[days - 1] / entry - 1, 4) if len(closes) >= days else ""

    exit_price = target if event == "TARGET_HIT" else stop if event == "STOP_HIT" else (
        closes[-1] if event == "TIME_EXIT" else None
    )
    multiple = round((exit_price - entry) / (entry - stop), 2) if exit_price else ""
    return [
        sid, ticker, signal_date, "YES", signal_date, entry, return_at(3), return_at(5),
        return_at(10), "", "", event, event_date, event, multiple,
        "일봉 OHLC 기준 · 동일일 목표/손절은 손절 우선", normalize_date(datetime.now(timezone.utc).date()),
    ]


def main():
    service = sheet_service()
    spreadsheet_id = os.environ["GOOGLE_SHEET_ID"]
    signal_rows = values(service, spreadsheet_id, "Signals!A4:Z")
    outcome_rows = values(service, spreadsheet_id, "Outcomes!A4:Q")
    existing_ids = {row[0] for row in signal_rows if row}
    module = load_analysis_module()
    cached_bars = {}
    new_rows = []
    scan_date = None

    for ticker, company in UNIVERSE:
        try:
            bars = module.fetch_history(ticker, 140)
            cached_bars[ticker] = bars
            analysis = module.analyze_bars(ticker, bars)
            if not scanner_passed(analysis):
                continue
            candidate_date = normalize_date(analysis.get("asOfDate"))
            scan_date = scan_date or candidate_date
            sid = signal_id(ticker, candidate_date)
            if sid not in existing_ids:
                new_rows.append(make_signal_row(
                    ticker, company, analysis, f"RUN-v{RULE_VERSION}-{candidate_date.replace('-', '')}", candidate_date
                ))
        except Exception as error:
            print(f"scan failed: {ticker} {type(error).__name__}", file=sys.stderr)
        time.sleep(0.12)

    append_rows(service, spreadsheet_id, "Signals!A4:Z", new_rows)
    all_signals = signal_rows + new_rows
    live_signals = [row for row in all_signals if len(row) >= 26 and LIVE_MARKER in str(row[25])]
    current_outcomes = {row[0]: index + 4 for index, row in enumerate(outcome_rows) if row}
    appended_outcomes = 0
    refreshed_outcomes = 0

    for source_row in live_signals:
        ticker = source_row[3]
        bars = cached_bars.get(ticker)
        if bars is None:
            try:
                bars = module.fetch_history(ticker, 140)
                cached_bars[ticker] = bars
            except Exception as error:
                print(f"outcome failed: {ticker} {type(error).__name__}", file=sys.stderr)
                continue
        outcome = evaluate_outcome(bars, source_row)
        if not outcome:
            continue
        row_number = current_outcomes.get(outcome[0])
        if row_number:
            write_row(service, spreadsheet_id, f"Outcomes!A{row_number}:Q{row_number}", outcome)
            refreshed_outcomes += 1
        else:
            append_rows(service, spreadsheet_id, "Outcomes!A4:Q", [outcome])
            appended_outcomes += 1

    print(json.dumps({
        "asOfDate": scan_date,
        "newSignals": len(new_rows),
        "outcomesAppended": appended_outcomes,
        "outcomesRefreshed": refreshed_outcomes,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
