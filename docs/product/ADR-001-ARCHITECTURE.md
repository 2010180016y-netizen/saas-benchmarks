# 아키텍처 결정 기록: ADR-001

**제목:** ADR-001: Cloudflare Workers + Static Assets + D1 (SQLite) 기반의 무자본 서버리스 성과 매칭 엔진 채택  
**일자:** 2026-10-31  
**상태:** 승인됨 (Accepted)  
**결정 주체:** Codex Staff Engineer / Systems Architect  

---

## 1. 맥락 및 문제 정의 (Context)

- **기존 인프라 현황:** `saas-benchmarks` 저장소는 Cloudflare Workers Static Assets를 통해 글로벌 엣지(한국 서울 PoP 포함)에서 정적 HTML/인포그래픽을 0원 비용으로 25초 이내 자동 배포하고 있음.
- **신규 사업 요건 (SlotBridge Korea):**
  1. **$0 운영비 원칙:** 초기 자본 투입 없이 Cloudflare Free Tier 내에서 모든 인프라(컴퓨팅, 데이터베이스, 스토리지, 배포)를 구동해야 함.
  2. **극단적 성능:** 모바일 접속자 LCP < 1.2초, API TTFB < 80ms.
  3. **데이터 무결성 및 동시성:** 24~72시간 잔여 슬롯의 이중 배정(Race Condition)을 원천 차단하는 원자적 트랜잭션 필요.
  4. **개인정보 보호:** PII 필드 레벨 암호화(`AES-256-GCM`) 및 엄격한 격리.
  5. **벤더 락인 최소화:** 특정 클라우드 종속을 방지하고 필요 시 10분 내 타 환경(Docker, Fly.io, PostgreSQL)으로 이전 가능한 표준 기술 채택.

---

## 2. 평가된 대안 비교 (Options Considered)

| 비교 항목 | 옵션 A: Supabase + Next.js (Vercel) | 옵션 B: AWS Lambda + DynamoDB | **옵션 C: Cloudflare Workers + D1 + Vanilla JS (선택)** |
|---|---|---|---|
| **월간 인프라 비용** | 무료 티어 1주 미사용 시 일시정지, 초과 시 $25/mo | 최소 $0~$15/mo (Route53, API Gateway 등) | **$0.00 (영구 무료 티어 한도 내 충분)** |
| **한국 엣지 지연시간** | 도쿄/싱가포르 리전 경유 (120~200ms) | 서울 리전 (50ms)이나 콜드스타트 1~2초 | **서울 PoP 엣지 즉시 실행 (15~35ms, 콜드스타트 0)** |
| **슬롯 동시성 제어** | PostgreSQL Row Lock (`FOR UPDATE`) | 조건부 쓰기 (Conditional Put) 복잡성 | **SQLite 기반 단일 라이터 완전 직렬화 (이중 예약 0%)** |
| **기존 레포 공존성** | 기존 정적 자산 전면 리팩터링 필요 (충돌) | 별도 인프라 파이프라인 구축 필요 | **기존 `saas-benchmarks`와 단일 레포 완전 공존** |

---

## 3. 아키텍처 결정 사항 (Decision)

### 3.1 하이브리드 단일 레포 구조 (Monorepo Coexistence)
- 기존 SaaS 제휴 벤치마크 사이트(`main` 브랜치)는 루트(`/`)와 기존 하위 경로에서 100% 불변 보존.
- 신규 SlotBridge Korea는 피처 브랜치(`feat/slotbridge-kr-mvp`)에서 `/slotbridge/*` 경로 및 `/api/*` 엔드포인트로 격리 배포.

### 3.2 컴퓨팅 및 데이터 계층
- **API 레이어:** Cloudflare Workers (TypeScript/JavaScript). 초경량 라우터(Hono 기반 또는 네이티브 `fetch` 라우터) 사용으로 번들 크기 50KB 미만 유지.
- **데이터베이스:** Cloudflare D1 (Serverless Distributed SQLite).
  - 무료 한도: 일일 5,000,000회 읽기, 100,000회 쓰기, 5GB 스토리지. (월 1,000건 매칭 시 일일 쓰기 1,000회 미만으로 무료 한도의 1% 미만 사용).
  - SQLite 표준 SQL 채택으로 향후 PostgreSQL이나 Turso, SQLite on VPS로 즉시 이전 가능.

### 3.3 어댑터 패턴 기반 외부 I/O 추상화 (Loose Coupling)
- **알림 인터페이스 (`NotificationAdapter`):**
  - MVP 단계: `ConsoleMockNotifier` 및 고객 웹 폴링/알림으로 $0 운영.
  - 상용화 단계: 기능 플래그(`FEATURE_KAKAO_ALIMTALK = true`) 전환만으로 Solapi/알리고 알림톡 API 교체 가능.
- **정산/세무 인터페이스 (`BillingAdapter`):**
  - MVP 단계: `CsvExportBillingAdapter` (홈택스 일괄 업로드용 표준 CSV 생성).
  - 상용화 단계: 팝빌/바로빌 전자세금계산서 자동 발급 API 플러그인.

---

## 4. 결정의 결과 (Consequences)

### 긍정적 효과 (Positive)
1. **자본 비용 0원:** 서버비, DB 호스팅비, API 호출비 등 고정비 0원 달성.
2. **동시성 완벽 해결:** D1 SQLite의 트랜잭션 보장으로 1개 잔여 슬롯에 대해 2개 청소팀이 동시 수락하는 Race Condition 원천 차단.
3. **극한의 로딩 속도:** 프레임워크 런타임이 없는 순수 바닐라 JS로 모바일 환경에서 1초 미만 화면 렌더링.

### 부정적 효과 및 완화책 (Negative & Mitigations)
1. **D1 멀티 리전 지연:** D1 프라이머리 라이터 위치에 따른 쓰기 지연 (완화: 한국 사용자에 맞춰 아시아 동부 리전 고정).
2. **복잡한 쿼리 제약:** SQLite 특성상 고도 분석 쿼리 한계 (완화: 트랜잭션용 D1과 별개로 월간 정산은 집계 배치 스크립트로 분리).
