import json
import re
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler

NAVER_PRICE_URL = (
    "https://polling.finance.naver.com/api/realtime/has/price"
)
MAX_TICKERS = 30
TICKER_PATTERN = re.compile(r"^[0-9]{6}$")


def fetch_batch_prices(tickers):
    if not tickers:
        return {}

    params = urllib.parse.urlencode(
        {"itemCodes": ",".join(tickers)}
    )
    request = urllib.request.Request(
        f"{NAVER_PRICE_URL}?{params}",
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 Chrome/124 Safari/537.36"
            ),
            "Referer": "https://finance.naver.com/",
            "Accept": "application/json",
        },
    )

    results = {}
    with urllib.request.urlopen(request, timeout=5) as response:
        payload = json.loads(
            response.read().decode("utf-8")
        )

    areas = payload.get("result", {}).get("areas", [])
    items = areas[0].get("datas", []) if areas else []

    for item in items:
        ticker = str(item.get("cd", "")).strip()
        price = int(item.get("nv") or 0)
        difference = int(item.get("cv") or 0)
        rate = float(item.get("cr") or 0)
        direction = str(item.get("rf", "3"))

        if direction in {"4", "5"}:
            difference = -abs(difference)
            rate = -abs(rate)

        if ticker in tickers and price > 0:
            results[ticker] = {
                "success": True,
                "ticker": ticker,
                "price": price,
                "diff": difference,
                "rate": rate,
                "source": "naver_polling",
                "timestamp": int(time.time() * 1000),
            }

    for ticker in tickers:
        results.setdefault(
            ticker,
            {
                "success": False,
                "ticker": ticker,
                "error": "Price unavailable",
            },
        )

    return results


class handler(BaseHTTPRequestHandler):
    def send_json(self, status, payload):
        body = json.dumps(
            payload,
            ensure_ascii=False,
        ).encode("utf-8")

        self.send_response(status)
        self.send_header(
            "Content-Type",
            "application/json; charset=utf-8",
        )
        self.send_header(
            "Cache-Control",
            "no-store, max-age=0",
        )
        self.send_header(
            "Access-Control-Allow-Origin",
            "*",
        )
        self.send_header(
            "Access-Control-Allow-Methods",
            "GET, OPTIONS",
        )
        self.send_header(
            "Access-Control-Allow-Headers",
            "Content-Type",
        )
        self.send_header(
            "X-Content-Type-Options",
            "nosniff",
        )
        self.send_header(
            "Content-Length",
            str(len(body)),
        )
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_json(200, {"success": True})

    def do_GET(self):
        try:
            parsed = urllib.parse.urlparse(self.path)
            query = urllib.parse.parse_qs(parsed.query)
            raw_tickers = query.get("tickers", [""])[0]

            requested = [
                ticker.strip()
                for ticker in raw_tickers.split(",")
                if ticker.strip()
            ]

            tickers = list(dict.fromkeys(requested))

            if not tickers:
                self.send_json(
                    400,
                    {
                        "success": False,
                        "error": "tickers is required",
                    },
                )
                return

            if len(tickers) > MAX_TICKERS:
                self.send_json(
                    400,
                    {
                        "success": False,
                        "error": (
                            f"Maximum {MAX_TICKERS} tickers"
                        ),
                    },
                )
                return

            invalid = [
                ticker
                for ticker in tickers
                if not TICKER_PATTERN.fullmatch(ticker)
            ]

            if invalid:
                self.send_json(
                    400,
                    {
                        "success": False,
                        "error": "Invalid ticker format",
                        "invalidTickers": invalid,
                    },
                )
                return

            prices = fetch_batch_prices(tickers)
            succeeded = sum(
                1
                for item in prices.values()
                if item.get("success")
            )

            self.send_json(
                200,
                {
                    "success": succeeded > 0,
                    "prices": prices,
                    "requestedCount": len(tickers),
                    "updatedCount": succeeded,
                    "delayed": succeeded < len(tickers),
                    "serverTime": int(time.time() * 1000),
                },
            )

        except Exception as error:
            self.send_json(
                502,
                {
                    "success": False,
                    "error": "Upstream price service unavailable",
                    "detail": type(error).__name__,
                },
            )
