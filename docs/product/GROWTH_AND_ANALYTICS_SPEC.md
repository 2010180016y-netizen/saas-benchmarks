# SlotBridge Korea — 무자본 그로스 & 퍼널 분석 규격서 (GROWTH_AND_ANALYTICS_SPEC.md)

**문서 버전:** 1.0.0  
**작성일자:** 2026-10-31  
**관련 법령:** 정보통신망법 제50조(영리목적의 광고성 정보 전송 제한), 개인정보보호법 제17조  
**적용 대상:** 그로스 엔진(`analytics.py`), SEO 랜딩(`seo/`), 그로스 대시보드(`growth-dashboard.html`)  
**작성 주체:** Codex Growth Engineering & Analytics Team  

---

## 1. 무자본 유통 전략 및 CAC 0원 원칙

1. **원 유입업체 화이트라벨 위젯 (70%):** 일정 마감된 기존 청소업체가 자사 마감 안내 화면에 5KB 위젯을 탑재하여 버려지던 오버플로우 문의를 회수.
2. **실측 공급 기반 롱테일 SEO (20%):** 서울 관악구 및 동작구의 실제 등록된 가용 슬롯 데이터에 기반한 정밀 캐노니컬 랜딩 페이지만 발행하며, 무차별 도어웨이 자동 생성(Doorway Page Spam)을 전면 금지.
3. **지역 부동산·인테리어 B2B 제휴 (10%):** 이사 잔금일 직후 급행 청소가 필요한 입주민에게 관내 공인 슬롯 직접 안내.
4. **CAC = 0원 확정:** 유료 키워드 광고(CPC) 및 인위적 마케팅비를 집행하지 않으며, 실측 순기여이익(Net Profit) 건당 13,000원(65% 마진)을 100% 보존.

---

## 2. 11단계 실측 전환 퍼널 정의

| 단계 # | 이벤트명 | 데이터 성격 및 개인정보 처리 | 비고 |
|---|---|---|---|
| **1** | `page_view` | 단방향 해시 세션 식별 (PII 완전 배제) | 방문 트래픽 |
| **2** | `form_start` | 견적 폼 인풋 최초 인터랙션 감지 | 인입 시도 |
| **3** | `request_submitted` | 필수 개인정보 동의 및 폼 제출 완료 | 잠재 리드 |
| **4** | `qualified` | 24~72시간 범위 및 시군구 가용 슬롯 유효성 검증 | 유효 인입 |
| **5** | `partner_offer` | 결정적 규칙 1순위 파트너에게 15분 독점 오퍼 발송 | 1:1 매칭 제안 |
| **6** | `accepted` | 파트너가 15분 이내 조건 수락 (슬롯 원자적 락) | 오퍼 수락 |
| **7** | `booked` | 고객-파트너 간 금액 최종 합의 및 매칭 확정 | 계약 성립 |
| **8** | `completed` | 현장 시공 완료 및 전·후 사진 2장 업로드 | 작업 완결 |
| **9** | `verified` | 시공 검수 완료 (복식부기 원장 미수금 분개) | 정산 확정 |
| **10** | `invoice_issued` | 익월 5일 B2B 정식 청구서 발행 | 세금계산서 청구 |
| **11** | `payment_confirmed` | 파트너 수수료 입금 대사 완료 | 현금 회수 |

---

## 3. SEO 및 검색엔진 색인 제어 정책

1. **도어웨이 스팸 원천 금지:** 실제로 가용 파트너가 없는 수천 개 행정동/날짜를 스크립트로 자동 조합하여 색인을 유도하는 행위를 엄격히 금지함.
2. **캐노니컬 및 Schema.org 구조화 데이터:**
   - 관악구: `https://saas-benchmarks.pages.dev/slotbridge/seo/gwanak.html` (LocalBusiness / CleaningService JSON-LD)
   - 동작구: `https://saas-benchmarks.pages.dev/slotbridge/seo/dongjak.html` (LocalBusiness / CleaningService JSON-LD)
3. **제휴 관계 및 중개자 고지:** 모든 SEO 랜딩 하단에 전자상거래법상 통신판매중개자 지위 및 실제 용역 제공 주체에 대한 고지문을 명문화함.

---

## 4. A/B 테스트 프레임워크 및 가드레일

- **결정적 사용자 분할:** MD5(`experiment_id` + `user_hash`) 기반 50:50 분할 (`variant_a` vs `variant_b`).
- **종료 조건 (Stop Condition):** 최소 노출 표본 수(`min_sample`, 기본 1,000건) 충족 전까지는 조기 승자 선언을 금지하여 가짜 양성(False Positive) 방지.
- **광고성 통지 금지 가드레일:** 정보통신망법 제50조에 따라 `agreed_marketing == 1`을 명시적으로 체크하지 않은 고객에게는 일체의 사후 홍보성 SMS/이메일/알림 발송을 시스템 수준(`can_send_promotional_message`)에서 차단(Fail-Closed)함.
