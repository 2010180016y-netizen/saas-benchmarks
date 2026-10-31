# SlotBridge Korea — 유통 위젯 및 오버플로우 네트워크 규격서 (DISTRIBUTION_AND_WIDGET_SPEC.md)

**문서 버전:** 1.0.0  
**작성일자:** 2026-10-31  
**적용 대상:** 원 유입 파트너 임베드 위젯(`widget.js`), 데모(`widget-demo.html`), 대시보드(`partner-dashboard.html`), 귀속 엔진(`attribution.py`)  
**작성 주체:** Codex Growth & Security Architecture Team  

---

## 1. 배포 위젯 핵심 목표 및 무자본 유통 구조

1. **CAC 0원 획득 채널 (Zero CAC Acquisition):** 기존 청소업체가 일정 마감으로 단순 거절하던 오버플로우 문의의 30~50%를 위젯을 통해 고객의 자발적 동의 하에 흡수.
2. **파트너 윈-윈 보상 체계:** 원 유입 파트너에게 성과 수수료(8%) 중 **35%(건당 7,000원, VAT 별도)**를 배분하여, 거절 고객을 통한 추가 무자본 수익원 제공.
3. **고객 직접 동의 원칙 (No Unauthorized Lead Dumps):** 원 청소업체가 자사 DB의 고객 정보를 엑셀로 넘기거나 임의 업로드하는 행위를 엄격히 금지하며, 반드시 고객이 위젯 링크를 통해 직접 접속하여 동의 후 신청하도록 강제.

---

## 2. 위젯 보안 및 임베드 아키텍처

### 2.1 보안 및 프라이버시 설계 (CSP & Anti-Clickjacking)
- **외부 종속성 0개:** 번들 크기 5KB 미만의 순수 바닐라 JavaScript로 호스트 사이트 성능 저하 제로.
- **쿠키 없는 귀속 (Cookie-Free Attribution):** 3자 쿠키(Third-party Cookies)를 일체 사용하지 않으며, 암호화된 HMAC 서명 토큰(`attribution_token`)을 URL 파라미터로 결속하여 브라우저의 쿠키 차단(ITP 등) 영향을 받지 않음.
- **클릭재킹 방지:** 임베드 위젯은 호스트 DOM 내에 표준 시맨틱 엘리먼트로 주입되며, 플랫폼 백엔드는 `X-Frame-Options: SAMEORIGIN` 및 `Content-Security-Policy: frame-ancestors 'self'`를 강제하여 악의적 투명 iframe 납치를 차단.
- **브랜드 혼동 방지:** 위젯 상단에 파트너사 상호명을 명시함과 동시에, 하단에 "SlotBridge Korea 공인 연합 청소 네트워크를 통해 연결됩니다"라는 중개 지위를 100% 투명하게 고지.

---

## 3. 부정 거래 방지 및 기여도 우선순위 규칙 (Anti-Fraud & Attribution Rules)

| 부정 시나리오 | 탐지 및 차단 메커니즘 | 위반 시 조치 |
|---|---|---|
| **자기 거래 (Self-Referral / Wash Trading)** | 파트너 A가 자사 코드로 유입시킨 고객 건을 파트너 A 본인이 작업 수행자로 수락하여 8% 플랫폼 수수료를 35% 할인받으려 시도 | 수행 파트너와 유입 파트너 ID가 일치할 경우 **리퍼럴 배분을 0원으로 자동 무효화** (`validate_attribution_for_match`) |
| **토큰 위·변조 (Tampering)** | URL의 `partner_id`를 임의로 조작하거나 유효기간을 조작 | SHA-256 HMAC 16자리 서명 불일치 시 **즉시 기여 무효화 및 일반 유기적 유입(Organic)으로 격리** |
| **쿠키 스터핑 (Cookie Stuffing / Click Farm)** | 봇을 동원해 단시간에 수천 회 위젯 클릭을 유발하여 향후 모든 유입을 독점하려 시도 | IP당 분당 10회 Rate Limit 및 **서명 토큰 TTL 7일 강제 만료** |
| **원천 기여 우선순위 (Attribution Priority)** | 동일 고객이 여러 제휴 링크를 클릭한 경우 | **최초 유효 신청 시점의 토큰 (First-Touch Valid Intake)**을 불변 결속 (`inquiries.attribution_partner_id`) |

---

## 4. 무비용 알림 아웃박스 정책 ($0 Notification Policy)

- 유료 문자(SMS/알림톡) 건당 15~40원 지출을 원천 배제하기 위해, 모든 시스템 알림 이벤트(오퍼 도착, 매칭 확정, 만족도 설문)는 `notification_outbox` 테이블에 `status = 'MOCKED'` 상태로 기록된다.
- 고객과 청소업체는 1회성 웹 링크(`/slotbridge/status.html`) 및 직접 유선 통화를 통해 상태를 확인하므로, 월간 외부 통신비 지출은 **$0.00**을 유지한다.
