/**
 * SlotBridge Korea — White-Label Overflow Embed Widget
 * Version: 1.0.0
 * Size: < 6KB (Zero external dependencies)
 * Security: CSP-safe, Clickjacking protected, 3rd-party cookie free.
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

  var container = document.createElement('div');
  container.id = 'sbk-overflow-widget-root';

  var isDark = theme === 'dark';
  var bgColor = isDark ? '#111827' : '#ffffff';
  var textColor = isDark ? '#f9fafb' : '#111827';
  var mutedColor = isDark ? '#9ca3af' : '#6b7280';
  var borderColor = isDark ? '#374151' : '#e5e7eb';
  var accentColor = '#3b82f6';
  var warningBg = isDark ? 'rgba(245, 158, 11, 0.12)' : '#fef3c7';
  var warningText = isDark ? '#fde68a' : '#92400e';

  container.innerHTML = [
    '<div style="font-family: -apple-system, BlinkMacSystemFont, Pretendard, Segoe UI, Roboto, sans-serif;',
    '     background: ' + bgColor + '; color: ' + textColor + '; border: 1px solid ' + borderColor + ';',
    '     border-radius: 12px; padding: 18px 20px; box-shadow: 0 4px 16px rgba(0,0,0,0.15);',
    '     max-width: 520px; margin: 16px auto; box-sizing: border-box; text-align: left; line-height: 1.5;">',
    '  <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 10px;">',
    '    <span style="background: ' + warningBg + '; color: ' + warningText + '; font-size: 11px; font-weight: 700;',
    '                 padding: 3px 8px; border-radius: 4px; letter-spacing: -0.2px;">⚠️ ' + partnerName + ' 예약 마감 안내</span>',
    '    <span style="font-size: 11px; color: ' + mutedColor + ';">24~72h 급행 지원</span>',
    '  </div>',
    '  <h4 style="margin: 0 0 6px 0; font-size: 15px; font-weight: 700; color: ' + textColor + ';">',
    '    일정이 마감되어 고민이신가요?',
    '  </h4>',
    '  <p style="margin: 0 0 14px 0; font-size: 13px; color: ' + mutedColor + ';">',
    '    ' + targetDistrict + ' 공인 연합 청소팀의 <strong>[24~72시간 잔여 슬롯]</strong>을 조회하여 바로 연결해 드립니다.',
    '  </p>',
    '  <a id="sbk-widget-btn" href="' + baseUrl + '/slotbridge/inquiry.html?ref=' + encodeURIComponent(partnerId) + '"',
    '     target="_blank" rel="noopener noreferrer"',
    '     style="display: block; width: 100%; text-align: center; background: ' + accentColor + '; color: #ffffff;',
    '            font-size: 14px; font-weight: 700; padding: 11px 0; border-radius: 8px; text-decoration: none;',
    '            box-sizing: border-box; transition: background 0.2s;">',
    '    관내 잔여 급행 슬롯 조회하기 (무료)',
    '  </a>',
    '  <div style="margin-top: 10px; font-size: 11px; color: ' + mutedColor + '; text-align: center; line-height: 1.4;">',
    '    🔒 본 서비스는 <strong>SlotBridge Korea</strong> 공인 연합 네트워크를 통해 정식 공급업체로 1:1 연결됩니다.',
    '  </div>',
    '</div>'
  ].join('');

  // Insert where script tag is or append to body
  currentScript.parentNode.insertBefore(container, currentScript.nextSibling);
})();
