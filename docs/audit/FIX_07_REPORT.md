# [SBK-FIX-07] REVENUE RECONCILIATION — NO PHANTOM CASH REPORT

**Document ID:** SBK-DOC-FIX-007  
**Executed Date:** 2026-10-31 22:25 (Asia/Seoul)  
**Ticket Status:** COMPLETE & VERIFIED (PASS)  
**Target Vulnerabilities Fixed:** B02 (가짜 대사 상수 로직), B08 (월별 필터 부재 및 250k 가정 대입), B09 (원장/역분개 중복), B10 (임의 세금계산서 번호)

---

## 1. 결함 진단 및 위험 분석 (Root Cause & Risk)

### 1.1 기존 구현의 문제점 (B02, B08, B09, B10)
- **가짜 대사 상수 로직 (B02):** `reconcile_billing_period()`가 실제 은행 입금 내역이나 청구서 상태와 무관하게 `expected = collected = inv_sum`, `discrepancy = 0`, `status = 'BALANCED'`를 하드코딩하여 미수금을 은폐하고 있었음.
- **월별 필터 누락 및 가상 금액 대입 (B08):** 청구서/지급서 생성 시 기간 필터가 없어 전월/익월 매칭이 누적 청구되었고, `final_amount`가 NULL일 때 임의로 250,000원을 가정 대입하여 허위 청구가 발생할 위험이 있었음.
- **원장 및 역분개 중복 허용 (B09):** 동일 매칭에 대해 `record_verified_match_ledger` 또는 `record_ledger_reversal`을 다회 호출할 경우 중복 분개가 제한 없이 적재되어 원장이 왜곡됨.
- **임의 세금계산서 번호 (B10):** 자체 생성된 `TX-` 번호가 국세청 정식 전자세금계산서 승인 번호인 것처럼 취급됨.

---

## 2. 핵심 수정 및 기술 구현 (Key Implementations)

### 2.1 `src/revenue.py`: 실증빙 기반 수익 인식 및 대사 엔진 구축
1. **월별 완료 필터 및 NULL 대입 원천 차단 (B08 해결):**
   - `generate_monthly_invoice` 및 `generate_monthly_payout`에 `billing_period` 정밀 필터(`s.slot_date LIKE 'YYYY-MM%' OR m.created_at LIKE 'YYYY-MM%'`)를 적용하여 전월/익월 거래를 엄격히 격리.
   - `m.final_amount IS NOT NULL AND m.final_amount > 0` 조건을 강제하여 증빙 없는 가상 250k 대입을 원천 차단.
2. **원장 및 역분개 중복 방지 가드레일 (B09 해결):**
   - `record_verified_match_ledger`: 이미 `REVENUE_ACCRUED` 분개가 존재하는 매칭의 경우 `DUPLICATE_LEDGER_EVENT` (409) 예외 발생.
   - `record_ledger_reversal`: 이미 `REVERSAL_ACCRUED` 분개가 존재하는 매칭의 경우 `ALREADY_REVERSED` (409) 예외 발생.
3. **실제 현금 수취 및 분개 연동 (`record_invoice_payment`):**
   - 은행 입금 확인 시에만 청구서를 `PAID`로 갱신하고, 차변: 현금(`CASH_BANK_ACCOUNT`) / 대변: 미수금(`ACCOUNTS_RECEIVABLE_PARTNER`) 정식 현금 수취 분개를 기록.
   - 부분 입금 시 `PARTIAL_PAYMENT` (400)로 거부하고 은행 적요/거래번호를 필수로 요구.
4. **실제 차액 기반 월말 대사 (`reconcile_billing_period` - B02 해결):**
   - 하드코딩된 상수 로직을 영구 폐기하고, 청구 총액과 실제 은행 수취액의 차액(`discrepancy_amount = total_invoiced - total_collected`)을 실측.
   - 미수금이 존재하거나 입금 증빙이 없을 경우 엄격하게 `UNRECONCILED` 판정.
5. **국세청 승인 번호 분리 (B10 해결):**
   - 외부 국세청 24자리 정식 승인번호 입력 시에만 `nts_verified=True`로 인증하며, 내부 발급 건은 `TX-INT-` 식별자로 명확히 격리.

### 2.2 `src/matching.py`: 합의 금액 원자적 동기화
- 고객 합의 금액(`agreed_price`)이 존재하는 `BOOKED` 전이 시 `matches.final_amount`를 원자적으로 업데이트하여 검증된 금액만이 회계 원장으로 전달되도록 결속.

### 2.3 `tests/test_revenue_reconciliation_audit.py`: 10대 검증 테스트 스위트 신설
1. `test_01_cross_month_transaction_isolation`: 전월(10월) 거래가 당월(11월) 청구서/지급서에 누출되지 않음을 실측.
2. `test_02_duplicate_ledger_accrual_blocked`: 동일 건 2회 원장 분개 시도 시 409 차단 실측.
3. `test_03_null_amount_blocked_without_assumption`: NULL 금액 분개 시도 시 400 차단 실측.
4. `test_04_uncollected_cash_reconciliation_unreconciled`: 미입금 시 불일치(UNRECONCILED, discrepancy 22,000원) 판정 실측.
5. `test_05_partial_payment_rejected`: 부분 입금 시 400 에러 차단 실측.
6. `test_06_payout_withheld_without_tax_invoice`: 세금계산서 미수취 시 지급 승인 거부 및 PENDING 유지 실측.
7. `test_07_duplicate_reversal_blocked`: 2회 역분개 시 409 차단 실측.
8. `test_08_multi_partner_attribution_isolated`: 3개 리퍼럴 파트너 각각 35% 정산액 완벽 분리 실측.
9. `test_09_zero_volume_invoice`: 실적 0건 파트너의 0원 청구서 정상 생성 실측.
10. `test_10_full_cash_settlement_reaches_balanced`: 은행 전액 입금 후 현금 원장 분개 및 대사 일치(`BALANCED`, discrepancy 0원) 완결 실측.

---

## 3. 테스트 및 회귀 검증 결과

- **실행 명령:** `python3 -m unittest discover -s tests -p "test_*.py"`
- **테스트 결과:** **Ran 126 tests in 1.263s — OK (전원 무결성 통과)**
- **드라이런 검증:** 50건 합성 트래픽 차변 475,200원 == 대변 475,200원 (Balanced: True)
- **회귀 발생 건수:** 0건 (기존 116개 테스트 전원 통과 유지 + 신규 10개 통과)
- **종합 판정:** **PASS (완전 통과)**
