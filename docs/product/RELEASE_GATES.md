# SlotBridge Korea — 릴리스 게이트 및 인수 테스트 기준 (RELEASE_GATES.md)

**문서 버전:** 1.0.0  
**작성일자:** 2026-10-31  
**품질 기준:** "테스트되지 않은 코드는 배포되지 않는다 (Untested = Unreleased)"  
**작성 주체:** Codex Staff Engineer & QA Architecture Team  

---

## 1. 단계별 릴리스 게이트 (Stage-Gate Framework)

| 게이트 ID | 단계 명칭 | 필수 통과 요건 (Verification Criteria) | 판정 기준 |
|---|---|---|:---:|
| **Gate G-0** | 설계 및 PRD 완성 (Step 03) | • PRD, 사용자 여정, 화면 명세, OpenAPI 규약, ADR, 위협 모델, 비용 정책 완비<br>• 기존 `main` 브랜치 정적 자산 무결성 100% 보존 확인 | **PASS** |
| **Gate G-1** | 데이터 모델 및 보안 기반 (Step 04) | • D1 마이그레이션 스크립트 무결성 검증<br>• PII `AES-256-GCM` 암호화 및 복호화 단위 테스트 통과<br>• 콘솔 로그 내 전화번호/이름 마스킹 테스트 통과 | 대기 |
| **Gate G-2** | 공급자망 및 슬롯 엔진 (Step 05) | • 동시 수락 시 이중 예약(Race Condition) 방지 트랜잭션 테스트 통과<br>• 미승인 파트너 오퍼 노출 차단 검증 | 대기 |
| **Gate G-3** | 고객 인입 및 법정 동의 (Step 06) | • 필수 동의 체크박스 해제 시 폼 제출 차단 검증<br>• `consent_events` 불변 감사 레코드 정상 적재 확인 | 대기 |
| **Gate G-4** | 매칭 및 15분 타임아웃 (Step 07) | • 15분 미수락 시 차순위 파트너 자동 재배정 시뮬레이션 통과<br>• 원 유입업체 리퍼럴 ID 불변 결속 검증 | 대기 |
| **Gate G-5** | 상용 배포 및 운영 개시 (Step 08+) | • 운영자 사업자등록증 및 B2B 계약서 체결 (`BLOCKED_EXTERNAL` 해제)<br>• 관내 청소팀 2곳 이상 LOI 체결 및 가용 슬롯 등록 완료 | 대기 |

---

## 2. 인수 테스트 케이스 매트릭스 (Acceptance Test Matrix)

### TC-SEC-01: PII 암호화 및 로그 마스킹 테스트
- **Given:** 고객이 성명 '김관악', 연락처 '010-1234-5678'로 인입 신청을 제출했을 때
- **When:** D1 데이터베이스의 `inquiries` 테이블을 원시 SQL로 직접 조회하면
- **Then:** `customer_name_encrypted`, `customer_phone_encrypted` 컬럼은 암호화된 바이너리/Base64 형태여야 하며, 평문 문자열이 데이터베이스에 존재해서는 안 된다. 또한 Cloudflare Worker 콘솔 로그 출력 시 `010-****-5678`, `김*기`로 마스킹되어야 한다.

### TC-CONCUR-01: 슬롯 동시 점유 방지 (Double Booking Prevention)
- **Given:** 1개의 가용 슬롯(2026-11-02 오전)에 대해 2명의 파트너가 동시에 수락 API(`/api/partner/offers/{id}/accept`)를 호출했을 때
- **When:** 두 요청이 동일한 밀리초(ms) 단위로 Worker에 도달하면
- **Then:** D1 SQLite 트랜잭션에 의해 단 1개의 요청만 `HTTP 200 (CONFIRMED)`을 반환해야 하며, 나머지 1개 요청은 `HTTP 409 (CONFLICT / ALREADY_RESERVED)`를 반환하고 즉시 롤백되어야 한다.

### TC-LEGAL-01: 필수 동의 누락 시 폼 제출 거부
- **Given:** 고객이 '개인정보 제3자 제공 동의' 체크박스를 해제한 상태로 폼을 제출할 때
- **When:** `POST /api/inquiries` API가 호출되면
- **Then:** 서버는 `HTTP 400 Bad Request`와 함께 `{"error_code": "CONSENT_REQUIRED"}`를 반환해야 하며, 데이터베이스에 인입 레코드가 생성되지 않아야 한다.

---

## 3. 회귀 방지 및 롤백 정책 (Rollback Plan)

1. **독립 브랜치 격리:** 모든 MVP 코드는 `feat/slotbridge-kr-mvp` 브랜치에만 커밋되며, 관리자 최종 승인 전까지 `main` 브랜치로 절대 머지하지 않는다.
2. **배포 즉시 롤백:** 배포 후 예상치 못한 오류 발생 시, Cloudflare Pages/Workers 대시보드에서 이전 정상 빌드로 1-Click 즉시 롤백(소요시간 < 10초)을 수행한다.
3. **기존 자산 불간섭 검증:** 빌드 완료 후 기존 `saas-benchmarks` 메인 홈(`/`) 및 벤치마크 3종 경로에 대한 HTTP 200 반환 여부를 엔드투엔드로 자동 검증한다.
