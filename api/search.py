import json
import re
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler

AUTOCOMPLETE_URL = "https://ac.stock.naver.com/ac"
MOBILE_BASIC_URL = "https://m.stock.naver.com/api/stock/{ticker}/basic"
TICKER_PATTERN = re.compile(r"^[0-9]{6}$")
MAX_RESULTS = 12

COMMON_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) "
        "AppleWebKit/605.1.15 Version/17.5 Mobile/15E148 Safari/604.1"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
}


def request_json(url, referer):
    request = urllib.request.Request(
        url,
        headers={**COMMON_HEADERS, "Referer": referer},
    )

    with urllib.request.urlopen(request, timeout=6) as response:
        return json.loads(response.read().decode("utf-8"))


def normalize_market(value):
    text = str(value or "").upper()

    if "KOSDAQ" in text or "코스닥" in text:
        return "KOSDAQ"

    if "ETF" in text:
        return "ETF"

    return "KOSPI"


def walk_items(value):
    if isinstance(value, dict):
        yield value

        for child in value.values():
            yield from walk_items(child)

    elif isinstance(value, list):
        for child in value:
            yield from walk_items(child)


def normalize_item(item):
    ticker = str(
        item.get("code")
        or item.get("itemCode")
        or item.get("stockCode")
        or ""
    ).strip()

    name = str(
        item.get("name")
        or item.get("itemName")
        or item.get("stockName")
        or ""
    ).strip()

    nation = str(
        item.get("nationCode")
        or item.get("nationName")
        or "KOR"
    ).upper()

    if not TICKER_PATTERN.fullmatch(ticker) or not name:
        return None

    if nation and nation not in {"KOR", "KR", "대한민국"}:
        return None

    market_value = (
        item.get("typeCode")
        or item.get("market")
        or item.get("stockExchangeType")
        or item.get("typeName")
    )

    return {
        "ticker": ticker,
        "name": name,
        "market": normalize_market(market_value),
    }


def search_autocomplete(query):
    params = urllib.parse.urlencode(
        {
            "q": query,
            "target": "stock,index,marketindicator",
        }
    )
    payload = request_json(
        f"{AUTOCOMPLETE_URL}?{params}",
        "https://finance.naver.com/",
    )

    results = []
    seen = set()

    for item in walk_items(payload):
        normalized = normalize_item(item)

        if not normalized:
            continue

        ticker = normalized["ticker"]

        if ticker in seen:
            continue

        seen.add(ticker)
        results.append(normalized)

        if len(results) >= MAX_RESULTS:
            break

    return results


def lookup_ticker(ticker):
    payload = request_json(
        MOBILE_BASIC_URL.format(ticker=ticker),
        f"https://m.stock.naver.com/domestic/stock/{ticker}/total",
    )

    name = str(
        payload.get("stockName")
        or payload.get("itemName")
        or payload.get("name")
        or ""
    ).strip()

    if not name:
        return None

    market_value = (
        payload.get("stockExchangeType")
        or payload.get("marketType")
        or payload.get("stockEndType")
    )

    return {
        "ticker": ticker,
        "name": name,
        "market": normalize_market(market_value),
    }


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
            "public, max-age=300, stale-while-revalidate=600",
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
            search_query = query.get("q", [""])[0].strip()

            if not search_query:
                self.send_json(
                    400,
                    {
                        "success": False,
                        "error": "q is required",
                    },
                )
                return

            if len(search_query) > 40:
                self.send_json(
                    400,
                    {
                        "success": False,
                        "error": "q is too long",
                    },
                )
                return

            results = search_autocomplete(search_query)

            if TICKER_PATTERN.fullmatch(search_query):
                direct = lookup_ticker(search_query)

                if direct:
                    results = [
                        direct,
                        *[
                            item
                            for item in results
                            if item["ticker"] != search_query
                        ],
                    ]

            self.send_json(
                200,
                {
                    "success": True,
                    "query": search_query,
                    "results": results[:MAX_RESULTS],
                    "count": len(results[:MAX_RESULTS]),
                },
            )

        except urllib.error.HTTPError as error:
            self.send_json(
                502,
                {
                    "success": False,
                    "error": "Upstream search service unavailable",
                    "detail": f"HTTP {error.code}",
                },
            )
        except Exception as error:
            self.send_json(
                500,
                {
                    "success": False,
                    "error": "Search API internal error",
                    "detail": type(error).__name__,
                },
            )
