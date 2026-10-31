/**
 * SlotBridge Korea — White-Label Overflow Embed Widget (SBK-FIX-08)
 * Version: 2.0.0 (Secure DOM API & Signed Attribution Gateway)
 * Size: < 5KB (Zero external dependencies)
 * Security: Strictly safe DOM API (Zero string injection), Zero 3rd-party cookies, Signed HMAC Token.
 */

(function () {
  'use strict';

  // Read configuration from script tag
  var currentScript = document.currentScript || (function () {
    var scripts = document.getElementsByTagName('script');
    return scripts[scripts.length - 1];
  })();

  var partnerId = currentScript.getAttribute('data-partner') || 'partner_sl_master';
  var partnerName = currentScript.getAttribute('data-partner-name') || '제휴 청소 전문점';
  var theme = currentScript.getAttribute('data-theme') || 'dark';
  var targetDistrict = currentScript.getAttribute('data-district') || '서울 관악·동작구';
  var baseUrl = currentScript.src.substring(0, currentScript.src.lastIndexOf('/slotbridge'));

  // Prevent multiple injections
  if (document.getElementById('sbk-overflow-widget-root')) return;

  var root = document.createElement('div');
  root.id = 'sbk-overflow-widget-root';

  var isDark = theme === 'dark';
  var bgColor = isDark ? '#111827' : '#ffffff';
  var textColor = isDark ? '#f9fafb' : '#111827';
  var mutedColor = isDark ? '#9ca3af' : '#6b7280';
  var borderColor = isDark ? '#374151' : '#e5e7eb';
  var accentColor = '#3b82f6';
  var warningBg = isDark ? 'rgba(245, 158, 11, 0.12)' : '#fef3c7';
  var warningText = isDark ? '#fde68a' : '#92400e';

  // Card Container
  var card = document.createElement('div');
  card.style.fontFamily = '-apple-system, BlinkMacSystemFont, Pretendard, Segoe UI, Roboto, sans-serif';
  card.style.background = bgColor;
  card.style.color = textColor;
  card.style.border = '1px solid ' + borderColor;
  card.style.borderRadius = '12px';
  card.style.padding = '18px 20px';
  card.style.boxShadow = '0 4px 16px rgba(0,0,0,0.15)';
  card.style.maxWidth = '520px';
  card.style.margin = '16px auto';
  card.style.boxSizing = 'border-box';
  card.style.textAlign = 'left';
  card.style.lineHeight = '1.5';

  // Header Row
  var header = document.createElement('div');
  header.style.display = 'flex';
  header.style.alignItems = 'center';
  header.style.justifyContent = 'space-between';
  header.style.marginBottom = '10px';

  var badge = document.createElement('span');
  badge.style.background = warningBg;
  badge.style.color = warningText;
  badge.style.fontSize = '11px';
  badge.style.fontWeight = '700';
  badge.style.padding = '3px 8px';
  badge.style.borderRadius = '4px';
  badge.style.letterSpacing = '-0.2px';
  badge.textContent = '⚠️ ' + partnerName + ' 예약 마감 안내';

  var subBadge = document.createElement('span');
  subBadge.style.fontSize = '11px';
  subBadge.style.color = mutedColor;
  subBadge.textContent = '24~72h 급행 지원';

  header.appendChild(badge);
  header.appendChild(subBadge);
  card.appendChild(header);

  // Title
  var title = document.createElement('h4');
  title.style.margin = '0 0 6px 0';
  title.style.fontSize = '15px';
  title.style.fontWeight = '700';
  title.style.color = textColor;
  title.textContent = '일정이 마감되어 고민이신가요?';
  card.appendChild(title);

  // Description
  var desc = document.createElement('p');
  desc.style.margin = '0 0 14px 0';
  desc.style.fontSize = '13px';
  desc.style.color = mutedColor;
  desc.textContent = targetDistrict + ' 공인 연합 청소팀의 [24~72시간 잔여 슬롯]을 조회하여 바로 연결해 드립니다.';
  card.appendChild(desc);

  // Action Button (Default base URL without signed token)
  var btn = document.createElement('a');
  btn.id = 'sbk-widget-btn';
  btn.target = '_blank';
  btn.rel = 'noopener noreferrer';
  btn.style.display = 'block';
  btn.style.width = '100%';
  btn.style.textAlign = 'center';
  btn.style.background = accentColor;
  btn.style.color = '#ffffff';
  btn.style.fontSize = '14px';
  btn.style.fontWeight = '700';
  btn.style.padding = '11px 0';
  btn.style.borderRadius = '8px';
  btn.style.textDecoration = 'none';
  btn.style.boxSizing = 'border-box';
  btn.style.transition = 'background 0.2s';
  btn.textContent = '관내 잔여 급행 슬롯 조회하기 (무료)';
  btn.href = baseUrl + '/slotbridge/inquiry.html?ref=' + encodeURIComponent(partnerId);

  var partnerToken = currentScript.getAttribute('data-token') || '';
  var partnerKey = currentScript.getAttribute('data-partner-key') || '';

  if (partnerToken) {
    btn.href = baseUrl + '/slotbridge/inquiry.html?ref=' + encodeURIComponent(partnerId) + '&token=' + encodeURIComponent(partnerToken);
  } else if (partnerKey) {
    // Authenticated request for signed attribution token from server
    try {
      var tokenEndpoint = baseUrl + '/api/v1/slotbridge/attribution/token?partner_id=' + encodeURIComponent(partnerId) + '&partner_key=' + encodeURIComponent(partnerKey);
      fetch(tokenEndpoint)
        .then(function (res) { return res.json(); })
        .then(function (data) {
          if (data && data.ok && data.token) {
            btn.href = baseUrl + '/slotbridge/inquiry.html?ref=' + encodeURIComponent(partnerId) + '&token=' + encodeURIComponent(data.token);
          }
        })
        .catch(function () {
          // Fallback gracefully without breaking UI
        });
    } catch (e) {}
  }

  card.appendChild(btn);

  // Footer Note (Compliance with FTC guidelines & E-Commerce Broker Notice)
  var footer = document.createElement('div');
  footer.style.marginTop = '10px';
  footer.style.fontSize = '10px';
  footer.style.color = mutedColor;
  footer.style.textAlign = 'center';
  footer.style.lineHeight = '1.4';
  footer.textContent = '🔒 통신판매중개: SlotBridge Korea (거래 당사자 아님) · 공정위 심사지침 준수: 이용 시 추천 제휴사에 소정의 중개 수수료가 배분될 수 있습니다.';
  card.appendChild(footer);

  root.appendChild(card);
  currentScript.parentNode.insertBefore(root, currentScript.nextSibling);
})();
