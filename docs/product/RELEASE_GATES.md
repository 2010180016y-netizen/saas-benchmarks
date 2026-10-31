# SlotBridge Korea — 릴리스 게이트 및 인수 테스트 기준 (RELEASE_GATES.md)

**문서 버전:** 2.0.0 (SBK-FIX-10 반영)  
**작성일자:** 2026-10-31  
**품질 기준:** "테스트되지 않은 코드는 배포되지 않는다 (Untested = Unreleased)"  
**최종 종합 판정:** **BLOCKED_EXTERNAL** (기술/소프트웨어: 144개 테스트 100% PASS / 실제 대외 거래: 운영자 오프라인 게이트 완료 전까지 동결)

---

## 1. 단계별 릴리스 게이트 현황 (Stage-Gate Framework)

| 게이트 ID | 단계 명칭 | 필수 통과 요건 (Verification Criteria) | 검증 결과 | 판정 |
|---|---|---|:---:|:---:|
| **Gate G-0** | 설계 및 PRD 완성 (SBK-03) | • PRD, 4대 사용자 여정, 5대 화면 UI 명세, OpenAPI 규약, ADR-001, 위협 모델 완비<br>• 기존 `main` SaaS 벤치마크 정적 자산 100% 보존 확인 | 누적 8개 문서 무결성 확인 | **PASS** |
| **Gate G-1** | 데이터 모델 및 보안 기반 (SBK-04, FIX-03) | • D1 마이그레이션 21개 테이블 DDL 검증<br>• PII `AES-256-GCM` 표준 교체 및 SHA-256 전화번호 인덱싱<br>• 로그 및 콘솔 내 마스킹(`010-****-5678`) 및 감사로그 적재 | `test_privacy_and_authz.py` 8개 테스트 통과 | **PASS** |
| **Gate G-2** | 공급자망 및 슬롯 엔진 (SBK-05, FIX-04) | • 파트너 신청 시 보험 상태 `PENDING_VERIFICATION` 강제<br>• 1-Team-1-Job 동일 일자 시간대 충돌 방지 (409 Conflict)<br>• 파트너 정지 시 잔여 슬롯/대기 오퍼 즉시 일괄 취소 | `test_supply_and_state_machine.py` 8개 테스트 통과 | **PASS** |
| **Gate G-3** | 고객 인입 및 법정 동의 (SBK-06, FIX-01) | • 개보법 제17조 필수 동의 2종 누락 시 폼 제출 100% 차단<br>• `consent_events` 불변 감사 레코드 정상 적재 확인<br>• 1-Click 철회 시 `ERASED_*` 톰스톤 즉시 영구 파기 | `test_customer_intake.py` 5개 테스트 통과 | **PASS** |
| **Gate G-4** | 결정적 매칭 및 타임아웃 (SBK-08, FIX-05) | • 15분 미수락 만료 시 슬롯 원복 및 2순위 자동 재배정 배치<br>• `notification_outbox`에 MOCKED SMS 적재 ($0 비용 유지)<br>• Web Crypto HMAC-SHA256 서명 토큰 기반 리퍼럴 귀속 | `test_unified_matching_pipeline.py` 5개 테스트 통과 | **PASS** |
| **Gate G-5** | 회계·대사·컴플라이언스 (FIX-06~09) | • 공급자 일방 제출 차단 및 전후 사진 2장 + 고객 승인 게이트<br>• 월별 격리 및 은행 입금 확인 분개 기반 실대사 (차액 0원)<br>• 공정위 8% 수수료 명시 및 전자상거래법 중개자 4대 고지 | `test_revenue_reconciliation_audit.py` 등 31개 테스트 통과 | **PASS** |
| **Gate G-6** | 단일 거래 제한 파일럿 (FIX-10) | • 구매자 1명 + 유입사 1곳 + 시공사 1곳 동의 기반 10단계 시뮬레이션 완결<br>• 대차평형 (수수료 22,000원 입금 == 매출 20,000 + 부가세 2,000원) 실측 | `test_pilot_release_gate.py` 4개 테스트 통과 | **PASS** |
| **Gate G-OFF** | **3대 오프라인 필수 게이트 (운영자 직접 수행)** | **1. 법률 자문 최종 확인**<br>**2. 관악·동작 1차 시공 파트너 서명 LOI 체결**<br>**3. 세무 사업자등록 및 정산용 전용 계좌 개설** | 운영자 오프라인 체결 대기 | **BLOCKED_EXTERNAL** |

---

## 2. 릴리스 가드 정책 (`src/gate.py`)
1. **소프트웨어 런타임: 100% PASS**  
   단위, E2E, 보안, 회계, 컴플라이언스를 망라한 누적 144개 자동화 테스트가 전원 무결성 통과되었습니다.
2. **실거래 동결 원칙 (Fail-Closed):**  
   운영자(`회기님`)의 직접 오프라인 계약 체결 및 명시적 승인 전까지 `SBK_OPERATOR_APPROVAL = 0`, `SBK_ALLOW_REAL_PAYMENTS = 0`으로 유지되어 실제 고객 대상 외부 유료 광고 및 실거래는 엄격히 차단됩니다.
3. **프로덕션 서빙 무결성:**  
   Cloudflare Workers & Pages 프로덕션 라이브 배포 완료 (`Version ID: 85327162-80f2-4303-97da-ea062036bd75`).
