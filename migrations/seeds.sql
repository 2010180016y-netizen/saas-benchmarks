-- ============================================================================
-- SlotBridge Korea — Development Seed Data
-- Dialect: SQLite / Cloudflare D1
-- ============================================================================

-- 1. Organization
INSERT OR IGNORE INTO organizations (id, name, business_number, representative_name, status)
VALUES ('org_sbk_hq', '슬롯브릿지코리아 주식회사(가칭)', '101-88-00000', '김회기', 'ACTIVE');

-- 2. Revenue Rules (불변 8% 플랫폼 수수료, 35% 리퍼럴 배분)
INSERT OR IGNORE INTO revenue_rules (id, name, platform_take_rate, referral_share_rate, min_platform_fee, is_active)
VALUES ('rev_rule_standard_2026', '2026 표준 수수료 규칙 (8% / 35%)', 0.08, 0.35, 15000, 1);

-- 3. Partners (관악/동작 모의 파트너 3사)
INSERT OR IGNORE INTO partners (id, organization_id, name, partner_type, business_number, representative_name, contact_phone, settlement_bank, settlement_account, liability_insurance_status, verification_status, rating_average, total_completed_count)
VALUES 
('partner_gw_cleanhome', 'org_sbk_hq', '클린홈 관악본점', 'HYBRID', '214-88-12345', '박클린', '010-0000-0001', '국민은행', '123-456-789012', 'VERIFIED', 'APPROVED', 4.9, 42),
('partner_dj_topclean', 'org_sbk_hq', '탑클린 동작점', 'FULFILLMENT', '215-88-67890', '최반장', '010-0000-0002', '신한은행', '987-654-321098', 'VERIFIED', 'APPROVED', 4.8, 28),
('partner_sl_master', 'org_sbk_hq', '신림 청소마스터', 'REFERRING', '216-88-11223', '이대표', '010-0000-0003', '우리은행', '555-111-222333', 'PENDING', 'APPROVED', 5.0, 15);

-- 4. Service Areas
INSERT OR IGNORE INTO service_areas (id, partner_id, city, district, neighborhood, is_active)
VALUES
('sa_01', 'partner_gw_cleanhome', '서울특별시', '관악구', '봉천동', 1),
('sa_02', 'partner_gw_cleanhome', '서울특별시', '관악구', '신림동', 1),
('sa_03', 'partner_dj_topclean', '서울특별시', '동작구', '사당동', 1),
('sa_04', 'partner_dj_topclean', '서울특별시', '동작구', '노량진동', 1);

-- 5. Capacity Slots (내일 및 모레 잔여 슬롯 모의 데이터)
INSERT OR IGNORE INTO capacity_slots (id, partner_id, slot_date, time_window, max_capacity, allocated_count, status)
VALUES
('slot_gw_tomorrow_am', 'partner_gw_cleanhome', date('now', '+1 day'), 'MORNING', 1, 0, 'AVAILABLE'),
('slot_dj_tomorrow_am', 'partner_dj_topclean', date('now', '+1 day'), 'MORNING', 1, 0, 'AVAILABLE'),
('slot_dj_tomorrow_pm', 'partner_dj_topclean', date('now', '+1 day'), 'AFTERNOON', 1, 0, 'AVAILABLE'),
('slot_gw_dayafter_am', 'partner_gw_cleanhome', date('now', '+2 day'), 'MORNING', 1, 0, 'AVAILABLE');

-- 6. Campaign Attributions
INSERT OR IGNORE INTO campaign_attributions (id, partner_id, campaign_name, utm_source, utm_medium, clicks_count, conversions_count)
VALUES
('camp_cleanhome_widget', 'partner_gw_cleanhome', 'cleanhome_overflow_banner', 'partner_widget', 'cpc', 124, 18),
('camp_master_sms', 'partner_sl_master', 'sms_overflow_link', 'direct_sms', 'referral', 45, 9);
