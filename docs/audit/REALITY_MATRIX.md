# [SBK-AUDIT-01] REALITY MATRIX: End-to-End Business Journey Truth Audit

**Audit Baseline:** 2026-10-31 21:00 (Asia/Seoul)  
**Target Repository:** `2010180016y-netizen/saas-benchmarks` (Branch: `main`, Commit: `8b7d6e0`, Baseline: `d5a026e`)  
**Auditor Roles:** Staff Engineer, Marketplace Architect, Security Auditor, Revenue Operations Auditor  
**Audit Principle:** 무수정(NO CODE MODIFICATION) 증거 기반 정밀 감사. 코드 레벨의 시뮬레이션(Mock)과 프로덕션 실서비스를 엄격히 분리하여 팩트만 기록함.

---

## 1. 14단계 비즈니스 여정 현실 검증 매트릭스 (Journey Reality Matrix)

| 단계 | 비즈니스 여정 (Business Step) | A. 실제 코드 존재? | B. 실제 API 연결? | C. DB 영속 저장? | D. 보안/권한 검증? | E. 실제 사업 성과 직결? | F. 테스트 실환경 검증? | G. 수정 필수 여부? |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **01** | **고객 유입 (Lead Ingestion)**<br>위젯/검색/SEO를 통한 폼 접근 | **YES**<br>(`inquiry.html`, `widget.js`) | **NO**<br>(정적 호스팅만 서빙) | **NO**<br>(클라이언트 DOM만 조작) | **PARTIAL**<br>(위젯 ref 위변조 취약) | **FAIL**<br>(고객 인입 유실) | **FAIL**<br>(Python 로컬만 테스트) | **YES (P0)**<br>(B01, B15) |
| **02** | **개인정보 동의 (Consent)**<br>개보법 제17조 제3자 제공 동의 | **YES**<br>(체크박스 2종 분리) | **NO**<br>(서버 미전송) | **NO**<br>(`consent_events` 미적재) | **NO**<br>(동의 감사로그 부재) | **FAIL**<br>(법적 과태료 위험) | **FAIL**<br>(로컬 함수 호출만) | **YES (P0)**<br>(B01, B04) |
| **03** | **신청 저장 (Inquiry Storage)**<br>접수 토큰 생성 및 데이터베이스 적재 | **PARTIAL**<br>(Python 엔진만 존재) | **NO**<br>(`inquiry.html`이 `Math.random` 모의 토큰 생성) | **NO**<br>(브라우저 메모리 휘발) | **NO**<br>(평문 노출 위험) | **FAIL**<br>(신청 100% 증발) | **FAIL**<br>(로컬 SQLite 모의) | **YES (P0)**<br>(B01, B03) |
| **04** | **공급업체 검증 (Supplier Verify)**<br>사업자등록·영업배상책임보험 심사 | **PARTIAL**<br>(`supplier.py` 존재) | **NO**<br>(`partner-apply.html` API 미호출) | **NO**<br>(DB 미저장) | **NO**<br>(증권번호 문자열만 있으면 VERIFIED) | **FAIL**<br>(유령/미검증 업체 사고) | **FAIL**<br>(가상 seeds.sql 의존) | **YES (P0)**<br>(B01, B06) |
| **05** | **실제 가용 슬롯 (Capacity Slot)**<br>24~72h 관악/동작 급행 슬롯 등록 | **PARTIAL**<br>(`supplier.py` 존재) | **NO**<br>(`partner-slots.html` 정적 alert) | **NO**<br>(DB 미저장) | **PARTIAL**<br>(동/작업조건 필터 누락) | **FAIL**<br>(존재하지 않는 슬롯 노출) | **FAIL**<br>(로컬 단위 테스트) | **YES (P1)**<br>(B02, B11, B14) |
| **06** | **독점 오퍼 (15m Offer Dispatch)**<br>1순위 파트너 15분 독점 발송 | **PARTIAL**<br>(`matching.py` 존재) | **NO**<br>(Worker 엔트리 부재) | **NO**<br>(런타임 부재) | **NO**<br>(15분 자동 만료 트리거 없음) | **FAIL**<br>(파트너 오퍼 미수신/방치) | **FAIL**<br>(수동 함수 호출로만 테스트) | **YES (P0)**<br>(B03, B12) |
| **07** | **업체 수락 (Partner Accept)**<br>15분 내 수락 및 슬롯 선점 | **PARTIAL**<br>(`supplier.py` 존재) | **NO**<br>(화면은 모달 alert만) | **NO**<br>(DB 선점 안 됨) | **NO**<br>(실시간 동시성 락 미검증) | **FAIL**<br>(이중 예약/슬롯 펑크) | **FAIL**<br>(단일 스레드 순차 호출) | **YES (P0)**<br>(B02, B05) |
| **08** | **고객 확정 (Booking Confirm)**<br>유선 금액 합의 및 `BOOKED` 전이 | **PARTIAL**<br>(`matching.py` 존재) | **NO**<br>(고객 확인 경로 없음) | **NO**<br>(서버 확인 부재) | **NO**<br>(공급자 일방 주장 가능) | **FAIL**<br>(허위 예약 생성) | **FAIL**<br>(인위적 인자 주입) | **YES (P1)**<br>(B07, B11) |
| **09** | **서비스 완료 (Fulfillment)**<br>현장 청소 시공 및 사진 2장 제출 | **PARTIAL**<br>(`engine.py` 존재) | **NO**<br>(사진 업로드 스토리지 부재) | **NO**<br>(URL 문자열만 저장) | **NO**<br>(사진 진위 검증 없음) | **FAIL**<br>(가짜 시공 완료 보고) | **FAIL**<br>(더미 문자열 테스트) | **YES (P0)**<br>(B07) |
| **10** | **완료 검증 (Verification)**<br>고객 검수 및 관리자 최종 확인 | **PARTIAL**<br>(`engine.py` 존재) | **NO**<br>(공급자 제출 시 자동 VERIFIED) | **NO**<br>(상태 머신 CAS 부재) | **NO**<br>(고객/관리자 검증 패스) | **FAIL**<br>(하자 미해결 채 수수료 청구) | **FAIL**<br>(자체 플래그 확인) | **YES (P0)**<br>(B07) |
| **11** | **성과 수수료 발생 (Fee Accrual)**<br>건당 8%(2만원) 원장 복식부기 분개 | **PARTIAL**<br>(`revenue.py` 존재) | **NO**<br>(런타임 부재) | **NO**<br>(영속 D1 미기재) | **NO**<br>(중복 분개 멱등성 키 부재) | **FAIL**<br>(원장 왜곡 및 중복 매출) | **FAIL**<br>(로컬 SQLite 메모리) | **YES (P0)**<br>(B08, B10) |
| **12** | **월간 청구 (Invoicing)**<br>익월 5일 B2B 인보이스 발행 | **PARTIAL**<br>(`revenue.py` 존재) | **NO**<br>(`admin-billing.html` 정적 화면) | **NO**<br>(청구서 저장 안 됨) | **NO**<br>(가짜 `TX-` 임의번호 생성) | **FAIL**<br>(세무 신고 불가/허위 청구) | **FAIL**<br>(월별 날짜 필터링 없음) | **YES (P0)**<br>(B08, B10) |
| **13** | **실제 입금 (Cash Collection)**<br>수수료 계좌 입금 확인 및 대사 | **PARTIAL**<br>(`revenue.py` 존재) | **NO**<br>(은행 연동 없음) | **NO**<br>(무조건 `BALANCED` 하드코딩) | **NO**<br>(미수금 100% 은폐) | **CRITICAL FAIL**<br>(가짜 현금 매출 인식) | **FAIL**<br>(expected=collected 대입) | **YES (P0)**<br>(B09) |
| **14** | **원 유입사 정산 (Referral Payout)**<br>수수료의 35%(7,000원) 송금 배분 | **PARTIAL**<br>(`revenue.py` 존재) | **NO**<br>(화면 정적 숫자 98,000원) | **NO**<br>(송금 증빙 부재) | **NO**<br>(세금계산서 수취 전산 없음) | **FAIL**<br>(정산 분쟁 및 세무 가산세) | **FAIL**<br>(로컬 파라미터 테스트) | **YES (P0)**<br>(B08, B16) |

---

## 2. 세부 기술 현실 추적 요약

1. **프론트엔드 (UI Layer):**
   - 배포된 정적 HTML 파일들(`inquiry.html`, `partner-apply.html`, `status.html`, `partner-slots.html`, `admin-billing.html`, `growth-dashboard.html`)은 실제 백엔드 API와 통신하지 않는 **완전한 시연용(Mock) UI** 상태임.
   - 폼 제출 시 `Math.random()` 기반의 가짜 토큰을 생성하여 쿼리스트링으로 넘기거나(`inquiry.html`), 자바스크립트 변수로 DOM 텍스트만 변경(`status.html`의 개인정보 파기 안내)하고 있음.
   - 외부 고객이 실제로 신청서를 제출해도 서버나 데이터베이스에 단 1바이트도 도달하지 않고 소멸함.

2. **런타임 및 네트워크 (Runtime & Worker Layer):**
   - 저장소의 `wrangler.json`은 정적 자산 서빙(`"assets": { "directory": "." }`)만 선언되어 있음.
   - `docs/product/DEPLOY_RUNBOOK.md`에 기재된 `src/worker.js`는 파일 자체가 물리적으로 존재하지 않음 (`NOT_FOUND`).
   - D1 데이터베이스 바인딩, KV 바인딩, 환경변수 Secrets(`SBK_ENCRYPTION_KEY`, `SBK_ATTRIBUTION_SECRET`)이 Cloudflare 설정에 전혀 연결되어 있지 않음.
   - Python 도메인 모듈들(`src/*.py`)은 Cloudflare 엣지 런타임에 탑재되지 못하고 오직 로컬 CLI 환경에서만 격리 실행됨.

3. **데이터베이스 및 보안 (DB & Security Layer):**
   - D1 SQLite 방언으로 작성된 마이그레이션 SQL(`migrations/0001_initial_schema.sql`)은 프로덕션 Cloudflare D1 인스턴스에 적용되지 않았음.
   - 개인정보 암호화(`src/crypto.py`)는 선언된 표준 AES-256-GCM이 아니라 자체 제작한 `HMAC-SHA256 CTR` 형태의 XOR 스트림 암호화이며, 코드 내에 `DEFAULT_KEY`가 하드코딩되어 있어 보안 감사 통과가 불가능함.
   - 유입 파트너 귀속(`attribution_partner_id`)은 서명 검증 없이 URL 파라미터(`?ref=...`)를 그대로 수용할 수 있는 취약점을 내포함.

4. **회계 및 비즈니스 (FinOps Layer):**
   - 월말 대사 함수(`reconcile_billing_period`)가 실제 입금 여부를 검증하지 않고 `total_revenue_collected = inv_sum`으로 대입하여 항상 `discrepancy_amount = 0`, `status = 'BALANCED'`를 데이터베이스에 강제 삽입함.
   - 실제 은행 계좌에 현금이 입금되지 않아도 전산상으로는 100% 수금 완료된 것으로 표시되는 치명적인 회계 왜곡(Phantom Cash) 구조임.
