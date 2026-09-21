import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler


PRICE_HISTORY_URL = (
    "https://m.stock.naver.com/api/stock/{ticker}/price"
)
TICKER_PATTERN = re.compile(r"^[0-9]{6}$")
MIN_BARS = 60
HISTORY_BARS = 140
MAX_PAGE_SIZE = 60

COMMON_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) "
        "AppleWebKit/605.1.15 Version/17.5 Mobile/15E148 Safari/604.1"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
}


def parse_number(value, number_type=float):
    if value is None:
        return number_type(0)
    cleaned = re.sub(r"[^0-9+\-.]", "", str(value))
    if cleaned in {"", "+", "-", "."}:
        return number_type(0)
    return number_type(cleaned)


def round_price(value):
    if value >= 500000:
        unit = 1000
    elif value >= 100000:
        unit = 500
    elif value >= 10000:
        unit = 50
    elif value >= 5000:
        unit = 10
    elif value >= 1000:
        unit = 5
    else:
        unit = 1
    return int(round(value / unit) * unit)


def simple_average(values):
    return sum(values) / len(values) if values else None


def calculate_rsi(closes, period=14):
    changes = [closes[index] - closes[index - 1]
               for index in range(1, len(closes))]
    recent = changes[-period:]
    if len(recent) < period:
        return None
    gains = sum(max(change, 0) for change in recent) / period
    losses = sum(max(-change, 0) for change in recent) / period
    if losses == 0:
        return 100.0
    strength = gains / losses
    return 100 - (100 / (1 + strength))


def calculate_atr(bars, period=14):
    ranges = []
    for index in range(1, len(bars)):
        current = bars[index]
        previous_close = bars[index - 1]["close"]
        ranges.append(max(
            current["high"] - current["low"],
            abs(current["high"] - previous_close),
            abs(current["low"] - previous_close),
        ))
    return simple_average(ranges[-period:]) if len(ranges) >= period else None


def normalize_history(payload):
    rows = payload
    if isinstance(payload, dict):
        for key in ("result", "prices", "data"):
            if isinstance(payload.get(key), list):
                rows = payload[key]
                break
    if not isinstance(rows, list):
        return []

    bars = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        close = parse_number(row.get("closePrice"))
        high = parse_number(row.get("highPrice"))
        low = parse_number(row.get("lowPrice"))
        volume = parse_number(row.get("accumulatedTradingVolume"))
        traded_at = row.get("localTradedAt") or row.get("date")
        if close > 0 and high > 0 and low > 0:
            bars.append({
                "date": traded_at,
                "close": close,
                "high": high,
                "low": low,
                "volume": max(volume, 0),
            })

    bars.sort(key=lambda item: str(item.get("date") or ""))
    return bars


def fetch_history(ticker):
    headers = {
        **COMMON_HEADERS,
        "Referer": f"https://m.stock.naver.com/domestic/stock/{ticker}/total",
    }
    bars_by_date = {}
    remaining = HISTORY_BARS
    page = 1

    while remaining > 0:
        page_size = min(remaining, MAX_PAGE_SIZE)
        params = urllib.parse.urlencode({
            "pageSize": page_size,
            "page": page,
        })
        url = f"{PRICE_HISTORY_URL.format(ticker=ticker)}?{params}"
        request = urllib.request.Request(url, headers=headers)

        with urllib.request.urlopen(request, timeout=7) as response:
            payload = json.loads(response.read().decode("utf-8"))

        page_bars = normalize_history(payload)
        if not page_bars:
            break

        count_before = len(bars_by_date)
        for bar in page_bars:
            key = str(bar.get("date") or f"page-{page}-{len(bars_by_date)}")
            bars_by_date[key] = bar

        added = len(bars_by_date) - count_before
        if added <= 0:
            break
        remaining -= added
        page += 1

    bars = list(bars_by_date.values())
    bars.sort(key=lambda item: str(item.get("date") or ""))
    return bars[-HISTORY_BARS:]


def analyze_bars(ticker, bars):
    if len(bars) < MIN_BARS:
        return {
            "success": False,
            "ticker": ticker,
            "status": "INSUFFICIENT_DATA",
            "message": f"일봉이 {MIN_BARS}개보다 적어 분석을 확정하지 않았습니다.",
            "barCount": len(bars),
        }

    closes = [bar["close"] for bar in bars]
    volumes = [bar["volume"] for bar in bars]
    current = closes[-1]
    ma20 = simple_average(closes[-20:])
    ma60 = simple_average(closes[-60:])
    ma120 = simple_average(closes[-120:]) if len(closes) >= 120 else None
    rsi14 = calculate_rsi(closes)
    atr14 = calculate_atr(bars)
    volume20 = simple_average(volumes[-21:-1])
    volume_ratio = (
        volumes[-1] / volume20
        if volume20 and volumes[-1] > 0 else None
    )

    prior20 = bars[-21:-1]
    recent_low = min(bar["low"] for bar in prior20)
    prior_high20 = max(bar["high"] for bar in prior20)

    support_candidates = [recent_low, ma20, ma60]
    support_below = [value for value in support_candidates if value <= current * 1.02]
    support = max(support_below) if support_below else recent_low

    atr = atr14 or current * 0.025
    buy_low = round_price(max(support - atr * 0.35, 1))
    buy_high = round_price(max(support + atr * 0.35, buy_low))
    stop_loss = round_price(max(support - atr * 1.5, 1))
    target = round_price(prior_high20)
    entry = (buy_low + buy_high) / 2
    risk = entry - stop_loss
    reward = target - entry
    risk_reward = reward / risk if risk > 0 else 0
    first_target = round_price(entry + risk * 2) if risk > 0 else None
    risk_percent = (risk / entry) * 100 if entry > 0 else None

    strategy_scores = {
        "PULLBACK": 0,
        "BREAKOUT_RETEST": 0,
        "OVERSOLD_REBOUND": 0,
    }

    if current >= ma60:
        strategy_scores["PULLBACK"] += 30
    if abs(current - ma20) / current <= 0.05:
        strategy_scores["PULLBACK"] += 25
    if 40 <= rsi14 <= 60:
        strategy_scores["PULLBACK"] += 20

    breakout_distance = (current - prior_high20) / prior_high20
    if -0.03 <= breakout_distance <= 0.05:
        strategy_scores["BREAKOUT_RETEST"] += 40
    if current >= ma20 >= ma60:
        strategy_scores["BREAKOUT_RETEST"] += 25
    if volume_ratio is not None and volume_ratio >= 1.2:
        strategy_scores["BREAKOUT_RETEST"] += 20

    if rsi14 <= 42:
        strategy_scores["OVERSOLD_REBOUND"] += 35
    if current > min(closes[-5:]):
        strategy_scores["OVERSOLD_REBOUND"] += 20
    if current >= recent_low:
        strategy_scores["OVERSOLD_REBOUND"] += 15

    strategy = max(strategy_scores, key=strategy_scores.get)
    technical_score = strategy_scores[strategy]
    if current >= ma60:
        technical_score += 10
    if volume_ratio is not None and 0.7 <= volume_ratio <= 3.0:
        technical_score += 5
    technical_score = min(technical_score, 100)

    target_plausible = target <= current * 1.35
    structure_valid = (
        stop_loss < buy_low <= buy_high < target and
        risk_reward >= 2.0 and
        technical_score >= 60 and
        target_plausible
    )
    in_zone = buy_low <= current <= buy_high
    gap_to_zone = ((current - buy_high) / buy_high) * 100

    status = "QUALIFIED" if structure_valid else "NOT_QUALIFIED"
    if structure_valid and in_zone:
        status = "IN_BUY_ZONE"

    strategy_names = {
        "PULLBACK": "추세 눌림목",
        "BREAKOUT_RETEST": "돌파 후 지지 확인",
        "OVERSOLD_REBOUND": "과매도 반등",
    }
    reasons = [
        f"20일선 {round_price(ma20):,}원 · 60일선 {round_price(ma60):,}원",
        f"RSI(14) {rsi14:.1f} · ATR(14) {round_price(atr):,}원",
        (
            f"거래량 20일 평균 대비 {volume_ratio:.2f}배"
            if volume_ratio is not None else
            "거래량 비교 데이터 없음"
        ),
        f"최근 저항 {target:,}원 · 예상 손익비 1:{risk_reward:.2f}",
    ]
    warnings = []
    if risk_reward < 2:
        warnings.append("예상 손익비가 1:2 미만입니다.")
    if technical_score < 60:
        warnings.append("기술 점수가 60점 미만입니다.")
    if target <= buy_high:
        warnings.append("확인된 저항이 Buy Zone보다 높지 않습니다.")
    if not target_plausible:
        warnings.append("최근 저항이 현재가보다 35% 이상 높아 목표가로 사용하지 않았습니다.")

    return {
        "success": True,
        "ticker": ticker,
        "status": status,
        "qualified": structure_valid,
        "strategy": strategy,
        "strategyName": strategy_names[strategy],
        "technicalScore": technical_score,
        "currentPrice": round_price(current),
        "buyZoneLow": buy_low if structure_valid else None,
        "buyZoneHigh": buy_high if structure_valid else None,
        "stopLoss": stop_loss if structure_valid else None,
        "entryPrice": round_price(entry) if structure_valid else None,
        "firstTargetPrice": first_target if structure_valid else None,
        "riskPercent": round(risk_percent, 2) if structure_valid else None,
        "targetPrice": target if structure_valid else None,
        "riskReward": round(risk_reward, 2),
        "inBuyZone": bool(structure_valid and in_zone),
        "gapToZonePct": round(gap_to_zone, 2) if structure_valid else None,
        "indicators": {
            "ma20": round_price(ma20),
            "ma60": round_price(ma60),
            "ma120": round_price(ma120) if ma120 else None,
            "rsi14": round(rsi14, 2),
            "atr14": round_price(atr),
            "volumeRatio20": round(volume_ratio, 2) if volume_ratio else None,
            "recentLow20": round_price(recent_low),
            "resistance20": target,
        },
        "reasons": reasons,
        "warnings": warnings,
        "barCount": len(bars),
        "asOfDate": bars[-1].get("date"),
        "calculatedAt": int(time.time() * 1000),
        "source": "Naver mobile daily OHLCV",
        "disclaimer": "기술적 참고 모델이며 적정가·수익을 보장하지 않습니다.",
    }


class handler(BaseHTTPRequestHandler):
    def send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "public, max-age=300, s-maxage=300")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_json(200, {"success": True})

    def do_GET(self):
        try:
            parsed = urllib.parse.urlparse(self.path)
            query = urllib.parse.parse_qs(parsed.query)
            ticker = query.get("ticker", [""])[0].strip()
            if not TICKER_PATTERN.fullmatch(ticker):
                self.send_json(400, {
                    "success": False,
                    "error": "Valid 6-digit ticker is required",
                })
                return
            bars = fetch_history(ticker)
            result = analyze_bars(ticker, bars)
            self.send_json(200 if result.get("success") else 422, result)
        except urllib.error.HTTPError as error:
            self.send_json(502, {
                "success": False,
                "error": "Upstream history service unavailable",
                "detail": f"HTTP {error.code}",
            })
        except urllib.error.URLError as error:
            self.send_json(502, {
                "success": False,
                "error": "Upstream history service unavailable",
                "detail": type(error.reason).__name__,
            })
        except Exception as error:
            self.send_json(500, {
                "success": False,
                "error": "Analysis API internal error",
                "detail": type(error).__name__,
            })
