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

async function getCryptoKey(env) {
  const secret = env.SBK_ENCRYPTION_KEY;
  if (!secret || typeof secret !== "string" || secret.trim().length === 0) {
    const err = new Error("MISSING_ENCRYPTION_KEY: SBK_ENCRYPTION_KEY is required in environment bindings (Fail-Closed).");
    err.status = 503;
    throw err;
  }
  const enc = new TextEncoder();
  const keyBytes = await crypto.subtle.digest("SHA-256", enc.encode(secret));
  return await crypto.subtle.importKey("raw", keyBytes, { name: "AES-GCM" }, false, ["encrypt", "decrypt"]);
}

async function encryptPii(plaintext, env) {
  if (!plaintext) return "";
  const key = await getCryptoKey(env);
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const enc = new TextEncoder();
  const ciphertextBuffer = await crypto.subtle.encrypt(
    { name: "AES-GCM", iv, additionalData: new Uint8Array([0x02]) },
    key,
    enc.encode(plaintext)
  );
  const combined = new Uint8Array(1 + 12 + ciphertextBuffer.byteLength);
  combined[0] = 0x02;
  combined.set(iv, 1);
  combined.set(new Uint8Array(ciphertextBuffer), 13);
  return btoa(String.fromCharCode(...combined));
}

async function verifyAttributionToken(token, env) {
  if (!token || token.split(":").length !== 4) return null;
  const [partnerId, campaignId, expStr, sig] = token.split(":");
  const expiresAt = parseInt(expStr, 10);
  if (isNaN(expiresAt) || Date.now() / 1000 > expiresAt) return null;

  const secret = env.SBK_ATTRIBUTION_SECRET;
  if (!secret || typeof secret !== "string" || secret.trim().length === 0) {
    // Fail-Closed: Secret missing in environment, reject attribution verification
    return null;
  }
  const enc = new TextEncoder();
  const key = await crypto.subtle.importKey(
    "raw",
    enc.encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"]
  );
  const payload = `${partnerId}:${campaignId}:${expiresAt}`;
  const sigBuffer = await crypto.subtle.sign("HMAC", key, enc.encode(payload));
  const expectedSig = Array.from(new Uint8Array(sigBuffer))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("")
    .slice(0, sig.length);

  if (sig !== expectedSig) return null;
  return { partnerId, campaignId, expiresAt };
}

async function generateAttributionToken(partnerId, campaignId, env, ttlSeconds = 86400 * 7) {
  const secret = env.SBK_ATTRIBUTION_SECRET;
  if (!secret || typeof secret !== "string" || secret.trim().length === 0) {
    const err = new Error("MISSING_ATTRIBUTION_SECRET: SBK_ATTRIBUTION_SECRET is required in environment bindings (Fail-Closed).");
    err.status = 503;
    throw err;
  }
  const expiresAt = Math.floor(Date.now() / 1000) + ttlSeconds;
  const payload = `${partnerId}:${campaignId}:${expiresAt}`;
  const enc = new TextEncoder();
  const key = await crypto.subtle.importKey(
    "raw",
    enc.encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"]
  );
  const sigBuffer = await crypto.subtle.sign("HMAC", key, enc.encode(payload));
  const sig = Array.from(new Uint8Array(sigBuffer))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("")
    .slice(0, 32);
  return `${payload}:${sig}`;
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

    // SBK-MINI-01 Defense-in-depth: Block static web access to private internal paths
    const privatePrefixes = ["/src", "/docs", "/tests", "/migrations", "/scripts", "/.git"];
    for (const prefix of privatePrefixes) {
      if (url.pathname === prefix || url.pathname.startsWith(prefix + "/")) {
        return new Response("Not Found", { status: 404 });
      }
    }
    if (url.pathname.startsWith("/.env") || url.pathname.endsWith(".py") || url.pathname.endsWith(".sql")) {
      return new Response("Not Found", { status: 404 });
    }

    // Fallback to static assets (SaaS comparisons, images, redirects, slotbridge HTML)
    if (env.ASSETS) {
      return await env.ASSETS.fetch(request);
    }

    return new Response("Not Found", { status: 404 });
  },
};

function checkD1Database(env) {
  if (!env.DB) {
    return errorResponse(
      "SERVICE_UNAVAILABLE",
      "D1 데이터베이스 바인딩이 누락되었습니다. 실거래 데이터 저장 및 조회가 불가합니다 (Fail-Closed).",
      503
    );
  }
  return null;
}

function checkOperatorReleaseGate(env) {
  // Hard blocker: Operator approval cannot be overridden by client query params, headers, or body
  const isApproved = (env.SBK_OPERATOR_APPROVAL === "1" || env.SBK_OPERATOR_APPROVAL === "true");
  if (!isApproved) {
    return errorResponse(
      "OPERATOR_APPROVAL_REQUIRED",
      "운영자(회기님)의 정식 승인 및 오프라인 3대 게이트(계약/사업자/법률)가 체결되지 않아 실거래 및 고객 접수가 안전하게 동결(BLOCKED_EXTERNAL)되어 있습니다.",
      503
    );
  }
  return null;
}

async function handleApiRequest(request, url, env) {
  const path = url.pathname.replace("/api/v1/slotbridge", "");
  const method = request.method;

  // 1. Health & Gate Status Endpoint
  if (path === "/health" || path === "") {
    const isApproved = (env.SBK_OPERATOR_APPROVAL === "1" || env.SBK_OPERATOR_APPROVAL === "true");
    const d1Connected = Boolean(env.DB);
    return jsonResponse({
      ok: true,
      service: "SlotBridge Korea Engine API",
      version: "1.2.0",
      d1_connected: d1Connected,
      release_gate: {
        operator_approved: isApproved,
        status: (isApproved && d1Connected) ? "READY" : "BLOCKED_EXTERNAL",
        offline_gates_required: [
          "GATE-01: 법률 자문 최종 확인 (통판중개 면책)",
          "GATE-02: 관악·동작 1차 시공 파트너 서명 LOI 체결",
          "GATE-03: 세무 사업자등록 및 정산 전용 계좌 개설"
        ]
      },
      time: new Date().toISOString(),
    });
  }

  // 1.1 Attribution Token Issue: GET /attribution/token?partner_id=...
  if (path === "/attribution/token" && method === "GET") {
    const dbErr = checkD1Database(env);
    if (dbErr) return dbErr;

    const partnerId = url.searchParams.get("partner_id");
    const campaignId = url.searchParams.get("campaign_id") || "overflow_widget";

    if (!partnerId) {
      return errorResponse("PARTNER_ID_REQUIRED", "partner_id 파라미터가 필요합니다.", 400);
    }

    const partner = await env.DB.prepare(
      "SELECT id, verification_status FROM partners WHERE id = ?"
    ).bind(partnerId).first();

    if (!partner || !["APPROVED", "VERIFIED"].includes(partner.verification_status)) {
      return errorResponse("PARTNER_INACTIVE", "승인된 활성 파트너만 위젯 서명 토큰을 발급받을 수 있습니다.", 403);
    }

    // Increment click stats
    const nowIso = new Date().toISOString();
    await env.DB.prepare(
      `INSERT INTO campaign_attributions (id, partner_id, campaign_name, clicks_count, conversions_count, created_at, updated_at)
       VALUES (?, ?, ?, 1, 0, ?, ?)
       ON CONFLICT(partner_id, campaign_name) DO UPDATE SET clicks_count = clicks_count + 1, updated_at = ?`
    ).bind("camp_" + crypto.randomUUID(), partnerId, campaignId, nowIso, nowIso, nowIso).run().catch(() => {});

    let token;
    try {
      token = await generateAttributionToken(partnerId, campaignId, env);
    } catch (err) {
      return errorResponse("SERVER_CONFIG_ERROR", err.message || "토큰 발급 실패 (Fail-Closed)", err.status || 503);
    }
    return jsonResponse({
      ok: true,
      partner_id: partnerId,
      campaign_id: campaignId,
      token,
      referral_url: `/slotbridge/inquiry.html?ref=${encodeURIComponent(partnerId)}&token=${encodeURIComponent(token)}`
    });
  }

  // 2. Inquiries: POST /inquiries
  if (path === "/inquiries" && method === "POST") {
    const dbErr = checkD1Database(env);
    if (dbErr) return dbErr;

    const gateErr = checkOperatorReleaseGate(env);
    if (gateErr) return gateErr;

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
      
      // SBK-FIX-03: Attribution verification (B05 defense)
      let attributionPartnerId = null;
      if (body.attribution_token) {
        const verified = await verifyAttributionToken(body.attribution_token, env);
        if (verified && verified.partnerId) {
          attributionPartnerId = verified.partnerId;
        }
      }

      // SBK-FIX-03: AES-256-GCM Encryption (B04 defense)
      let encName, encPhone;
      try {
        encName = await encryptPii(name, env);
        encPhone = await encryptPii(phone, env);
      } catch (err) {
        return errorResponse("ENCRYPTION_FAILED", err.message || "암호화 실패 (Fail-Closed)", err.status || 503);
      }

      const inquiryId = "inq_" + crypto.randomUUID();
      const trackingToken = crypto.randomUUID();
      const nowIso = new Date().toISOString();
      const expiresAt = new Date(Date.now() + 72 * 3600 * 1000).toISOString();

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
          encName,
          encPhone,
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
    const dbErr = checkD1Database(env);
    if (dbErr) return dbErr;

    const token = matchInq[1];
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

  // 4. Inquiry Revoke: DELETE /inquiries/:token
  if (matchInq && method === "DELETE") {
    const dbErr = checkD1Database(env);
    if (dbErr) return dbErr;

    const token = matchInq[1];
    const nowIso = new Date().toISOString();

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

  // 5. Partners Apply: POST /partners/apply
  if (path === "/partners/apply" && method === "POST") {
    const dbErr = checkD1Database(env);
    if (dbErr) return dbErr;

    const gateErr = checkOperatorReleaseGate(env);
    if (gateErr) return gateErr;

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
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'PENDING_APPROVAL', 'PENDING_VERIFICATION', ?, ?)`
      )
        .bind(partnerId, orgId, name, partnerType, bizNum, rep, phone, nowIso, nowIso)
        .run();

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

  // 6. Match Fulfillment: POST /matches/:id/fulfill (SBK-FIX-06: Two-Way Verification Gateway)
  const matchFulfill = path.match(/^\/matches\/([a-zA-Z0-9_-]+)\/fulfill$/);
  if (matchFulfill && method === "POST") {
    const dbErr = checkD1Database(env);
    if (dbErr) return dbErr;

    try {
      const matchId = matchFulfill[1];
      const body = await request.json();
      const partnerId = body.partner_id;
      const finalAmount = Number(body.final_amount) || 0;
      const photoUrls = body.photo_urls || [];

      if (!partnerId) {
        return errorResponse("PARTNER_ID_REQUIRED", "파트너 ID가 필요합니다.", 400);
      }
      if (!photoUrls || photoUrls.length < 2) {
        return errorResponse("INSUFFICIENT_EVIDENCE", "현장 시공 전후 사진 2장 이상의 증빙이 필수적입니다.", 400);
      }
      if (finalAmount <= 0) {
        return errorResponse("INVALID_AMOUNT", "최종 정산 금액은 0원보다 커야 합니다.", 400);
      }

      const match = await env.DB.prepare("SELECT * FROM matches WHERE id = ? AND partner_id = ?").bind(matchId, partnerId).first();
      if (!match) {
        return errorResponse("MATCH_NOT_FOUND", "일치하는 매칭 정보를 찾을 수 없습니다.", 404);
      }
      if (match.status === "DISPUTED") {
        return errorResponse("MATCH_DISPUTED", "분쟁 진행 중인 매칭에 대해서는 완료 보고를 제출할 수 없습니다.", 400);
      }
      const inq = await env.DB.prepare("SELECT status FROM inquiries WHERE id = ?").bind(match.inquiry_id).first();
      if (inq && inq.status === "CANCELLED") {
        return errorResponse("INQUIRY_CANCELLED", "고객이 철회한 요청 건에 대해서는 시공 완료를 접수할 수 없습니다.", 400);
      }

      const nowIso = new Date().toISOString();
      const evidenceId = "evi_" + crypto.randomUUID();

      // Save evidence with PENDING_CUSTOMER_CONFIRMATION
      await env.DB.prepare(
        `INSERT INTO fulfillment_evidence (
          id, match_id, photo_urls, completion_timestamp, partner_submitted_amount, verification_status, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, 'PENDING_CUSTOMER_CONFIRMATION', ?, ?)`
      ).bind(evidenceId, matchId, JSON.stringify(photoUrls), nowIso, finalAmount, nowIso, nowIso).run();

      await env.DB.prepare(
        "UPDATE matches SET status = 'COMPLETED', final_amount = ?, updated_at = ? WHERE id = ?"
      ).bind(finalAmount, nowIso, matchId).run();

      return jsonResponse({
        ok: true,
        match_id: matchId,
        status: "COMPLETED",
        verification_status: "PENDING_CUSTOMER_CONFIRMATION",
        message: "시공 완료 보고가 접수되었습니다. 고객 최종 확인 또는 분쟁 유예기간 경과 후 정산 청구 자격이 부여됩니다.",
      });
    } catch (err) {
      return errorResponse("SERVER_ERROR", err.message, 500);
    }
  }

  // 7. Match Confirmation: POST /matches/:id/confirm (SBK-FIX-06)
  const matchConfirm = path.match(/^\/matches\/([a-zA-Z0-9_-]+)\/confirm$/);
  if (matchConfirm && method === "POST") {
    const dbErr = checkD1Database(env);
    if (dbErr) return dbErr;

    try {
      const matchId = matchConfirm[1];
      const body = await request.json();
      const rating = Number(body.rating) || 5;

      const match = await env.DB.prepare("SELECT * FROM matches WHERE id = ?").bind(matchId).first();
      if (!match) {
        return errorResponse("MATCH_NOT_FOUND", "매칭을 찾을 수 없습니다.", 404);
      }
      if (match.status === "DISPUTED") {
        return errorResponse("MATCH_DISPUTED", "분쟁 진행 중인 건은 완료 확인할 수 없습니다.", 400);
      }

      const nowIso = new Date().toISOString();
      await env.DB.prepare(
        "UPDATE fulfillment_evidence SET verification_status = 'VERIFIED', updated_at = ? WHERE match_id = ?"
      ).bind(nowIso, matchId).run();

      await env.DB.prepare(
        "UPDATE matches SET status = 'BILLABLE', updated_at = ? WHERE id = ?"
      ).bind(nowIso, matchId).run();

      return jsonResponse({
        ok: true,
        match_id: matchId,
        status: "BILLABLE",
        verification_status: "VERIFIED",
        rating,
        message: "고객님의 시공 완료 확인이 완료되었습니다. 정산 대기열로 안전하게 등록되었습니다.",
      });
    } catch (err) {
      return errorResponse("SERVER_ERROR", err.message, 500);
    }
  }

  // 6. Admin Endpoints (Strictly Protected by X-SBK-Admin-Key)
  if (path.startsWith("/admin/")) {
    const adminKey = request.headers.get("X-SBK-Admin-Key");
    const expectedKey = env.SBK_ADMIN_KEY;

    if (!expectedKey || typeof expectedKey !== "string" || expectedKey.trim().length === 0) {
      return errorResponse("SERVER_CONFIG_ERROR", "관리자 시크릿 키가 환경변수에 설정되지 않았습니다 (Fail-Closed).", 503);
    }

    if (!adminKey || adminKey !== expectedKey) {
      return errorResponse("UNAUTHORIZED", "관리자 인증 키가 유효하지 않습니다. 접근이 거부되었습니다.", 401);
    }

    if (path === "/admin/invoices" && method === "GET") {
      const dbErr = checkD1Database(env);
      if (dbErr) return dbErr;
      const rows = await env.DB.prepare("SELECT * FROM invoices ORDER BY created_at DESC LIMIT 20").all();
      return jsonResponse({ ok: true, invoices: rows.results || [] });
    }

    if (path === "/admin/payouts" && method === "GET") {
      const dbErr = checkD1Database(env);
      if (dbErr) return dbErr;
      const rows = await env.DB.prepare("SELECT * FROM payouts ORDER BY created_at DESC LIMIT 20").all();
      return jsonResponse({ ok: true, payouts: rows.results || [] });
    }
  }

  // 7. Cron Trigger: Process 15-minute offer timeouts & auto-reassignment (B12)
  if (path === "/cron/process-timeouts" && method === "POST") {
    const adminKey = request.headers.get("X-SBK-Admin-Key");
    const expectedKey = env.SBK_ADMIN_KEY;

    if (!expectedKey || typeof expectedKey !== "string" || expectedKey.trim().length === 0) {
      return errorResponse("SERVER_CONFIG_ERROR", "관리자 시크릿 키가 환경변수에 설정되지 않았습니다 (Fail-Closed).", 503);
    }

    if (!adminKey || adminKey !== expectedKey) {
      return errorResponse("UNAUTHORIZED", "크론 작업 실행 권한이 없습니다.", 401);
    }
    const dbErr = checkD1Database(env);
    if (dbErr) return dbErr;

    const nowIso = new Date().toISOString();
    const expiredOffers = await env.DB.prepare(
      "SELECT id, inquiry_id, slot_id FROM assignment_offers WHERE status = 'PENDING' AND expires_at < ?"
    ).bind(nowIso).all();

    let processed = 0;
    for (const off of (expiredOffers.results || [])) {
      await env.DB.prepare(
        "UPDATE assignment_offers SET status = 'EXPIRED', updated_at = ? WHERE id = ? AND status = 'PENDING'"
      ).bind(nowIso, off.id).run();

      await env.DB.prepare(
        "UPDATE capacity_slots SET status = 'AVAILABLE', updated_at = ? WHERE id = ?"
      ).bind(nowIso, off.slot_id).run();

      await env.DB.prepare(
        "UPDATE inquiries SET status = 'PENDING_MATCH', updated_at = ? WHERE id = ?"
      ).bind(nowIso, off.inquiry_id).run();
      processed++;
    }

    return jsonResponse({ ok: true, processed_count: processed, timestamp: nowIso });
  }

  return errorResponse("NOT_FOUND", `존재하지 않는 엔드포인트입니다: ${path}`, 404);
}
