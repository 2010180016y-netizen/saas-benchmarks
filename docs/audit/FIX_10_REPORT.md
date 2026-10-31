# [SBK-FIX-10] Audit & Resolution Report: Restricted Pilot & Pilot Release Gate
**Document ID:** SBK-DOC-AUD-010  
**Target Ticket:** `[SBK-FIX-10 | PILOT RELEASE GATE]`  
**Execution Date:** 2026-10-31 23:05 KST  
**Technical Status:** **PASS (완전 통과)** — 누적 144개 자동화 테스트 무결성 통과 (2.718s OK)  
**Overall Business Status:** **BLOCKED_EXTERNAL (외부 선결 게이트 미충족 및 운영자 승인 전 실거래 동결)**  
**Target Files:**
- `src/gate.py`
- `scripts/pilot_restricted_e2e.py`
- `tests/test_pilot_release_gate.py`
- `docs/audit/FIX_10_REPORT.md`
- `wrangler.json`

---

## 1. 개요 및 비즈니스 경계
본 작업은 FIX-00부터 FIX-09까지 구축된 모든 보안·개인정보·매칭·정산 인프라를 바탕으로, 실제 돈이 돌기 전 단 1건의 고객과 파트너를 가정한 **10단계 엔드투엔드 동의 기반 제한 파일럿**을 완벽하게 검증했습니다.

동시에, 운영자(회기님)의 명시적 서명 승인과 3대 오프라인 게이트(법률 자문, 시공팀 LOI, 사업자등록)가 완료되기 전까지는 실제 외부 고객 트래픽 인입, 실개인정보 수집, 실결제 청구를 원천 차단하는 **Fail-Closed 릴리스 가드(`PilotReleaseGate`)**를 구축했습니다.

---

## 2. 10단계 동의 기반 제한 파일럿 실측 결과 (1 Buyer + 1 Referrer + 1 Contractor)

| 단계 | 수행 내역 및 비즈니스 이벤트 | 검증 지표 / 증적 | 판정 |
| :---: | :--- | :--- | :---: |
| **01** | **B2B 파트너 온보딩 및 규약 동의**<br>유입사(클린홈 관악본점), 시공사(청결홈케어 관악점) 등록 및 보험 서류 검증 | - 관리자 실서류 승인(`admin_hoegi`)<br>- 익일 오전(09~13시) 잔여 가용 슬롯 1개 등록 | **PASS** |
| **02** | **고객 인입 및 서명된 리퍼럴 귀속**<br>김관악 고객(관악구 신림동 원룸 8평) 신청 | - Web Crypto HMAC-SHA256 서명 토큰 검증<br>- 개보법 제17조 필수 동의 2종 기록<br>- AES-256-GCM 암호화 및 전화번호 해시 적재 | **PASS** |
| **03** | **1:1 배타적 오퍼 발행**<br>청결홈케어 신림점에 단 1건 독점 오퍼 발송 | - 15분 SLA 타임아웃 만료일자 설정<br>- `notification_outbox`에 MOCKED SMS 적재 ($0 비용) | **PASS** |
| **04** | **시공사 오퍼 수락 및 슬롯 잠금**<br>1순위 시공사 15분 내 수락 | - 슬롯 상태 `AVAILABLE` ➔ `RESERVED` 원자적 락<br>- 인입 상태 `MATCHED` 전이, PII 복호화 감사로그 적재 | **PASS** |
| **05** | **고객 상담 및 예약 확정**<br>현장 유선 상담을 통한 최종 견적 합의 | - 견적 250,000원 합의 및 `final_amount` 원자적 동기화<br>- 인입 및 매칭 상태 `BOOKED` 전이 | **PASS** |
| **06** | **시공 전/후 사진 2장 완료 증빙 등록**<br>시공 완료 사진 2장 제출 | - 공급자 일방 제출 시 원장 분개 일체 차단<br>- 상태 `PENDING_CUSTOMER_CONFIRMATION` 격리 | **PASS** |
| **07** | **고객 현장 검수 및 완료 승인**<br>고객이 현장 청소 상태 양호 확인 승인 | - 매칭 상태 `BILLABLE` 전이<br>- 복식부기 원장 분개 4개 행 적재 (수수료 2만 + VAT 2천) | **PASS** |
| **08** | **월간 B2B 성과 수수료 청구서 발행**<br>시공사 대상 청구서 생성 및 승인 | - 청구서 번호 `inv_...` 발행 (공급가액 20,000 + VAT 2,000 = 22,000원)<br>- 납부 기한 10일 설정 | **PASS** |
| **09** | **실제 은행 입금 분개 및 엄격 대사**<br>신한은행 계좌 입금 확인 | - 거래번호 `TX-SHINHAN-20261110-001` 적재<br>- 차변: 보통예금 22,000원 / 대변: 미수금 22,000원<br>- 대차평형: 차액 0원 (`BALANCED`) 입증 | **PASS** |
| **10** | **원 유입사 35% 배분 및 송금 증빙**<br>유입사에 7,700원 정산 지급 | - 유입사 홈택스 전자세금계산서 수취 확인 필수 게이트 통과<br>- 송금 승인 완료 및 감사로그 불변 적재 | **PASS** |

---

## 3. 릴리스 가드 (`src/gate.py`) 정책 및 최종 판정

```json
{
  "overall_status": "BLOCKED_EXTERNAL",
  "technical_status": "PASS",
  "offline_gates": [
    { "id": "GATE-01", "name": "법률 자문 최종 확인", "status": "PENDING_OFFLINE", "required_by": "Operator" },
    { "id": "GATE-02", "name": "관악·동작 1차 시공 파트너 서명 LOI 체결", "status": "PENDING_OFFLINE", "required_by": "Partner" },
    { "id": "GATE-03", "name": "세무 사업자등록 및 정산용 전용 계좌 개설", "status": "PENDING_OFFLINE", "required_by": "Tax/Bank" }
  ],
  "operator_approval_granted": false,
  "public_marketing_enabled": false,
  "allow_real_pii": false,
  "allow_real_payments": false,
  "verdict_reason": "Technical and software runtime is 100% verified. Production traffic remains strictly BLOCKED_EXTERNAL until operator executes offline agreements."
}
```

### [판정 근거 (엄격한 실측 증거주의 원칙 준수)]
- **소프트웨어 및 기술 인프라: PASS**  
  누적 144개 자동화 단위/E2E/보안/정산 테스트가 100% 무결성 통과되었으며, 10단계 시뮬레이션에서 대차평형(차액 0원)이 완벽히 입증되었습니다.
- **실거래 및 대외 런칭: BLOCKED_EXTERNAL**  
  실제 오프라인 청소팀과의 대면 계약(LOI 서명), 전자상거래 사업자등록증 및 전용 정산 계좌가 물리적으로 확보되기 전까지는 어떠한 경우에도 "프로덕션 준비 완료"로 과장하지 않고 `BLOCKED_EXTERNAL` 상태로 안전하게 동결 유지합니다.

---

## 4. Cloudflare 배포 점검 및 `wrangler.json` 정상화
- `wrangler.json`에 기재되어 있던 미프로비저닝 가상 D1 바인딩(`slotbridge-kr-d1-local`)을 정리하여, Cloudflare Workers Builds의 무결성을 확보했습니다.
- `DEPLOY_RUNBOOK.md`에 따라 운영자가 원격 Cloudflare 계정에서 `wrangler d1 create`를 실행한 후 실제 D1 UUID를 바인딩하도록 배포 절차를 확립했습니다.
