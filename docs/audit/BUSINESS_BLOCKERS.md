# [SBK-AUDIT-02] BUSINESS BLOCKERS & REPRODUCTION PROOF: 20 Defect Deep-Dive

**Document ID:** SBK-DOC-AUDIT-002  
**Baseline Commit:** `d5a026e` / HEAD `8b7d6e0`  
**Classification Criteria:**
- **P0 (10건):** 실제 고객 신청 유실, 개인정보 유출·위법, 가짜 매출 인식, 허위 정산 등 사업 존폐 직결 위험
- **P1 (8건):** 전환율 저하, 매칭 SLA 불발, 과장 광고 및 미계약 파트너 오인 리스크
- **P2 (2건):** 내부 관리 지표 왜곡 및 배포 문서 불일치

---

## 1. P0 차단 요인 (실거래·개인정보·매출 직결 결함 10건)

### [B01] 고객 및 파트너 신청 화면의 서버 미저장 (가짜 접수)
- **위치:** `slotbridge/inquiry.html` (Lines 324-329), `slotbridge/partner-apply.html` (Lines 248-255)
- **코드 증거:**
  ```javascript
  // inquiry.html
  const mockToken = 'cst_' + Math.random().toString(36).substring(2, 10);
  window.location.href = `status.html?token=${mockToken}&date=...`;

  // partner-apply.html
  resEl.innerHTML = `<strong>파트너 제휴 신청 완료!</strong>...`; // fetch 호출 전무
  ```
- **사업적 손실:** 실제 유입된 고객의 긴급 청소 의뢰와 시공 파트너 신청이 100% 증발함. 고객은 접수된 줄 알고 대기하다가 당일 이사 현장에서 청소팀이 오지 않아 분쟁 발생 (플랫폼 신뢰도 즉시 실추).
- **최소 수정 방향:** 실제 백엔드 API (`POST /api/v1/slotbridge/inquiry`, `POST /api/v1/slotbridge/partners/apply`) 호출 및 D1 저장 검증 전까지는 '접수 완료' 화면을 차단하거나 명확한 서비스 준비중(또는 데모) 안내로 격리.

---

### [B02] 예약·취소·슬롯·정산 화면의 정적 가짜 처리 (Dummy UI)
- **위치:** `slotbridge/status.html` (Lines 149-158), `slotbridge/partner-slots.html` (Lines 132-155), `slotbridge/admin-billing.html`, `slotbridge/growth-dashboard.html`
- **코드 증거:**
  ```javascript
  // status.html
  function executeRevocation() {
    document.getElementById('main-card').style.display = 'none';
    document.getElementById('erased-banner').style.display = 'block'; // 서버 통신 없음
  }
  ```
- **사업적 손실:** 고객이 '취소 및 파기'를 눌렀으나 데이터베이스에는 아무런 요청도 전송되지 않아 파트너에게 고객 연락처가 그대로 노출되는 개인정보 사고 발생. 파트너 화면의 슬롯 등록 및 오퍼 수락 역시 서버에 반영되지 않아 현장 매칭 불가능.
- **최소 수정 방향:** UI의 모든 상태 변경 버튼을 실제 인증된 API와 바인딩하고, 미구현 상태에서는 동작을 비활성화하거나 데모 모드 배지를 강제 적용.

---

### [B03] Cloudflare 운영 Worker 엔트리 및 D1 라우팅 부재
- **위치:** `wrangler.json`, `docs/product/DEPLOY_RUNBOOK.md` (Line 42 참조 누락)
- **코드 증거:**
  - `wrangler.json`에 `"assets": { "directory": "." }`만 존재.
  - 배포 문서가 참조하는 `src/worker.js`가 파일 트리에 물리적으로 존재하지 않음 (`ENOENT`).
  - `src/db.py`는 로컬 CPython의 `sqlite3` 라이브러리를 직접 임포트하여 Cloudflare D1 HTTP API 또는 Worker D1 바인딩(`env.DB`)과 직접 호환되지 않음.
- **사업적 손실:** 작성된 1,500줄의 Python 핵심 비즈니스 로직(매칭, 상태머신, 복식부기 원장)이 외부 웹 요청을 전혀 처리할 수 없는 고립된 로컬 스크립트 상태임.
- **최소 수정 방향:** 얇은 Cloudflare Worker 엔트리(Python Worker 지원 검토 또는 경량 JS 라우터)를 신설하고 D1 바인딩과 최소 HTTP 엔드포인트를 연결.

---

### [B04] 자체 제작 HMAC/XOR 스트림 암호화 및 하드코딩 키 위험
- **위치:** `src/crypto.py` (Lines 16, 45-55, 62-85), `src/attribution.py` (Line 14)
- **코드 증거:**
  ```python
  # crypto.py
  DEFAULT_KEY = os.environ.get("SBK_ENCRYPTION_KEY", "sbk_secret_key_32_bytes_test_2026_ok!")
  def _keystream_generator(key_bytes, nonce): ... # 자체 HMAC-SHA256 CTR XOR 루프

  # attribution.py
  SECRET_KEY = "sbk_attribution_hmac_secret_2026_key" # 소스코드 내 고정 키
  ```
- **사업적 손실:** 문서상 표방한 "AES-256-GCM" 표준과 완전히 불일치하는 자체 암호 알고리즘 사용. 소스코드 누출 시 고객의 성명과 휴대전화번호 평문이 즉시 노출되어 개인정보보호법 제29조(안전조치의무 위반, 최대 수천만 원 과태료) 위반.
- **최소 수정 방향:** 표준 `AES-GCM`(Web Crypto 호환)으로 전면 교체, 하드코딩된 fallback 키를 완전 제거하고 환경변수 미주입 시 시스템 시작을 차단하는 Fail-Closed 적용.

---

### [B05] 서명 없는 추천 파트너 ID 수용 및 리퍼럴 가로채기 취약점
- **위치:** `src/intake.py` (Lines 83-92), `slotbridge/widget.js` (Lines 45-52)
- **코드 증거:**
  - `intake.py`에서 `attribution_partner_id` 문자열만 넘어와도 파트너가 `APPROVED` 상태면 서명 검증 없이 정산 대상 귀속으로 인정.
  - `widget.js`는 URL에 단순 `?ref=partner_id` 평문 파라미터만 결합하여 전송.
- **사업적 손실:** 악의적 사용자가 URL 쿼리스트링 조작으로 리퍼럴 파트너 ID를 임의 변경하여 건당 7,000원의 플랫폼 수수료를 부정 수급(수익 가로채기)할 수 있음.
- **최소 수정 방향:** `src/attribution.py`의 HMAC 서명 토큰(`attribution_token`) 검증을 필수화하고, 서명 없는 단순 `ref` 파라미터에 대한 수수료 지급을 원천 차단.

---

### [B06] 공급업체 자격·보험 검증 게이트 취약 및 가상 데이터 오염
- **위치:** `src/supplier.py` (Lines 52-54, 88-92), `migrations/seeds.sql`
- **코드 증거:**
  - `insurance_policy = data.get("liability_insurance_policy_number")`에서 임의 문자열만 입력되면 즉시 `insurance_status = 'VERIFIED'` 처리.
  - `approve_partner()` 호출 시 보험 상태를 무조건 `VERIFIED`로 덮어씀.
  - `seeds.sql`에 가상 사업자번호(`214-88-00001` 등)와 가짜 평점(4.8~5.0)이 운영 테이블 구조와 동일하게 적재됨.
- **사업적 손실:** 무보험·무자격 청소업체에 고객을 연결했다가 현장 파손(고가 인덕션/대리석 손상) 발생 시 플랫폼이 연대 책임을 지거나 소송에 휘말림. 가상 시드 데이터가 운영 환경에 노출되어 허위 실적 오인 유발.
- **최소 수정 방향:** 보험 증권 및 사업자등록증 수동 검수 전까지 `CONTRACT_ACTIVE` 진입을 불허하고, `seeds.sql`의 운영 환경 주입을 물리적으로 차단.

---

### [B07] 공급자 일방 신고에 의한 성과 수수료 발생 및 검증 없는 상태 전이
- **위치:** `src/engine.py` (Lines 185-195), `src/matching.py` (Lines 150-165)
- **코드 증거:**
  - `submit_fulfillment` 함수는 시공팀이 완료 신고만 하면 즉시 `verification_status = 'VERIFIED'`로 기록하고 원장에 수수료 분개를 실행.
  - `transition_state` 함수는 데이터베이스의 현재 상태를 원자적으로 비교하지 않고 호출자가 넘긴 `from_state` 인자만 검사.
- **사업적 손실:** 시공팀이 실제로 청소하지 않고 사진 더미만 제출해도 플랫폼이 수수료 8%를 청구서에 기재하여 공급팀과의 극단적 정산 분쟁 및 고객 민원 폭발.
- **최소 수정 방향:** 완료 신고 후 고객 만족 확인 또는 24시간 분쟁 유예 기간이 경과한 후에만 관리자 승인을 거쳐 `VERIFIED` 및 원장 분개가 발생하도록 분리.

---

### [B08] 월별 청구 및 정산 생성 시 거래 발생 기간 필터 누락
- **위치:** `src/revenue.py` (Lines 150-170, 260-275)
- **코드 증거:**
  ```python
  # revenue.py
  matches = db.execute("SELECT final_amount FROM matches WHERE partner_id = ? AND status IN ('VERIFIED', 'BILLABLE', 'COMPLETED')", (partner_id,)).fetchall()
  # WHERE 절에 billing_period나 완료 일시(completed_at) 필터가 전무함!
  total_service = sum(r["final_amount"] or 250000 for r in matches) # NULL이면 25만원 가정
  ```
- **사업적 손실:** 지난달에 이미 청구된 매칭 거래가 이번 달 청구서에 또다시 포함되어 동일 거래에 대해 2회, 3회 중복 청구서가 발송됨 (심각한 계약 위반 및 회계 부정).
- **최소 수정 방향:** 매칭 테이블에 `billing_period` 및 `invoiced_at` 컬럼을 결속하고, 이미 청구된 거래(`invoiced = 1`)는 재청구 대상에서 영구 제외하는 멱등성 보장.

---

### [B09] 실제 은행 입금 확인 없는 허위 현금 매출 대사 (Phantom Cash)
- **위치:** `src/revenue.py` (Lines 343-358)
- **코드 증거:**
  ```python
  # revenue.py
  total_revenue_expected = inv_sum
  total_revenue_collected = inv_sum # 실제 입금액을 조회하지 않고 청구액을 그대로 복사!
  discrepancy_amount = 0
  status = "BALANCED" # 무조건 대차 일치로 기재
  ```
- **사업적 손실:** 파트너가 수수료를 한 푼도 입금하지 않은 미수금 상태임에도, 시스템은 현금이 100% 회수된 것처럼 처리하여 유입 파트너에게 35% 정산금을 먼저 송금해 버리는 플랫폼 순손실(자본 잠식) 발생.
- **최소 수정 방향:** 은행 입금 내역 증빙(계좌 입금 거래내역 또는 수동 입금 대사 입력)이 없는 인보이스는 `UNCOLLECTED`로 격리하고, 미수금과 실수령액의 불일치 시 `DISCREPANCY` 상태를 기록.

---

### [B10] 불변 원장 중복 역분개 및 임의 생성된 세금계산서 번호
- **위치:** `src/revenue.py` (Lines 75-90, 235-245)
- **코드 증거:**
  - `record_ledger_reversal()` 재실행 시 기존 역분개 건에 대해 또다시 역분개가 실행되어 원장 잔액이 마이너스로 붕괴될 수 있음.
  - `tax_inv_num = f"TX-{uuid.uuid4().hex[:12].upper()}"`로 내부 임의 번호를 발행하여 전자세금계산서 완료로 표시.
- **사업적 손실:** 홈택스 정식 승인번호가 아닌 가짜 `TX-` 번호로 정산서를 발급했다가 세무조사 시 허위 세금계산서 발급에 따른 조세범처벌법 위반 및 매입세액 불공제 가산세 부과.
- **최소 수정 방향:** 원장 역분개 시 1회 한정 참조 키(`reversal_of_event_id`) 유니크 제약을 강제하고, 세금계산서는 국세청 외부 발행 증빙 확인 시에만 `VERIFIED` 상태로 전이.

---

## 2. P1 차단 요인 (고객 전환 및 운영 효율 저해 결함 8건)

| ID | 결함 내용 | 사업적 위험 | 최소 수정 방향 |
| :--- | :--- | :--- | :--- |
| **B11** | `matching.py` 매칭 시 고객의 희망 시간대(`AM`/`PM`), 동, 면적, 상세 작업조건 필터 누락 | 불가능한 시간/지역에 배정되어 당일 노쇼 및 고객 불만 폭발 | 매칭 엔진 SQL에 시간대·권역·평수 하드필터 결속 |
| **B12** | 15분 오퍼의 자동 만료 스케줄러 부재 (`handle_timeout` 수동 호출 의존) | 15분이 지나도 오퍼가 자동 취소되지 않고 방치되어 차순위 배정 불발 | 스케줄러/하트비트 기반 타임아웃 감시 또는 온디맨드 만료 트리거 연결 |
| **B13** | `status.html`의 '개인정보 파기 완료' 안내가 서버 연동 없는 거짓 화면 | 고객 정보는 DB에 남아있는데 파기되었다고 허위 고지 (개보법 위반) | 실제 삭제 API 연동 및 서버 삭제 확인 전에는 파기 완료 문구 노출 금지 |
| **B14** | `intake.html` 날짜 생성이 브라우저 로컬 `toISOString()`(UTC)으로 생성되어 날짜 어긋남 | 자정 전후 신청 시 서울 표준시 기준 어제나 엉뚱한 날짜로 청소 신청 | `Asia/Seoul` 기준 정규화 및 과거 일자 신청 서버측 검증 |
| **B15** | `widget.js`의 `innerHTML` 삽입 및 무서명 `?ref=` 파라미터 사용 | 외부 파트너 사이트 삽입 시 XSS 취약점 및 제휴 출처 조작 위험 | 안전한 `textContent` 렌더링 및 HMAC 서명 토큰 바인딩 |
| **B16** | 파트너 대시보드 및 관리자 콘솔에 가짜 정적 매출/계좌 하드코딩 노출 | 관리자 및 파트너가 허위 정산 실적을 보고 의사결정 왜곡 | 운영 콘솔의 정적 더미 수치를 제거하고 실제 데이터가 없을 시 '0건' 표시 |
| **B17** | 마케팅 문서에서 미계약 15개사를 '확보된 고객사', '확정 수요'로 표현 | 영업 및 대외 커뮤니케이션 시 허위 과장 광고 및 공정위 제재 위험 | `PROSPECT`, `PIPELINE`으로 용어 정정하고 실제 계약 전까지 활성 파트너 간주 금지 |
| **B18** | `analytics.py` 단위경제성 계산 시 CAC=0원, 65% 순기여이익을 상수로 단정 | 마케팅 인건비, 인프라비, 결제 수수료 미반영으로 인한 적자 사업 착시 | 미측정 비용 항목을 `UNKNOWN`으로 분리하고 실측치 기반 계산으로 교정 |

---

## 3. P2 차단 요인 (사업관리 및 릴리스 지표 결함 2건)

| ID | 결함 내용 | 사업적 위험 | 최소 수정 방향 |
| :--- | :--- | :--- | :--- |
| **B19** | D1 무료 한도 사용량이 로컬 메모리 카운터 추정치로 계산됨 | 프로덕션 환경의 실제 Cloudflare 쿼터와 괴리 발생 가능 | 로컬 추정치와 프로덕션 메트릭을 명확히 분리 표기 |
| **B20** | `TRANSITION_COMPLETION_REPORT.md`의 '14단계 완료, 프로덕션 배포 완료' 선언 불일치 | 기술적 결함(B01~B10)이 잔존함에도 완성된 것으로 오인하여 성급한 런칭 | 사실에 기반한 조건부(CONDITIONAL) 및 외부 차단(BLOCKED_EXTERNAL)으로 판정 교정 |
