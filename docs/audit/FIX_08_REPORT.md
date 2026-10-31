# [SBK-FIX-08] ATTRIBUTION & PARTNER SHARE REPORT

**Document ID:** SBK-DOC-FIX-008  
**Executed Date:** 2026-10-31 22:30 (Asia/Seoul)  
**Ticket Status:** COMPLETE & VERIFIED (PASS)  
**Target Vulnerabilities Fixed:** B05 (임의 ref 변조 및 부정 귀속), B15 (자가 귀속/워시 트레이딩), B16 (위젯 innerHTML XSS 취약점), B17 (타사 사이트 추적 쿠키 위험)

---

## 1. 결함 진단 및 위험 분석 (Root Cause & Risk)

### 1.1 기존 구현의 문제점 (B05, B15, B16, B17)
- **임의 ref 변조 (B05):** URL 쿼리 파라미터 `?ref=PARTNER_ID`를 누구나 임의로 조작하여 특정 파트너에게 부당하게 35% 리퍼럴 수수료를 몰아줄 수 있었음.
- **자가 귀속 수수료 탈루 (B15):** 시공을 직접 수행하는 파트너가 본인의 유입 링크를 통해 접수된 건에 대해 플랫폼 수수료 8% 중 35%를 다시 돌려받는 자가 귀속(Self-Referral)을 방어하지 못함.
- **위젯 문자열 삽입 취약점 (B16):** `slotbridge/widget.js`가 파트너 상호명과 권역명을 `container.innerHTML` 문자열로 결합하여 XSS 잠재 위험 존재.
- **불필요한 추적 위험 (B17):** 타사 웹사이트에 임베드되는 위젯이 쿠키나 로컬 스토리지를 사용할 경우 개인정보보호법상 불필요한 추적 논란 발생 위험.

---

## 2. 핵심 수정 및 기술 구현 (Key Implementations)

### 2.1 `src/worker.js`: 서버 서명 발급 API (`/attribution/token`)
- `GET /api/v1/slotbridge/attribution/token?partner_id=...` 엔드포인트를 신설하여 D1 `partners` 테이블에서 `APPROVED`/`VERIFIED` 상태의 활성 파트너만 서명 토큰을 발급받을 수 있도록 통제.
- Web Crypto HMAC-SHA256 기반 서명 및 만료일자(`expires_at`)를 포함한 불변 토큰 생성.
- 발급 시 `campaign_attributions` 테이블에 클릭수를 불변 기록.

### 2.2 `slotbridge/widget.js`: 100% 안전한 DOM API 렌더링
- `innerHTML`을 전면 영구 제거하고 `document.createElement()`, `element.textContent`, DOM 스타일 프로퍼티 설정만을 사용해 악의적 문자열 삽입 원천 차단.
- 백그라운드 비동기 `fetch()`로 서버 서명 토큰을 수신하여 링크에 `&token=...`을 자동 결속.
- 쿠키(`document.cookie`) 및 로컬 스토리지(`localStorage`) 일체 미사용(Zero 3rd-party Cookies).

### 2.3 `src/attribution.py`: 자가 귀속 원천 차단 및 Fail-Closed
- `validate_attribution_for_match`: 유입 파트너(`attribution_partner_id`)와 시공 파트너(`fulfillment_partner_id`)가 동일한 경우 즉시 리퍼럴 귀속을 취소(`None`)하고 `audit_log`에 `SELF_REFERRAL_BLOCKED` 감사 로그 불변 적재.
- `get_attribution_secret`: 비밀키 누락 시 즉시 `AttributionError`를 발생시키는 Fail-Closed 원칙 강화.

### 2.4 `tests/test_attribution_and_partner_share.py`: 8대 검증 테스트 스위트 신설
1. `test_01_tampered_token_rejected`: 위조 파트너 ID 및 서명 변조 토큰 거부 실측.
2. `test_02_expired_token_rejected`: 만료 토큰 거부 실측.
3. `test_03_raw_ref_without_token_sanitized_in_strict_mode`: 서명 없는 `?ref=` 직접 조작 시 귀속 취소(`None`) 소독 실측.
4. `test_04_valid_signed_token_grants_approved_attribution`: 정상 서명 토큰의 승인 파트너 귀속 검증.
5. `test_05_self_referral_wash_trading_blocked`: 자가 귀속 시 리퍼럴 취소 및 감사 로그 적재 실측.
6. `test_06_unapproved_partner_attribution_rejected`: 미승인 파트너 귀속 거부 실측.
7. `test_07_fail_closed_missing_secret`: 비밀키 누락 시 예외 발생 실측.
8. `test_08_widget_js_safe_dom_and_zero_cookies`: `widget.js` 코드 내 `innerHTML`, `document.cookie` 부재 및 순수 DOM API 사용 정적 감사 통과.

---

## 3. 테스트 및 회귀 검증 결과

- **실행 명령:** `python3 -m unittest discover -s tests -p "test_*.py"`
- **테스트 결과:** **Ran 134 tests in 1.392s — OK (전원 무결성 통과)**
- **드라이런 검증:** 50건 합성 트래픽 차변 475,200원 == 대변 475,200원 (Balanced: True)
- **회귀 발생 건수:** 0건 (기존 126개 테스트 전원 통과 유지 + 신규 8개 통과)
- **종합 판정:** **PASS (완전 통과)**
