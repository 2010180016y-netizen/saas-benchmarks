# [SBK-MINI-00] Baseline Audit: Latest Source Verification & Problem Registry
**Document ID:** SBK-DOC-MINI-000  
**Inspection Date:** 2026-10-31  
**Target Commit:** `4f8ee067e29ad622e3626f8acb9436f9bc35f0f9` (origin/main)  
**Target Repository:** `https://github.com/2010180016y-netizen/saas-benchmarks`  
**Execution Mode:** READ-ONLY (No code modification permitted)  

---

## 1. 9대 핵심 결함 정밀 검증 매트릭스

| 번호 | 결함 항목 | 식별된 코드 위치 (파일:행) | 코드 증거 및 상세 내용 | 판정 |
| :---: | :--- | :--- | :--- | :---: |
| **01** | **기본 암호화/관리자/추천 키** | `src/worker.js:47`<br>`src/worker.js:75, 102`<br>`src/worker.js:586` | • `env.SBK_ENCRYPTION_KEY \|\| "sbk_test_dev_key_32_bytes_safe_2026_ok!"`<br>• `env.SBK_ATTRIBUTION_SECRET \|\| "sbk_attribution_hmac_secret_2026_key"`<br>• `env.SBK_ADMIN_KEY \|\| "sbk_admin_secret_dev_2026"`<br>환경변수 부재 시 하드코딩된 기본 시크릿으로 우회 동작 (Fail-Closed 위반). | **CONFIRMED** |
| **02** | **평문 전화번호 저장** | `src/worker.js:277`<br>`src/worker.js:449` | • `inquiries` D1 삽입 시 `customer_phone_hash` 컬럼에 `phone.replace(/[^0-9]/g, "")` 평문 숫자를 그대로 바인딩.<br>• `partners.contact_phone`에 대표자 전화번호 평문 저장. | **CONFIRMED** |
| **03** | **고객 확인 무인증** | `src/worker.js:540-575` | • `POST /matches/:id/confirm` 엔드포인트에 고객 토큰(`tracking_token`), 서명, 인증 헤더 검증 로직이 전무함.<br>• 임의 호출자가 `matchId`만 알면 `BILLABLE` 및 완료 상태로 강제 전이 가능 (IDOR). | **CONFIRMED** |
| **04** | **D1 없을 때 성공 응답** | `src/worker.js:317-327`<br>`src/worker.js:350-360`<br>`src/worker.js:390-397`<br>`src/worker.js:460-470`<br>`src/worker.js:530-536`<br>`src/worker.js:570-576` | • `env.DB` 바인딩 부재 시 503 오류가 아닌 `{ ok: true, demo_mode: true }`를 HTTP 200/201로 반환.<br>• DB에 저장되지 않았음에도 클라이언트에 성공한 것처럼 은폐. | **CONFIRMED** |
| **05** | **실제 Worker API 누락** | `src/worker.js` (전체) | • 슬롯 등록/조회(`/slots`), 오퍼 평가/배정(`/dispatch`), 오퍼 수락/거절(`/offers/:id/accept`), 청구서/정산 대사(`/admin/invoices`, `/admin/reconcile`) 라우팅이 Worker JS에 전무함.<br>• Python 파일에만 존재하여 엣지 서버리스에서 비즈니스 라이프사이클 완주 불가. | **CONFIRMED** |
| **06** | **Python-only 출시 게이트** | `src/gate.py:12-61`<br>`src/worker.js` (미적용) | • `src/gate.py`에 선언된 `PilotReleaseGate`(운영자 승인 전 실거래/실개인정보/공개마케팅 차단)가 `src/worker.js`에는 전혀 바인딩되지 않음.<br>• Worker 인입 시 출시 게이트 우회 위험. | **CONFIRMED** |
| **07** | **공개 정적 파일 범위** | `wrangler.json:6`<br>`.assetsignore:1-3` | • `wrangler.json`에 `"assets": { "directory": "." }`로 전체 루트 지정.<br>• `.assetsignore`가 `.git`, `.assetsignore`, `wrangler.json` 단 3개만 제외하여, `src/*.py`, `migrations/*.sql`, `tests/`, `docs/` 등 내부 소스 전체가 웹 정적 에셋으로 공개 노출 위험. | **CONFIRMED** |
| **08** | **시연용 UI** | `slotbridge/partner-slots.html:248-265`<br>`slotbridge/inquiry.html:310-315, 350` | • `partner-slots.html`의 `acceptOffer()`, `addSlot()`이 `alert("[데모 모드]...")` 단순 팝업만 실행.<br>• `inquiry.html`이 URL에서 `?token=`을 파싱하지 않고 `attribution_token` 필드를 제출하지 않음. | **CONFIRMED** |
| **09** | **실제 Worker 테스트 부재** | `tests/test_api_adapter.py:49-53` | • `test_api_adapter.py`가 실제 `src/worker.js` 런타임이 아닌 Python 모의 클래스 `EdgeWorkerMockRouter`를 검증함.<br>• 실제 JavaScript Worker 동작을 검증하는 테스트 스위트 부재. | **CONFIRMED** |

---

## 2. 정상 코드 동결 목록 (Code Freeze List)

아래 자산은 검증이 완료되었거나 비즈니스 연속성을 유지해야 하므로 향후 티켓에서 절대 임의 수정하거나 파기하지 않습니다:

1. **기존 SaaS 비교 사이트 및 엣지 단축 리디렉션:**
   - `index.html` (메인 홈 SaaS 포털)
   - `benchmarks/` (`crm/`, `project-management/`, `analytics/`)
   - `go/` (엣지 307 단축 리디렉션 시스템)
   - `css/`, `js/` 디렉터리 내 기존 스타일 및 스크립트
2. **검증된 핵심 Python 도메인 엔진:**
   - `src/db.py`: SQLite / D1 스키마 관리자 및 트랜잭션 러너
   - `src/crypto.py`: 표준 `AES-256-GCM` PII 암호화 및 무작위 IV 생성기
   - `src/attribution.py`: Web Crypto 호환 HMAC-SHA256 귀속 토큰 발급 및 서명 검증기
   - `src/supplier.py`: 파트너 심사, 보험 서류 검증 대기, 1-Team-1-Job 슬롯 충돌 방어 엔진
   - `src/intake.py`: 개보법 제17조 동의 검증 및 1-Click 톰스톤 파기 엔진
   - `src/matching.py`: 10단계 상태머신, 결정적 랭킹 평가, 15분 독점 오퍼 타임아웃 회수 배치
   - `src/engine.py`: 양방향 시공 전후 사진 2장 게이트 및 고객 확인 완료 처리기
   - `src/revenue.py`: 복식부기 원장 분개, 월별 정밀 필터, 은행 입금 실대사 엔진
   - `migrations/0001_initial_schema.sql`: 21개 D1 불변 테이블 스키마
3. **기존 회귀 자동화 테스트 스위트:**
   - `tests/test_*.py` 20개 테스트 스위트 (144개 테스트 케이스)
