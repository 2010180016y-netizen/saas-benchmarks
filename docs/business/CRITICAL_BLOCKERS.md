# [SBK-00] CRITICAL BLOCKERS REGISTER
**Project:** SlotBridge Korea (한국형 급행 입주청소 오버플로우 매칭 네트워크)  
**Status:** ACTIVE AUDIT  
**Enforcement Policy:** Any `BLOCKER` marked with `BLOCKED_EXTERNAL` halts real-world customer data transmission, charging, and partner communication. Code implementation may proceed using Mocks and Adapters only.

---

## 1. Blocker Summary Matrix

| Blocker ID | Domain | Severity | Blocker Description | Resolution Type | Gate Status |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **BLK-LEG-01** | Legal | **BLOCKER** | 개인정보 제3자 제공 동의 규정(개인정보보호법 제17조) 법적 요건 미비 | **BLOCKED_EXTERNAL** | 🔴 BLOCKED |
| **BLK-LEG-02** | Legal | **CRITICAL** | 통신판매중개자 법적 고지 및 사업자 신원 확인 의무 미정립 | **BLOCKED_EXTERNAL** | 🔴 BLOCKED |
| **BLK-LEG-03** | Legal | **MAJOR** | 정보통신망법 제50조에 따른 영리목적 자동 메시지(알림톡/SMS) 규제 | TECHNICAL / POLICY | 🟡 CONDITIONAL |
| **BLK-BIZ-01** | Business | **BLOCKER** | 실제 계약된 청소 공급업체(수락업체) 및 유통 파트너(원 유입업체) 0건 | **BLOCKED_EXTERNAL** | 🔴 BLOCKED |
| **BLK-BIZ-02** | Operations | **CRITICAL** | 작업 완료 및 현금 거래 시 성과 수수료 고의 누락(Disintermediation) 방지책 부재 | TECHNICAL / OPERATIONS | 🟡 CONDITIONAL |
| **BLK-TECH-01**| Technical | **MAJOR** | Cloudflare D1 무료 한도(일 10만 쓰기) 초과 시 fail-closed 정책 위험 | ARCHITECTURE | 🟢 SOLVABLE |
| **BLK-FIN-01** | FinOps | **MAJOR** | 유료 SMS/카카오 알림톡 API 연동 비용(0원 자본 제약 위배) | ARCHITECTURE | 🟢 SOLVABLE |

---

## 2. Detailed Blocker Analysis & Unblocking Gates

### 🔴 BLK-LEG-01: 개인정보 제3자 제공 동의 미비 (Legal Blocker)
- **위험 내용:** A청소업체로 유입된 고객의 성명, 연락처, 주소(상세주소 포함)를 B청소업체로 전달할 때, 개인정보보호법 제17조에 따른 '제공받는 자, 제공 목적, 제공 항목, 보유 및 이용 기간'을 명확히 알리고 동의를 받지 않으면 5년 이하의 징역 또는 5천만 원 이하의 벌금에 처해질 수 있음.
- **차단 기준 (Safety Gate):**
  - 고객이 체크박스를 통해 명시적으로 동의하지 않은 데이터는 데이터베이스 저장 및 B업체 전송이 시스템적으로 전면 차단됨.
  - 제공 대상 업체명이 확정되지 않은 다자간 불특정 판매 방식은 코드 레벨에서 금지됨.
- **해제 조건 (Unblocking Gate):**
  - 표준 개인정보 수집 및 제3자 제공 동의문(법적 버전 관리 포함) 작성 및 운영자 법률 검토 완료.
  - 고객 동의 시점의 동의 영수증(Consent Receipt: IP, User-Agent, 동의 일시, 약관 버전) 암호화 저장 로직 구현.

### 🔴 BLK-BIZ-01: 실계약 공급자 네트워크 0개사 (Business Blocker)
- **위험 내용:** 플랫폼 코드가 100% 완성되어도 실제로 24~72시간 내 출동 가능한 검증된 청소업체가 없으면 매칭 실패율 100%가 발생하며, 고객 신뢰가 즉시 파괴됨.
- **차단 기준 (Safety Gate):**
  - 관리자 승인(`status: APPROVED`) 및 B2B 서면 계약(`contract_status: ACTIVE`)이 체결되지 않은 업체는 매칭 후보 풀에 진입할 수 없음.
  - 가짜 업체, 가짜 실시간 견적, 허위 리뷰 생성 절대 금지.
- **해제 조건 (Unblocking Gate):**
  - 파일럿 대상 지역(서울 관악구/동작구 등) 청소업체 최소 2곳 이상과 서면 B2B 파트너십(작업 완료 후 8% 성과 수수료 정산 합의) 체결 완료.

### 🟡 BLK-BIZ-02: 직거래 유도 및 성과료 탈루 위험 (Operations Blocker)
- **위험 내용:** 청소업체가 고객과 직접 연락한 뒤 "플랫폼 취소하고 현금으로 2만 원 깎아드릴게요"라며 플랫폼을 우회할 경우, 플랫폼은 인프라 비용만 지출하고 수익을 회수할 수 없음.
- **완화 설계 (Mitigation Architecture):**
  - 고객에게 '서비스 완료 및 불만족 케어 확인서'를 모바일 링크로 전송하여 성사 여부를 크로스 검증.
  - B2B 계약서에 부정 행위 적발 시 위약벌 조항 명시 및 파트너 자격 영구 박탈 정책 수립.
  - 청소 전후 검수 사진 증빙 제출 시에만 분쟁 보증 및 파트너 등급 인센티브 제공.

### 🟢 BLK-TECH-01: Cloudflare D1 Quota Guard (Technical Blocker)
- **기술적 해결책:**
  - 2026년 9월 개정된 Cloudflare D1 정책에 따라, 일일 100,000건 쓰기 한도에 도달하면 쿼리가 거부됨.
  - 메모리 내 LRU 캐시 및 Cloudflare KV(일 10만 건 무료 읽기)를 엣지 프론트에 배치하여 상태 조회를 오프로드함.
  - 쓰기 연산은 트랜잭션 단위로 묶어 처리(Batch Insert)하여 D1 I/O를 70% 이상 절감함.

### 🟢 BLK-FIN-01: 0원 통신 아키텍처 (FinOps Blocker)
- **기술적 해결책:**
  - 유료 SMS(건당 15원)나 알림톡(건당 8원) 대신, 초기에는 완전 무료인 이메일 트랜잭션 API(Resend Free Tier 일 100건 또는 Cloudflare Email Workers)와 웹 기반 모바일 대시보드로 알림 파이프라인을 구축함.
  - 향후 거래 볼륨 및 성과 수익 발생 시 알림톡 모듈로 점진적 승급(Feature Flag 추상화) 지원.
