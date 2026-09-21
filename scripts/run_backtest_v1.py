"""Collect the Swing Dataset v1 from public daily OHLCV data.

Run from the repository root. Output is intentionally a compact JSON dataset
that can be loaded into Google Sheets without re-running historical scans.
"""

import importlib.util
import json
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
    ("066570", "LG전자"), ("003550", "LG"),
    ("003670", "POSCO홀딩스"), ("010130", "고려아연"),
    ("035420", "NAVER"), ("035720", "카카오"),
    ("034730", "SK"), ("105560", "KB금융"),
    ("055550", "신한지주"), ("086790", "하나금융지주"),
    ("316140", "우리금융지주"), ("032830", "삼성생명"),
    ("329180", "HD현대중공업"), ("009540", "HD한국조선해양"),
    ("047810", "한국항공우주"), ("259960", "크래프톤"),
    ("068270", "셀트리온"), ("096770", "SK이노베이션"),
]
PAGES = (1, 6, 11)
HISTORY_BARS_PER_CHUNK = 300
HORIZON = 10


def load_analysis_module():
    module_path = Path(__file__).resolve().parents[1] / "api" / "analysis.py"
    spec = importlib.util.spec_from_file_location("stock_analysis", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def collect_ticker(module, ticker, company):
    by_date = {}
    for page_start in PAGES:
        bars = module.fetch_history(
            ticker,
            HISTORY_BARS_PER_CHUNK,
            page_start,
        )
        for bar in bars:
            by_date[str(bar["date"])] = bar
        time.sleep(0.15)

    bars = sorted(by_date.values(), key=lambda item: str(item["date"]))
    result = module.backtest_v1(ticker, bars, HORIZON)
    result["company"] = company
    result["barRange"] = {
        "from": bars[0]["date"] if bars else None,
        "to": bars[-1]["date"] if bars else None,
        "count": len(bars),
    }
    for signal in result.get("signals", []):
        signal["company"] = company
        signal["ruleVersion"] = "v1.0"
    return result


def main():
    module = load_analysis_module()
    results = []
    errors = []
    for ticker, company in UNIVERSE:
        try:
            results.append(collect_ticker(module, ticker, company))
        except Exception as error:  # keep other symbols collecting if one fails
            errors.append({"ticker": ticker, "company": company, "error": type(error).__name__})

    signals = [signal for result in results for signal in result.get("signals", [])]
    target_hits = sum(signal["firstEvent"] == "TARGET_HIT" for signal in signals)
    stop_hits = sum(signal["firstEvent"] == "STOP_HIT" for signal in signals)
    resolved = target_hits + stop_hits
    payload = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "ruleVersion": "v1.0",
        "strategy": "SWING",
        "horizonTradingDays": HORIZON,
        "universe": [{"ticker": ticker, "company": company} for ticker, company in UNIVERSE],
        "summary": {
            "universeCount": len(UNIVERSE),
            "completedTickerCount": len(results),
            "errorCount": len(errors),
            "signalCount": len(signals),
            "targetHitCount": target_hits,
            "stopHitCount": stop_hits,
            "targetHitRate": round(target_hits / resolved * 100, 2) if resolved else None,
            "averageR": round(sum(signal["rMultiple"] for signal in signals) / len(signals), 2) if signals else None,
        },
        "errors": errors,
        "results": results,
    }
    output = Path(__file__).resolve().parents[1] / "data" / "backtests" / "swing_v1_latest.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
