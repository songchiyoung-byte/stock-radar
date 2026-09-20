#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI 주식 관찰 장바구니 - 원클릭 실시간 증권 데이터 연동 엔진
=============================================================
- 증권사/포털(네이버/다음) 실시간 시세 크롤러 내장
- 단일 스크립트 실행으로 백엔드 서버 + 대시보드 웹앱 동시 가동
- 맥북 기본 브라우저 자동 오픈 (http://localhost:8000)
"""

import sys
import json
import os
import re
import urllib.request
import urllib.error
import urllib.parse
import webbrowser
from http.server import HTTPServer, SimpleHTTPRequestHandler

PORT = int(os.environ.get("PORT", 8000))
DIR_PATH = os.path.dirname(os.path.abspath(__file__))

def fetch_live_stock_price(ticker):
    """
    네이버 및 다음 금융 실시간 시세를 조회합니다.
    """
    # 1. 네이버 금융 모바일 실시간 API 시도
    naver_url = f"https://m.stock.naver.com/api/stock/{ticker}/realtime"
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    }
    try:
        req = urllib.request.Request(naver_url, headers=headers)
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            now_str = str(data.get("nowValue", "0")).replace(",", "")
            change_str = str(data.get("changeValue", "0")).replace(",", "")
            change_type = data.get("changeType", {}).get("name", "FLAT")
            rate_str = str(data.get("changeRate", "0.0"))
            
            price = int(now_str) if now_str.isdigit() else 0
            diff = int(change_str) if change_str.isdigit() else 0
            if change_type == "FALL":
                diff = -diff
                
            if price > 0:
                return {
                    "success": True,
                    "ticker": ticker,
                    "price": price,
                    "diff": diff,
                    "rate": float(rate_str) if rate_str.replace(".", "", 1).isdigit() else 0.0,
                    "source": "naver"
                }
    except Exception:
        pass

    # 2. 다음 금융 모바일 API 폴백
    try:
        daum_url = f"https://m.finance.daum.net/api/quotes/A{ticker}?summary=1"
        daum_headers = {
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_5 like Mac OS X)",
            "Referer": "https://m.finance.daum.net/"
        }
        req = urllib.request.Request(daum_url, headers=daum_headers)
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            price = int(data.get("tradePrice", 0))
            diff = int(data.get("changePrice", 0))
            if data.get("change") == "FALL":
                diff = -diff
            rate = float(data.get("changeRate", 0.0)) * 100
            if price > 0:
                return {
                    "success": True,
                    "ticker": ticker,
                    "price": price,
                    "diff": diff,
                    "rate": round(rate, 2),
                    "source": "daum"
                }
    except Exception:
        pass

    return {"success": False, "ticker": ticker, "error": "Fetch failed"}

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

        # 실시간 주가 API 엔드포인트
        if path == "/api/prices":
            tickers_arg = query.get("tickers", [""])
            tickers = tickers_arg[0].split(",") if tickers_arg and tickers_arg[0] else []
            data = {}
            for t in tickers:
                t = t.strip()
                if t:
                    data[t] = fetch_live_stock_price(t)
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps({"success": True, "prices": data}, ensure_ascii=False).encode("utf-8"))
            return

        # 대시보드 메인 페이지 서빙
        if path == "/" or path == "/dashboard":
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
    url = f"http://localhost:{PORT}"
    
    print("\n" + "=" * 65)
    print("🚀 [Buy Zone Radar] 실시간 증권 데이터 연동 엔진 가동 완료!")
    print(f"📡 서버 주소: {url}")
    print("✨ 실제 거래소/증권사 실시간 시세 파이프라인 직결 활성화")
    print("=" * 65 + "\n")
    print(f"👉 브라우저가 자동으로 열리지 않으면 주소창에 {url} 을 입력해 주세요.")
    print("👉 종료하려면 터미널에서 Ctrl + C 를 누르세요.\n")

    try:
        webbrowser.open(url)
    except Exception:
        pass

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n서버를 종료합니다.")
        httpd.server_close()

if __name__ == "__main__":
    main()
