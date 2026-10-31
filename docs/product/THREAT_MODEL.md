# SlotBridge Korea — 위협 모델 분석 보고서 (THREAT_MODEL.md)

**문서 버전:** 1.0.0  
**작성일자:** 2026-10-31  
**분석 프레임워크:** Microsoft STRIDE 위협 모델링 기법  
**작성 주체:** Codex Security Engineer / Security Architecture Team  

---

## 1. 아키텍처 자산 및 신뢰 경계 (Trust Boundaries)

```
[인터넷 / 비신뢰 영역]
       │
       ▼ (TLS 1.3 / Cloudflare WAF & Turnstile CAPTCHA)
[신뢰 경계 1: 엣지 Worker API 레이어]
       │
       ▼ (내부 바인딩 / AES-256-GCM 암호화)
[신뢰 경계 2: 영속 저장소 Cloudflare D1 (암호화된 PII / 불변 감사로그)]
```

---

## 2. STRIDE 위협 분석 및 보안 통제 매트릭스

| 위협 분류 (STRIDE) | 식별된 보안 위협 시나리오 | 위험도 | 시스템 구현 통제 대책 (Mitigations) |
|---|---|:---:|---|
| **Spoofing (신원 도용)** | 1. 악의적 봇이 타인의 휴대전화번호로 허위 급행 청소를 대량 접수하여 파트너 업무 방해<br>2. 미승인 공격자가 승인된 파트너 ID를 사칭하여 매칭 오퍼 수락 | **HIGH** | • Cloudflare 무료 Turnstile CAPTCHA 적용 (봇 원천 차단)<br>• 국내 휴대전화번호 포맷(`010-XXXX-XXXX`) 정규식 서버 검증<br>• IP당 분당 3회 인입 Rate Limit<br>• 파트너 API 호출 시 Bearer JWT 및 HMAC 서명 검증 |
| **Tampering (데이터 변조)** | 1. 파트너가 클라이언트 요청을 변조하여 플랫폼 수수료율(8%)을 0%로 조작<br>2. 고객 개인정보 제공 동의 기록 위·변조 | **CRITICAL** | • 수수료율(8%) 및 리퍼럴 비율(35%)은 클라이언트 제출 불허, **서버 코드 및 DB 불변 상수로 강제**<br>• `consent_events` 테이블은 `INSERT ONLY`(수정/삭제 권한 박탈) 격리 |
| **Repudiation (부인 방지)** | 1. 파트너가 정상 시공 후 대금을 수취했으나 "고객이 취소했다"며 수수료 납부 회피<br>2. 고객이 개인정보 제3자 제공 동의 사실을 부인 | **HIGH** | • 시공 완료 시 현장 사진 2장 업로드 필수<br>• 취소 보고 건에 대해 고객에게 0원 웹 만족도 교차 검증 설문 자동 발송<br>• 동의 시점의 IP 해시, 약관 버전, 타임스탬프 불변 감사로그 보관 |
| **Information Disclosure (정보 유출)** | 1. 데이터베이스 탈취 시 고객 성명·연락처 대량 노출<br>2. 콘솔 로그 및 깃허브 코드에 평문 PII 및 암호화 키 유출<br>3. 매칭 안 된 파트너가 타 고객의 인입 정보 열람 | **CRITICAL** | • D1 데이터베이스 내 PII 필드 레벨 `AES-256-GCM` 암호화 저장<br>• Worker Secret으로 암호화 키 격리, 소스코드 저장 절대 금지<br>• 콘솔 및 감사로그 출력 시 전화번호(`010-****-1234`) 100% 마스킹<br>• 1:1 배타적 오퍼 매칭: 승인된 1개 파트너만 1회성 복호화 권한 부여 |
| **Denial of Service (서비스 거부)** | 악의적 대량 트래픽으로 Cloudflare D1 무료 할당량(일 10만 회 쓰기)을 고갈시켜 서비스 장애 유발 | **MEDIUM** | • Cloudflare WAF 레벨의 악성 IP 자동 차단<br>• 엔드포인트별 인메모리 엣지 Rate Limiting 적용<br>• 무료 한도 80% 도달 시 비필수 로깅 차단 및 Fail-Closed 회로 차단기(Circuit Breaker) 가동 |
| **Elevation of Privilege (권한 상승)** | 일반 파트너가 관리자 정산 엔드포인트(`/api/admin/*`)에 무단 접근하여 전체 거래 원장 열람 | **HIGH** | • 관리자 경로는 Cloudflare Access(Zero Trust) 2단계 인증 필수화<br>• API 레벨에서 별도의 `X-Admin-Secret` 토큰 이중 검증 |

---

## 3. 침해사고 비상 킬스위치 (Emergency Kill-Switch)

보안 이상 징후(비정상 인입 급증, 키 노출 의심 등) 발생 시, 관리자는 Cloudflare 환경변수 한 줄 변경으로 시스템을 즉시 비상 격리 상태로 전환할 수 있다:

```bash
# 긴급 킬스위치 가동: 신규 인입 및 PII 복호화 전면 차단
wrangler secret put SYSTEM_KILL_SWITCH <<< "TRUE"
```
- `SYSTEM_KILL_SWITCH = "TRUE"` 설정 시 모든 인입 API는 HTTP 503(Service Unavailable)을 반환하며, 기존 데이터베이스에 대한 쓰기 및 PII 복호화 함수 실행이 즉시 동결(Fail-Safe)된다.
