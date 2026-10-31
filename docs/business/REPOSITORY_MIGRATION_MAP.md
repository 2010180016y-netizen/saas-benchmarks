# [SBK-00] REPOSITORY MIGRATION MAP: SlotBridge Korea
**Source Repo:** `2010180016y-netizen/saas-benchmarks`  
**Base Branch:** `main` (Preserved / Protected)  
**Target Feature Branch:** `feat/slotbridge-kr-mvp`  
**Migration Goal:** Establish SlotBridge Korea B2B2C marketplace infrastructure without degrading existing B2B SaaS portal traffic, rankings, or 307 affiliate redirects.

---

## 1. Branching & Isolation Strategy

```text
git: origin/main (Protected Production)
   │
   ├── [Live Traffic] saas-benchmarks.hoiki0808.workers.dev/ (SaaS Matrix Home)
   ├── [Live Traffic] /cursor-vs-copilot, /sunsama-vs-motion, /make-vs-zapier
   └── [Edge Redirects] /make, /cursor, /sunsama (307 Temporary Redirects)
   │
   └── git checkout -b feat/slotbridge-kr-mvp (Isolated Development)
         │
         ├── Add: /slotbridge/ (Korean Emergency Cleaning Overflow Portal)
         ├── Add: src/ (Cloudflare Workers API Router + D1 Database Bindings)
         ├── Add: migrations/ (D1 SQLite Schemas)
         └── Add: docs/product/, docs/business/ (PRD, ADR, Specs)
```

- **규칙 1 (메인 브랜치 불변성):** `main` 브랜치에 직접 커밋하거나 기존 HTML/리디렉션을 수정하는 행위를 엄격히 금지함.
- **규칙 2 (도메인 경로 공존):**
  - 루트 경로 `/` 및 벤치마크 슬러그: SaaS Matrix 포털 유지.
  - 슬롯브릿지 전용 경로 `/slotbridge/*`: 한국형 매칭 서비스 웹 UI 및 위젯 서빙.
  - API 엔드포인트 `/api/slotbridge/*`: 매칭 및 동의 처리 백엔드 Worker 라우팅.

---

## 2. Target Directory Structure (Target State in `feat/slotbridge-kr-mvp`)

```text
saas-benchmarks/ (Repository Root)
├── .assetsignore                     [Preserve] 빌드 시 비공개 문서 제외
├── _headers                          [Update] /slotbridge/widget 경로 전용 frame-ancestors 완화
├── _redirects                        [Preserve] 기존 8개 SaaS 307 규칙 유지 + 신규 규칙 추가
├── wrangler.json                     [Update] D1 Database binding 추가 (slotbridge-db)
│
├── [EXISTING ASSETS - PRESERVE]
├── index.html                        (SaaS Matrix 홈)
├── cursor-vs-copilot/                (SaaS 1호 원고)
├── sunsama-vs-motion/                (SaaS 2호 원고)
├── make-vs-zapier/                   (SaaS 원고)
├── assets/                           (SaaS 인포그래픽)
│
├── [NEW: SLOTBRIDGE KOREA MVP]
├── slotbridge/
│   ├── index.html                    (소비자 24~72h 급행 청소 접수 및 동의 폼)
│   ├── partner-intake.html           (공급업체 온보딩 신청 및 가용 슬롯 등록)
│   ├── widget.html                   (원 유입업체 웹사이트 임베드용 iframe 위젯)
│   ├── widget.js                     (임베드 스크립트: 5KB 미만 무경량 번들)
│   ├── status.html                   (고객 본인 요청 실시간 진행 조회 및 동의 철회)
│   └── admin.html                    (관리자 매칭 큐 및 수동 개입 대시보드)
│
├── src/
│   ├── index.ts (or js)              (Cloudflare Worker 메인 라우터: 정적 에셋 + API 분기)
│   ├── api/
│   │   ├── inquiries.ts              (고객 접수, 동의 검증, 멱등성 보장)
│   │   ├── partners.ts               (업체 가용 슬롯 등록, 오퍼 수락/거절)
│   │   ├── matching.ts               (결정론적 매칭 엔진, TTL 10분 배타적 락)
│   │   ├── settlement.ts             (이중기입 원장 기록, 청구서 생성)
│   │   └── webhooks.ts               (완료 증빙 수신, 알림 아웃박스)
│   └── lib/
│       ├── db.ts                     (D1 쿼리 래퍼 및 쿼타 가드)
│       ├── crypto.ts                 (개인정보 해싱 및 동의 영수증 서명)
│       └── validation.ts             (전화번호/지역/면적 입력 검증 스키마)
│
├── migrations/
│   ├── 0001_initial_schema.sql       (organizations, partners, inquiries, matches 등)
│   └── 0002_ledger_and_billing.sql   (invoices, ledger_events, payouts)
│
└── docs/
    ├── business/
    │   ├── BASELINE_AUDIT.md         [Complete]
    │   ├── ASSUMPTION_REGISTER.csv   [Complete]
    │   ├── CRITICAL_BLOCKERS.md      [Complete]
    │   └── REPOSITORY_MIGRATION_MAP.md [Complete]
    ├── product/                      (Step 3 PRD, ADR, Screen Spec)
    └── qa/                           (Step 11 Release Report, Test Matrix)
```

---

## 3. Coexistence & Routing Architecture (Cloudflare Workers)

Cloudflare Workers의 요청 수명주기에서 두 서비스는 완벽히 분리되어 동작함:

```typescript
// src/index.ts 라우팅 개념도
export default {
  async fetch(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    const url = new URL(request.url);

    // 1. SlotBridge API 요청 분기 (/api/slotbridge/*)
    if (url.pathname.startsWith("/api/slotbridge/")) {
      return handleSlotBridgeApi(request, env, ctx);
    }

    // 2. SlotBridge 프론트엔드 서빙 (/slotbridge/*)
    // Static Assets가 public/slotbridge 경로의 HTML/CSS/JS를 직접 서빙

    // 3. 기존 SaaS Matrix 정적 에셋 및 307 리디렉션 처리
    // Cloudflare Workers Static Assets 엔진에 위임
    return env.ASSETS.fetch(request);
  }
};
```

---

## 4. Phase-by-Phase Execution Roadmap (M0 ~ M5)

| 마일스톤 | 단계 | 핵심 수행 내용 | 진입 조건 및 게이트 |
| :--- | :--- | :--- | :--- |
| **M0: 검증** | **Step 00~02** | 저장소 정밀 감사, 급행 청소 시장/해자 검증, 법적 고지/동의 설계 | **M0 PASS / 조건부 승인** |
| **M1: 오프라인 준비** | **Step 03** | PRD/ADR 완성, B2B 파트너 계약 템플릿, 개인정보 제3자 제공 동의안 | 운영자 법무/계약 오프라인 검토 |
| **M2: 코드 구축** | **Step 04~08** | D1 스키마, 공급자 포털, 고객 접수 폼, 오버플로우 위젯, 매칭 엔진 | 자동화 테스트 100% 통과 (Mock 데이터) |
| **M3: 금전관리 구축**| **Step 09~10** | 이중기입 B2B 정산 원장, 성과 수수료 청구 로직, SEO/성장 분석 | FinOps 및 계산 검증 통과 |
| **M4: 실무 파일럿** | **Step 11~12** | 레드팀 QA, 보안 감사, 실제 청소업체 2곳 계약 연동, 소규모 실거래 | **LEGAL_SIGNOFF + CONTRACTS_PRESENT** |
| **M5: 확장 평가** | **사후 운영** | 3개월 실제 정산 데이터 분석, 가여 이익 평가 후 뷰티/수리로 확장 판정 | 실제 성과료 입금 이력 확인 |

---

## 5. Codex Self-Resolvable vs Operator-Required Actions

| 작업 영역 | Codex 자체 구현 가능 (In-Scope) | 운영자 실제 오프라인 집행 필수 (Operator Scope) |
| :--- | :--- | :--- |
| **인프라/DB** | Cloudflare D1 DDL 작성, 마이그레이션 SQL 작성, 쿼리 최적화 | Cloudflare 대시보드에서 D1 데이터베이스 인스턴스 생성 및 바인딩 |
| **보안/동의** | 개인정보보호법 부합 동의 UI, 영수증 해싱 로직, 입력값 검증 | 최종 이용약관 및 개인정보 처리방침에 대한 법률 전문가 날인/승인 |
| **비즈니스** | 매칭 규칙 엔진, TTL 10분 오퍼 배정 로직, 가용 슬롯 캘린더 UI | 대상 지역(관악/동작 등) 실제 청소업체 2곳 이상과 B2B 서면 계약 체결 |
| **정산/매출** | 이중기입 가상 원장 구현, 성과료 계산기, 청구서 생성 로직 | 사업자 통장 개설, 세무 신고 및 실제 성과 수수료 무통장 입금 확인 |
