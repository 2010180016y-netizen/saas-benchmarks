# SaaS Matrix 제휴 파트너 정산 및 수익 수령 완벽 가이드

본 문서는 SaaS Matrix(`saas-benchmarks.hoiki0808.workers.dev`) 사이트에 게재된 제휴 링크를 통해 방문자가 결제했을 때, 실제 회기님의 계좌로 달러가 입금되는 메커니즘과 설정 방법을 상세히 설명합니다.

---

## 1. 수익 정산 흐름 (Money Flow)

```
[방문자 독자]
       ↓ (1) 기사 내 추천 버튼 클릭 (/make, /cursor 등)
[Cloudflare Edge 307 단축 링크]
       ↓ (2) 회기님의 고유 레퍼럴 파라미터 결속 + 30~90일 추적 쿠키 심기
[SaaS 공식 결제 페이지]
       ↓ (3) 독자가 가입 후 유료 플랜 구독 결제 (예: 월 $20 결제)
[SaaS 파트너 제휴 플랫폼 (Rewardful / PartnerStack 등)]
       ↓ (4) 수수료(결제액의 20~30% = 매달 $4~$6)가 회기님 파트너 계정에 자동 누적
[월말 정산 (Net-30 주기)]
       ↓ (5) 회기님의 외화통장 / PayPal / Wise로 달러($) 자동 송금
[회기님 수령 (원화 환전 또는 달러 보유)]
```

---

## 2. 제품별 주요 제휴 플랫폼 & 가입 경로

SaaS 회사들은 대부분 전문 제휴 플랫폼에 정산 관리를 위탁하여 운영합니다:

| 도구명 | 소속 제휴 플랫폼 | 커미션 조건 | 가입 링크 / 방법 |
| :--- | :--- | :--- | :--- |
| **Make.com** | Rewardful 기반 자체 포털 | **20% 평생 리커링** | `https://www.make.com/en/affiliate-program` 접속 후 이메일 가입 |
| **Cursor AI** | FirstPromoter / 자체 제휴 | **20% 12개월 또는 리커링** | `https://www.cursor.com` 하단 Affiliate/Partnership 또는 문의 신청 |
| **Sunsama** | Rewardful | **20% 12개월 리커링** | `https://www.sunsama.com/affiliates` 가입 |
| **beehiiv** | Rewardful | **최대 50% 12개월 리커링** | `https://www.beehiiv.com/partner-program` 가입 |
| **Notion / Webflow** | PartnerStack | **20~30% 리커링** | `https://partnerstack.com` 마켓플레이스에서 신청 |

---

## 3. 정산 수단 등록 (어떻게 돈을 받나?)

제휴 포털(Rewardful, PartnerStack, FirstPromoter 등) 가입 후 `Payout Settings`에서 1회만 등록해 두면 매월 정산일에 자동으로 송금됩니다.

### 추천 방식 A: PayPal (가장 간편, 즉시 원화 환전)
1. 제휴 포털의 `Payout Method`에서 **PayPal** 선택.
2. 회기님의 페이팔 계정 이메일 입력.
3. 매월 15~30일 사이에 페이팔로 달러 입금 -> 페이팔 앱에서 국내 연결 은행(하나/신한/국민 등)으로 1영업일 내 원화 출금.

### 추천 방식 B: Wise / Payoneer (외화 수수료 최저)
1. Wise(구 TransferWise)에서 미국 가상 계좌(Routing Number, Account Number) 발급.
2. 제휴 포털의 `Direct Deposit / ACH`에 미국 계좌번호 입력.
3. 미국 내 로컬 송금으로 처리되어 수수료 $0에 수령 -> Wise에서 국내 통장으로 최저 환전 수수료로 송금.

### 추천 방식 C: 국내 외화통장 직접 송금 (Stripe Express / SWIFT)
1. 국내 은행(신한, 하나, 국민, 우리 등)의 외화통장 개설.
2. 은행의 영문명, 영문 주소, SWIFT 코드, 외화 계좌번호 입력.
3. 달러 그대로 국내 외화통장에 꽂히며, 환율이 높을 때 직접 원화 환전 가능.

---

## 4. 사이트 적용 방법 (단 1줄 교체)

회기님이 발급받은 본인 고유 링크(예: `https://www.make.com/?fpr=kimhoegi`)를 사이트에 반영하는 것은 매우 간단합니다. 본문의 코드를 일일이 수정할 필요 없이, 저장소 루트의 `_redirects` 파일만 수정하면 됩니다:

1. `_redirects` 파일을 엽니다.
2. 기존 임시 링크를 회기님의 실링크로 변경합니다:
   ```text
   # 기존
   /make  https://www.make.com/en/register?pc=buykoreanow  307
   
   # 변경 (회기님의 실링크)
   /make  https://www.make.com/en/register?fpr=YOUR_ID  307
   ```
3. `git commit -am "Update Make affiliate link" && git push` 실행.
4. 25초 후 Cloudflare 글로벌 엣지 전체에 실시간 반영 완료.
