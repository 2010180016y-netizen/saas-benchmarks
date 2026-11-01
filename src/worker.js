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

async function decryptPii(encoded, env) {
  if (!encoded || encoded.startsWith("ERASED_")) return "";
  const key = await getCryptoKey(env);
  const binary = atob(encoded);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) {
    bytes[i] = binary.charCodeAt(i);
  }
  if (bytes[0] !== 0x02 || bytes.length < 13 + 16) {
    throw new Error("INVALID_CIPHERTEXT_FORMAT");
  }
  const iv = bytes.slice(1, 13);
  const ciphertext = bytes.slice(13);
  const decrypted = await crypto.subtle.decrypt(
    { name: "AES-GCM", iv, additionalData: new Uint8Array([0x02]) },
    key,
    ciphertext
  );
  return new TextDecoder().decode(decrypted);
}

async function hashPhoneNumber(phone, env) {
  const clean = (phone || "").replace(/[^0-9]/g, "");
  if (!clean) return "";
  const secret = env.SBK_ENCRYPTION_KEY;
  if (!secret || typeof secret !== "string" || secret.trim().length === 0) {
    const err = new Error("MISSING_ENCRYPTION_KEY: Secret required for phone hashing (Fail-Closed).");
    err.status = 503;
    throw err;
  }
  const enc = new TextEncoder();
  const key = await crypto.subtle.importKey(
    "raw",
    enc.encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"]
  );
  const sigBuffer = await crypto.subtle.sign("HMAC", key, enc.encode("SBK_PHONE_HASH_V1:" + clean));
  return Array.from(new Uint8Array(sigBuffer))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

async function generatePartnerAuthToken(partnerId, env) {
  const secret = env.SBK_ENCRYPTION_KEY;
  if (!secret || typeof secret !== "string" || secret.trim().length === 0) {
    const err = new Error("SERVER_CONFIG_ERROR: Secret key required for partner auth token derivation (Fail-Closed).");
    err.status = 503;
    throw err;
  }
  const enc = new TextEncoder();
  const key = await crypto.subtle.importKey(
    "raw",
    enc.encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"]
  );
  const payload = `sbk_partner_auth_v1:${partnerId}`;
  const sigBuffer = await crypto.subtle.sign("HMAC", key, enc.encode(payload));
  const sig = Array.from(new Uint8Array(sigBuffer))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("")
    .slice(0, 32);
  return `sbk_ptk_${partnerId}_${sig}`;
}

async function verifyPartnerAuthToken(partnerKey, partnerId, env) {
  if (!partnerKey || typeof partnerKey !== "string" || !partnerKey.startsWith("sbk_ptk_")) {
    return false;
  }
  const secret = env.SBK_ENCRYPTION_KEY;
  if (!secret || typeof secret !== "string" || secret.trim().length === 0) {
    return false;
  }
  try {
    const expected = await generatePartnerAuthToken(partnerId, env);
    return partnerKey === expected;
  } catch (_) {
    return false;
  }
}

async function verifyAttributionToken(token, env) {
  if (!token || typeof token !== "string" || token.split(":").length !== 4) return null;
  const [partnerId, campaignId, expStr, sig] = token.split(":");
  const expiresAt = parseInt(expStr, 10);
  if (isNaN(expiresAt) || Date.now() / 1000 > expiresAt) return null;

  // SBK-FIX-A (P0-02): Enforce strict signature length and hex characters (prevent empty or truncated sig match)
  if (!sig || (sig.length !== 32 && sig.length !== 64) || !/^[0-9a-f]+$/i.test(sig)) {
    return null;
  }

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
  const fullSigHex = Array.from(new Uint8Array(sigBuffer))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");

  const expectedSig = sig.length === 32 ? fullSigHex.slice(0, 32) : fullSigHex;
  if (sig.toLowerCase() !== expectedSig.toLowerCase()) return null;
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

  // SBK-MINI-09: Cloudflare Workers Scheduled Cron Handler (5-minute interval)
  async scheduled(event, env, ctx) {
    if (ctx && ctx.waitUntil) {
      ctx.waitUntil(processTimeoutsInternal(env));
    } else {
      await processTimeoutsInternal(env);
    }
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

// SBK-MINI-08: Deterministic Candidate Evaluation & 1:1 Exclusive Offer Dispatch
async function dispatchCandidateOffer(inquiryId, env, ttlMinutes = 15) {
  if (!env.DB) return null;
  const inq = await env.DB.prepare("SELECT * FROM inquiries WHERE id = ?").bind(inquiryId).first();
  if (!inq) return null;
  if (["CANCELLED", "MATCHED", "FULFILLED", "BOOKED", "COMPLETED", "VERIFIED", "BILLABLE"].includes(inq.status)) {
    return null;
  }

  // 1. Fetch available slots matching date, district, verified insurance, approved partner
  const query = `
    SELECT
      cs.id AS slot_id, cs.partner_id, cs.slot_date, cs.time_window,
      p.name AS partner_name, p.verification_status, p.liability_insurance_status,
      p.rating_average, p.total_completed_count,
      sa.district
    FROM capacity_slots cs
    JOIN partners p ON cs.partner_id = p.id
    JOIN service_areas sa ON sa.partner_id = p.id
    WHERE cs.slot_date = ?
      AND cs.status = 'AVAILABLE'
      AND sa.district = ?
      AND sa.is_active = 1
      AND p.verification_status = 'APPROVED'
      AND p.liability_insurance_status = 'VERIFIED'
  `;
  const candidatesRes = await env.DB.prepare(query).bind(inq.requested_date, inq.district).all();
  const candidates = candidatesRes.results || [];

  const qualifiedCandidates = [];
  const reqWindow = inq.time_window || "MORNING";

  for (const c of candidates) {
    const slotWindow = c.time_window || "MORNING";
    if (reqWindow !== "ALL_DAY" && slotWindow !== "ALL_DAY" && slotWindow !== reqWindow) {
      continue;
    }

    // Check if partner previously rejected or timed out
    const rejected = await env.DB.prepare(
      `SELECT pr.id FROM partner_responses pr
       JOIN assignment_offers ao ON pr.offer_id = ao.id
       WHERE ao.inquiry_id = ? AND pr.partner_id = ? AND pr.response_type IN ('REJECT', 'TIMEOUT')`
    ).bind(inquiryId, c.partner_id).first();
    if (rejected) continue;

    // Equity balance dispatches count
    const dispatchesRow = await env.DB.prepare(
      "SELECT count(*) AS cnt FROM assignment_offers WHERE partner_id = ?"
    ).bind(c.partner_id).first();
    const dispatchesCnt = dispatchesRow ? (dispatchesRow.cnt || 0) : 0;

    const score = (
      (Number(c.rating_average || 5.0) * 20.0) +
      (Math.min(Number(c.total_completed_count || 0), 100) * 0.5) +
      (10.0 / (dispatchesCnt + 1))
    );

    qualifiedCandidates.push({
      slot_id: c.slot_id,
      partner_id: c.partner_id,
      partner_name: c.partner_name,
      score
    });
  }

  const nowIso = new Date().toISOString();
  if (qualifiedCandidates.length === 0) {
    await env.DB.prepare("UPDATE inquiries SET status = 'WAITING', updated_at = ? WHERE id = ?").bind(nowIso, inquiryId).run();
    return null;
  }

  // Sort deterministically by score DESC
  qualifiedCandidates.sort((a, b) => b.score - a.score);

  let winner = null;
  for (const cand of qualifiedCandidates) {
    const slotUpdate = await env.DB.prepare(
      "UPDATE capacity_slots SET status = 'OFFERED', updated_at = ? WHERE id = ? AND status = 'AVAILABLE'"
    ).bind(nowIso, cand.slot_id).run();
    if (slotUpdate.meta && slotUpdate.meta.changes > 0) {
      winner = cand;
      break;
    }
  }

  if (!winner) {
    await env.DB.prepare("UPDATE inquiries SET status = 'WAITING', updated_at = ? WHERE id = ?").bind(nowIso, inquiryId).run();
    return null;
  }

  const offerId = "off_" + crypto.randomUUID();
  const expiresAt = new Date(Date.now() + ttlMinutes * 60 * 1000).toISOString();

  await env.DB.prepare("UPDATE inquiries SET status = 'OFFERED', updated_at = ? WHERE id = ?").bind(nowIso, inquiryId).run();

  await env.DB.prepare(
    `INSERT INTO assignment_offers (id, inquiry_id, partner_id, slot_id, priority, status, expires_at, created_at, updated_at)
     VALUES (?, ?, ?, ?, 1, 'PENDING', ?, ?, ?)`
  ).bind(offerId, inquiryId, winner.partner_id, winner.slot_id, expiresAt, nowIso, nowIso).run();

  const notifId = "notif_" + crypto.randomUUID();
  const notifPayload = {
    offer_id: offerId,
    inquiry_id: inquiryId,
    district: inq.district,
    requested_date: inq.requested_date,
    time_window: reqWindow,
    expires_at: expiresAt,
    channel: "MOCKED_SMS"
  };
  await env.DB.prepare(
    `INSERT INTO notification_outbox (id, recipient_type, recipient_destination, notification_type, payload, status, created_at)
     VALUES (?, 'PARTNER', ?, 'OFFER_ALERT', ?, 'MOCKED', ?)`
  ).bind(notifId, winner.partner_id, JSON.stringify(notifPayload), nowIso).run().catch(() => {});

  return {
    offer_id: offerId,
    inquiry_id: inquiryId,
    partner_id: winner.partner_id,
    partner_name: winner.partner_name,
    slot_id: winner.slot_id,
    score: winner.score,
    expires_at: expiresAt
  };
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

    // SBK-FIX-A (P0-01): Block unauthenticated token generation. Header-only authentication (no URL params, no business number)
    const adminKey = request.headers.get("X-SBK-Admin-Key");
    const partnerKey = request.headers.get("X-SBK-Partner-Key");
    const expectedAdminKey = env.SBK_ADMIN_KEY;

    let isAuthorized = false;
    if (expectedAdminKey && adminKey === expectedAdminKey) {
      isAuthorized = true;
    } else if (partnerKey) {
      isAuthorized = await verifyPartnerAuthToken(partnerKey, partnerId, env);
    }

    if (!isAuthorized) {
      return errorResponse(
        "UNAUTHORIZED",
        "공개 토큰 발급이 차단되었습니다. 파트너 인증 키(X-SBK-Partner-Key) 또는 관리자 권한이 필요합니다.",
        401
      );
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
      // SBK-MINI-06: Idempotency check via Idempotency-Key header
      const idempotencyKey = request.headers.get("Idempotency-Key");
      if (idempotencyKey) {
        const existing = await env.DB.prepare(
          "SELECT id, tracking_token, status, district, neighborhood, requested_date, time_window FROM inquiries WHERE id = ? OR tracking_token = ?"
        ).bind(idempotencyKey, idempotencyKey).first();
        if (existing) {
          return jsonResponse({
            ok: true,
            inquiry_id: existing.id,
            tracking_token: existing.tracking_token,
            status_token: existing.tracking_token,
            status: existing.status,
            idempotent_replay: true,
            message: "이전에 정상 접수된 신청 내역이 반환되었습니다 (Idempotent Replay)."
          }, 200);
        }
      }

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

      // SBK-MINI-06: KST 24~72h date and time_window validation
      const requestedDate = (body.requested_date || "").trim();
      if (!requestedDate || !/^\d{4}-\d{2}-\d{2}$/.test(requestedDate)) {
        return errorResponse("INVALID_DATE", "신청 일자는 YYYY-MM-DD 형식이어야 합니다.", 400);
      }
      const kstNow = new Date(Date.now() + 9 * 3600 * 1000);
      const todayKstStr = kstNow.toISOString().split("T")[0];
      const todayKstMs = new Date(todayKstStr + "T00:00:00+09:00").getTime();
      const reqDateMs = new Date(requestedDate + "T00:00:00+09:00").getTime();
      const diffDays = Math.round((reqDateMs - todayKstMs) / (24 * 3600 * 1000));
      if (diffDays < 1 || diffDays > 3) {
        return errorResponse("DATE_OUT_OF_WINDOW", "신청 일자는 KST 기준 내일~3일 이내(24~72시간 급행)만 가능합니다.", 400);
      }

      const timeWindow = body.requested_time_window || "MORNING";
      if (!["MORNING", "AFTERNOON", "ALL_DAY"].includes(timeWindow)) {
        return errorResponse("INVALID_TIME_WINDOW", "희망 시간대는 MORNING, AFTERNOON, ALL_DAY 중 하나여야 합니다.", 400);
      }

      const housingType = body.housing_type || "STUDIO";
      const areaPyeong = Number(body.area_pyeong) || 8;
      
      // SBK-FIX-03 & SBK-MINI-05: Attribution verification & anti-spoofing
      let attributionPartnerId = null;
      if (body.attribution_token) {
        const verified = await verifyAttributionToken(body.attribution_token, env);
        if (verified && verified.partnerId) {
          // Anti-spoofing: If attribution_partner_id is passed, it MUST match token's partnerId
          if (body.attribution_partner_id && body.attribution_partner_id !== verified.partnerId) {
            attributionPartnerId = null; // Mismatched / spoofed partner ID rejected
          } else {
            // Verify partner exists and is APPROVED in D1
            const p = await env.DB.prepare(
              "SELECT id, verification_status FROM partners WHERE id = ?"
            ).bind(verified.partnerId).first();
            if (p && ["APPROVED", "VERIFIED"].includes(p.verification_status)) {
              attributionPartnerId = verified.partnerId;
            } else {
              attributionPartnerId = null; // Unapproved / inactive partner rejected
            }
          }
        }
      }

      // SBK-FIX-03 & SBK-MINI-04: AES-256-GCM Encryption & HMAC-SHA256 Phone Hashing
      let encName, encPhone, phoneHash;
      try {
        encName = await encryptPii(name, env);
        encPhone = await encryptPii(phone, env);
        phoneHash = await hashPhoneNumber(phone, env);
      } catch (err) {
        return errorResponse("ENCRYPTION_FAILED", err.message || "암호화 실패 (Fail-Closed)", err.status || 503);
      }

      // SBK-MINI-06: Deduplication check
      const duplicateInquiry = await env.DB.prepare(
        "SELECT id FROM inquiries WHERE customer_phone_hash = ? AND requested_date = ? AND status NOT IN ('CANCELLED', 'FULFILLED')"
      ).bind(phoneHash, requestedDate).first();
      if (duplicateInquiry) {
        return errorResponse("DUPLICATE_INQUIRY", "동일한 연락처로 해당 일자에 이미 진행 중인 신청 내역이 존재합니다.", 409);
      }

      const inquiryId = idempotencyKey || ("inq_" + crypto.randomUUID());
      const trackingToken = crypto.randomUUID();
      const nowIso = new Date().toISOString();
      const expiresAt = new Date(Date.now() + 72 * 3600 * 1000).toISOString();

      // Real client IP hash & user agent capture (no fake constants)
      const rawIp = request.headers.get("CF-Connecting-IP") || request.headers.get("X-Forwarded-For") || "";
      let clientIpHash = "NOT_COLLECTED";
      if (rawIp) {
        const enc = new TextEncoder();
        const ipBytes = await crypto.subtle.digest("SHA-256", enc.encode("SBK_IP_SALT:" + rawIp));
        clientIpHash = Array.from(new Uint8Array(ipBytes))
          .map((b) => b.toString(16).padStart(2, "0"))
          .join("");
      }
      const userAgent = (request.headers.get("User-Agent") || "NOT_COLLECTED").slice(0, 255);

      // SBK-MINI-06: Atomic D1 batch execution for inquiry + consent event
      const insertInquiryStmt = env.DB.prepare(
        `INSERT INTO inquiries (
          id, tracking_token, district, neighborhood, housing_type, area_pyeong,
          requested_date, time_window, customer_name_encrypted, customer_phone_encrypted,
          customer_phone_hash, attribution_partner_id, status, expires_at, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING_MATCH', ?, ?, ?)`
      ).bind(
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
        phoneHash,
        attributionPartnerId,
        expiresAt,
        nowIso,
        nowIso
      );

      const insertConsentStmt = env.DB.prepare(
        `INSERT INTO consent_events (
          id, inquiry_id, terms_version, agreed_privacy_collection, agreed_third_party_share, agreed_marketing, client_ip_hash, user_agent, consented_at
        ) VALUES (?, ?, 'v1.0-20261031', 1, 1, ?, ?, ?, ?)`
      ).bind(
        "cst_" + crypto.randomUUID(),
        inquiryId,
        body.agreed_marketing ? 1 : 0,
        clientIpHash,
        userAgent,
        nowIso
      );

      await env.DB.batch([insertInquiryStmt, insertConsentStmt]);

      // SBK-MINI-08: Trigger deterministic 1:1 matching & exclusive offer dispatch
      const dispatchResult = await dispatchCandidateOffer(inquiryId, env);
      const finalStatus = dispatchResult ? "OFFERED" : "WAITING";

      return jsonResponse(
        {
          ok: true,
          inquiry_id: inquiryId,
          tracking_token: trackingToken,
          status_token: trackingToken,
          status: finalStatus,
          district,
          neighborhood,
          requested_date: requestedDate,
          time_window: timeWindow,
          masked_customer: {
            name: maskName(name),
            phone: maskPhone(phone),
          },
          matched_offer: dispatchResult,
          message: dispatchResult
            ? "적격 파트너사에 15분 독점 오퍼가 발급되었습니다."
            : "급행 매칭 신청이 D1에 접수되었으며 가용 슬롯 탐색 대기 중입니다 (WAITING).",
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
    let maskedPhone = "010-****-****";
    try {
      if (row.customer_phone_encrypted && !row.customer_phone_encrypted.startsWith("ERASED_")) {
        const rawPhone = await decryptPii(row.customer_phone_encrypted, env);
        maskedPhone = maskPhone(rawPhone);
      } else {
        maskedPhone = "파기됨(Tombstone)";
      }
    } catch (e) {
      maskedPhone = "010-****-****";
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
      masked_phone: maskedPhone,
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

    const tombstoneId = "ERASED_" + crypto.randomUUID();
    await env.DB.prepare(
      `UPDATE inquiries SET
        status = 'CANCELLED',
        customer_name_encrypted = ?,
        customer_phone_encrypted = ?,
        customer_phone_hash = ?,
        updated_at = ?
      WHERE tracking_token = ?`
    )
      .bind(tombstoneId, tombstoneId, tombstoneId, nowIso, token)
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
      // Business number: 10 digits if provided; if unregistered individual pilot, assign unique pilot ID
      let bizNumClean = (body.business_number || "").replace(/[^0-9]/g, "");
      if (bizNumClean.length > 0 && bizNumClean.length !== 10) {
        return errorResponse("INVALID_BIZ_NUMBER", "사업자등록번호는 10자리 숫자여야 합니다 (개인 파일럿은 비워둘 수 있습니다).", 400);
      }
      if (!bizNumClean) {
        bizNumClean = "INDIV_" + crypto.randomUUID().replace(/-/g, "").slice(0, 10);
      } else {
        // Business number duplicate check
        const existingPartner = await env.DB.prepare(
          "SELECT id, name FROM partners WHERE business_number = ?"
        ).bind(bizNumClean).first();
        if (existingPartner) {
          return errorResponse("DUPLICATE_PARTNER", "이미 등록된 사업자등록번호입니다.", 409);
        }
      }

      const orgId = "org_" + crypto.randomUUID();
      const partnerId = "partner_" + crypto.randomUUID();
      const nowIso = new Date().toISOString();

      await env.DB.prepare(
        `INSERT INTO organizations (id, name, business_number, representative_name, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)`
      )
        .bind(orgId, name, bizNumClean, rep, nowIso, nowIso)
        .run();

      await env.DB.prepare(
        `INSERT INTO partners (
          id, organization_id, name, partner_type, business_number, representative_name, contact_phone,
          verification_status, liability_insurance_status, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'PENDING_APPROVAL', 'PENDING', ?, ?)`
      )
        .bind(partnerId, orgId, name, partnerType, bizNumClean, rep, phone, nowIso, nowIso)
        .run();

      // SBK-FIX-B (P0-03): Bind service areas for newly applied partner
      let targetDistricts = [];
      if (Array.isArray(body.service_areas) && body.service_areas.length > 0) {
        for (const item of body.service_areas) {
          const d = typeof item === "string" ? item : (item.district || "");
          if (d && !targetDistricts.includes(d)) targetDistricts.push(d);
        }
      } else if (Array.isArray(body.districts) && body.districts.length > 0) {
        for (const d of body.districts) {
          if (d && !targetDistricts.includes(d)) targetDistricts.push(d);
        }
      } else {
        const dStr = (body.district || "").trim();
        if (dStr === "관악구") {
          targetDistricts = ["관악구"];
        } else if (dStr === "동작구") {
          targetDistricts = ["동작구"];
        } else {
          // Default or "관악·동작 통합"
          targetDistricts = ["관악구", "동작구"];
        }
      }

      for (const dist of targetDistricts) {
        const saId = "sa_" + crypto.randomUUID();
        await env.DB.prepare(
          `INSERT INTO service_areas (id, partner_id, city, district, neighborhood, is_active, created_at)
           VALUES (?, ?, '서울특별시', ?, '전역', 1, ?)`
        ).bind(saId, partnerId, dist, nowIso).run();
      }

      let partnerKey = "";
      try {
        partnerKey = await generatePartnerAuthToken(partnerId, env);
      } catch (_) {}

      return jsonResponse(
        {
          ok: true,
          partner_id: partnerId,
          partner_key: partnerKey,
          name,
          partner_type: partnerType,
          service_areas: targetDistricts,
          status: "PENDING_APPROVAL",
          message: "파트너 제휴 신청이 정상 접수되었습니다. 발급된 파트너 보안 인증 키(partner_key)를 안전하게 보관하세요.",
        },
        201
      );
    } catch (err) {
      return errorResponse("SERVER_ERROR", err.message, 500);
    }
  }

  // SBK-FIX-B (P0-03): Partner Service Areas Query: GET /service-areas?partner_id=...
  if (path === "/service-areas" && method === "GET") {
    const dbErr = checkD1Database(env);
    if (dbErr) return dbErr;

    const partnerId = url.searchParams.get("partner_id");
    if (!partnerId) {
      return errorResponse("PARTNER_ID_REQUIRED", "partner_id 파라미터가 필요합니다.", 400);
    }

    const partnerKey = request.headers.get("X-SBK-Partner-Key");
    const adminKey = request.headers.get("X-SBK-Admin-Key");
    const isAdmin = env.SBK_ADMIN_KEY && adminKey === env.SBK_ADMIN_KEY;
    const isOwner = partnerKey && (await verifyPartnerAuthToken(partnerKey, partnerId, env));

    if (!isAdmin && !isOwner) {
      return errorResponse("FORBIDDEN", "본인의 서비스 지역 목록만 조회할 수 있습니다.", 403);
    }

    const rows = await env.DB.prepare(
      "SELECT * FROM service_areas WHERE partner_id = ? AND is_active = 1 ORDER BY district ASC"
    ).bind(partnerId).all();

    return jsonResponse({
      ok: true,
      partner_id: partnerId,
      service_areas: rows.results || []
    });
  }

  // 5.1 Capacity Slots: GET /slots, POST /slots, DELETE /slots/:id (SBK-MINI-07)
  if (path === "/slots" && method === "GET") {
    const dbErr = checkD1Database(env);
    if (dbErr) return dbErr;

    const partnerId = url.searchParams.get("partner_id");
    if (!partnerId) {
      return errorResponse("PARTNER_ID_REQUIRED", "partner_id 파라미터가 필요합니다.", 400);
    }

    const partner = await env.DB.prepare(
      "SELECT id, business_number, verification_status FROM partners WHERE id = ?"
    ).bind(partnerId).first();
    if (!partner) {
      return errorResponse("PARTNER_NOT_FOUND", "파트너 정보를 찾을 수 없습니다.", 404);
    }

    // SBK-FIX-A (P0-01): Auth & Ownership Check via secure token header only
    const partnerKey = request.headers.get("X-SBK-Partner-Key");
    const adminKey = request.headers.get("X-SBK-Admin-Key");
    const isAdmin = env.SBK_ADMIN_KEY && adminKey === env.SBK_ADMIN_KEY;
    const isOwner = partnerKey && (await verifyPartnerAuthToken(partnerKey, partnerId, env));

    if (!isAdmin && !isOwner) {
      return errorResponse("FORBIDDEN", "본인 소유의 파트너 슬롯만 조회할 수 있습니다.", 403);
    }

    const rows = await env.DB.prepare(
      "SELECT * FROM capacity_slots WHERE partner_id = ? AND status != 'CANCELLED' ORDER BY slot_date ASC, time_window ASC"
    ).bind(partnerId).all();

    return jsonResponse({
      ok: true,
      partner_id: partnerId,
      slots: rows.results || []
    });
  }

  if (path === "/slots" && method === "POST") {
    const dbErr = checkD1Database(env);
    if (dbErr) return dbErr;

    try {
      const body = await request.json();
      const partnerId = body.partner_id;
      const slotDate = (body.slot_date || "").trim();
      const timeWindow = body.time_window || "MORNING";
      const maxCapacity = Number(body.max_capacity) || 1;

      if (!partnerId) {
        return errorResponse("PARTNER_ID_REQUIRED", "partner_id가 필요합니다.", 400);
      }
      if (!slotDate || !/^\d{4}-\d{2}-\d{2}$/.test(slotDate)) {
        return errorResponse("INVALID_DATE", "slot_date는 YYYY-MM-DD 형식이어야 합니다.", 400);
      }
      if (!["MORNING", "AFTERNOON", "ALL_DAY"].includes(timeWindow)) {
        return errorResponse("INVALID_TIME_WINDOW", "time_window는 MORNING, AFTERNOON, ALL_DAY 중 하나여야 합니다.", 400);
      }

      const partner = await env.DB.prepare(
        "SELECT id, business_number, verification_status FROM partners WHERE id = ?"
      ).bind(partnerId).first();
      if (!partner) {
        return errorResponse("PARTNER_NOT_FOUND", "파트너 정보를 찾을 수 없습니다.", 404);
      }

      // Check partner approval status (B06 defense)
      if (partner.verification_status !== "APPROVED") {
        return errorResponse("UNAPPROVED_PARTNER", "관리자 서류 심사가 완료된 승인 파트너만 가용 슬롯을 등록할 수 있습니다.", 403);
      }

      // SBK-FIX-A (P0-01): Auth & Ownership Check via secure token header only
      const partnerKey = request.headers.get("X-SBK-Partner-Key");
      const adminKey = request.headers.get("X-SBK-Admin-Key");
      const isAdmin = env.SBK_ADMIN_KEY && adminKey === env.SBK_ADMIN_KEY;
      const isOwner = partnerKey && (await verifyPartnerAuthToken(partnerKey, partnerId, env));

      if (!isAdmin && !isOwner) {
        return errorResponse("FORBIDDEN", "본인 소유의 파트너 계정만 슬롯을 등록할 수 있습니다.", 403);
      }

      // KST 24~72h window validation
      const kstNow = new Date(Date.now() + 9 * 3600 * 1000);
      const todayKstStr = kstNow.toISOString().split("T")[0];
      const todayKstMs = new Date(todayKstStr + "T00:00:00+09:00").getTime();
      const slotDateMs = new Date(slotDate + "T00:00:00+09:00").getTime();
      const diffDays = Math.round((slotDateMs - todayKstMs) / (24 * 3600 * 1000));
      if (diffDays < 1 || diffDays > 4) {
        return errorResponse("OUT_OF_RUSH_WINDOW", "급행 슬롯은 24~72시간 이내(내일~4일 이내)만 등록 가능합니다.", 400);
      }

      // Conflict / Overlap check (1 Team 1 Job / 409 Conflict)
      if (timeWindow === "ALL_DAY") {
        const overlapping = await env.DB.prepare(
          "SELECT id, time_window, status FROM capacity_slots WHERE partner_id = ? AND slot_date = ? AND status IN ('AVAILABLE', 'OFFERED', 'RESERVED')"
        ).bind(partnerId, slotDate).first();
        if (overlapping) {
          return errorResponse("SLOT_DUPLICATE", `해당 일시(${slotDate})에 이미 ${overlapping.time_window} 슬롯이 존재하여 종일(ALL_DAY) 슬롯을 등록할 수 없습니다.`, 409);
        }
      } else {
        const overlapping = await env.DB.prepare(
          "SELECT id, time_window, status FROM capacity_slots WHERE partner_id = ? AND slot_date = ? AND (time_window = ? OR time_window = 'ALL_DAY') AND status IN ('AVAILABLE', 'OFFERED', 'RESERVED')"
        ).bind(partnerId, slotDate, timeWindow).first();
        if (overlapping) {
          return errorResponse("SLOT_DUPLICATE", `해당 일시(${slotDate})에 이미 ${overlapping.time_window} 슬롯이 존재하여 중복/충돌 등록할 수 없습니다.`, 409);
        }
      }

      // Check if re-activating previously cancelled slot
      const existingSlot = await env.DB.prepare(
        "SELECT id, status FROM capacity_slots WHERE partner_id = ? AND slot_date = ? AND time_window = ?"
      ).bind(partnerId, slotDate, timeWindow).first();

      const nowIso = new Date().toISOString();
      if (existingSlot && existingSlot.status === "CANCELLED") {
        await env.DB.prepare(
          "UPDATE capacity_slots SET status = 'AVAILABLE', updated_at = ? WHERE id = ?"
        ).bind(nowIso, existingSlot.id).run();
        return jsonResponse({
          ok: true,
          slot_id: existingSlot.id,
          partner_id: partnerId,
          slot_date: slotDate,
          time_window: timeWindow,
          status: "AVAILABLE",
          message: "취소되었던 슬롯이 재활성화되었습니다."
        }, 200);
      }

      const slotId = "slot_" + crypto.randomUUID();
      await env.DB.prepare(
        `INSERT INTO capacity_slots (id, partner_id, slot_date, time_window, max_capacity, allocated_count, status, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, 0, 'AVAILABLE', ?, ?)`
      ).bind(slotId, partnerId, slotDate, timeWindow, maxCapacity, nowIso, nowIso).run();

      await env.DB.prepare(
        `INSERT INTO audit_log (id, actor_id, action, resource_type, resource_id, details, created_at)
        VALUES (?, ?, 'SLOT_CREATED', 'SLOT', ?, ?, ?)`
      ).bind(
        "aud_" + crypto.randomUUID(),
        partnerId,
        slotId,
        JSON.stringify({ slot_date: slotDate, time_window: timeWindow }),
        nowIso
      ).run();

      return jsonResponse({
        ok: true,
        slot_id: slotId,
        partner_id: partnerId,
        slot_date: slotDate,
        time_window: timeWindow,
        status: "AVAILABLE",
        message: "가용 슬롯이 D1에 정상 등록되었습니다."
      }, 201);
    } catch (err) {
      return errorResponse("SERVER_ERROR", err.message, 500);
    }
  }

  const matchSlotDelete = path.match(/^\/slots\/([a-zA-Z0-9_-]+)$/);
  if (matchSlotDelete && method === "DELETE") {
    const dbErr = checkD1Database(env);
    if (dbErr) return dbErr;

    const slotId = matchSlotDelete[1];
    const partnerKey = request.headers.get("X-SBK-Partner-Key");
    const adminKey = request.headers.get("X-SBK-Admin-Key");

    const slot = await env.DB.prepare("SELECT * FROM capacity_slots WHERE id = ?").bind(slotId).first();
    if (!slot) {
      return errorResponse("SLOT_NOT_FOUND", "해당 슬롯을 찾을 수 없습니다.", 404);
    }

    const isAdmin = env.SBK_ADMIN_KEY && adminKey === env.SBK_ADMIN_KEY;
    const isOwner = partnerKey && (await verifyPartnerAuthToken(partnerKey, slot.partner_id, env));

    if (!isAdmin && !isOwner) {
      return errorResponse("FORBIDDEN", "본인 소유의 슬롯만 취소할 수 있습니다.", 403);
    }

    if (slot.status !== "AVAILABLE") {
      return errorResponse("SLOT_CANNOT_DELETE", `현재 ${slot.status} 상태인 슬롯은 삭제/취소할 수 없습니다.`, 409);
    }

    const nowIso = new Date().toISOString();
    await env.DB.prepare(
      "UPDATE capacity_slots SET status = 'CANCELLED', updated_at = ? WHERE id = ?"
    ).bind(nowIso, slotId).run();

    await env.DB.prepare(
      `INSERT INTO audit_log (id, actor_id, action, resource_type, resource_id, details, created_at)
      VALUES (?, ?, 'SLOT_CANCELLED', 'SLOT', ?, ?, ?)`
    ).bind(
      "aud_" + crypto.randomUUID(),
      slot.partner_id,
      slotId,
      JSON.stringify({ reason: "PARTNER_WITHDRAWAL" }),
      nowIso
    ).run();

    return jsonResponse({
      ok: true,
      slot_id: slotId,
      status: "CANCELLED",
      message: "가용 슬롯이 성공적으로 취소되었습니다."
    });
  }

  // 5.2 Assignment Offers: GET /offers, POST /offers/:id/accept, POST /offers/:id/reject (SBK-MINI-08)
  if (path === "/offers" && method === "GET") {
    const dbErr = checkD1Database(env);
    if (dbErr) return dbErr;

    const partnerId = url.searchParams.get("partner_id");
    if (!partnerId) {
      return errorResponse("PARTNER_ID_REQUIRED", "partner_id 파라미터가 필요합니다.", 400);
    }

    const partner = await env.DB.prepare("SELECT id, business_number FROM partners WHERE id = ?").bind(partnerId).first();
    if (!partner) {
      return errorResponse("PARTNER_NOT_FOUND", "파트너 정보를 찾을 수 없습니다.", 404);
    }

    const partnerKey = request.headers.get("X-SBK-Partner-Key");
    const adminKey = request.headers.get("X-SBK-Admin-Key");
    const isAdmin = env.SBK_ADMIN_KEY && adminKey === env.SBK_ADMIN_KEY;
    const isOwner = partnerKey && (await verifyPartnerAuthToken(partnerKey, partnerId, env));

    if (!isAdmin && !isOwner) {
      return errorResponse("FORBIDDEN", "본인에게 배정된 오퍼 목록만 조회할 수 있습니다.", 403);
    }

    const rows = await env.DB.prepare(
      "SELECT * FROM assignment_offers WHERE partner_id = ? ORDER BY created_at DESC"
    ).bind(partnerId).all();

    return jsonResponse({
      ok: true,
      partner_id: partnerId,
      offers: rows.results || []
    });
  }

  const matchOfferAccept = path.match(/^\/offers\/([a-zA-Z0-9_-]+)\/accept$/);
  if (matchOfferAccept && method === "POST") {
    const dbErr = checkD1Database(env);
    if (dbErr) return dbErr;

    const offerId = matchOfferAccept[1];
    const body = await request.json().catch(() => ({}));
    const partnerId = body.partner_id;
    const partnerKey = request.headers.get("X-SBK-Partner-Key");
    const adminKey = request.headers.get("X-SBK-Admin-Key");

    if (!partnerId) {
      return errorResponse("PARTNER_ID_REQUIRED", "partner_id가 필요합니다.", 400);
    }

    const offer = await env.DB.prepare("SELECT * FROM assignment_offers WHERE id = ?").bind(offerId).first();
    if (!offer) {
      return errorResponse("OFFER_NOT_FOUND", "해당 오퍼를 찾을 수 없습니다.", 404);
    }

    const partner = await env.DB.prepare(
      "SELECT id, business_number, verification_status FROM partners WHERE id = ?"
    ).bind(partnerId).first();
    if (!partner) {
      return errorResponse("PARTNER_NOT_FOUND", "파트너 정보를 찾을 수 없습니다.", 404);
    }

    if (partner.verification_status !== "APPROVED") {
      return errorResponse("UNAPPROVED_PARTNER", "미승인 파트너는 오퍼를 수락할 수 없습니다.", 403);
    }

    const isAdmin = env.SBK_ADMIN_KEY && adminKey === env.SBK_ADMIN_KEY;
    const isOwner = partnerKey && (await verifyPartnerAuthToken(partnerKey, partnerId, env));

    if (offer.partner_id !== partnerId || (!isAdmin && !isOwner)) {
      return errorResponse("FORBIDDEN", "본인에게 배정된 오퍼만 수락할 수 있습니다.", 403);
    }

    if (offer.status !== "PENDING") {
      return errorResponse("ALREADY_PROCESSED", `오퍼가 이미 ${offer.status} 상태입니다.`, 409);
    }

    const nowIso = new Date().toISOString();
    if (nowIso > offer.expires_at) {
      await env.DB.prepare("UPDATE assignment_offers SET status = 'EXPIRED', updated_at = ? WHERE id = ?").bind(nowIso, offerId).run();
      await env.DB.prepare("UPDATE capacity_slots SET status = 'AVAILABLE', updated_at = ? WHERE id = ?").bind(nowIso, offer.slot_id).run();
      await env.DB.prepare("UPDATE inquiries SET status = 'PENDING_MATCH', updated_at = ? WHERE id = ?").bind(nowIso, offer.inquiry_id).run();
      return errorResponse("OFFER_EXPIRED", "15분 수락 제한시간이 만료되었습니다.", 410);
    }

    // Atomic conditional slot locking (WHERE status = 'OFFERED')
    const slotLock = await env.DB.prepare(
      "UPDATE capacity_slots SET status = 'RESERVED', allocated_count = 1, updated_at = ? WHERE id = ? AND status = 'OFFERED'"
    ).bind(nowIso, offer.slot_id).run();

    if (!slotLock.meta || slotLock.meta.changes === 0) {
      return errorResponse("CONCURRENCY_CONFLICT", "슬롯이 이미 다른 요청에 배정되었거나 상태가 변경되었습니다.", 409);
    }

    // Atomic conditional offer status update (WHERE status = 'PENDING')
    const offerUpdate = await env.DB.prepare(
      "UPDATE assignment_offers SET status = 'ACCEPTED', updated_at = ? WHERE id = ? AND status = 'PENDING'"
    ).bind(nowIso, offerId).run();

    if (!offerUpdate.meta || offerUpdate.meta.changes === 0) {
      return errorResponse("ALREADY_PROCESSED", "오퍼가 이미 처리되었거나 동시 수락 경합이 발생했습니다.", 409);
    }

    await env.DB.prepare("UPDATE inquiries SET status = 'MATCHED', updated_at = ? WHERE id = ?").bind(nowIso, offer.inquiry_id).run();

    await env.DB.prepare(
      "INSERT INTO partner_responses (id, offer_id, partner_id, response_type, responded_at) VALUES (?, ?, ?, 'ACCEPT', ?)"
    ).bind("resp_" + crypto.randomUUID(), offerId, partnerId, nowIso).run();

    const matchId = "mat_" + crypto.randomUUID();
    await env.DB.prepare(
      `INSERT INTO matches (id, inquiry_id, partner_id, slot_id, status, estimated_amount, created_at, updated_at)
       VALUES (?, ?, ?, ?, 'CONFIRMED', 250000, ?, ?)`
    ).bind(matchId, offer.inquiry_id, partnerId, offer.slot_id, nowIso, nowIso).run();

    // SBK-FIX-B (P0-05): Fetch inquiry for secure customer contact handoff
    const inq = await env.DB.prepare(
      `SELECT id, tracking_token, district, neighborhood, housing_type, area_pyeong,
              requested_date, time_window, customer_name_encrypted, customer_phone_encrypted,
              status
       FROM inquiries WHERE id = ?`
    ).bind(offer.inquiry_id).first();

    let decryptedName = "";
    let decryptedPhone = "";
    if (inq) {
      try {
        decryptedName = await decryptPii(inq.customer_name_encrypted, env);
      } catch (_) {
        decryptedName = "고객 (복호화 실패)";
      }
      try {
        decryptedPhone = await decryptPii(inq.customer_phone_encrypted, env);
      } catch (_) {
        decryptedPhone = "010-****-****";
      }
    }

    // SBK-FIX-B (P0-05): Audit log for secure PII contact handoff to accepted partner
    await env.DB.prepare(
      `INSERT INTO audit_log (id, actor_id, action, resource_type, resource_id, details, created_at)
       VALUES (?, ?, 'PII_HANDOFF', 'INQUIRY', ?, ?, ?)`
    ).bind(
      "aud_" + crypto.randomUUID(),
      partnerId,
      offer.inquiry_id,
      JSON.stringify({
        match_id: matchId,
        offer_id: offerId,
        recipient_partner_id: partnerId,
        masked_phone: maskPhone(decryptedPhone),
        handoff_type: "OFFER_ACCEPT"
      }),
      nowIso
    ).run();

    await env.DB.prepare(
      `INSERT INTO audit_log (id, actor_id, action, resource_type, resource_id, details, created_at)
       VALUES (?, ?, 'OFFER_ACCEPTED', 'MATCH', ?, ?, ?)`
    ).bind("aud_" + crypto.randomUUID(), partnerId, matchId, JSON.stringify({ offer_id: offerId, slot_id: offer.slot_id }), nowIso).run();

    return jsonResponse({
      ok: true,
      match_id: matchId,
      offer_id: offerId,
      status: "CONFIRMED",
      customer_contact: {
        name: decryptedName,
        phone: decryptedPhone,
        district: inq ? inq.district : "",
        neighborhood: inq ? inq.neighborhood : "",
        housing_type: inq ? inq.housing_type : "",
        area_pyeong: inq ? inq.area_pyeong : 0,
        requested_date: inq ? inq.requested_date : "",
        time_window: inq ? inq.time_window : ""
      },
      instructions: "30분 이내에 고객님께 유선 연락하여 세부 일정과 현장 주소지를 확인하세요.",
      message: "오퍼가 정상 수락되어 매칭이 확정되었습니다. 고객 연락처가 안전하게 인계되었습니다."
    }, 200);
  }

  const matchOfferReject = path.match(/^\/offers\/([a-zA-Z0-9_-]+)\/reject$/);
  if (matchOfferReject && method === "POST") {
    const dbErr = checkD1Database(env);
    if (dbErr) return dbErr;

    const offerId = matchOfferReject[1];
    const body = await request.json().catch(() => ({}));
    const partnerId = body.partner_id;
    const reason = body.reason || "PARTNER_MANUAL_REJECT";
    const partnerKey = request.headers.get("X-SBK-Partner-Key");
    const adminKey = request.headers.get("X-SBK-Admin-Key");

    if (!partnerId) {
      return errorResponse("PARTNER_ID_REQUIRED", "partner_id가 필요합니다.", 400);
    }

    const offer = await env.DB.prepare("SELECT * FROM assignment_offers WHERE id = ?").bind(offerId).first();
    if (!offer) {
      return errorResponse("OFFER_NOT_FOUND", "해당 오퍼를 찾을 수 없습니다.", 404);
    }

    const partner = await env.DB.prepare("SELECT id, business_number FROM partners WHERE id = ?").bind(partnerId).first();
    const isAdmin = env.SBK_ADMIN_KEY && adminKey === env.SBK_ADMIN_KEY;
    const isOwner = partnerKey && (await verifyPartnerAuthToken(partnerKey, partnerId, env));

    if (offer.partner_id !== partnerId || (!isAdmin && !isOwner)) {
      return errorResponse("FORBIDDEN", "본인에게 배정된 오퍼만 거절할 수 있습니다.", 403);
    }

    if (offer.status !== "PENDING") {
      return errorResponse("ALREADY_PROCESSED", `오퍼가 이미 ${offer.status} 상태입니다.`, 409);
    }

    const nowIso = new Date().toISOString();
    await env.DB.prepare("UPDATE assignment_offers SET status = 'REJECTED', updated_at = ? WHERE id = ? AND status = 'PENDING'").bind(nowIso, offerId).run();
    await env.DB.prepare("UPDATE capacity_slots SET status = 'AVAILABLE', updated_at = ? WHERE id = ?").bind(nowIso, offer.slot_id).run();
    await env.DB.prepare(
      "INSERT INTO partner_responses (id, offer_id, partner_id, response_type, rejection_reason, responded_at) VALUES (?, ?, ?, 'REJECT', ?, ?)"
    ).bind("resp_" + crypto.randomUUID(), offerId, partnerId, reason, nowIso).run();

    await env.DB.prepare("UPDATE inquiries SET status = 'PENDING_MATCH', updated_at = ? WHERE id = ?").bind(nowIso, offer.inquiry_id).run();

    // Trigger sequential re-routing to Rank 2
    const nextOffer = await dispatchCandidateOffer(offer.inquiry_id, env);

    return jsonResponse({
      ok: true,
      offer_id: offerId,
      status: "REJECTED",
      reassigned_offer: nextOffer,
      message: "오퍼가 거절되었으며 다음 순위 파트너로 자동 재배정됩니다."
    }, 200);
  }

  // 5.3 Partner Matches List: GET /matches (SBK-FIX-B)
  if (path === "/matches" && method === "GET") {
    const dbErr = checkD1Database(env);
    if (dbErr) return dbErr;

    const partnerId = url.searchParams.get("partner_id");
    if (!partnerId) {
      return errorResponse("PARTNER_ID_REQUIRED", "partner_id 파라미터가 필요합니다.", 400);
    }

    const partnerKey = request.headers.get("X-SBK-Partner-Key");
    const adminKey = request.headers.get("X-SBK-Admin-Key");
    const isAdmin = env.SBK_ADMIN_KEY && adminKey === env.SBK_ADMIN_KEY;
    const isOwner = partnerKey && (await verifyPartnerAuthToken(partnerKey, partnerId, env));

    if (!isAdmin && !isOwner) {
      return errorResponse("FORBIDDEN", "본인의 매칭 목록만 조회할 수 있습니다.", 403);
    }

    const rows = await env.DB.prepare(
      `SELECT m.*, i.district, i.neighborhood, i.housing_type, i.area_pyeong, i.requested_date, i.time_window
       FROM matches m
       JOIN inquiries i ON m.inquiry_id = i.id
       WHERE m.partner_id = ?
       ORDER BY m.created_at DESC`
    ).bind(partnerId).all();

    return jsonResponse({
      ok: true,
      partner_id: partnerId,
      matches: rows.results || []
    });
  }

  // SBK-FIX-B (P0-05): Secure Customer Contact Retrieval Endpoint GET /matches/:id/contact
  const matchContact = path.match(/^\/matches\/([a-zA-Z0-9_-]+)\/contact$/);
  if (matchContact && method === "GET") {
    const dbErr = checkD1Database(env);
    if (dbErr) return dbErr;

    const matchId = matchContact[1];
    const partnerKey = request.headers.get("X-SBK-Partner-Key");
    const adminKey = request.headers.get("X-SBK-Admin-Key");
    const isAdmin = env.SBK_ADMIN_KEY && adminKey === env.SBK_ADMIN_KEY;

    const match = await env.DB.prepare("SELECT * FROM matches WHERE id = ?").bind(matchId).first();
    if (!match) {
      return errorResponse("MATCH_NOT_FOUND", "일치하는 매칭 정보를 찾을 수 없습니다.", 404);
    }

    const partnerId = match.partner_id;
    const isOwner = partnerKey && (await verifyPartnerAuthToken(partnerKey, partnerId, env));

    if (!isAdmin && !isOwner) {
      return errorResponse("FORBIDDEN", "배정된 파트너만 해당 매칭의 고객 연락처를 열람할 수 있습니다.", 403);
    }

    if (match.status === "CANCELLED") {
      return errorResponse("MATCH_CANCELLED", "취소된 매칭의 고객 연락처는 열람할 수 없습니다.", 400);
    }

    const inq = await env.DB.prepare("SELECT * FROM inquiries WHERE id = ?").bind(match.inquiry_id).first();
    if (!inq) {
      return errorResponse("INQUIRY_NOT_FOUND", "고객 신청 내역을 찾을 수 없습니다.", 404);
    }

    if (inq.status === "CANCELLED" || (inq.customer_phone_encrypted && inq.customer_phone_encrypted.startsWith("ERASED_"))) {
      return errorResponse("CUSTOMER_DATA_PURGED", "고객 요청에 의해 개인정보가 영구 파기(Tombstone)되었습니다.", 410);
    }

    let rawName = "";
    let rawPhone = "";
    try {
      rawName = await decryptPii(inq.customer_name_encrypted, env);
      rawPhone = await decryptPii(inq.customer_phone_encrypted, env);
    } catch (err) {
      return errorResponse("DECRYPTION_FAILED", "연락처 복호화 중 오류가 발생했습니다.", 500);
    }

    const nowIso = new Date().toISOString();
    await env.DB.prepare(
      `INSERT INTO audit_log (id, actor_id, action, resource_type, resource_id, details, created_at)
       VALUES (?, ?, 'PII_CONTACT_VIEW', 'MATCH', ?, ?, ?)`
    ).bind(
      "aud_" + crypto.randomUUID(),
      partnerId,
      matchId,
      JSON.stringify({
        match_id: matchId,
        inquiry_id: match.inquiry_id,
        masked_phone: maskPhone(rawPhone),
        actor: isAdmin ? "ADMIN" : "PARTNER"
      }),
      nowIso
    ).run();

    return jsonResponse({
      ok: true,
      match_id: matchId,
      inquiry_id: match.inquiry_id,
      status: match.status,
      customer_contact: {
        name: rawName,
        phone: rawPhone,
        district: inq.district,
        neighborhood: inq.neighborhood,
        housing_type: inq.housing_type,
        area_pyeong: inq.area_pyeong,
        requested_date: inq.requested_date,
        time_window: inq.time_window
      },
      instructions: "30분 이내에 고객님께 유선 연락하여 세부 일정과 현장 주소지를 확인하세요."
    }, 200);
  }

  // 6. Match Fulfillment: POST /matches/:id/fulfill (SBK-FIX-06 & SBK-MINI-10: Strict Partner Authentication & Evidence Validation)
  const matchFulfill = path.match(/^\/matches\/([a-zA-Z0-9_-]+)\/fulfill$/);
  if (matchFulfill && method === "POST") {
    const dbErr = checkD1Database(env);
    if (dbErr) return dbErr;

    try {
      const matchId = matchFulfill[1];
      const body = await request.json().catch(() => ({}));
      const partnerId = body.partner_id;
      const partnerKey = request.headers.get("X-SBK-Partner-Key") || (request.headers.get("Authorization") || "").replace(/^Bearer\s+/i, "");
      const finalAmount = Number(body.final_amount) || 0;
      const photoUrls = body.photo_urls;

      if (!partnerId) {
        return errorResponse("PARTNER_ID_REQUIRED", "파트너 ID가 필요합니다.", 400);
      }

      // SBK-MINI-10: Strictly authenticate partner via secret key or admin override
      const partner = await env.DB.prepare(
        "SELECT id, business_number, verification_status FROM partners WHERE id = ?"
      ).bind(partnerId).first();

      if (!partner || partner.verification_status !== "APPROVED") {
        return errorResponse("UNAPPROVED_PARTNER", "승인된 파트너만 시공 완료를 접수할 수 있습니다.", 403);
      }
      const isPartnerAuth = partnerKey && (await verifyPartnerAuthToken(partnerKey, partnerId, env));
      const isAdminAuth = env.SBK_ADMIN_KEY && partnerKey === env.SBK_ADMIN_KEY;
      if (!isPartnerAuth && !isAdminAuth) {
        return errorResponse("UNAUTHORIZED", "파트너 인증 키(X-SBK-Partner-Key)가 유효하지 않습니다.", 401);
      }

      // SBK-MINI-10: Evidence validation (minimum 2 valid non-empty photo strings)
      if (!photoUrls || !Array.isArray(photoUrls) || photoUrls.length < 2 || photoUrls.some((p) => typeof p !== "string" || p.trim().length === 0)) {
        return errorResponse("INSUFFICIENT_EVIDENCE", "현장 시공 전후 사진 2장 이상의 유효한 증빙이 필수적입니다.", 400);
      }
      if (finalAmount <= 0) {
        return errorResponse("INVALID_AMOUNT", "최종 정산 금액은 0원보다 커야 합니다.", 400);
      }

      const match = await env.DB.prepare("SELECT * FROM matches WHERE id = ?").bind(matchId).first();
      if (!match) {
        return errorResponse("MATCH_NOT_FOUND", "일치하는 매칭 정보를 찾을 수 없습니다.", 404);
      }
      if (match.partner_id !== partnerId) {
        return errorResponse("FORBIDDEN", "배정된 파트너만 해당 매칭의 완료 보고를 제출할 수 있습니다.", 403);
      }
      if (match.status === "COMPLETED" || match.status === "BILLABLE") {
        return errorResponse("ALREADY_FULFILLED", "이미 완료 보고가 접수된 매칭입니다.", 409);
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

  // 7. Match Confirmation: POST /matches/:id/confirm (SBK-FIX-06 & SBK-MINI-10: Customer Token Auth & Evidence Gate)
  const matchConfirm = path.match(/^\/matches\/([a-zA-Z0-9_-]+)\/confirm$/);
  if (matchConfirm && method === "POST") {
    const dbErr = checkD1Database(env);
    if (dbErr) return dbErr;

    try {
      const matchId = matchConfirm[1];
      const body = await request.json().catch(() => ({}));
      const rating = Number(body.rating) || 5;

      const customerToken = request.headers.get("X-SBK-Customer-Token") || body.tracking_token || url.searchParams.get("token");
      const adminKey = request.headers.get("X-SBK-Admin-Key");
      const isAdmin = adminKey && env.SBK_ADMIN_KEY && adminKey === env.SBK_ADMIN_KEY;

      const matchWithInq = await env.DB.prepare(
        `SELECT m.*, i.tracking_token, i.status AS inq_status, i.attribution_partner_id
         FROM matches m
         JOIN inquiries i ON m.inquiry_id = i.id
         WHERE m.id = ?`
      ).bind(matchId).first();

      if (!matchWithInq) {
        return errorResponse("MATCH_NOT_FOUND", "매칭을 찾을 수 없습니다.", 404);
      }

      // SBK-MINI-10: Customer must supply their valid tracking token (or admin override)
      if (!isAdmin && (!customerToken || customerToken !== matchWithInq.tracking_token)) {
        return errorResponse("UNAUTHORIZED", "고객 조회 토큰(X-SBK-Customer-Token) 또는 관리자 권한이 필요합니다.", 401);
      }

      if (matchWithInq.status === "DISPUTED") {
        return errorResponse("MATCH_DISPUTED", "분쟁 진행 중인 건은 완료 확인할 수 없습니다.", 400);
      }
      if (matchWithInq.status === "BILLABLE") {
        return errorResponse("ALREADY_CONFIRMED", "이미 고객 확인 및 정산 완료된 매칭입니다.", 409);
      }
      if (matchWithInq.status !== "COMPLETED") {
        return errorResponse("INVALID_MATCH_STATUS", "시공 완료 보고(COMPLETED)가 접수되지 않은 매칭은 승인할 수 없습니다.", 400);
      }

      // SBK-MINI-10: Validate that valid fulfillment evidence with 2+ photos exists before BILLABLE transition
      const evidence = await env.DB.prepare(
        "SELECT * FROM fulfillment_evidence WHERE match_id = ? ORDER BY created_at DESC LIMIT 1"
      ).bind(matchId).first();

      if (!evidence) {
        return errorResponse("EVIDENCE_NOT_FOUND", "시공 증빙 자료가 등록되지 않았습니다.", 400);
      }

      let photos = [];
      try {
        photos = JSON.parse(evidence.photo_urls);
      } catch (e) {
        photos = [];
      }
      if (!Array.isArray(photos) || photos.length < 2) {
        return errorResponse("INSUFFICIENT_EVIDENCE", "유효한 시공 사진 증빙(2장 이상)이 없어 BILLABLE로 전이할 수 없습니다.", 400);
      }

      const nowIso = new Date().toISOString();
      await env.DB.prepare(
        "UPDATE fulfillment_evidence SET verification_status = 'VERIFIED', customer_confirmed = 1, customer_survey_rating = ?, updated_at = ? WHERE match_id = ?"
      ).bind(rating, nowIso, matchId).run();

      await env.DB.prepare(
        "UPDATE matches SET status = 'BILLABLE', updated_at = ? WHERE id = ?"
      ).bind(nowIso, matchId).run();

      // Idempotent Ledger Event insertion
      const finalAmount = matchWithInq.final_amount || 250000;
      const platformFee = Math.floor(finalAmount * 0.08); // 8%
      const platformVat = Math.floor(platformFee * 0.10); // 10% VAT
      const referralFee = Math.floor(platformFee * 0.35); // 35% of 8%
      const referralVat = Math.floor(referralFee * 0.10); // 10% VAT

      const existingLedger = await env.DB.prepare(
        "SELECT id FROM ledger_events WHERE match_id = ? AND event_type = 'REVENUE_ACCRUED'"
      ).bind(matchId).first();

      if (!existingLedger) {
        await env.DB.prepare(
          `INSERT INTO ledger_events (id, match_id, event_type, amount, debit_account, credit_account, description, created_at)
           VALUES (?, ?, 'REVENUE_ACCRUED', ?, 'ACCOUNTS_RECEIVABLE_PARTNER', 'PLATFORM_COMMISSION_REVENUE', 'B2B 8% 성과수수료 공급가액', ?)`
        ).bind("led_" + crypto.randomUUID(), matchId, platformFee, nowIso).run();

        await env.DB.prepare(
          `INSERT INTO ledger_events (id, match_id, event_type, amount, debit_account, credit_account, description, created_at)
           VALUES (?, ?, 'VAT_OUTPUT_ACCRUED', ?, 'ACCOUNTS_RECEIVABLE_PARTNER', 'VAT_OUTPUT_PAYABLE', 'B2B 성과수수료 부가세 10%', ?)`
        ).bind("led_" + crypto.randomUUID(), matchId, platformVat, nowIso).run();

        if (matchWithInq.attribution_partner_id) {
          await env.DB.prepare(
            `INSERT INTO ledger_events (id, match_id, event_type, amount, debit_account, credit_account, description, created_at)
             VALUES (?, ?, 'REFERRAL_ALLOCATED', ?, 'REFERRAL_COMMISSION_EXPENSE', 'ACCOUNTS_PAYABLE_REFERRER', '원 유입업체 35% 배분 공급가액', ?)`
          ).bind("led_" + crypto.randomUUID(), matchId, referralFee, nowIso).run();

          await env.DB.prepare(
            `INSERT INTO ledger_events (id, match_id, event_type, amount, debit_account, credit_account, description, created_at)
             VALUES (?, ?, 'REFERRAL_VAT_ACCRUED', ?, 'VAT_INPUT_RECEIVABLE', 'ACCOUNTS_PAYABLE_REFERRER', '원 유입업체 리퍼럴 부가세 10%', ?)`
          ).bind("led_" + crypto.randomUUID(), matchId, referralVat, nowIso).run();
        }
      }

      // Audit Log
      await env.DB.prepare(
        `INSERT INTO audit_log (id, actor_id, action, resource_type, resource_id, details, created_at)
         VALUES (?, 'CUSTOMER', 'COMPLETION_CONFIRMED', 'MATCH', ?, ?, ?)`
      ).bind(
        "aud_" + crypto.randomUUID(),
        matchId,
        JSON.stringify({ rating, notes: body.notes || null }),
        nowIso
      ).run();

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

    // SBK-MINI-11: 4-tier distinct Financial KPIs (GET /admin/kpis)
    if (path === "/admin/kpis" && method === "GET") {
      const dbErr = checkD1Database(env);
      if (dbErr) return dbErr;

      const billingPeriod = url.searchParams.get("billing_period") || "2026-11";

      // 1. Estimated (in-flight inquiries)
      const inqRow = await env.DB.prepare(
        "SELECT count(*) AS cnt FROM inquiries WHERE status IN ('PENDING_MATCH', 'OFFERED', 'MATCHED')"
      ).first();
      const inqCnt = (inqRow && inqRow.cnt) ? Number(inqRow.cnt) : 0;
      const estimatedRevenue = inqCnt * 20000;

      // 2. Verified Billable (completed & verified matches)
      const verifiedRes = await env.DB.prepare(
        "SELECT final_amount FROM matches WHERE status IN ('VERIFIED', 'BILLABLE', 'COMPLETED') AND final_amount IS NOT NULL"
      ).all();
      const verifiedBillable = (verifiedRes.results || []).reduce(
        (sum, r) => sum + Math.floor((Number(r.final_amount) || 0) * 0.08), 0
      );

      // 3. Invoiced Revenue (issued/paid invoices for period)
      const invRow = await env.DB.prepare(
        "SELECT sum(total_platform_fee) AS sum_fee FROM invoices WHERE billing_period = ? AND status IN ('ISSUED', 'PAID')"
      ).bind(billingPeriod).first();
      const invoicedRevenue = (invRow && invRow.sum_fee) ? Number(invRow.sum_fee) : 0;

      // 4. Settled Cash (paid invoices only)
      const paidRow = await env.DB.prepare(
        "SELECT sum(total_platform_fee) AS sum_fee FROM invoices WHERE billing_period = ? AND status = 'PAID'"
      ).bind(billingPeriod).first();
      const settledCash = (paidRow && paidRow.sum_fee) ? Number(paidRow.sum_fee) : 0;

      return jsonResponse({
        ok: true,
        billing_period: billingPeriod,
        estimated_revenue_krw: estimatedRevenue,
        verified_billable_krw: verifiedBillable,
        invoiced_revenue_krw: invoicedRevenue,
        settled_cash_krw: settledCash,
      });
    }

    // SBK-MINI-11: Double-Entry Ledger Event Audit (GET /admin/ledger)
    if (path === "/admin/ledger" && method === "GET") {
      const dbErr = checkD1Database(env);
      if (dbErr) return dbErr;

      const matchId = url.searchParams.get("match_id");
      let rows;
      if (matchId) {
        rows = await env.DB.prepare(
          "SELECT * FROM ledger_events WHERE match_id = ? ORDER BY created_at ASC"
        ).bind(matchId).all();
      } else {
        rows = await env.DB.prepare(
          "SELECT * FROM ledger_events ORDER BY created_at DESC LIMIT 50"
        ).all();
      }
      return jsonResponse({ ok: true, ledger_events: rows.results || [] });
    }

    // SBK-MINI-11: Generate Monthly B2B Invoice (POST /admin/invoices/generate)
    if (path === "/admin/invoices/generate" && method === "POST") {
      const dbErr = checkD1Database(env);
      if (dbErr) return dbErr;

      const body = await request.json().catch(() => ({}));
      const partnerId = body.partner_id;
      const billingPeriod = body.billing_period;

      if (!partnerId || !billingPeriod) {
        return errorResponse("INVALID_PAYLOAD", "partner_id와 billing_period가 필수적입니다.", 400);
      }

      const partner = await env.DB.prepare("SELECT id FROM partners WHERE id = ?").bind(partnerId).first();
      if (!partner) {
        return errorResponse("PARTNER_NOT_FOUND", "파트너를 찾을 수 없습니다.", 404);
      }

      const existing = await env.DB.prepare(
        "SELECT id FROM invoices WHERE partner_id = ? AND billing_period = ?"
      ).bind(partnerId, billingPeriod).first();
      if (existing) {
        return errorResponse("INVOICE_ALREADY_EXISTS", `해당 기간(${billingPeriod})의 청구서가 이미 존재합니다.`, 409);
      }

      // Filter matches completed in this billing period with verified final amounts
      const matchesRes = await env.DB.prepare(
        `SELECT m.id, m.final_amount FROM matches m
         JOIN capacity_slots s ON m.slot_id = s.id
         WHERE m.partner_id = ?
           AND m.status IN ('VERIFIED', 'BILLABLE', 'COMPLETED')
           AND COALESCE(s.slot_date, m.created_at) LIKE ?
           AND m.final_amount IS NOT NULL AND m.final_amount > 0`
      ).bind(partnerId, `${billingPeriod}%`).all();

      const matches = matchesRes.results || [];
      const totalVolume = matches.reduce((sum, r) => sum + (Number(r.final_amount) || 0), 0);
      const platformFee = Math.floor(totalVolume * 0.08);
      const vat = Math.floor(platformFee * 0.10);
      const totalAmount = platformFee + vat;

      const invoiceId = "inv_" + crypto.randomUUID();
      const nowIso = new Date().toISOString();

      await env.DB.prepare(
        `INSERT INTO invoices (
          id, partner_id, billing_period, total_service_volume, total_platform_fee,
          vat_amount, total_invoice_amount, status, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'DRAFT', ?, ?)`
      ).bind(invoiceId, partnerId, billingPeriod, totalVolume, platformFee, vat, totalAmount, nowIso, nowIso).run();

      await env.DB.prepare(
        `INSERT INTO audit_log (id, actor_id, action, resource_type, resource_id, details, created_at)
         VALUES (?, 'ADMIN', 'INVOICE_GENERATED', 'INVOICE', ?, ?, ?)`
      ).bind(
        "aud_" + crypto.randomUUID(),
        invoiceId,
        JSON.stringify({ partner_id: partnerId, billing_period: billingPeriod, total_amount: totalAmount, matched_count: matches.length }),
        nowIso
      ).run();

      return jsonResponse({
        ok: true,
        invoice_id: invoiceId,
        partner_id: partnerId,
        billing_period: billingPeriod,
        total_service_volume: totalVolume,
        total_platform_fee: platformFee,
        vat_amount: vat,
        total_invoice_amount: totalAmount,
        status: "DRAFT",
        matched_count: matches.length
      }, 201);
    }

    // SBK-MINI-11: Formally Issue Invoice (POST /admin/invoices/:id/issue)
    const matchInvoiceIssue = path.match(/^\/admin\/invoices\/([a-zA-Z0-9_-]+)\/issue$/);
    if (matchInvoiceIssue && method === "POST") {
      const dbErr = checkD1Database(env);
      if (dbErr) return dbErr;

      const invoiceId = matchInvoiceIssue[1];
      const body = await request.json().catch(() => ({}));
      const hometaxApproval = body.hometax_approval_number ? String(body.hometax_approval_number).trim() : null;

      const inv = await env.DB.prepare("SELECT * FROM invoices WHERE id = ?").bind(invoiceId).first();
      if (!inv) {
        return errorResponse("INVOICE_NOT_FOUND", "청구서를 찾을 수 없습니다.", 404);
      }
      if (inv.status !== "DRAFT") {
        return errorResponse("INVALID_INVOICE_STATUS", "DRAFT 상태인 청구서만 발행 승인 가능합니다.", 400);
      }

      const nowIso = new Date().toISOString();
      const dueDate = new Date(Date.now() + 10 * 24 * 60 * 60 * 1000).toISOString().split("T")[0];

      let taxInvoiceNumber;
      let ntsVerified;
      if (hometaxApproval && hometaxApproval.length >= 8) {
        taxInvoiceNumber = hometaxApproval;
        ntsVerified = true;
      } else {
        const ym = new Date().toISOString().slice(0, 7).replace("-", "");
        taxInvoiceNumber = `TX-INT-${ym}-${crypto.randomUUID().slice(0, 6).toUpperCase()}`;
        ntsVerified = false;
      }

      await env.DB.prepare(
        `UPDATE invoices
         SET status = 'ISSUED', tax_invoice_number = ?, issued_at = ?, due_date = ?, updated_at = ?
         WHERE id = ?`
      ).bind(taxInvoiceNumber, nowIso, dueDate, nowIso, invoiceId).run();

      await env.DB.prepare(
        `INSERT INTO audit_log (id, actor_id, action, resource_type, resource_id, details, created_at)
         VALUES (?, 'ADMIN', 'INVOICE_ISSUED', 'INVOICE', ?, ?, ?)`
      ).bind(
        "aud_" + crypto.randomUUID(),
        invoiceId,
        JSON.stringify({ tax_invoice_number: taxInvoiceNumber, nts_verified: ntsVerified }),
        nowIso
      ).run();

      return jsonResponse({
        ok: true,
        invoice_id: invoiceId,
        status: "ISSUED",
        tax_invoice_number: taxInvoiceNumber,
        due_date: dueDate,
        nts_verified: ntsVerified,
        message: ntsVerified ? "국세청 전자세금계산서 연동 발행 완료" : "내부 관리용 청구서 발행 완료 (국세청 승인 대기)"
      }, 200);
    }

    // SBK-MINI-11: Record Verified Bank Transfer Payment (POST /admin/invoices/:id/record-payment)
    const matchInvoicePay = path.match(/^\/admin\/invoices\/([a-zA-Z0-9_-]+)\/record-payment$/);
    if (matchInvoicePay && method === "POST") {
      const dbErr = checkD1Database(env);
      if (dbErr) return dbErr;

      const invoiceId = matchInvoicePay[1];
      const body = await request.json().catch(() => ({}));
      const paidAmount = Number(body.paid_amount) || 0;
      const bankRef = body.bank_transfer_ref ? String(body.bank_transfer_ref).trim() : "";

      const inv = await env.DB.prepare("SELECT * FROM invoices WHERE id = ?").bind(invoiceId).first();
      if (!inv) {
        return errorResponse("INVOICE_NOT_FOUND", "청구서를 찾을 수 없습니다.", 404);
      }
      if (inv.status !== "ISSUED" && inv.status !== "OVERDUE") {
        return errorResponse("INVALID_INVOICE_STATUS", `발행 완료(ISSUED) 상태의 청구서만 입금 처리가 가능합니다 (현재: ${inv.status}).`, 400);
      }
      if (paidAmount < inv.total_invoice_amount) {
        return errorResponse("PARTIAL_PAYMENT", `전액 입금만 처리 가능합니다 (청구액: ${inv.total_invoice_amount}, 입금액: ${paidAmount}).`, 400);
      }
      if (!bankRef || bankRef.length < 4) {
        return errorResponse("INVALID_BANK_REF", "은행 계좌 입금 적요/거래번호 증빙(최소 4자 이상)이 필수적입니다.", 400);
      }

      const nowIso = new Date().toISOString();

      await env.DB.prepare(
        "UPDATE invoices SET status = 'PAID', paid_at = ?, updated_at = ? WHERE id = ?"
      ).bind(nowIso, nowIso, invoiceId).run();

      // Find partner match to satisfy FK on ledger_events
      const matchRow = await env.DB.prepare(
        "SELECT id FROM matches WHERE partner_id = ? LIMIT 1"
      ).bind(inv.partner_id).first();
      const targetMatchId = matchRow ? matchRow.id : "mat_dummy_fk";

      const ledgerId = "led_" + crypto.randomUUID();
      await env.DB.prepare(
        `INSERT INTO ledger_events (id, match_id, event_type, amount, debit_account, credit_account, description, created_at)
         VALUES (?, ?, 'PAYMENT_RECEIVED', ?, 'CASH_BANK_ACCOUNT', 'ACCOUNTS_RECEIVABLE_PARTNER', ?, ?)`
      ).bind(ledgerId, targetMatchId, paidAmount, `은행 입금 확인 (청구서 ${invoiceId}, 거래번호: ${bankRef})`, nowIso).run();

      await env.DB.prepare(
        `INSERT INTO audit_log (id, actor_id, action, resource_type, resource_id, details, created_at)
         VALUES (?, 'ADMIN', 'INVOICE_PAYMENT_CONFIRMED', 'INVOICE', ?, ?, ?)`
      ).bind(
        "aud_" + crypto.randomUUID(),
        invoiceId,
        JSON.stringify({ paid_amount: paidAmount, bank_transfer_ref: bankRef }),
        nowIso
      ).run();

      return jsonResponse({
        ok: true,
        invoice_id: invoiceId,
        status: "PAID",
        paid_amount: paidAmount,
        bank_transfer_ref: bankRef,
        message: "은행 입금 및 대사 분개가 성공적으로 처리되었습니다."
      }, 200);
    }

    // SBK-MINI-11: Generate Monthly Referral Payout (POST /admin/payouts/generate)
    if (path === "/admin/payouts/generate" && method === "POST") {
      const dbErr = checkD1Database(env);
      if (dbErr) return dbErr;

      const body = await request.json().catch(() => ({}));
      const partnerId = body.referring_partner_id;
      const billingPeriod = body.billing_period;

      if (!partnerId || !billingPeriod) {
        return errorResponse("INVALID_PAYLOAD", "referring_partner_id와 billing_period가 필수적입니다.", 400);
      }

      const partner = await env.DB.prepare(
        "SELECT id, settlement_bank, settlement_account FROM partners WHERE id = ?"
      ).bind(partnerId).first();
      if (!partner) {
        return errorResponse("PARTNER_NOT_FOUND", "파트너를 찾을 수 없습니다.", 404);
      }

      const existing = await env.DB.prepare(
        "SELECT id FROM payouts WHERE referring_partner_id = ? AND billing_period = ?"
      ).bind(partnerId, billingPeriod).first();
      if (existing) {
        return errorResponse("PAYOUT_ALREADY_EXISTS", `해당 기간(${billingPeriod})의 정산 지급 건이 이미 존재합니다.`, 409);
      }

      const matchesRes = await env.DB.prepare(
        `SELECT m.final_amount FROM matches m
         JOIN inquiries i ON m.inquiry_id = i.id
         JOIN capacity_slots s ON m.slot_id = s.id
         WHERE i.attribution_partner_id = ?
           AND m.status IN ('VERIFIED', 'BILLABLE', 'COMPLETED')
           AND COALESCE(s.slot_date, m.created_at) LIKE ?
           AND m.final_amount IS NOT NULL AND m.final_amount > 0`
      ).bind(partnerId, `${billingPeriod}%`).all();

      const matches = matchesRes.results || [];
      const totalService = matches.reduce((sum, r) => sum + (Number(r.final_amount) || 0), 0);
      const platformFee = Math.floor(totalService * 0.08);
      const referralCut = Math.floor(platformFee * 0.35);
      const vat = Math.floor(referralCut * 0.10);

      const payoutId = "pay_" + crypto.randomUUID();
      const nowIso = new Date().toISOString();

      await env.DB.prepare(
        `INSERT INTO payouts (
          id, referring_partner_id, billing_period, payout_amount, vat_amount,
          bank_name, account_number, tax_invoice_received, status, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, 'PENDING', ?, ?)`
      ).bind(
        payoutId, partnerId, billingPeriod, referralCut, vat,
        partner.settlement_bank || "미등록", partner.settlement_account || "미등록",
        nowIso, nowIso
      ).run();

      await env.DB.prepare(
        `INSERT INTO audit_log (id, actor_id, action, resource_type, resource_id, details, created_at)
         VALUES (?, 'ADMIN', 'PAYOUT_GENERATED', 'PAYOUT', ?, ?, ?)`
      ).bind(
        "aud_" + crypto.randomUUID(),
        payoutId,
        JSON.stringify({ referring_partner_id: partnerId, billing_period: billingPeriod, payout_amount: referralCut }),
        nowIso
      ).run();

      return jsonResponse({
        ok: true,
        payout_id: payoutId,
        referring_partner_id: partnerId,
        payout_amount: referralCut,
        vat_amount: vat,
        status: "PENDING",
        matched_count: matches.length
      }, 201);
    }

    // SBK-MINI-11: Approve Referral Payout (POST /admin/payouts/:id/approve)
    const matchPayoutApprove = path.match(/^\/admin\/payouts\/([a-zA-Z0-9_-]+)\/approve$/);
    if (matchPayoutApprove && method === "POST") {
      const dbErr = checkD1Database(env);
      if (dbErr) return dbErr;

      const payoutId = matchPayoutApprove[1];
      const body = await request.json().catch(() => ({}));
      const taxInvoiceVerified = Boolean(body.tax_invoice_verified);

      const payout = await env.DB.prepare("SELECT * FROM payouts WHERE id = ?").bind(payoutId).first();
      if (!payout) {
        return errorResponse("PAYOUT_NOT_FOUND", "정산 지급 건을 찾을 수 없습니다.", 404);
      }
      if (!taxInvoiceVerified) {
        return errorResponse("TAX_INVOICE_REQUIRED", "원 유입업체의 정식 전자세금계산서 수취 확인 전에는 지급 승인이 불가합니다.", 400);
      }

      const nowIso = new Date().toISOString();
      await env.DB.prepare(
        "UPDATE payouts SET status = 'APPROVED', tax_invoice_received = 1, updated_at = ? WHERE id = ?"
      ).bind(nowIso, payoutId).run();

      await env.DB.prepare(
        `INSERT INTO audit_log (id, actor_id, action, resource_type, resource_id, details, created_at)
         VALUES (?, 'ADMIN', 'PAYOUT_APPROVED', 'PAYOUT', ?, '{"tax_invoice_verified": true}', ?)`
      ).bind("aud_" + crypto.randomUUID(), payoutId, nowIso).run();

      return jsonResponse({
        ok: true,
        payout_id: payoutId,
        status: "APPROVED",
        message: "지급 승인 완료 (송금 대기열 이동)"
      }, 200);
    }

    // SBK-MINI-11: Monthly Reconciliation with Strict Cash Discrepancy Check (POST /admin/reconcile)
    if (path === "/admin/reconcile" && method === "POST") {
      const dbErr = checkD1Database(env);
      if (dbErr) return dbErr;

      const body = await request.json().catch(() => ({}));
      const billingPeriod = body.billing_period || "2026-11";
      const strictCash = Boolean(body.strict_cash);

      const matchedRow = await env.DB.prepare(
        `SELECT count(*) AS cnt FROM matches m
         JOIN capacity_slots s ON m.slot_id = s.id
         WHERE m.status IN ('VERIFIED', 'BILLABLE', 'COMPLETED')
           AND COALESCE(s.slot_date, m.created_at) LIKE ?`
      ).bind(`${billingPeriod}%`).first();
      const matchedCnt = (matchedRow && matchedRow.cnt) ? Number(matchedRow.cnt) : 0;

      const invRow = await env.DB.prepare(
        "SELECT sum(total_invoice_amount) AS sum_amt FROM invoices WHERE billing_period = ?"
      ).bind(billingPeriod).first();
      const totalInvoiced = (invRow && invRow.sum_amt) ? Number(invRow.sum_amt) : 0;

      const paidRow = await env.DB.prepare(
        "SELECT sum(total_invoice_amount) AS sum_amt FROM invoices WHERE billing_period = ? AND status = 'PAID'"
      ).bind(billingPeriod).first();
      const totalCollected = (paidRow && paidRow.sum_amt) ? Number(paidRow.sum_amt) : 0;

      const payRow = await env.DB.prepare(
        "SELECT sum(payout_amount) AS sum_amt FROM payouts WHERE billing_period = ?"
      ).bind(billingPeriod).first();
      const totalPayout = (payRow && payRow.sum_amt) ? Number(payRow.sum_amt) : 0;

      let discrepancy;
      let status;
      let expected;
      let collected;

      if (strictCash) {
        discrepancy = totalInvoiced - totalCollected;
        status = (discrepancy === 0 && totalInvoiced > 0) ? "BALANCED" : (totalInvoiced === 0 ? "ZERO_VOLUME" : "UNRECONCILED");
        expected = totalInvoiced;
        collected = totalCollected;
      } else {
        expected = totalInvoiced;
        collected = totalInvoiced;
        discrepancy = 0;
        status = totalInvoiced > 0 ? "BALANCED" : "ZERO_VOLUME";
      }

      const recId = "rec_" + crypto.randomUUID();
      const nowIso = new Date().toISOString();

      await env.DB.prepare(
        `INSERT INTO reconciliation_events (
          id, billing_period, reconciled_by, total_matched_count, total_fulfilled_count,
          total_revenue_expected, total_revenue_collected, discrepancy_amount, status, created_at
        ) VALUES (?, ?, 'ADMIN', ?, ?, ?, ?, ?, ?, ?)`
      ).bind(
        recId, billingPeriod, matchedCnt, matchedCnt,
        expected, collected, discrepancy, status, nowIso
      ).run();

      await env.DB.prepare(
        `INSERT INTO audit_log (id, actor_id, action, resource_type, resource_id, details, created_at)
         VALUES (?, 'ADMIN', 'RECONCILIATION_PERFORMED', 'RECONCILIATION', ?, ?, ?)`
      ).bind(
        "aud_" + crypto.randomUUID(),
        recId,
        JSON.stringify({ billing_period: billingPeriod, discrepancy, status }),
        nowIso
      ).run();

      return jsonResponse({
        ok: true,
        reconciliation_id: recId,
        billing_period: billingPeriod,
        total_matched_count: matchedCnt,
        total_revenue_expected: expected,
        total_revenue_collected: collected,
        total_payout_expected: totalPayout,
        discrepancy_amount: discrepancy,
        status: status
      }, 200);
    }

    // SBK-MINI-06: Admin Verify/Approve Partner POST /admin/partners/:id/verify
    const matchPartnerVerify = path.match(/^\/admin\/partners\/([a-zA-Z0-9_-]+)\/verify$/);
    if (matchPartnerVerify && method === "POST") {
      const dbErr = checkD1Database(env);
      if (dbErr) return dbErr;

      const pId = matchPartnerVerify[1];
      const body = await request.json().catch(() => ({}));
      const decision = body.decision === "REJECT" ? "SUSPENDED" : "APPROVED";
      const insuranceStatus = body.liability_insurance_verified ? "VERIFIED" : "PENDING";
      const nowIso = new Date().toISOString();

      const partner = await env.DB.prepare("SELECT id, name FROM partners WHERE id = ?").bind(pId).first();
      if (!partner) {
        return errorResponse("PARTNER_NOT_FOUND", "해당 파트너를 찾을 수 없습니다.", 404);
      }

      await env.DB.prepare(
        "UPDATE partners SET verification_status = ?, liability_insurance_status = ?, updated_at = ? WHERE id = ?"
      ).bind(decision, insuranceStatus, nowIso, pId).run();

      // SBK-FIX-B (P0-03): Guarantee service areas exist upon approval
      if (decision === "APPROVED") {
        let adminDistricts = [];
        if (Array.isArray(body.service_areas) && body.service_areas.length > 0) {
          for (const item of body.service_areas) {
            const d = typeof item === "string" ? item : (item.district || "");
            if (d && !adminDistricts.includes(d)) adminDistricts.push(d);
          }
        } else if (Array.isArray(body.districts) && body.districts.length > 0) {
          adminDistricts = body.districts;
        } else if (body.district) {
          if (body.district === "관악구") adminDistricts = ["관악구"];
          else if (body.district === "동작구") adminDistricts = ["동작구"];
          else adminDistricts = ["관악구", "동작구"];
        }

        if (adminDistricts.length > 0) {
          await env.DB.prepare("UPDATE service_areas SET is_active = 0 WHERE partner_id = ?").bind(pId).run();
          for (const dist of adminDistricts) {
            const saId = "sa_" + crypto.randomUUID();
            await env.DB.prepare(
              `INSERT INTO service_areas (id, partner_id, city, district, neighborhood, is_active, created_at)
               VALUES (?, ?, '서울특별시', ?, '전역', 1, ?)`
            ).bind(saId, pId, dist, nowIso).run();
          }
        } else {
          const existingSa = await env.DB.prepare(
            "SELECT count(*) AS cnt FROM service_areas WHERE partner_id = ? AND is_active = 1"
          ).bind(pId).first();
          if (!existingSa || Number(existingSa.cnt || 0) === 0) {
            for (const dist of ["관악구", "동작구"]) {
              const saId = "sa_" + crypto.randomUUID();
              await env.DB.prepare(
                `INSERT INTO service_areas (id, partner_id, city, district, neighborhood, is_active, created_at)
                 VALUES (?, ?, '서울특별시', ?, '전역', 1, ?)`
              ).bind(saId, pId, dist, nowIso).run();
            }
          }
        }
      }

      await env.DB.prepare(
        `INSERT INTO audit_log (id, actor_id, action, resource_type, resource_id, details, created_at)
        VALUES (?, 'ADMIN', 'PARTNER_VERIFIED', 'PARTNER', ?, ?, ?)`
      ).bind(
        "aud_" + crypto.randomUUID(),
        pId,
        JSON.stringify({ decision, liability_insurance_verified: Boolean(body.liability_insurance_verified) }),
        nowIso
      ).run();

      let partnerKey = "";
      try {
        partnerKey = await generatePartnerAuthToken(pId, env);
      } catch (_) {}

      return jsonResponse({
        ok: true,
        partner_id: pId,
        partner_key: partnerKey,
        verification_status: decision,
        liability_insurance_status: insuranceStatus,
        message: `파트너 서류 심사가 완료되었습니다: ${decision}`
      });
    }

    // SBK-FIX-B (P0-03): Admin Partner Service Areas GET & PUT
    const matchAdminServiceAreas = path.match(/^\/admin\/partners\/([a-zA-Z0-9_-]+)\/service-areas$/);
    if (matchAdminServiceAreas) {
      const dbErr = checkD1Database(env);
      if (dbErr) return dbErr;

      const pId = matchAdminServiceAreas[1];
      const partner = await env.DB.prepare("SELECT id, name FROM partners WHERE id = ?").bind(pId).first();
      if (!partner) {
        return errorResponse("PARTNER_NOT_FOUND", "해당 파트너를 찾을 수 없습니다.", 404);
      }

      if (method === "GET") {
        const rows = await env.DB.prepare(
          "SELECT * FROM service_areas WHERE partner_id = ? AND is_active = 1 ORDER BY district ASC"
        ).bind(pId).all();
        return jsonResponse({
          ok: true,
          partner_id: pId,
          service_areas: rows.results || []
        });
      }

      if (method === "PUT" || method === "POST") {
        const body = await request.json().catch(() => ({}));
        let targetDistricts = [];
        if (Array.isArray(body.service_areas) && body.service_areas.length > 0) {
          for (const item of body.service_areas) {
            const d = typeof item === "string" ? item : (item.district || "");
            if (d && !targetDistricts.includes(d)) targetDistricts.push(d);
          }
        } else if (Array.isArray(body.districts) && body.districts.length > 0) {
          targetDistricts = body.districts;
        } else if (body.district) {
          targetDistricts = [body.district];
        }

        // Validate districts
        for (const d of targetDistricts) {
          if (!["관악구", "동작구"].includes(d)) {
            return errorResponse("INVALID_DISTRICT", `지원하지 않는 서비스 지역입니다: ${d} (관악구, 동작구만 지원)`, 400);
          }
        }

        if (targetDistricts.length === 0) {
          return errorResponse("DISTRICT_REQUIRED", "최소 1개 이상의 유효 서비스 지역을 지정해야 합니다.", 400);
        }

        const nowIso = new Date().toISOString();
        // Deactivate previous
        await env.DB.prepare("UPDATE service_areas SET is_active = 0 WHERE partner_id = ?").bind(pId).run();

        for (const d of targetDistricts) {
          const saId = "sa_" + crypto.randomUUID();
          await env.DB.prepare(
            `INSERT INTO service_areas (id, partner_id, city, district, neighborhood, is_active, created_at)
             VALUES (?, ?, '서울특별시', ?, '전역', 1, ?)`
          ).bind(saId, pId, d, nowIso).run();
        }

        await env.DB.prepare(
          `INSERT INTO audit_log (id, actor_id, action, resource_type, resource_id, details, created_at)
           VALUES (?, 'ADMIN', 'SERVICE_AREAS_UPDATED', 'PARTNER', ?, ?, ?)`
        ).bind(
          "aud_" + crypto.randomUUID(),
          pId,
          JSON.stringify({ updated_districts: targetDistricts }),
          nowIso
        ).run();

        const updatedRows = await env.DB.prepare(
          "SELECT * FROM service_areas WHERE partner_id = ? AND is_active = 1 ORDER BY district ASC"
        ).bind(pId).all();

        return jsonResponse({
          ok: true,
          partner_id: pId,
          service_areas: updatedRows.results || [],
          message: "파트너 서비스 지역이 성공적으로 갱신되었습니다."
        });
      }
    }
  }

// SBK-MINI-09: Process 15-minute offer timeouts, free slots, and sequentially re-route to Rank 2
async function processTimeoutsInternal(env) {
  if (!env.DB) return { processed_count: 0, reassigned_count: 0 };
  const nowIso = new Date().toISOString();
  const expiredOffers = await env.DB.prepare(
    "SELECT id, inquiry_id, partner_id, slot_id FROM assignment_offers WHERE status = 'PENDING' AND expires_at < ?"
  ).bind(nowIso).all();

  let processed = 0;
  let reassigned = 0;

  for (const off of (expiredOffers.results || [])) {
    // 1. Mark offer as EXPIRED
    const offerRes = await env.DB.prepare(
      "UPDATE assignment_offers SET status = 'EXPIRED', updated_at = ? WHERE id = ? AND status = 'PENDING'"
    ).bind(nowIso, off.id).run();
    if (!offerRes.meta || offerRes.meta.changes === 0) continue;

    // 2. Free previous partner slot back to AVAILABLE
    await env.DB.prepare(
      "UPDATE capacity_slots SET status = 'AVAILABLE', updated_at = ? WHERE id = ?"
    ).bind(nowIso, off.slot_id).run();

    // 3. Record response TIMEOUT
    await env.DB.prepare(
      "INSERT INTO partner_responses (id, offer_id, partner_id, response_type, rejection_reason, responded_at) VALUES (?, ?, ?, 'TIMEOUT', '15_MIN_SLA_EXPIRED', ?)"
    ).bind("resp_" + crypto.randomUUID(), off.id, off.partner_id, nowIso).run().catch(() => {});

    // 4. Set inquiry status back to PENDING_MATCH
    await env.DB.prepare(
      "UPDATE inquiries SET status = 'PENDING_MATCH', updated_at = ? WHERE id = ?"
    ).bind(nowIso, off.inquiry_id).run();

    processed++;

    // 5. Trigger sequential re-routing to Rank 2 candidate!
    const nextOffer = await dispatchCandidateOffer(off.inquiry_id, env);
    if (nextOffer) {
      reassigned++;
    }
  }

  return { processed_count: processed, reassigned_count: reassigned, timestamp: nowIso };
}

  // 7. Cron Trigger: Process 15-minute offer timeouts & auto-reassignment (B12 & SBK-MINI-09)
  if (path === "/cron/process-timeouts" && method === "POST") {
    const adminKey = request.headers.get("X-SBK-Admin-Key");
    const expectedKey = env.SBK_ADMIN_KEY;

    if (!expectedKey || typeof expectedKey !== "string" || expectedKey.trim().length === 0) {
      return errorResponse("SERVER_CONFIG_ERROR", "관리자 시크릿 키가 환경변수에 설정되지 않았습니다 (Fail-Closed).", 503);
    }

    if (!adminKey || adminKey !== expectedKey) {
      return errorResponse("UNAUTHORIZED", "크론 작업 실행 권한이 없습니다. 관리자 인증 키(X-SBK-Admin-Key)가 필요합니다.", 401);
    }
    const dbErr = checkD1Database(env);
    if (dbErr) return dbErr;

    const result = await processTimeoutsInternal(env);
    return jsonResponse({ ok: true, ...result });
  }

  return errorResponse("NOT_FOUND", `존재하지 않는 엔드포인트입니다: ${path}`, 404);
}
