# SlotBridge Korea — 실행 설명서 및 보안 운영 런북 (RUNBOOK.md)

**문서 버전:** 1.0.0  
**작성일자:** 2026-10-31  
**대상 환경:** 로컬 개발(Local SQLite Emulation) 및 프로덕션(Cloudflare D1 & Workers)  
**작성 주체:** Codex Staff Engineer / DevOps Architecture Team  

---

## 1. 프로젝트 구조 및 핵심 컴포넌트

```text
saas-benchmarks/ (feat/slotbridge-kr-mvp)
├── migrations/
│   ├── 0001_initial_schema.sql    # 21개 핵심 엔티티 DDL 및 인덱스
│   ├── 0001_rollback.sql          # 롤백 스크립트 (DROP TABLE 21개)
│   └── seeds.sql                  # 모의 파트너 3사, 슬롯, 수수료 규칙 개발 시드
├── src/
│   ├── crypto.py                  # AES-256-GCM 필드 암호화 및 PII 마스킹
│   ├── db.py                      # D1/SQLite 연결, 트랜잭션, 80% 쿼터 회로 차단기
│   └── engine.py                  # 1:1 배타적 매칭, 동시성 락, 복식부기 원장, 1-Click 파기
├── tests/
│   └── test_sbk_engine.py         # 10대 인수 조건 자동화 테스트 스위트
├── .env.example                   # 비밀값 없는 환경변수 템플릿
└── docs/product/                  # PRD, 아키텍처, 위협모델, 런북 문서군
```

---

## 2. 로컬 테스트 및 검증 실행 명령

### 2.1 자동화 테스트 스위트 실행
```bash
# 전체 테스트 실행 (10개 테스트 케이스: 마이그레이션, 롤백, PII 암호화, 동시성, 회로차단기)
python3 -m unittest discover -s tests -p "test_*.py" -v
```
**테스트 실행 결과 (실측 완료):**
- `test_01_all_21_tables_exist`: 21개 테이블 완전 생성 검증 (OK)
- `test_02_migration_rollback_and_replay`: 롤백 및 재생성 무결성 검증 (OK)
- `test_03_seed_data_integrity`: 8% 수수료 / 35% 리퍼럴 시드 정합성 검증 (OK)
- `test_04_pii_encryption_zero_plaintext_in_db`: DB 내 평문 PII 0건 및 복호화 검증 (OK)
- `test_05_pii_log_masking`: 010-****-5678 로그 마스킹 검증 (OK)
- `test_06_mandatory_consent_rejection`: 법정 필수 동의 누락 시 폼 제출 거부 검증 (OK)
- `test_07_1to1_exclusive_offer_and_atomic_acceptance`: 1:1 오퍼 및 이중 수락 방지 락 검증 (OK)
- `test_08_fulfillment_and_immutable_ledger_accounting`: 8% 매출 및 35% 리퍼럴 원장 자동 기재 검증 (OK)
- `test_09_1click_revocation_and_tombstone`: 1-Click 즉시 톰스톤 파기 검증 (OK)
- `test_10_circuit_breaker_fail_closed`: 무료 한도 80% 도달 시 Fail-Closed 차단 검증 (OK)
- **최종 결과:** `Ran 10 tests in 0.058s - OK`

---

## 3. Cloudflare 프로덕션 배포 절차 (D1 마이그레이션)

Cloudflare D1 연동 시 아래 표준 명령어를 통해 프로덕션 데이터베이스에 마이그레이션을 적용합니다:

```bash
# 1. D1 데이터베이스 생성 (최초 1회)
wrangler d1 create slotbridge-kr-db

# 2. 암호화 시크릿 주입 (32바이트 안전한 난수 키)
wrangler secret put SBK_ENCRYPTION_KEY

# 3. 로컬 D1 에뮬레이션 마이그레이션 적용
wrangler d1 execute slotbridge-kr-db --local --file=./migrations/0001_initial_schema.sql

# 4. 프로덕션 D1 원격 마이그레이션 적용 (승인 게이트 후)
wrangler d1 execute slotbridge-kr-db --remote --file=./migrations/0001_initial_schema.sql

# 5. 긴급 롤백 실행 (필요 시)
wrangler d1 execute slotbridge-kr-db --remote --file=./migrations/0001_rollback.sql
```

---

## 4. 백업 및 재해 복구 (Disaster Recovery)

1. **자동 백업:** Cloudflare D1은 타임 트래블(Time Travel) 기능을 통해 지난 30일간의 임의 시점으로 1분 단위 즉시 롤백 가능 (`wrangler d1 time-travel restore`).
2. **콜드 백업:** 월말 정산 직후 SQLite 원시 덤프 파일을 생성하여 Cloudflare R2 프라이빗 버킷에 보관:
   ```bash
   wrangler d1 export slotbridge-kr-db --output=./backups/backup_$(date +%Y%m%d).sql
   ```
3. **데이터 복원 검증:** 백업 파일은 암호화된 PII 필드만 포함하므로 유출 시에도 안전하며, 복원 시 `0001_initial_schema.sql`과 동일한 스키마로 즉각 복구 가능.
