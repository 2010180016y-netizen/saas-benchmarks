# [SBK-FIX-06] BOOKING / FULFILLMENT VERIFICATION REPORT

**Document ID:** SBK-DOC-FIX-006  
**Executed Date:** 2026-10-31 22:15 (Asia/Seoul)  
**Ticket Status:** COMPLETE & VERIFIED (PASS)  
**Target Vulnerabilities Fixed:** B07 (공급자 단독 완료 제출 시 무검증 VERIFIED 처리 및 무단 회계 원장 직행 결함)

---

## 1. 결함 진단 및 위험 분석 (Root Cause & Risk)

### 1.1 기존 구현의 문제점 (B07)
- **공급자 일방 완료 승인:** 시공업체가 `submit_fulfillment()` 호출 시 사진 증빙 유무나 고객 확인 없이 즉시 `verification_status = 'VERIFIED'`로 기록됨.
- **무검증 회계 원장 직행:** 고객이 청소 상태를 보지도 못했거나 불만족으로 분쟁이 일어날 수 있는 시점에 8% 플랫폼 수수료 및 35% 리퍼럴 분개가 즉시 생성되어 허위 매출이 과다 인식될 위험 존재.
- **예외 가드레일 부재:** 사진이 0장이거나 1장뿐인 경우, 시공 전 고객이 1-Click 철회한 건, 타 업체에 배정된 매칭, 분쟁 중인 건에 대한 완료 시도가 차단되지 않음.

---

## 2. 핵심 수정 및 기술 구현 (Key Implementations)

### 2.1 `src/engine.py`: 5대 완료 가드레일 및 양방향 확인 게이트 구축
1. **5대 방어 가드레일 탑재:**
   - `MATCH_NOT_FOUND` (404): 타 파트너가 타인의 매칭 건을 완료 제출하는 행위 원천 차단.
   - `INSUFFICIENT_EVIDENCE` (400): 시공 전후 사진 2장 미만 제출 시 거부.
   - `INVALID_AMOUNT` (400): 최종 정산 금액이 0원 이하일 경우 거부.
   - `INQUIRY_CANCELLED` (400): 고객이 1-Click 철회한 요청에 대한 시공 완료 보고 원천 차단.
   - `MATCH_DISPUTED` (400): 분쟁 진행 중인 매칭에 대한 완료 보고 및 정산 청구 시도 동결 차단.
2. **단독 제출과 고객 확인의 물리적 분리 (`auto_accrue=False`):**
   - 공급자 단독 제출 시 증빙 상태를 `PENDING_CUSTOMER_CONFIRMATION`으로 기록하고 매칭 상태를 `COMPLETED`로 변경하되, **회계 원장 분개는 일체 생성하지 않음**.
3. **고객 명시 승인 게이트 (`confirm_customer_completion`):**
   - 고객이 작업 상태를 확인하고 평가(rating, notes)를 남겨야만 `VERIFIED` 및 `BILLABLE`로 전이되며, 이 시점에 비로소 불변 원장에 `REVENUE_ACCRUED`(8%) 및 `REFERRAL_ALLOCATED`(35%) 분개가 기록됨.
4. **관리자 예외 승인 감사 게이트 (`admin_override_completion`):**
   - 현장 고객의 구두 확인 녹취 등 관리자가 예외로 승인할 경우, 5자 이상의 구체적 근거 사유(`reason`) 입력을 강제하고 불변 감사 로그(`ADMIN_OVERRIDE_COMPLETION`)에 비ASCII 보존 JSON으로 기록.

### 2.2 `src/worker.js`: 엣지 API 양방향 완료 엔드포인트 연동
- `POST /api/v1/slotbridge/matches/:id/fulfill`: 시공 전후 사진 2장, 정산 금액 검증 후 `PENDING_CUSTOMER_CONFIRMATION` 상태로 접수.
- `POST /api/v1/slotbridge/matches/:id/confirm`: 고객의 명시적 승인을 수신하여 매칭을 `BILLABLE`로 전이.

### 2.3 `tests/test_two_way_verification.py`: 7대 검증 테스트 스위트 신설
1. `test_01_insufficient_photos_rejected`: 사진 0장 및 1장 제출 시 400 에러 차단 실측.
2. `test_02_invalid_amount_rejected`: 0원 정산 금액 제출 시 400 에러 차단 실측.
3. `test_03_unassigned_partner_rejected`: 미배정 업체의 완료 시도 시 404 차단 실측.
4. `test_04_revoked_inquiry_fulfillment_blocked`: 철회된 인입 건에 대한 시공 보고 차단 실측.
5. `test_05_unilateral_submission_not_accrued_without_confirmation`: 단독 제출 시 원장 미기록 및 고객 승인 후 원장 정상 분개 검증.
6. `test_06_disputed_match_blocks_fulfillment_and_confirmation`: 분쟁 발생 시 승인 차단 실측.
7. `test_07_admin_override_requires_reason`: 관리자 예외 승인 시 5자 이상 사유 강제 및 감사 로그 검증.

---

## 3. 테스트 및 회귀 검증 결과

- **실행 명령:** `python3 -m unittest discover -s tests -p "test_*.py"`
- **테스트 결과:** **Ran 116 tests in 1.159s — OK (전원 무결성 통과)**
- **회귀 발생 건수:** 0건 (기존 109개 테스트 전원 통과 유지 + 신규 7개 통과)
- **종합 판정:** **PASS (완전 통과)**
