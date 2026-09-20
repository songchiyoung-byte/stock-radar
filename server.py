#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import sys
import json
import os
import urllib.request
import urllib.parse
from http.server import HTTPServer, SimpleHTTPRequestHandler

PORT = int(os.environ.get("PORT", 8000))
DIR_PATH = os.path.dirname(os.path.abspath(__file__))

def fetch_batch_prices(tickers):
    """
    네이버 증권 멀티 시세 API를 사용하여 여러 종목의 현재가를 한 번에 조회합니다.
    (해외 서버 IP에서도 차단 없이 응답)
    """
    if not tickers:
        return {}

    ticker_param = ",".join(tickers)
    url = f"https://polling.finance.naver.com/api/realtime/has/price?itemCodes={ticker_param}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://finance.naver.com/"
    }

    result = {}
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            items = data.get("result", {}).get("areas", [{}])[0].get("datas", [])
            for item in items:
                code = item.get("cd")
                price = int(item.get("nv", 0))          # 현재가
                diff = int(item.get("cv", 0))           # 전일 대비
                rate = float(item.get("cr", 0.0))       # 등락률
                # 하락/상승 부호 반영
                rf = item.get("rf", "3") # 4: 하한, 5: 하락, 2: 상승, 1: 상한
                if rf in ["4", "5"]:
                    diff = -abs(diff)
                    rate = -abs(rate)

                if price > 0:
                    result[code] = {
                        "success": True,
                        "ticker": code,
                        "price": price,
                        "diff": diff,
                        "rate": rate,
                        "source": "naver_polling"
                    }
    except Exception as e:
        print(f"Fetch batch failed: {e}")

    # 실패한 종목은 개별 폴백
    for t in tickers:
        if t not in result:
            result[t] = {"success": False, "ticker": t, "error": "Fetch failed"}

    return result

class StockRadarHandler(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        if path == "/api/prices":
            tickers_arg = query.get("tickers", [""])
            tickers = [t.strip() for t in tickers_arg[0].split(",") if t.strip()] if tickers_arg else []
            data = fetch_batch_prices(tickers)
            
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps({"success": True, "prices": data}, ensure_ascii=False).encode("utf-8"))
            return

        if path in ["/", "/dashboard"]:
            html_file_path = os.path.join(DIR_PATH, "dashboard.html")
            if os.path.exists(html_file_path):
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                with open(html_file_path, "rb") as f:
                    self.wfile.write(f.read())
                return

        return super().do_GET()

def main():
    server_address = ("0.0.0.0", PORT)
    httpd = HTTPServer(server_address, StockRadarHandler)
    print(f"🚀 Stock Radar Server running on port {PORT}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        httpd.server_close()

if __name__ == "__main__":
    main()
