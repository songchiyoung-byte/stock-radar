import concurrent.futures
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler

MOBILE_PRICE_URL = "https://m.stock.naver.com/api/stock/{ticker}/basic"
BATCH_PRICE_URL = (
    "https://polling.finance.naver.com/api/realtime/has/price"
)
MAX_TICKERS = 30
TICKER_PATTERN = re.compile(r"^[0-9]{6}$")

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

    cleaned = re.sub(
        r"[^0-9+\-.]",
        "",
        str(value),
    )

    if cleaned in {"", "+", "-", "."}:
        return number_type(0)

    return number_type(cleaned)


def fetch_mobile_price(ticker):
    url = MOBILE_PRICE_URL.format(ticker=ticker)
    headers = {
        **COMMON_HEADERS,
        "Referer": (
            f"https://m.stock.naver.com/domestic/stock/{ticker}/total"
        ),
    }
    request = urllib.request.Request(url, headers=headers)

    with urllib.request.urlopen(request, timeout=5) as response:
        payload = json.loads(
            response.read().decode("utf-8")
        )

    price = parse_number(payload.get("closePrice"), int)
    difference = parse_number(
        payload.get("compareToPreviousClosePrice"),
        int,
    )
    rate = parse_number(
        payload.get("fluctuationsRatio"),
        float,
    )

    if price <= 0:
        raise ValueError("Missing closePrice")

    return {
        "success": True,
        "ticker": ticker,
        "price": price,
        "diff": difference,
        "rate": rate,
        "marketStatus": payload.get("marketStatus"),
        "source": "naver_mobile",
        "timestamp": int(time.time() * 1000),
    }


def fetch_mobile_prices(tickers):
    results = {}
    errors = {}

    worker_count = min(6, len(tickers))

    with concurrent.futures.ThreadPoolExecutor(
        max_workers=worker_count
    ) as executor:
        futures = {
            executor.submit(fetch_mobile_price, ticker): ticker
            for ticker in tickers
        }

        for future in concurrent.futures.as_completed(futures):
            ticker = futures[future]

            try:
                results[ticker] = future.result()
            except urllib.error.HTTPError as error:
                errors[ticker] = (
                    f"naver_mobile_http_{error.code}"
                )
            except urllib.error.URLError as error:
                errors[ticker] = (
                    f"naver_mobile_network_{type(error.reason).__name__}"
                )
            except Exception as error:
                errors[ticker] = (
                    f"naver_mobile_{type(error).__name__}"
                )

    return results, errors


def fetch_batch_prices(tickers):
    if not tickers:
        return {}, {}

    params = urllib.parse.urlencode(
        {"itemCodes": ",".join(tickers)}
    )
    headers = {
        **COMMON_HEADERS,
        "Referer": "https://finance.naver.com/",
    }
    request = urllib.request.Request(
        f"{BATCH_PRICE_URL}?{params}",
        headers=headers,
    )

    results = {}
    errors = {}

    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            payload = json.loads(
                response.read().decode("utf-8")
            )

        areas = payload.get("result", {}).get("areas", [])
        items = (
            areas[0].get("datas", [])
            if areas
            else []
        )

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

    except urllib.error.HTTPError as error:
        reason = f"naver_polling_http_{error.code}"
        errors.update({
            ticker: reason
            for ticker in tickers
        })
    except urllib.error.URLError as error:
        reason = (
            "naver_polling_network_"
            f"{type(error.reason).__name__}"
        )
        errors.update({
            ticker: reason
            for ticker in tickers
        })
    except Exception as error:
        reason = (
            "naver_polling_"
            f"{type(error).__name__}"
        )
        errors.update({
            ticker: reason
            for ticker in tickers
        })

    return results, errors


def fetch_prices(tickers):
    results, errors = fetch_mobile_prices(tickers)

    missing = [
        ticker
        for ticker in tickers
        if ticker not in results
    ]

    if missing:
        fallback_results, fallback_errors = (
            fetch_batch_prices(missing)
        )
        results.update(fallback_results)

        for ticker, reason in fallback_errors.items():
            previous = errors.get(ticker)

            errors[ticker] = (
                f"{previous};{reason}"
                if previous
                else reason
            )

    for ticker in tickers:
        if ticker not in results:
            results[ticker] = {
                "success": False,
                "ticker": ticker,
                "error": errors.get(
                    ticker,
                    "Price unavailable",
                ),
            }

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

            prices = fetch_prices(tickers)
            succeeded = sum(
                1
                for item in prices.values()
                if item.get("success")
            )

            self.send_json(
                200 if succeeded else 502,
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
                500,
                {
                    "success": False,
                    "error": "Price API internal error",
                    "detail": type(error).__name__,
                },
            )
