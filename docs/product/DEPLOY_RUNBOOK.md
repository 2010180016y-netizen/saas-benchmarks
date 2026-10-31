# SlotBridge Korea — 프로덕션 배포 런북 (DEPLOY_RUNBOOK.md)

**문서 버전:** 1.0.0  
**적용 대상:** Cloudflare Workers, Cloudflare D1, Cloudflare Pages/Static Assets  
**작성일자:** 2026-10-31  
**목표 배포 환경:** `feat/slotbridge-kr-mvp` ➔ `main` (Production)  

---

## 1. 사전 준비사항 (Prerequisites)

1. Cloudflare 계정 로그인 및 API 토큰 확보.
2. 로컬 터미널에 `wrangler` CLI 설치 및 인증:
   ```bash
   wrangler whoami
   ```
3. 기존 정적 사이트(`saas-benchmarks`)의 Cloudflare 프로젝트명 확인.

---

## 2. Cloudflare D1 데이터베이스 생성 및 마이그레이션

### 2.1 프로덕션 D1 데이터베이스 생성
```bash
wrangler d1 create slotbridge-db
```
*출력되는 `database_id`와 `database_name`을 `wrangler.toml`에 바인딩합니다.*

### 2.2 DDL 스키마 적용 (21개 테이블)
```bash
wrangler d1 execute slotbridge-db --file=./migrations/0001_initial_schema.sql --remote
```
*적용 후 테이블 생성 확인:*
```bash
wrangler d1 execute slotbridge-db --command="SELECT count(*) as count FROM sqlite_master WHERE type='table';" --remote
```

---

## 3. 비밀키 및 환경변수 주입 (Secrets Management)

민감한 암호화 키와 서명 시크릿은 코드에 절대 하드코딩하지 않고 Cloudflare Secret으로 안전하게 주입합니다:

```bash
# 1. AES-256-GCM 32바이트 대칭 암호화 키 (Base64)
wrangler secret put ENCRYPTION_KEY_BASE64

# 2. HMAC-SHA256 위젯 리퍼럴 토큰 서명 시크릿
wrangler secret put HMAC_SECRET

# 3. 비상 킬스위치 기본값 (비활성화)
wrangler secret put SYSTEM_KILL_SWITCH <<< "false"
```

---

## 4. `wrangler.toml` 라우팅 및 정적 자산 바인딩

기존 정적 SaaS 사이트와 SlotBridge 신규 모듈이 충돌 없이 공존하도록 라우팅을 구성합니다:

```toml
name = "saas-benchmarks"
main = "src/worker.js"
compatibility_date = "2026-10-31"

# D1 바인딩
[[d1_databases]]
binding = "DB"
database_name = "slotbridge-db"
database_id = "<DATABASE_ID>"

# 정적 자산 (기존 SaaS 비교 사이트 + SlotBridge UI)
[assets]
directory = "."
binding = "ASSETS"
```

- **라우팅 규칙:**
  - `GET /`, `GET /benchmarks/*`, `GET /assets/*` ➔ 기존 정적 파일 직접 서빙 (캐시 100%)
  - `GET /slotbridge/*` ➔ SlotBridge UI 서빙
  - `POST /api/v1/slotbridge/*` ➔ SlotBridge Workers 비즈니스 로직 및 D1 쿼리 처리

---

## 5. 프로덕션 배포 실행

```bash
cd static-sites/saas-benchmarks
wrangler deploy
```

---

## 6. 배포 후 스모크 테스트 (Post-Deployment Smoke Test)

1. **기존 SaaS 사이트 무결성 검증 (필수):**
   - `curl -I https://<도메인>/` ➔ HTTP 200 확인.
   - `curl -I https://<도메인>/benchmarks/deepseek-vs-claude.html` ➔ HTTP 200 확인.
   - `curl -I https://<도메인>/go/deepseek` ➔ HTTP 307 리디렉션 확인.
2. **SlotBridge UI 서빙 검증:**
   - `curl -I https://<도메인>/slotbridge/inquiry.html` ➔ HTTP 200 확인.
   - `curl -I https://<도메인>/slotbridge/partner-apply.html` ➔ HTTP 200 확인.
   - `curl -I https://<도메인>/slotbridge/widget.js` ➔ HTTP 200 (5KB 경량 스크립트) 확인.
3. **API 엔드포인트 헬스체크:**
   - `curl https://<도메인>/api/v1/slotbridge/health` ➔ `{"status": "ok", "circuit_breaker": "healthy"}`.

---

## 7. 긴급 롤백 가이드 (Emergency Rollback SOP)

### 7.1 [Lv 1] 5초 비상 킬스위치 가동
서비스 이상 징후 포착 즉시 모든 신규 인입 및 쓰기를 동결합니다:
```bash
wrangler secret put SYSTEM_KILL_SWITCH <<< "true"
```
*고객 폼 및 API는 즉시 503(안내 페이지)을 반환하며 데이터 오염이 100% 방지됩니다.*

### 7.2 [Lv 2] 배포 버전 즉시 롤백
Cloudflare 이전 안정 배포 버전으로 1초 만에 롤백합니다:
```bash
wrangler rollback
```

### 7.3 [Lv 3] D1 데이터베이스 롤백
```bash
wrangler d1 execute slotbridge-db --file=./migrations/0001_rollback.sql --remote
```
