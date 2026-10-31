# SaaS Matrix 수동 운영 및 타 인공지능(AI) 모델 연동 매뉴얼

본 문서는 현재 Pickle/수박 에이전트 환경에 종속되지 않고, **회기님이 직접 수동으로 운영**하거나 **향후 Claude 3.5 Sonnet, GPT-4o, Cursor, DeepSeek 등 다른 AI 모델을 활용**하여 사이트를 무한 확장할 수 있는 표준화 아키텍처를 안내합니다.

---

## 1. 아키텍처 원칙: 벤더 락인 제로 (Vendor Lock-in Zero)

- **순수 웹 표준 기반**: 특수 프레임워크나 폐쇄형 CMS에 의존하지 않고, Tailwind CDN과 표준 HTML5, 정적 자산으로만 구성되어 있습니다.
- **Git Push = 100% 무인 배포**: GitHub `main` 브랜치에 파일이 푸시되면 Cloudflare Workers Builds가 25초 내에 전 세계 300+ 엣지 데이터센터로 자동 정적 배포합니다.
- **로컬 독립 빌더 제공**: 파이썬 표준 라이브러리 기반의 `scripts/build_article_universal.py` 스크립트 하나로 마크다운 원고를 완전한 프로덕션 페이지로 즉시 컴파일합니다.

---

## 2. 다른 AI 모델(Claude, GPT, Cursor 등)을 활용한 신규 글 발행 절차

### 1단계: 원고 생성 프롬프트 실행
저장소 내 `templates/prompt_saas_benchmark.md`를 엽니다.
원하는 두 툴의 이름과 특징(예: Supabase vs Firebase, Linear vs Jira)을 넣고 **Claude 3.5 Sonnet** 또는 **ChatGPT(GPT-4o)**에 프롬프트를 전송합니다.

### 2단계: 마크다운 파일 저장
AI가 출력한 마크다운 원고를 `drafts/new-article.md` 형태로 저장합니다.

### 3단계: 범용 빌더 스크립트 1회 실행
터미널에서 아래 표준 CLI 명령어를 실행합니다:

```bash
python3 scripts/build_article_universal.py \
  --slug "supabase-vs-firebase" \
  --title "Supabase vs Firebase (2026): Open-Source Postgres vs Google Lock-In" \
  --subtitle "Cost, Row Level Security, and Real-World Migration Limits" \
  --category "Database & Backend" \
  --winner-slug "supabase" \
  --winner-url "https://supabase.com/?ref=yourid" \
  --runner-slug "firebase" \
  --runner-url "https://firebase.google.com" \
  --markdown "drafts/new-article.md"
```

위 명령어 하나로 다음 작업이 1초 만에 자동 완결됩니다:
1. `supabase-vs-firebase/index.html` 모던 다크 테마 페이지 자동 생성
2. 상단 와이어커터식 퀵 버딕트 & 헤드투헤드 비교 표 렌더링
3. Schema.org TechArticle 검색엔진 구조화 데이터 삽입
4. 메인 홈 `index.html`의 플래그십 카드 그리드에 자동 추가
5. `_redirects` 파일에 `/supabase` 엣지 307 단축 링크 자동 매핑
6. `sitemap.xml` 검색엔진 색인 URL 자동 갱신

### 4단계: 깃 푸시 (배포 끝)
```bash
git add .
git commit -m "Publish Supabase vs Firebase benchmark"
git push origin main
```
Cloudflare가 자동으로 감지해 25초 후 전 세계 라이브 서빙을 완료합니다.

---

## 3. 완전 수동 운영 방식 (사람이 직접 작성할 때)

AI 모델을 거치지 않고 회기님이 직접 글을 쓰실 때도 동일합니다:
1. 메모장이나 VS Code에서 비교 분석 글을 마크다운 문법으로 작성합니다.
2. 위 `build_article_universal.py` 명령어에 파일 경로만 지정하여 실행합니다.
3. Git push를 누르면 끝납니다.

---

## 4. GitHub 웹에서 노코드(No-Code)로 발행하는 대안 (GitHub Actions 워크플로)

로컬 터미널을 열기 번거로운 경우, GitHub 저장소의 `Actions` 탭에 자동화 워크플로를 탑재하여 다음과 같이 운영할 수도 있습니다:
1. GitHub 저장소의 `drafts/` 폴더에 마크다운 파일을 웹상에서 업로드.
2. GitHub Action이 자동으로 `build_article_universal.py`를 실행하고 자동 커밋/푸시.
3. 스마트폰 모바일 브라우저에서도 원클릭으로 새 글을 배포할 수 있습니다.
