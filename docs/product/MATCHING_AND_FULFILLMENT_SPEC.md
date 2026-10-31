# SlotBridge Korea — 매칭·배정·SLA 및 상태머신 규격서 (MATCHING_AND_FULFILLMENT_SPEC.md)

**문서 버전:** 1.0.0  
**작성일자:** 2026-10-31  
**적용 대상:** 결정적 규칙 매칭 엔진(`matching.py`), 상태 머신, 분쟁 중재 SOP, SLA 정책  
**작성 주체:** Codex Marketplace Product Manager & Systems Architect  

---

## 1. 10단계 선형 상태머신 및 5대 예외 분기

```
[인입 단계]
SUBMITTED ────────▶ VALIDATED ────────▶ CONSENTED ────────▶ OFFERED
                                                               │
                                  ┌────────────────────────────┴───────────────────────────┐
                                  ▼ (15분 내 수락)                                          ▼ (거절 또는 만료)
                              ACCEPTED                                                 REJECTED / EXPIRED
                                  │                                                        │
                                  ▼ (유선 통화 완료)                                         ▼ (순차 재배정)
                          CUSTOMER_CONFIRMED                                            OFFERED (차순위 파트너)
                                  │
                                  ▼ (금액 확정 증빙 필수)
                                BOOKED
                                  │
                                  ▼ (시공 전후 사진 2장 증빙 필수)
                              COMPLETED
                                  │
                                  ▼ (고객 확인 또는 검수 필수)
                               VERIFIED ────────(품질 불만/파손)────────▶ DISPUTED
                                  │                                          │
                                  ▼ (정산 및 청구 확정)                       ├─▶ REFUNDED (전액 환불)
                               BILLABLE                                      └─▶ VERIFIED (무상 A/S 완료)
```

### 전이 전제조건(Preconditions) 가드레일
1. `BOOKED`: 고객과 유선 상담을 통해 확정된 최종 금액(`agreed_price`) 증빙 없이 전이 불가. (단순 통화나 열람은 예약이 아님).
2. `COMPLETED`: 작업 현장 전/후 사진 최소 2장 증빙(`photo_urls`) 업로드 없이 전이 불가.
3. `BILLABLE`: 고객의 만족도 확인 또는 관리자의 정식 검수(`verified_completion = true`) 없이 전이 불가.

---

## 2. 결정적 규칙 매칭 엔진 (Deterministic Rule Matching Engine)

불투명한 블랙박스 AI 추천을 배제하고, 감사 및 리플레이가 100% 가능한 **하드 필터(Hard Filter) + 결정적 소프트 스코어(Soft Scoring)** 알고리즘을 적용한다.

### 2.1 5대 하드 필터 (Hard Filters — Pass/Fail)
1. **정식 승인 파트너:** `verification_status == 'APPROVED'` (미승인 파트너 배제)
2. **배상책임보험 검증:** `liability_insurance_status == 'VERIFIED'` (보험 미가입 파트너 배제)
3. **서비스 권역 일치:** 신청 지역(`district`)이 파트너의 등록 권역(`service_areas`)과 일치
4. **일시 가용 슬롯 보유:** 신청 날짜 및 시간대에 `status == 'AVAILABLE'`인 슬롯 보유
5. **이력 배제:** 해당 인입 건에 대해 이미 '거절(REJECT)' 또는 '시간만료(TIMEOUT)' 응답을 한 파트너는 재배정에서 영구 제외

### 2.2 결정적 소프트 랭킹 산식 (Soft Scoring with Equity Balance)
```python
score = (
    (rating_average * 20.0) +             # 평점 기여도 (최대 100점)
    (min(total_completed, 100) * 0.5) +   # 시공 경험치 (최대 50점)
    (10.0 / (recent_dispatches + 1))      # 독점 방지 및 균등 배분 보너스 (최대 10점)
)
```
- **형평성 보너스 (Equity Balancing Bonus):** 특정 대형 업체로의 쏠림을 방지하고 신규 파트너에게도 균등한 기회를 제공하기 위해, 최근 7일간 오퍼 수신 횟수가 적은 파트너에게 가산점을 부여함.

---

## 3. 15분 배타적 순차 재배정 (Sequential 1:1 Re-Routing)

1. **배타적 1:1 오퍼:** 한 시점에 오직 1개 1순위 파트너에게만 15분 TTL의 독점 수락 권한을 부여함. (다수 업체 동시 입찰 및 고객 정보 무차별 살포 금지).
2. **타임아웃 및 거절 처리:**
   - 15분 이내 미수락(TIMEOUT) 또는 명시적 거절(REJECT) 발생 시:
   - 해당 오퍼는 `EXPIRED` 또는 `REJECTED` 처리되고, 해당 파트너의 슬롯은 즉시 `AVAILABLE`로 반환됨.
   - 인입 건은 대기열로 복귀하여, 탈락 파트너를 제외한 차순위(Rank 2) 파트너에게 즉각 자동 재배정됨.
3. **후보 고갈 시:** 관내 모든 후보 파트너가 거절하거나 슬롯이 없을 경우, 허위 예약을 생성하지 않고 즉시 고객에게 "관내 잔여 슬롯 마감"을 솔직히 안내(`UNAVAILABLE_WAITING`).

---

## 4. SLA 및 투명한 운영 정책

1. **내부 SLA (Internal Operational Target):** '15분 이내 수락'은 파트너 네트워크의 회전율을 위한 **내부 성능 지표**이며, 고객에게 법적으로 확약하는 출동 보증이 아님.
2. **야간/영업 외 시간 정직 고지:** 파트너가 상주하지 않는 심야 시간대(21:00 ~ 익일 08:00)에는 "24시간 실시간 즉시 배정" 문구를 일체 사용하지 않으며, "영업 개시(09:00) 직후 1순위 순차 배정됩니다"를 명시함.

---

## 5. 분쟁 처리 생애주기 (Dispute Lifecycle)

1. **분쟁 접수:** 고객 불만 접수 시 매칭 상태를 `DISPUTED`로 즉각 전환하여 수수료 청구 및 정산 원장을 동결.
2. **무상 A/S 해결 경로:** 파트너가 24시간 이내 현장 재방문하여 보완 청소를 완료하고 고객이 만족한 경우 -> `VERIFIED`로 복원 후 정상 정산 진행.
3. **전액 환불 해결 경로:** 파트너 귀책으로 인한 미해결 시 -> `REFUNDED`로 종결, 플랫폼 수수료 전액 면제 및 해당 파트너에 위약벌 청구.
