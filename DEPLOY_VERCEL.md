# Stock Radar 배포

GitHub Pages는 정적 파일만 제공하므로 `server.py`와 `/api/prices`를 실행할 수 없습니다.
이 저장소는 Vercel에서 화면과 Python API를 같은 주소로 배포하도록 구성되어 있습니다.

## 최초 배포

1. https://vercel.com/new 에 로그인합니다.
2. `songchiyoung-byte/stock-radar` 저장소를 Import합니다.
3. Framework Preset은 `Other`, Root Directory는 기본값으로 둡니다.
4. Build Command와 Output Directory는 비워 둡니다.
5. Deploy를 누릅니다.
6. 발급된 Vercel 주소의 루트 경로를 엽니다.

별도 API 키나 환경변수는 필요하지 않습니다.

## 확인 주소

- 대시보드: `https://발급주소.vercel.app/`
- 가격 API: `https://발급주소.vercel.app/api/prices?tickers=005930,000660,360750`

API가 정상이라면 `success: true`와 각 종목의 `price`, `rate`가 반환됩니다.

## 주의

- GitHub Pages 주소에서는 API가 실행되지 않습니다. 실제 사용 주소는 Vercel 주소입니다.
- 가격 데이터는 네이버 증권의 공개 시세 응답을 사용하며 주문 기능은 없습니다.
- 대시보드는 열려 있는 동안 5초마다 API를 호출합니다.
- 무료 사용량을 줄이려면 `dashboard.html`의 `REFRESH_MS`를 `15000` 이상으로 변경하세요.
