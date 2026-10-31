# [SBK-FIX-04] COMPLETION REPORT: Real Supply Gate & State Machine Transaction Integrity

**Document ID:** SBK-DOC-FIX-04  
**Execution Date:** 2026-10-31 21:55 (Asia/Seoul)  
**Baseline Commit:** `5c53643`  
**Ticket:** `[SBK-FIX-04 | REAL SUPPLY GATE & STATE MACHINE INTEGRITY]`  
**Status:** **PASS** (공급업체 자격·보험 검증 분리, 슬롯 1팀1작업 충돌 방지, 원자적 상태 전이 및 15분 타임아웃 자동 회수 완결)

---

## 1. 개요 및 결함 해결 분석 (Problem & Architectural Resolution)

`[SBK-FIX-00]` 감사에서 적발된 **P0 결함 B06(공급업체 자격·보험 검증 미비)**, **P0 B07(원자적 검증 없는 일방적 상태 전이)**, **P1 B11(슬롯 동시성 경합)**, **P1 B12(15분 타임아웃 수동 호출 방치)**를 근원적으로 해결함.

### [해결된 핵심 결함 대조]
| 결함 ID | 기존 취약점 | FIX-04 개선 사항 | 비즈니스 보호 효과 |
| :--- | :--- | :--- | :--- |
| **B06** | 파트너 신청 시 보험증권번호 문자열만 입력해도 `insurance_status = 'VERIFIED'`로 기록되고, 승인 시 무조건 보험 검증 처리됨. | 1. 파트너 신청 시 보험 상태는 무조건 `PENDING_VERIFICATION` 고정.<br>2. 관리자 실서류 검증 시에만 `VERIFIED` 승인.<br>3. 파트너 정지 시 잔여 `AVAILABLE`/`OFFERED` 슬롯 및 `PENDING` 오퍼 일괄 취소 및 인입 재큐잉. | 무자격·무보험 업체의 고객 현장 출동 및 파손 사고 연대 책임 리스크 원천 차단. |
| **B07** | `transition_state`가 DB 현재 상태를 확인하지 않고 호출자의 `from_state` 인자만 신뢰하여 무조건 덮어씀. | DB 레벨의 원자적 조건부 쿼리 `WHERE id = ? AND status = from_state` 강제. 불일치 시 즉시 409 Conflict (`STATE_CONFLICT`) 예외 발생. | 상태 역행, 중복 처리, 동시 호출로 인한 결제/시공 상태 꼬임 원천 방지. |
| **B11** | 가용 슬롯 선점 및 오퍼 수락 시 동시성 락 부재로 동일 슬롯 중복 배정 가능성 존재. | 1. `evaluate_and_dispatch_candidates`에서 슬롯 잠금 시 `WHERE status = 'AVAILABLE'` 조건부 락 적용.<br>2. `accept_offer`에서 `WHERE status = 'PENDING'` 조건부 업데이트 적용. 동시 수락 시 차순위 409 Conflict 차단. | 동일 날짜/시간대 슬롯 2중 예약(Double Booking) 원천 차단. |
| **B12** | 15분 독점 오퍼 만료 처리가 수동 호출에 의존하여 타임아웃 후 오퍼 방치 위험 존재. | 1. `process_expired_offers()` 일괄 배치 함수 신설 (만료 오퍼 회수 -> 슬롯 원복 -> 2순위 자동 재배정).<br>2. Worker `POST /api/v1/slotbridge/cron/process-timeouts` 엣지 크론 엔드포인트 탑재. | 15분 경과 후 방치 오퍼 0건 달성 및 고객 급행 매칭 SLA 준수. |

---

## 2. 세부 구현 및 변경 파일 내역

| 파일 경로 | 수정 및 구현 내용 |
| :--- | :--- |
| `src/supplier.py` | 1. `apply_partner`: 보험 상태 `PENDING_VERIFICATION` 고정.<br>2. `approve_partner`: `insurance_verified` 파라미터 분리로 미검증 시 승인 불가.<br>3. `suspend_partner`: 가용 슬롯 취소뿐 아니라 대기 오퍼 취소 및 인입 건 `PENDING_MATCH` 자동 재큐잉.<br>4. `register_capacity_slot`: 1팀 1작업 원칙에 따라 동일 날짜 `ALL_DAY`와 `MORNING`/`AFTERNOON` 상호 충돌 감지(409 Conflict). |
| `src/matching.py` | 1. `transition_state`: DB 현재 상태 원자적 조건부 업데이트 (`WHERE id = ? AND status = ?`) 및 불일치 시 409 Conflict.<br>2. `evaluate_and_dispatch_candidates`: 고객 `time_window` 매칭 하드필터 추가 및 슬롯 원자적 락 적용.<br>3. `process_expired_offers`: 15분 SLA 초과 오퍼 일괄 회수 및 2순위 자동 재배정 배치 구현. |
| `src/engine.py` | `accept_offer`: `assignment_offers` 상태 전이 시 `WHERE status = 'PENDING'` 원자적 조건부 락 적용 (동시 수락 409 Conflict). |
| `src/worker.js` | `POST /api/v1/slotbridge/cron/process-timeouts` 엔드포인트 구현 (엣지 D1 만료 오퍼 회수 및 슬롯 원복). |
| `scripts/production_dry_run.py` | 1-Team-1-Job 규칙에 따라 슬롯 분산 배치 조정 (17개 가용 슬롯 충돌 없는 정상 서빙). |
| `tests/test_supply_and_state_machine.py` (신규) | 공급업체 게이트, 보험 검증, 슬롯 충돌, 원자적 상태머신, 동시성 락, 15분 배치 만료 8대 테스트 스위트 구축. |

---

## 3. 신규 자동화 테스트 검증 결과 (`tests/test_supply_and_state_machine.py`)

| 번호 | 테스트 시나리오 | 검증 결과 |
| :---: | :--- | :---: |
| 1 | **임의 보험번호 입력 시 미검증 유지:** 신청 시 무조건 `PENDING_VERIFICATION` 적재 | **통과 (0.004s)** |
| 2 | **무보험 파트너 하드필터 제외:** 보험 미검증 승인 파트너는 매칭 대상에서 완전 배제 | **통과 (0.003s)** |
| 3 | **파트너 정지 시 슬롯/오퍼 일괄 취소:** 정지 시 모든 활성 슬롯 및 대기 오퍼 즉시 취소 | **통과 (0.003s)** |
| 4 | **1-Team-1-Job 슬롯 충돌 차단:** 동일 일자 `MORNING` 등록 후 `ALL_DAY` 등록 시 409 Conflict | **통과 (0.002s)** |
| 5 | **원자적 상태머신 충돌 방어:** 이전 상태 불일치 시 전이 거부 및 409 Conflict 반환 | **통과 (0.003s)** |
| 6 | **고객 희망 시간대 필터링:** 고객 오후 희망 시 오전 슬롯 매칭 제외 | **통과 (0.003s)** |
| 7 | **오퍼 동시 수락 경합 방어:** 이미 수락된 오퍼의 2회차 수락 시도 시 409 Conflict 차단 | **통과 (0.005s)** |
| 8 | **15분 SLA 만료 일괄 회수 및 재배정:** 만료 오퍼 감지 즉시 `EXPIRED` 전이 및 차순위 재배정 | **통과 (0.007s)** |

---

## 4. 전체 회귀 테스트 검증 결과

- **테스트 실행 명령:** `python3 -m unittest discover -s tests -p "test_*.py"`
- **실행 결과:**
  ```text
  Ran 104 tests in 0.873s - OK
  ```
- **판정 요약:** 누적 104개 테스트 전원 통과 (기존 96개 + 신규 8개 상태머신·공급망 테스트, 회귀 0건, 100% 무결성).

---

## 5. 최종 판정 및 다음 티켓 안내

- **판정:** **PASS** (공급업체 자격 게이트, 슬롯 중복 충돌 방지, 원자적 상태 전이, 15분 만료 자동화 완결)
- **다음 순차 티켓:** **[SBK-FIX-05 | MATCHING THAT REALLY CONVERTS]**
  - 핵심 작업: 매칭 단일 진입점 일원화, 동/시간/가격/자격 종합 하드필터, 0원 알림 큐 일치.
