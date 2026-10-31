-- ============================================================================
-- SlotBridge Korea — Migration 0001 Rollback Script
-- Dialect: SQLite / Cloudflare D1
-- ============================================================================

PRAGMA foreign_keys = OFF;

DROP TABLE IF EXISTS notification_outbox;
DROP TABLE IF EXISTS data_erasure_requests;
DROP TABLE IF EXISTS audit_log;
DROP TABLE IF EXISTS campaign_attributions;
DROP TABLE IF EXISTS payouts;
DROP TABLE IF EXISTS reconciliation_events;
DROP TABLE IF EXISTS ledger_events;
DROP TABLE IF EXISTS invoices;
DROP TABLE IF EXISTS revenue_rules;
DROP TABLE IF EXISTS fulfillment_evidence;
DROP TABLE IF EXISTS booking_outcomes;
DROP TABLE IF EXISTS partner_responses;
DROP TABLE IF EXISTS assignment_offers;
DROP TABLE IF EXISTS matches;
DROP TABLE IF EXISTS consent_events;
DROP TABLE IF EXISTS inquiries;
DROP TABLE IF EXISTS capacity_slots;
DROP TABLE IF EXISTS service_areas;
DROP TABLE IF EXISTS partner_members;
DROP TABLE IF EXISTS partners;
DROP TABLE IF EXISTS organizations;

PRAGMA foreign_keys = ON;
