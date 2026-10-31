-- ============================================================================
-- SlotBridge Korea — D1 Database Initial Schema (Migration 0001)
-- Version: 1.0.0
-- Dialect: SQLite / Cloudflare D1
-- Created: 2026-10-31
-- Entities: 21 Core Tables
-- ============================================================================

PRAGMA foreign_keys = ON;

-- 1. Organizations (플랫폼 운영 주체 및 파트너 테넌트)
CREATE TABLE IF NOT EXISTS organizations (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    business_number TEXT,
    representative_name TEXT,
    status TEXT NOT NULL DEFAULT 'ACTIVE', -- ACTIVE, SUSPENDED, RETIRED
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 2. Partners (제휴 청소업체 및 원 유입 리퍼럴 파트너)
CREATE TABLE IF NOT EXISTS partners (
    id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL REFERENCES organizations(id),
    name TEXT NOT NULL,
    partner_type TEXT NOT NULL, -- REFERRING, FULFILLMENT, HYBRID
    business_number TEXT NOT NULL,
    representative_name TEXT NOT NULL,
    contact_phone TEXT NOT NULL,
    settlement_bank TEXT,
    settlement_account TEXT,
    liability_insurance_status TEXT NOT NULL DEFAULT 'PENDING', -- PENDING, VERIFIED, EXPIRED, REJECTED
    verification_status TEXT NOT NULL DEFAULT 'PENDING_APPROVAL', -- PENDING_APPROVAL, APPROVED, SUSPENDED, BANNED
    rating_average REAL NOT NULL DEFAULT 5.0,
    total_completed_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 3. Partner Members (파트너사 소속 팀장 및 작업자 계정)
CREATE TABLE IF NOT EXISTS partner_members (
    id TEXT PRIMARY KEY,
    partner_id TEXT NOT NULL REFERENCES partners(id),
    name TEXT NOT NULL,
    phone TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'MANAGER', -- OWNER, MANAGER, CREW
    status TEXT NOT NULL DEFAULT 'ACTIVE',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 4. Service Areas (서비스 제공 가능 행정 권역)
CREATE TABLE IF NOT EXISTS service_areas (
    id TEXT PRIMARY KEY,
    partner_id TEXT NOT NULL REFERENCES partners(id),
    city TEXT NOT NULL DEFAULT '서울특별시',
    district TEXT NOT NULL, -- 관악구, 동작구 등
    neighborhood TEXT NOT NULL, -- 봉천동, 신림동, 사당동, 노량진동 등
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 5. Capacity Slots (24~72시간 잔여 가용 슬롯)
CREATE TABLE IF NOT EXISTS capacity_slots (
    id TEXT PRIMARY KEY,
    partner_id TEXT NOT NULL REFERENCES partners(id),
    slot_date TEXT NOT NULL, -- YYYY-MM-DD
    time_window TEXT NOT NULL, -- MORNING(09-13), AFTERNOON(13-18), ALL_DAY
    max_capacity INTEGER NOT NULL DEFAULT 1,
    allocated_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'AVAILABLE', -- AVAILABLE, OFFERED, RESERVED, COMPLETED, CANCELLED
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(partner_id, slot_date, time_window)
);

-- 6. Inquiries (고객 급행 매칭 접수 — PII 암호화 저장)
CREATE TABLE IF NOT EXISTS inquiries (
    id TEXT PRIMARY KEY,
    tracking_token TEXT NOT NULL UNIQUE,
    attribution_partner_id TEXT REFERENCES partners(id),
    campaign_id TEXT,
    requested_date TEXT NOT NULL, -- YYYY-MM-DD
    time_window TEXT NOT NULL,
    district TEXT NOT NULL,
    neighborhood TEXT NOT NULL,
    housing_type TEXT NOT NULL, -- STUDIO, TWO_ROOM, OFFICETEL, APARTMENT
    area_pyeong INTEGER NOT NULL,
    add_ons TEXT, -- JSON string: ["VERANDA", "MOLD"]
    customer_name_encrypted TEXT NOT NULL, -- AES-256-GCM ciphertext
    customer_phone_encrypted TEXT NOT NULL, -- AES-256-GCM ciphertext
    customer_phone_hash TEXT NOT NULL, -- SHA-256 hash for deduplication/rate-limit
    status TEXT NOT NULL DEFAULT 'PENDING_MATCH', -- PENDING_MATCH, OFFERED, MATCHED, EXPIRED, CANCELLED, FULFILLED
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 7. Consent Events (개인정보보호법 제17조 동의 불변 증적 로그)
CREATE TABLE IF NOT EXISTS consent_events (
    id TEXT PRIMARY KEY,
    inquiry_id TEXT NOT NULL REFERENCES inquiries(id),
    terms_version TEXT NOT NULL,
    agreed_privacy_collection INTEGER NOT NULL,
    agreed_third_party_share INTEGER NOT NULL,
    agreed_marketing INTEGER NOT NULL DEFAULT 0,
    client_ip_hash TEXT NOT NULL,
    user_agent TEXT NOT NULL,
    consented_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 8. Matches (인입 건과 수행 파트너 간의 최종 매칭)
CREATE TABLE IF NOT EXISTS matches (
    id TEXT PRIMARY KEY,
    inquiry_id TEXT NOT NULL UNIQUE REFERENCES inquiries(id),
    partner_id TEXT NOT NULL REFERENCES partners(id),
    slot_id TEXT NOT NULL REFERENCES capacity_slots(id),
    matched_at TEXT NOT NULL DEFAULT (datetime('now')),
    status TEXT NOT NULL DEFAULT 'CONFIRMED', -- CONFIRMED, CANCELLED, COMPLETED, DISPUTED
    estimated_amount INTEGER NOT NULL DEFAULT 250000,
    final_amount INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 9. Assignment Offers (15분 배타적 1:1 오퍼 매칭 큐)
CREATE TABLE IF NOT EXISTS assignment_offers (
    id TEXT PRIMARY KEY,
    inquiry_id TEXT NOT NULL REFERENCES inquiries(id),
    partner_id TEXT NOT NULL REFERENCES partners(id),
    slot_id TEXT NOT NULL REFERENCES capacity_slots(id),
    priority INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'PENDING', -- PENDING, ACCEPTED, REJECTED, EXPIRED, CANCELLED
    expires_at TEXT NOT NULL, -- 15 minutes TTL
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 10. Partner Responses (파트너 오퍼 수락/거절 응답 이력)
CREATE TABLE IF NOT EXISTS partner_responses (
    id TEXT PRIMARY KEY,
    offer_id TEXT NOT NULL REFERENCES assignment_offers(id),
    partner_id TEXT NOT NULL REFERENCES partners(id),
    response_type TEXT NOT NULL, -- ACCEPT, REJECT, TIMEOUT
    rejection_reason TEXT,
    responded_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 11. Booking Outcomes (예약 확정 및 상담 결과 기록)
CREATE TABLE IF NOT EXISTS booking_outcomes (
    id TEXT PRIMARY KEY,
    match_id TEXT NOT NULL UNIQUE REFERENCES matches(id),
    consulted_at TEXT,
    is_confirmed INTEGER NOT NULL DEFAULT 0,
    agreed_price INTEGER,
    customer_notes TEXT,
    partner_notes TEXT,
    cancellation_reason TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 12. Fulfillment Evidence (시공 완료 사진 증빙 및 검수 내역)
CREATE TABLE IF NOT EXISTS fulfillment_evidence (
    id TEXT PRIMARY KEY,
    match_id TEXT NOT NULL REFERENCES matches(id),
    photo_urls TEXT NOT NULL, -- JSON array string
    completion_timestamp TEXT NOT NULL,
    partner_submitted_amount INTEGER NOT NULL,
    customer_survey_rating INTEGER, -- 1-5 stars
    customer_confirmed INTEGER DEFAULT 0,
    verification_status TEXT NOT NULL DEFAULT 'PENDING', -- PENDING, VERIFIED, DISPUTED, REJECTED
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 13. Revenue Rules (불변 수수료 계산 규칙)
CREATE TABLE IF NOT EXISTS revenue_rules (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    platform_take_rate REAL NOT NULL DEFAULT 0.08, -- 8%
    referral_share_rate REAL NOT NULL DEFAULT 0.35, -- 35% of platform take
    min_platform_fee INTEGER NOT NULL DEFAULT 15000,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 14. Invoices (월간 B2B 성과 수수료 청구서)
CREATE TABLE IF NOT EXISTS invoices (
    id TEXT PRIMARY KEY,
    partner_id TEXT NOT NULL REFERENCES partners(id),
    billing_period TEXT NOT NULL, -- YYYY-MM
    total_service_volume INTEGER NOT NULL,
    total_platform_fee INTEGER NOT NULL,
    vat_amount INTEGER NOT NULL,
    total_invoice_amount INTEGER NOT NULL,
    tax_invoice_number TEXT,
    status TEXT NOT NULL DEFAULT 'DRAFT', -- DRAFT, ISSUED, PAID, OVERDUE, CANCELLED
    issued_at TEXT,
    due_date TEXT,
    paid_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 15. Ledger Events (복식부기 정산 원장 로그)
CREATE TABLE IF NOT EXISTS ledger_events (
    id TEXT PRIMARY KEY,
    match_id TEXT NOT NULL REFERENCES matches(id),
    event_type TEXT NOT NULL, -- REVENUE_ACCRUED, REFERRAL_ALLOCATED, INVOICE_GENERATED, PAYOUT_SETTLED
    amount INTEGER NOT NULL,
    debit_account TEXT NOT NULL,
    credit_account TEXT NOT NULL,
    description TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 16. Reconciliation Events (월간 대사 및 마감 감사 로그)
CREATE TABLE IF NOT EXISTS reconciliation_events (
    id TEXT PRIMARY KEY,
    billing_period TEXT NOT NULL,
    reconciled_by TEXT NOT NULL,
    total_matched_count INTEGER NOT NULL,
    total_fulfilled_count INTEGER NOT NULL,
    total_revenue_expected INTEGER NOT NULL,
    total_revenue_collected INTEGER NOT NULL,
    discrepancy_amount INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'BALANCED', -- BALANCED, DISCREPANCY, RESOLVED
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 17. Payouts (원 유입업체 리퍼럴 수수료 송금 지급부)
CREATE TABLE IF NOT EXISTS payouts (
    id TEXT PRIMARY KEY,
    referring_partner_id TEXT NOT NULL REFERENCES partners(id),
    billing_period TEXT NOT NULL,
    payout_amount INTEGER NOT NULL,
    vat_amount INTEGER NOT NULL,
    bank_name TEXT NOT NULL,
    account_number TEXT NOT NULL,
    tax_invoice_received INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'PENDING', -- PENDING, APPROVED, TRANSFERRED, FAILED
    transferred_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 18. Campaign Attributions (리퍼럴 캠페인 및 유입 성과 추적)
CREATE TABLE IF NOT EXISTS campaign_attributions (
    id TEXT PRIMARY KEY,
    partner_id TEXT NOT NULL REFERENCES partners(id),
    campaign_name TEXT NOT NULL,
    utm_source TEXT,
    utm_medium TEXT,
    clicks_count INTEGER NOT NULL DEFAULT 0,
    conversions_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 19. Audit Log (불변 관리자 및 보안 감사 로그)
CREATE TABLE IF NOT EXISTS audit_log (
    id TEXT PRIMARY KEY,
    actor_id TEXT NOT NULL, -- SYSTEM, ADMIN, PARTNER_ID
    action TEXT NOT NULL, -- PII_DECRYPT, OFFER_DISPATCH, STATUS_OVERRIDE, ERASURE
    resource_type TEXT NOT NULL,
    resource_id TEXT NOT NULL,
    ip_address TEXT,
    details TEXT, -- JSON string
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 20. Data Erasure Requests (개인정보 파기 및 잊힐 권리 이행 증적)
CREATE TABLE IF NOT EXISTS data_erasure_requests (
    id TEXT PRIMARY KEY,
    inquiry_id TEXT NOT NULL,
    requested_by TEXT NOT NULL, -- CUSTOMER_TOKEN, SYSTEM_30D_RETENTION, ADMIN
    status TEXT NOT NULL DEFAULT 'COMPLETED', -- PENDING, COMPLETED, FAILED
    erased_fields TEXT NOT NULL, -- "customer_name,customer_phone"
    erased_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 21. Notification Outbox (비동기 알림 아웃박스 큐)
CREATE TABLE IF NOT EXISTS notification_outbox (
    id TEXT PRIMARY KEY,
    recipient_type TEXT NOT NULL, -- CUSTOMER, PARTNER, ADMIN
    recipient_destination TEXT NOT NULL, -- URL, PHONE_MASKED, EMAIL
    notification_type TEXT NOT NULL, -- OFFER_ALERT, MATCH_CONFIRMED, SURVEY_LINK, RETENTION_EXPIRY
    payload TEXT NOT NULL, -- JSON string
    status TEXT NOT NULL DEFAULT 'PENDING', -- PENDING, SENT, FAILED, MOCKED
    sent_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ============================================================================
-- Indexes for Performance & Security
-- ============================================================================
CREATE INDEX IF NOT EXISTS idx_inquiries_token ON inquiries(tracking_token);
CREATE INDEX IF NOT EXISTS idx_inquiries_phone_hash ON inquiries(customer_phone_hash);
CREATE INDEX IF NOT EXISTS idx_inquiries_status ON inquiries(status);
CREATE INDEX IF NOT EXISTS idx_capacity_slots_date_window ON capacity_slots(slot_date, time_window, status);
CREATE INDEX IF NOT EXISTS idx_assignment_offers_inquiry ON assignment_offers(inquiry_id, status);
CREATE INDEX IF NOT EXISTS idx_assignment_offers_partner ON assignment_offers(partner_id, status);
CREATE INDEX IF NOT EXISTS idx_matches_inquiry ON matches(inquiry_id);
CREATE INDEX IF NOT EXISTS idx_matches_partner ON matches(partner_id);
CREATE INDEX IF NOT EXISTS idx_invoices_partner_period ON invoices(partner_id, billing_period);
CREATE INDEX IF NOT EXISTS idx_ledger_match ON ledger_events(match_id);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_log(created_at);
