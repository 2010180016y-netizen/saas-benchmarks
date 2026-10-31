# [SBK-AUDIT-04] PROOF INDEX & AUDIT VERDICT: Evidence Ledger

**Document ID:** SBK-DOC-AUDIT-004  
**Audit Baseline Commit:** `d5a026ee84d39ba73ed4d49bfd22c02bb4d1e9ee` (HEAD `8b7d6e0`)  
**Audit Status:** AUDIT COMPLETE / NO CODE MODIFIED (Read-Only)  
**Overall Verdict:** **FAIL (Technical Staging Not Linked) & BLOCKED_EXTERNAL (Offline Partner Contracts Pending)**

---

## 1. 종합 감사 판정 (Executive Verdict)

### 1.1 기술 인프라 영역 판정: **FAIL**
- **근거:**
  1. `wrangler.json`에 Worker 엔트리 스크립트와 D1 바인딩이 누락되어 정적 파일 서빙 외에 API가 전혀 작동하지 않음.
  2. 고객 접수 화면(`inquiry.html`)과 파트너 신청 화면(`partner-apply.html`)이 클라이언트 측 자바스크립트 `Math.random()` 모의 토큰에 의존하여 서버에 단 1바이트도 저장되지 않음.
  3. `reconcile_billing_period()`가 실제 은행 입금 내역과 무관하게 `expected = collected`를 강제 삽입하여 미수금을 은폐함.
  4. 암호화가 선언된 AES-256-GCM 표준이 아닌 자체 HMAC 스트림 암호화이며, 코드에 기본 키가 하드코딩되어 있음.

### 1.2 비즈니스 운영 영역 판정: **BLOCKED_EXTERNAL**
- **근거:**
  1. 파이프라인 상의 15개 업체(시공 7곳, 공인중개 8곳)는 주소와 연락처가 확보된 영업 대상(`PROSPECT`)일 뿐, 실제 서명된 파트너 계약서나 LOI(제휴의향서)가 0건임.
  2. 통신판매중개자 법적 고지 및 개인정보 제3자 제공 동의 문안은 작성되었으나, 실제 고객 동의를 영속 적재할 감사 로그 인프라가 미연결 상태임.
  3. 실제 거래 및 은행 입금 증빙이 확보되지 않았으므로 '프로덕션 가동 중' 선언 불가.

---

## 2. 20대 결함 증거 색인표 (Defect Proof Index)

| 결함 ID | 우선순위 | 핵심 파일 경로 및 라인 | 구체적 증거 텍스트 | 대응 패치 티켓 |
| :--- | :---: | :--- | :--- | :---: |
| **B01** | **P0** | `slotbridge/inquiry.html` (L324-329)<br>`slotbridge/partner-apply.html` (L248-255) | `mockToken = 'cst_' + Math.random()...`<br>`resEl.innerHTML = '신청 완료' (fetch 없음)` | **SBK-FIX-01** |
| **B02** | **P0** | `slotbridge/status.html` (L149-158)<br>`slotbridge/partner-slots.html` (L132-155) | `function executeRevocation() { ... erased-banner.style.display = 'block' }`<br>`alert('오퍼 수락')` | **SBK-FIX-01** |
| **B03** | **P0** | `wrangler.json`<br>`docs/product/DEPLOY_RUNBOOK.md` (L42) | `"assets": { "directory": "." }` (Worker JS 부재)<br>`src/worker.js` 파일 물리적 부재 | **SBK-FIX-02** |
| **B04** | **P0** | `src/crypto.py` (L16, 45-55)<br>`src/attribution.py` (L14) | `DEFAULT_KEY = os.environ.get(..., "sbk_secret_key_...")`<br>`_keystream_generator() XOR 스트림`<br>`SECRET_KEY = "sbk_attribution_..."` | **SBK-FIX-03** |
| **B05** | **P0** | `src/intake.py` (L83-92)<br>`slotbridge/widget.js` (L45-52) | 서명 검증 없이 `attribution_partner_id` 수기 수용<br>`widget.js`의 단순 `?ref=` 구성 | **SBK-FIX-03**<br>**SBK-FIX-08** |
| **B06** | **P0** | `src/supplier.py` (L52-54, 88-92)<br>`migrations/seeds.sql` | `insurance_status = "VERIFIED" if policy else "PENDING"`<br>`approve_partner()` 무조건 VERIFIED 강제<br>가상 사업자·평점 시드 | **SBK-FIX-04** |
| **B07** | **P0** | `src/engine.py` (L185-195)<br>`src/matching.py` (L150-165) | `submit_fulfillment` 시 일방적 `VERIFIED` + 원장 분개<br>`transition_state`가 DB 상태와 무관하게 호출자 `from_state`만 검사 | **SBK-FIX-06** |
| **B08** | **P0** | `src/revenue.py` (L150-170, 260-275) | `generate_monthly_invoice` SQL에 기간 필터 누락<br>`total_service = sum(r["final_amount"] or 250000)` | **SBK-FIX-07** |
| **B09** | **P0** | `src/revenue.py` (L343-358) | `total_revenue_expected = inv_sum`<br>`total_revenue_collected = inv_sum`<br>`status = 'BALANCED'` 하드코딩 | **SBK-FIX-07** |
| **B10** | **P0** | `src/revenue.py` (L75-90, 235-245) | `record_ledger_reversal` 무제한 역분개 가능<br>`tax_inv_num = f"TX-{uuid.uuid4().hex[:12].upper()}"` 가짜 번호 생성 | **SBK-FIX-07** |
| **B11** | **P1** | `src/matching.py` (L60-95) | SQL 매칭 시 `time_window`(AM/PM), 평형, 상세조건 필터 누락 | **SBK-FIX-05** |
| **B12** | **P1** | `src/matching.py` (L120-145) | 15분 자동 만료 스케줄러 부재, 수동 호출 의존<br>`notification_outbox`가 `MOCKED` 상태 | **SBK-FIX-05** |
| **B13** | **P1** | `src/engine.py` (L130-145)<br>`slotbridge/status.html` | PII tombstone만 변경, 제3자 통지 부재<br>화면만 파기 완료 고지 | **SBK-FIX-03** |
| **B14** | **P1** | `slotbridge/inquiry.html` (L260-270)<br>`src/engine.py` | `toISOString()` UTC 날짜 생성으로 자정 전후 시차 불일치 | **SBK-FIX-01**<br>**SBK-FIX-04** |
| **B15** | **P1** | `slotbridge/widget.js` (L30-45) | `innerHTML`에 임의 속성 주입 (XSS 위험)<br>서명 없는 `ref` 파라미터 | **SBK-FIX-08** |
| **B16** | **P1** | `slotbridge/partner-dashboard.html`<br>`slotbridge/admin-billing.html` | 하드코딩된 가짜 정산 98,000원, 가짜 은행 계좌 노출 | **SBK-FIX-01** |
| **B17** | **P1** | `docs/business/MARKETING_STRATEGY.md`<br>`OUTREACH_PLAYBOOK.md` | 미체결 15개사를 '확보된 고객사', '확정 수요'로 표현 | **SBK-FIX-09** |
| **B18** | **P1** | `src/analytics.py` (L45-65) | CAC=0원, 순기여이익 65%를 상수로 확정 계산 | **SBK-FIX-09** |
| **B19** | **P2** | `src/db.py` (L30-50) | 로컬 파이썬 메모리 변수로 Cloudflare D1 쿼터 추정 | **SBK-FIX-02**<br>**SBK-FIX-10** |
| **B20** | **P2** | `docs/product/TRANSITION_COMPLETION_REPORT.md` | '14단계 완료, 프로덕션 배포 완료, 99% 무인' 과장 선언 | **SBK-FIX-10** |

---

## 3. 다음 순차 작업 (Next Steps)

- 본 티켓 **[SBK-FIX-00]**에서는 일체의 코드 수정을 진행하지 않았으며, 전수 조사를 통해 현실과 이상의 괴리를 100% 투명하게 규명함.
- 다음 단계인 **[SBK-FIX-01 | HONEST UI GATE]**로 진입하여, 외부 고객과 파트너가 처리되지도 않은 신청/취소/정산에 대해 성공한 것으로 오인하지 않도록 가짜 성공 처리를 즉시 격리하고 정직한 안내 및 데모 모드로 전환할 것을 권고함.
