# SlotBridge Korea — 최종 고객 요청·동의·보호 규격서 (CUSTOMER_INTAKE_SPEC.md)

**문서 버전:** 1.0.0  
**작성일자:** 2026-10-31  
**관련 법령:** 개인정보보호법 제15조·제17조·제22조, 전자상거래법 제20조 제2항  
**적용 대상:** 고객 모바일 웹 인입 폼(`inquiry.html`), 신청 추적 및 철회(`status.html`), 인입 컨트롤러(`intake.py`)  
**작성 주체:** Codex Marketplace Product Manager & Security Engineer  

---

## 1. 고객 경험 5대 설계 원칙

1. **최소 수집 원칙 (Data Minimization):** 초기 인입 단계에서는 시군구·동까지만 수집하며, 상세 동·호수 및 결제 수단은 일체 수집하지 않는다.
2. **실측 가용성 투명성 (No Fake Scarcity):** 관내 유효 가용 슬롯이 0개일 경우, 솔직하게 '마감' 및 '대기 접수' 상태로 안내하며, 가짜 실시간 마감 임박이나 허위 예약 건수를 절대 표시하지 않는다.
3. **법정 필수 동의 분리:** 개인정보 수집·이용 동의와 제3자 제공 동의를 각각 개별 체크박스로 분리하며, 기본 상태는 해제(`false`)로 둔다.
4. **리퍼럴 변조 방지:** URL 파라미터(`?ref=PARTNER_ID`)로 유입된 파트너 ID는 서버에서 정식 승인(`APPROVED`) 여부를 검증한 후 불변 결속한다.
5. **1-Click 즉시 철회권 보장:** 고객은 발급된 고유 토큰 웹 링크에서 언제든지 단 1회의 클릭으로 신청을 취소하고 성명·연락처를 데이터베이스에서 영구 파기(Tombstone)할 수 있다.

---

## 2. 고객 인입 데이터 유효성 검증 규칙 (Validation Rules)

| 입력 필드 | 필수 여부 | 형식 및 제약 조건 | 위반 시 서버 에러 코드 |
|---|:---:|---|---|
| `requested_date` | **필수** | `Asia/Seoul` 기준 익일 ~ +3일 이내 (24~72h 급행 범위) | `OUT_OF_RUSH_WINDOW` |
| `district` | **필수** | 서울특별시 `관악구` 또는 `동작구` | `UNSUPPORTED_AREA` |
| `housing_type` | **필수** | `STUDIO`, `TWO_ROOM`, `OFFICETEL`, `APARTMENT` | `INVALID_INPUT` |
| `area_pyeong` | **필수** | 5평 이상 60평 이하의 정수 | `INVALID_AREA` |
| `customer_name` | **필수** | 2자 이상의 문자열 | `INVALID_NAME` |
| `customer_phone` | **필수** | `010-XXXX-XXXX` 정규식 일치 필수 | `INVALID_PHONE` |
| `agreed_privacy_collection` | **필수** | `true` (기본값 false, 클라이언트 체크 필수) | `CONSENT_REQUIRED` |
| `agreed_third_party_share` | **필수** | `true` (단 1개 배타적 제휴 청소팀 제공) | `CONSENT_REQUIRED` |
| `agreed_marketing` | 선택 | `boolean` (기본값 false) | - |

---

## 3. 솔직한 가용성 분기 처리 (Honest Availability Logic)

```
[고객 인입 접수]
       │
       ▼ (관내 승인 파트너의 AVAILABLE 슬롯 카운트 조회)
┌──────────────────────────────────────────────┐
│ 가용 슬롯 수 > 0 ?                          │
└──────┬───────────────────────────────┬───────┘
       │ YES                           │ NO (슬롯 0개)
       ▼                               ▼
[상태: PENDING_MATCH]           [상태: UNAVAILABLE_WAITING]
- 실제 가용 슬롯 수 정직 표시       - "현재 관내 잔여 슬롯이 마감되었습니다." 솔직 고지
- 1:1 배타적 15분 오퍼 큐 진입    - 허위 카운트다운/조작 건수 0건
- 담당 청소팀 즉시 수락 대기      - 취소 슬롯 발생 시 1순위 연락 대기 안내
```

---

## 4. 1-Click 개인정보 완전 파기 절차 (Revocation SOP)

1. **고객 요청:** 고객이 `/slotbridge/status.html?token=...` 페이지에서 [신청 취소 및 개인정보 즉시 영구 파기] 클릭.
2. **트랜잭션 실행:**
   - `inquiries.customer_name_encrypted` -> `ERASED_{uuid}` 난수 덮어쓰기.
   - `inquiries.customer_phone_encrypted` -> `ERASED_{uuid}` 난수 덮어쓰기.
   - `inquiries.customer_phone_hash` -> `ERASED_{uuid}` 난수 덮어쓰기.
   - `inquiries.status` -> `CANCELLED`.
   - 진행 중인 모든 `assignment_offers` -> `CANCELLED`.
3. **증적 기록:** `data_erasure_requests` 및 `audit_log`에 파기 타임스탬프와 함께 법정 파기 증적 영구 보존.
4. **결과:** 이후 어떠한 청소업체나 관리자도 해당 고객의 연락처를 복호화할 수 없음.
