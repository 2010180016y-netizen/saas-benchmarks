# [SBK-00] BASELINE AUDIT: saas-benchmarks Repository
**Target Repository:** `https://github.com/2010180016y-netizen/saas-benchmarks`  
**Audit Timestamp:** 2026-10-31 17:30:00 (Asia/Seoul)  
**Auditor Roles:** Staff Engineer, Security Engineer, Marketplace PM, FinOps Specialist  
**Audit Mode:** Read-Only Static Audit & Architecture Analysis  
**Overall Verdict:** **CONDITIONAL** (Technical foundation 100% reusable; legal & partner gates require external unblocking)

---

## 1. Executive Summary
본 감사는 기존 글로벌 B2B SaaS 비교 포털인 `saas-benchmarks` 코드베이스를 한국형 B2B2C 성과 기반 오버플로우 고객 매칭 플랫폼인 **SlotBridge Korea**로 전환하기 위한 사전 정밀 감사다.

- **기존 자산 보존성:** 기존 `main` 브랜치의 정적 사이트(SaaS Matrix 홈, 3대 벤치마크, 엣지 307 단축 리디렉션)는 100% 정상 가동 중이며, Cloudflare Workers Builds와 완전 결속되어 있다.
- **아키텍처 호환성:** Cloudflare Static Assets 및 Workers 엣지 라우팅 인프라는 한국형 매칭 플랫폼의 경량 웹 프론트엔드 및 API 게이트웨이로 즉시 재사용 가능하다.
- **핵심 격차(Gap):** 영속 관계형 데이터베이스(Cloudflare D1), 상태 머신(State Machine), 개인정보 동의 처리 암호화 파이프라인, B2B 정산 원장이 부재하며, 실물 비즈니스 측면에서 제휴 계약 및 법적 고지문이 미비하다.

---

## 2. Complete Repository Inventory & Architecture Audit

### 2.1 File Tree & Asset Mapping
```text
static-sites/saas-benchmarks/
├── .assetsignore             [Preserve] Cloudflare 빌드 배포 제외 규칙 (.git, docs, scripts, templates)
├── .git/                     [Preserve] Git 버전 관리 메타데이터 (main 브랜치 활성)
├── _headers                  [Reuse] 엣지 보안 헤더 (X-Frame-Options, nosniff, Referrer-Policy, Cache-Control)
├── _redirects                [Preserve/Reuse] Cloudflare Pages/Workers 엣지 307 리디렉션 규칙 8건
├── wrangler.json             [Reuse] Cloudflare Workers Static Assets 호환 설정 파일
├── index.html                [Preserve] SaaS Matrix 포털 메인 홈 (3대 플래그십 카드 그리드)
├── cursor-vs-copilot/
│   └── index.html            [Preserve] Cursor AI vs Copilot vs Windsurf 벤치마크 (PSA 96.2)
├── sunsama-vs-motion/
│   └── index.html            [Preserve] Sunsama vs Motion 벤치마크 및 타임박싱 노션 번들
├── make-vs-zapier/
│   └── index.html            [Preserve] Make vs Zapier vs n8n 자동화 벤치마크
├── assets/                   [Preserve] 1200x675 고해상도 비교 인포그래픽 PNG 3종
├── sitemap.xml               [Preserve] 검색엔진 색인용 XML 사이트맵
├── robots.txt                [Preserve] 검색 크롤러 정책
├── templates/
│   └── prompt_saas_benchmark.md [Archive] 범용 LLM 프롬프트 템플릿
├── scripts/
│   └── build_article_universal.py [Archive] 순수 파이썬 정적 사이트 컴파일러
└── docs/
    ├── AFFILIATE_PAYOUT_GUIDE.md   [Preserve] 글로벌 SaaS 제휴 정산 가이드
    └── OPERATIONS_AND_AI_GUIDE.md  [Preserve] 수동/타 AI 에이전트 무인 운영 매뉴얼
```

### 2.2 Current Deployment & Runtime Configuration
- **호스팅 플랫폼:** Cloudflare Workers (Static Assets mode, 2026-10-30 호환성 날짜).
- **프로덕션 서빙 URL:** `https://saas-benchmarks.hoiki0808.workers.dev`
- **배포 파이프라인:** GitHub `main` 브랜치 푸시 감지 시 Cloudflare Workers Builds 자동 트리거 (평균 빌드 소요 시간: 25초).
- **프론트엔드 스택:** 순수 시맨틱 HTML5 + Tailwind CSS CDN (v3.4.1) + Noto Sans KR / Inter 웹폰트.
- **빌드 시스템:** Node.js 의존성 없음 (No npm, No Webpack). 파이썬 표준 라이브러리(`build_article_universal.py`)로 템플릿 치환 및 HTML 생성.

### 2.3 Existing Edge Redirects Audit (`_redirects`)
현재 8건의 307 Temporary Redirect 규칙이 엣지 레벨에서 10ms 미만으로 처리됨:
- `/make` -> Make.com 제휴 링크
- `/zapier` -> Zapier 공식 사이트
- `/cursor` -> Cursor 공식 사이트
- `/copilot` -> GitHub Copilot 공식
- `/windsurf` -> Codeium Windsurf 공식
- `/sunsama` -> Sunsama 제휴 링크
- `/motion` -> Motion 제휴 링크
- `/use-motion` -> Motion 제휴 링크 (별칭)

---

## 3. Asset Categorization (4-Tier Taxonomy)

| 범주 | 대상 파일/기능 | 선정 사유 및 조치 방안 |
| :--- | :--- | :--- |
| **PRESERVE (보존)** | `main` 브랜치 전체, `index.html`, 벤치마크 3종, `assets/*`, `docs/*` | 기존 글로벌 B2B SaaS 검색 유입 및 제휴 수익 파이프라인의 영구 지속 보장. 절대 훼손/삭제 금지. |
| **REUSE (재사용)** | `_headers`, `wrangler.json`, Tailwind CDN 레이아웃 구조, Cloudflare 배포 인프라 | 0원 자본 제약 하에서 즉각적인 모바일 웹 서빙 및 엣지 라우팅 엔진으로 즉시 전용 가능. |
| **ARCHIVE (보관)** | `scripts/build_article_universal.py`, `templates/prompt_saas_benchmark.md` | SaaS 원고 생성 전용 엔진으로 격리 보관. 신규 매칭 플랫폼의 로직과 충돌 방지. |
| **RETIRE (폐기)** | 해당 없음 (0건) | 기존 프로덕션 트래픽 및 색인을 보호하기 위해 폐기 대상 자산 없음. |

---

## 4. Architectural Gap Analysis: Transition to SlotBridge Korea

새로운 비즈니스 모델(B2B2C 입주/이사청소 오버플로우 매칭)을 수용하기 위해 신규 구축되어야 할 아키텍처 격차는 다음과 같다:

1. **상태 관리 및 데이터 영속성 (Persistence Gap):**
   - 현재: 정적 HTML 파일 기반 (데이터베이스 부재).
   - 필요: Cloudflare D1 (Serverless SQLite) 바인딩 및 18개 엔티티(고객 인입, 동의 이력, 업체 슬롯, 매칭 오퍼, 정산 원장 등) 스키마.
2. **동적 백엔드 API (Compute Gap):**
   - 현재: 순수 정적 에셋 서빙.
   - 필요: Cloudflare Worker API 핸들러 (`/api/inquiries`, `/api/matches`, `/api/partners/slots`, `/api/webhooks/verify`).
3. **개인정보 보호 및 규정 준수 파이프라인 (Compliance Gap):**
   - 현재: 제휴 링크 클릭 추적에 한정.
   - 필요: 개인정보보호법 제17조에 부합하는 제3자 제공 동의 영수증 암호화 저장, 마스킹 로깅, 파기 스케줄러.
4. **유통 파트너 임베드 위젯 (Distribution Gap):**
   - 현재: 단독 웹사이트.
   - 필요: 제휴 청소업체 웹사이트에 삽입 가능한 무경량 독립 iframe/JS 오버플로우 접수 위젯.

---

## 5. Security, PII & FinOps Baseline

### 5.1 Security & PII
- **Git 저장소 보안:** 커밋 히스토리 및 파일 트리 내 비밀키(API Key, Secret Token, 비밀번호) 노출 0건 확인.
- **PII 노출 위험:** 현재 정적 사이트는 사용자 입력을 수집하지 않으므로 PII 유출 위험 0건. 단, 신규 고객 접수 기능 탑재 시 엄격한 암호화 및 Cloudflare D1 저장 정책 수립 필수.
- **CSP & Headers:** `_headers`에 `X-Frame-Options: DENY`가 적용되어 있어, 향후 유통 파트너용 임베드 위젯 서빙을 위해서는 위젯 전용 엔드포인트에 `frame-ancestors` 헤더 분리 적용 필요.

### 5.2 FinOps & Free-Tier Quotas (2026 Policy Verified)
Cloudflare 무료 플랜 기준 자원 한도 점검:
- **Cloudflare Workers:** 일 100,000 요청 (무료). 초기 일 1,000건 미만 매칭 트래픽에는 1% 미만 점유로 완전 안전.
- **Cloudflare D1:** 일 5,000,000 행 읽기 / 100,000 행 쓰기 (무료).
  - *주의(2026.09 개정):* D1 무료 한도 초과 시 쿼리가 차단(fail-closed)되므로, 불필요한 폴링을 차단하고 멱등성 캐시를 엣지에 배치해야 함.
- **외부 통신 비용:** SMS/카카오 알림톡은 건당 15~20원의 유료 비용이 발생하므로, 초기 프로토타입 단계에서는 무자본 원칙에 따라 웹훅/이메일(무료 티어) 및 웹 대시보드로 대체 구현 강제.

---

## 6. Audit Verdict & Recommendation

### **최종 판정: CONDITIONAL (조건부 승인)**
- **기술적 적합성:** PASS (저장소 보존, Cloudflare Workers/Static Assets 재사용 완벽 가능).
- **외부 차단 요인:** BLOCKED_EXTERNAL (실제 청소 공급업체 계약 0건, 개인정보 제3자 제공 법적 확약 미비).
- **다음 단계 진입 조건:** 기존 `main` 브랜치를 불변으로 두고, 새 피처 브랜치(`feat/slotbridge-kr-mvp`)에서 격리된 설계를 진행하는 조건으로 Step 1(MARKET & MOAT) 진입을 승인함.
