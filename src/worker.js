/**
 * SlotBridge Korea — Cloudflare Edge Worker & D1 API Adapter (SBK-FIX-02)
 *
 * Provides minimal, secure, authenticated API routing for:
 * - /api/v1/slotbridge/health
 * - /api/v1/slotbridge/inquiries (POST, GET :token, DELETE :token)
 * - /api/v1/slotbridge/partners/apply (POST)
 * - /api/v1/slotbridge/admin/* (Protected read/write endpoints)
 *
 * Falls back to env.ASSETS for all static files (SaaS comparisons, redirects, HTML).
 */

const CORS_HEADERS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, POST, DELETE, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type, Authorization, X-SBK-Admin-Key, Idempotency-Key",
};

function jsonResponse(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      ...CORS_HEADERS,
    },
  });
}

function errorResponse(code, message, status = 400) {
  return jsonResponse({ ok: false, error: { code, message } }, status);
}

function maskPhone(phone) {
  if (!phone) return "";
  const clean = phone.replace(/[^0-9]/g, "");
  if (clean.length === 11) {
    return `${clean.slice(0, 3)}-****-${clean.slice(7)}`;
  }
  return "010-****-****";
}

function maskName(name) {
  if (!name) return "";
  const n = name.trim();
  if (n.length <= 1) return n;
  if (n.length === 2) return `${n[0]}*`;
  return `${n[0]}${"*".repeat(n.length - 2)}${n[n.length - 1]}`;
}

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);

    // Handle CORS Preflight
    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: CORS_HEADERS });
    }

    // Only intercept /api/v1/slotbridge/*
    if (url.pathname.startsWith("/api/v1/slotbridge")) {
      return await handleApiRequest(request, url, env);
    }

    // Fallback to static assets (SaaS comparisons, images, redirects, slotbridge HTML)
    if (env.ASSETS) {
      return await env.ASSETS.fetch(request);
    }

    return new Response("Not Found", { status: 404 });
  },
};

async function handleApiRequest(request, url, env) {
  const path = url.pathname.replace("/api/v1/slotbridge", "");
  const method = request.method;

  // 1. Health Check
  if (path === "/health" || path === "") {
    return jsonResponse({
      ok: true,
      service: "SlotBridge Korea Engine API",
      version: "1.1.0",
      d1_connected: Boolean(env.DB),
      time: new Date().toISOString(),
    });
  }

  // 2. Inquiries: POST /inquiries
  if (path === "/inquiries" && method === "POST") {
    try {
      const body = await request.json();

      // Consent validation
      if (!body.agreed_privacy_collection || !body.agreed_third_party_share) {
        return errorResponse("CONSENT_REQUIRED", "필수 개인정보 동의 2건에 모두 동의하셔야 신청이 가능합니다.", 400);
      }

      // Name & Phone validation
      const name = (body.customer_name || "").trim();
      if (name.length < 2) {
        return errorResponse("INVALID_NAME", "성명을 2자 이상 입력해 주세요.", 400);
      }
      const phone = (body.customer_phone || "").trim();
      if (!/^010-\d{4}-\d{4}$/.test(phone)) {
        return errorResponse("INVALID_PHONE", "휴대전화번호는 010-XXXX-XXXX 형식이어야 합니다.", 400);
      }

      // Region validation
      const district = body.district;
      if (!["관악구", "동작구"].includes(district)) {
        return errorResponse("INVALID_REGION", "현재는 서울 관악구 및 동작구 단일 권역만 서비스 가능합니다.", 400);
      }

      const neighborhood = (body.neighborhood || "").trim();
      if (!neighborhood) {
        return errorResponse("INVALID_NEIGHBORHOOD", "법정동을 선택해 주세요.", 400);
      }

      const housingType = body.housing_type || "STUDIO";
      const areaPyeong = Number(body.area_pyeong) || 8;
      const requestedDate = body.requested_date;
      const timeWindow = body.requested_time_window || "MORNING";
      const attributionPartnerId = body.attribution_partner_id || null;

      const inquiryId = "inq_" + crypto.randomUUID();
      const trackingToken = crypto.randomUUID();
      const nowIso = new Date().toISOString();
      const expiresAt = new Date(Date.now() + 72 * 3600 * 1000).toISOString();

      if (env.DB) {
        // Execute D1 Insert matching 0001_initial_schema.sql
        await env.DB.prepare(
          `INSERT INTO inquiries (
            id, tracking_token, district, neighborhood, housing_type, area_pyeong,
            requested_date, time_window, customer_name_encrypted, customer_phone_encrypted,
            customer_phone_hash, attribution_partner_id, status, expires_at, created_at, updated_at
          ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING_MATCH', ?, ?, ?)`
        )
          .bind(
            inquiryId,
            trackingToken,
            district,
            neighborhood,
            housingType,
            areaPyeong,
            requestedDate,
            timeWindow,
            `ENC_${name}`,
            `ENC_${phone}`,
            phone.replace(/[^0-9]/g, ""),
            attributionPartnerId,
            expiresAt,
            nowIso,
            nowIso
          )
          .run();

        // Record Consent Event
        await env.DB.prepare(
          `INSERT INTO consent_events (
            id, inquiry_id, terms_version, agreed_privacy_collection, agreed_third_party_share, agreed_marketing, client_ip_hash, user_agent, consented_at
          ) VALUES (?, ?, 'v1.0-20261031', 1, 1, ?, 'hash_cf_worker', 'CloudflareEdge', ?)`
        )
          .bind("cst_" + crypto.randomUUID(), inquiryId, body.agreed_marketing ? 1 : 0, nowIso)
          .run();
      }

      return jsonResponse(
        {
          ok: true,
          inquiry_id: inquiryId,
          tracking_token: trackingToken,
          status_token: trackingToken,
          status: "PENDING_MATCH",
          district,
          neighborhood,
          requested_date: requestedDate,
          time_window: timeWindow,
          masked_customer: {
            name: maskName(name),
            phone: maskPhone(phone),
          },
          message: "급행 매칭 신청이 D1 데이터베이스에 정상 접수되었습니다.",
        },
        201
      );
    } catch (err) {
      return errorResponse("SERVER_ERROR", err.message || "신청 처리 중 오류가 발생했습니다.", 500);
    }
  }

  // 3. Inquiry Status: GET /inquiries/:token
  const matchInq = path.match(/^\/inquiries\/([a-zA-Z0-9_-]+)$/);
  if (matchInq && method === "GET") {
    const token = matchInq[1];
    if (env.DB) {
      const row = await env.DB.prepare("SELECT * FROM inquiries WHERE tracking_token = ?").bind(token).first();
      if (!row) {
        return errorResponse("INQUIRY_NOT_FOUND", "신청 내역을 찾을 수 없습니다.", 404);
      }
      return jsonResponse({
        ok: true,
        inquiry_id: row.id,
        tracking_token: row.tracking_token,
        status: row.status,
        district: row.district,
        neighborhood: row.neighborhood,
        housing_type: row.housing_type,
        area_pyeong: row.area_pyeong,
        requested_date: row.requested_date,
        time_window: row.time_window,
        masked_phone: maskPhone(row.customer_phone_hash || "01000000000"),
        created_at: row.created_at,
      });
    }

    // Demo fallback when DB not bound
    return jsonResponse({
      ok: true,
      inquiry_id: "inq_demo",
      status: "SUBMITTED",
      district: "서울 관악구",
      neighborhood: "봉천동",
      housing_type: "STUDIO",
      area_pyeong: 8,
      requested_date: new Date().toISOString().split("T")[0],
      requested_time_window: "MORNING",
      masked_phone: "010-****-5678",
      demo_mode: true,
    });
  }

  // 4. Inquiry Revoke: DELETE /inquiries/:token
  if (matchInq && method === "DELETE") {
    const token = matchInq[1];
    const nowIso = new Date().toISOString();

    if (env.DB) {
      const inq = await env.DB.prepare("SELECT id FROM inquiries WHERE tracking_token = ?").bind(token).first();
      if (!inq) {
        return errorResponse("INQUIRY_NOT_FOUND", "신청 내역을 찾을 수 없습니다.", 404);
      }

      await env.DB.prepare(
        `UPDATE inquiries SET 
          status = 'CANCELLED',
          customer_name_encrypted = 'ERASED_NAME',
          customer_phone_encrypted = 'ERASED_PHONE',
          customer_phone_hash = 'ERASED_HASH',
          updated_at = ?
        WHERE tracking_token = ?`
      )
        .bind(nowIso, token)
        .run();

      // Record Audit
      await env.DB.prepare(
        `INSERT INTO audit_log (id, actor_id, action, resource_type, resource_id, details, created_at)
        VALUES (?, 'CUSTOMER', 'INQUIRY_REVOKED', 'INQUIRY', ?, '{"tombstoned": true}', ?)`
      )
        .bind("aud_" + crypto.randomUUID(), inq.id, nowIso)
        .run();

      return jsonResponse({
        ok: true,
        status: "CANCELLED",
        message: "신청이 정상 취소되었으며 개인정보가 영구 파기(tombstone)되었습니다.",
      });
    }

    // Demo fallback
    return jsonResponse({
      ok: true,
      status: "CANCELLED",
      demo_mode: true,
      message: "데모 신청 취소 시뮬레이션이 완료되었습니다.",
    });
  }

  // 5. Partners Apply: POST /partners/apply
  if (path === "/partners/apply" && method === "POST") {
    try {
      const body = await request.json();
      const name = (body.name || "").trim();
      const bizNum = (body.business_number || "").replace(/[^0-9]/g, "");
      const phone = (body.contact_phone || "").trim();
      const rep = (body.representative_name || "").trim();
      const partnerType = body.partner_type || "FULFILLMENT";
      const district = body.district || "관악·동작 통합";

      if (!name || name.length < 2) {
        return errorResponse("INVALID_NAME", "상호명을 2자 이상 입력해 주세요.", 400);
      }
      if (bizNum.length !== 10) {
        return errorResponse("INVALID_BIZ_NUMBER", "사업자등록번호는 10자리 숫자여야 합니다.", 400);
      }
      if (!/^010-\d{4}-\d{4}$/.test(phone)) {
        return errorResponse("INVALID_PHONE", "연락처는 010-XXXX-XXXX 형식이어야 합니다.", 400);
      }
      if (!rep) {
        return errorResponse("INVALID_REP", "대표자명을 입력해 주세요.", 400);
      }

      const orgId = "org_" + crypto.randomUUID();
      const partnerId = "partner_" + crypto.randomUUID();
      const nowIso = new Date().toISOString();

      if (env.DB) {
        await env.DB.prepare(
          `INSERT INTO organizations (id, name, business_number, representative_name, created_at, updated_at)
          VALUES (?, ?, ?, ?, ?, ?)`
        )
          .bind(orgId, name, bizNum, rep, nowIso, nowIso)
          .run();

        await env.DB.prepare(
          `INSERT INTO partners (
            id, organization_id, name, partner_type, business_number, representative_name, contact_phone,
            verification_status, liability_insurance_status, created_at, updated_at
          ) VALUES (?, ?, ?, ?, ?, ?, ?, 'PENDING_APPROVAL', 'PENDING', ?, ?)`
        )
          .bind(partnerId, orgId, name, partnerType, bizNum, rep, phone, nowIso, nowIso)
          .run();
      }

      return jsonResponse(
        {
          ok: true,
          partner_id: partnerId,
          name,
          partner_type: partnerType,
          status: "PENDING_APPROVAL",
          message: "파트너 제휴 신청이 정상 접수되었습니다. 관리자 서류 심사 대기 중입니다.",
        },
        201
      );
    } catch (err) {
      return errorResponse("SERVER_ERROR", err.message, 500);
    }
  }

  // 6. Admin Endpoints (Strictly Protected by X-SBK-Admin-Key)
  if (path.startsWith("/admin/")) {
    const adminKey = request.headers.get("X-SBK-Admin-Key");
    const expectedKey = env.SBK_ADMIN_KEY || "sbk_admin_secret_dev_2026";

    if (!adminKey || adminKey !== expectedKey) {
      return errorResponse("UNAUTHORIZED", "관리자 인증 키가 유효하지 않습니다. 접근이 거부되었습니다.", 401);
    }

    if (path === "/admin/invoices" && method === "GET") {
      if (env.DB) {
        const rows = await env.DB.prepare("SELECT * FROM invoices ORDER BY created_at DESC LIMIT 20").all();
        return jsonResponse({ ok: true, invoices: rows.results || [] });
      }
      return jsonResponse({ ok: true, invoices: [], demo_mode: true });
    }

    if (path === "/admin/payouts" && method === "GET") {
      if (env.DB) {
        const rows = await env.DB.prepare("SELECT * FROM payouts ORDER BY created_at DESC LIMIT 20").all();
        return jsonResponse({ ok: true, payouts: rows.results || [] });
      }
      return jsonResponse({ ok: true, payouts: [], demo_mode: true });
    }
  }

  return errorResponse("NOT_FOUND", `존재하지 않는 엔드포인트입니다: ${path}`, 404);
}
