// The shared page shell for every CESC page except the chat: the RDPR / KPCL portal header (two logos, titles, English /
// Kannada switch, the signed-in user), the navy app bar with the AI MODE badge, the paper sidebar with the section list,
// the drawer on tablets and phones, the opening splash and the logo transition between pages.
//
// A page marks up the empty containers (#siteNav, #langEn / #langKn, #userChip, #overlay ...) and loads this file before
// its own script; the shell fills them in. renderSiteNav(activeId) is kept for the page scripts that already call it.
// The language switch translates the shell's own labels (data-i18n); figures and page text stay in English, as before.
(function(){
  'use strict';
  const SECTIONS = [
    { id: 'dashboard', href: '/dashboard', i18n: 'navDash',
      icon: '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>' },
    { id: 'chat', href: '/chat', i18n: 'navChat', ai: true,
      icon: '<path d="M21 11.5a8.4 8.4 0 0 1-8.9 8.5 8.7 8.7 0 0 1-3.8-.8L3 21l1.9-5.1a8.4 8.4 0 0 1-.9-3.9A8.4 8.4 0 0 1 12.9 3a8.5 8.5 0 0 1 8.1 8.5z"/>' },
    { id: 'insights', href: '/insights', i18n: 'navInsights',
      icon: '<path d="M12 2l1.8 6.2L20 10l-6.2 1.8L12 18l-1.8-6.2L4 10l6.2-1.8z"/><path d="M19 15l.7 2.4L22 18l-2.3.6-.7 2.4-.7-2.4L16 18l2.3-.6Z"/>' },
    { id: 'reports', href: '/reports', i18n: 'navReports', icon: '<path d="M3 3v18h18"/><path d="M7 15l4-5 3 3 5-7"/>' },
    { id: 'tenders', href: '/tenders', i18n: 'navTenders',
      icon: '<path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><path d="M14 3v6h6"/><path d="M8 13h8"/><path d="M8 17h5"/>' },
    { id: 'about', href: '/about', i18n: 'navAbout', icon: '<circle cx="12" cy="12" r="9"/><path d="M12 16v-5"/><path d="M12 8h.01"/>' },
  ];
  const I18N = {
    en: {
      govLine1: 'Government of Karnataka', govLine2: 'Chamundeshwari Electricity Supply Corporation Limited, Mysuru',
      appBar: '<b>DCB · Demand, Collection &amp; Balance</b><span class="extra"><span class="sep">|</span>Holenarasipura Division<span class="sep">|</span>FY 2015-16 – FY 2025-26</span>',
      aiBadge: 'AI MODE', menu: 'Menu', navDash: 'Dashboard', navInsights: 'Insights', navReports: 'Reports', navTenders: 'Tenders',
      navChat: 'Ask the assistant', navAbout: 'About & glossary', stTitle: 'Data status', stLatest: 'Latest data', stPeriod: 'Period', stAssistant: 'Assistant', stOnline: 'Online', stOffline: 'Offline', stVersion: 'Data version',
      sideFoot: 'Proof of concept on the CESC Holenarasipura Division DCB statements, FY 2015-16 to 2025-26. Not for official use.',
      logout: 'Log out', signedIn: 'Signed in', signIn: 'Sign in', goChat: 'Entering AI mode', goPage: 'Opening', loading: 'Loading the latest figures',
      loggingOut: 'Logging out',
    },
    kn: {
      govLine1: 'ಕರ್ನಾಟಕ ಸರ್ಕಾರ', govLine2: 'ಚಾಮುಂಡೇಶ್ವರಿ ವಿದ್ಯುತ್ ಸರಬರಾಜು ನಿಗಮ ನಿಯಮಿತ, ಮೈಸೂರು',
      appBar: '<b>ಡಿಸಿಬಿ · ಬೇಡಿಕೆ, ಸಂಗ್ರಹಣೆ ಮತ್ತು ಬಾಕಿ</b><span class="extra"><span class="sep">|</span>ಹೊಳೆನರಸೀಪುರ ವಿಭಾಗ<span class="sep">|</span>2015-16 – 2025-26</span>',
      aiBadge: 'AI ಮೋಡ್', menu: 'ಮೆನು', navDash: 'ಡ್ಯಾಶ್‌ಬೋರ್ಡ್', navInsights: 'ಒಳನೋಟಗಳು', navReports: 'ವರದಿಗಳು', navTenders: 'ಟೆಂಡರ್‌ಗಳು',
      navChat: 'ಸಹಾಯಕನನ್ನು ಕೇಳಿ', navAbout: 'ಮಾಹಿತಿ ಮತ್ತು ಪದಕೋಶ', stTitle: 'ದತ್ತಾಂಶ ಸ್ಥಿತಿ', stLatest: 'ಇತ್ತೀಚಿನ ದತ್ತಾಂಶ', stPeriod: 'ಅವಧಿ', stAssistant: 'ಸಹಾಯಕ', stOnline: 'ಲಭ್ಯ', stOffline: 'ಲಭ್ಯವಿಲ್ಲ', stVersion: 'ದತ್ತಾಂಶ ಆವೃತ್ತಿ',
      sideFoot: 'ಸಿಇಎಸ್‌ಸಿ ಹೊಳೆನರಸೀಪುರ ವಿಭಾಗದ ಡಿಸಿಬಿ ಹೇಳಿಕೆಗಳ ಮೇಲಿನ ಪ್ರಾಯೋಗಿಕ ಮಾದರಿ, 2015-16 ರಿಂದ 2025-26. ಅಧಿಕೃತ ಬಳಕೆಗಲ್ಲ.',
      logout: 'ನಿರ್ಗಮಿಸಿ', signedIn: 'ಪ್ರವೇಶಿಸಲಾಗಿದೆ', signIn: 'ಪ್ರವೇಶಿಸಿ', goChat: 'AI ಮೋಡ್ ಪ್ರವೇಶಿಸಲಾಗುತ್ತಿದೆ', goPage: 'ತೆರೆಯಲಾಗುತ್ತಿದೆ',
      loading: 'ಇತ್ತೀಚಿನ ಅಂಕಿಅಂಶಗಳನ್ನು ತರಲಾಗುತ್ತಿದೆ', loggingOut: 'ನಿರ್ಗಮಿಸಲಾಗುತ್ತಿದೆ',
    },
  };
  const reduced = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const store = (k, v) => { try { if (v === undefined) return localStorage.getItem(k); localStorage.setItem(k, v); } catch (_e) { return null; } };
  const sess = (k, v) => { try { if (v === undefined) return sessionStorage.getItem(k); sessionStorage.setItem(k, v); } catch (_e) { return null; } };
  let lang = store('dcb-lang') === 'kn' ? 'kn' : 'en';
  const t = k => (I18N[lang] && I18N[lang][k]) ?? I18N.en[k] ?? k;
  const $ = id => document.getElementById(id);
  const page = () => document.body.dataset.page || '';

  function renderNav(activeId){
    const host = $('siteNav'); if (!host) return;
    const active = activeId || page();
    host.innerHTML = SECTIONS.map(s => `<a href="${s.href}" class="${s.id === active ? 'active' : ''}${s.ai ? ' ai-link' : ' page-link'}" data-label="${s.i18n}">`
      + `<svg viewBox="0 0 24 24">${s.icon}</svg><span data-i18n="${s.i18n}">${esc(t(s.i18n))}</span>${s.ai ? '<span class="pill">AI</span>' : ''}</a>`).join('');
    wireLinks(host);
  }

  function applyLang(l){
    lang = l === 'kn' ? 'kn' : 'en';
    document.documentElement.lang = lang;
    document.querySelectorAll('[data-i18n]').forEach(el => { const k = el.dataset.i18n; if (k in I18N.en) el.textContent = t(k); });
    document.querySelectorAll('[data-i18n-html]').forEach(el => { const k = el.dataset.i18nHtml; if (k in I18N.en) el.innerHTML = t(k); });
    const en = $('langEn'), kn = $('langKn');
    if (en) en.setAttribute('aria-pressed', lang === 'en'); if (kn) kn.setAttribute('aria-pressed', lang === 'kn');
    store('dcb-lang', lang);
    if (typeof renderStatus === 'function') renderStatus();
    window.dispatchEvent(new CustomEvent('cesc:lang', { detail: lang }));
  }

  // ---------------------------------------------------------------- transitions
  // A multi-page app: leaving a page plays the two-logo overlay for a beat, then navigates for real.
  function goOverlay(label){
    const ov = document.createElement('div'); ov.className = 'overlay go'; ov.setAttribute('aria-hidden', 'true');
    ov.innerHTML = '<div class="overlay-logos"><span class="halo"><img src="/static/cescom_official_logo.png" alt=""></span>'
      + '<span class="halo"><img src="/static/cesc_logo_clean.png" alt=""></span></div>'
      + `<div class="overlay-text"><b>${esc(label)}</b><small>CESC · Holenarasipura Division</small></div>`;
    document.body.appendChild(ov);
    return ov;
  }
  function go(href, label){
    goOverlay(label);
    sess('cesc-splash', '1');
    setTimeout(() => { location.href = href; }, reduced ? 80 : 650);
  }
  function wireLinks(root){
    (root || document).querySelectorAll('a.ai-link, a.page-link').forEach(a => {
      if (a.__wired) return; a.__wired = true;
      a.addEventListener('click', e => {
        if (e.ctrlKey || e.metaKey || e.shiftKey || e.button === 1) return;
        const href = a.getAttribute('href'); if (!href || href.startsWith('#')) return;
        if (a.classList.contains('active') && !a.classList.contains('ai-link')) { e.preventDefault(); document.body.classList.remove('sidebar-open'); return; }
        e.preventDefault();
        const lbl = a.classList.contains('ai-link') ? t('goChat') : `${t('goPage')} ${t(a.dataset.label || '') || a.textContent.trim()}`;
        go(href, lbl);
      });
    });
  }

  // The opening splash: the full logo animation the first time in a browser session, a quick fade after that. A page
  // calls Shell.ready() once its first figures are on screen (or the splash lifts by itself after 2.5 s).
  let splashDone = false;
  function ready(){
    if (splashDone) return; splashDone = true;
    const ov = $('overlay'); if (!ov) return;
    const first = !sess('cesc-splash'); sess('cesc-splash', '1');
    const wait = reduced ? 0 : first ? Math.max(0, 1100 - (performance.now() - T0)) : 0;
    setTimeout(() => { ov.classList.add('off'); setTimeout(() => ov.remove(), 500); }, wait);
  }
  const T0 = performance.now();


  // The sidebar's Data status card: the latest fiscal year, the period covered and whether the assistant's model is
  // reachable (/health). Re-drawn when the language changes.
  let HEALTH = null;
  function renderStatus(){
    const host = $('sideStatus'); if (!host) return;
    if (!HEALTH){ host.innerHTML = `<div class="ss-head">${esc(t('stTitle'))}</div><div class="ss-row"><span>…</span></div>`; return; }
    const on = !!HEALTH.llm_configured;
    host.innerHTML = `<div class="ss-head">${esc(t('stTitle'))}</div>`
      + `<div class="ss-row"><i class="live"></i><span>${esc(t('stLatest'))}</span><b>FY ${esc(HEALTH.data_to || '—')}</b></div>`
      + `<div class="ss-row"><i></i><span>${esc(t('stPeriod'))}</span><b>${esc(HEALTH.data_from || '—')} – ${esc((HEALTH.data_to || '').slice(2) || '—')}</b></div>`
      + `<div class="ss-row"><i class="${on ? 'live' : 'off'}"></i><span>${esc(t('stAssistant'))}</span><b class="${on ? 'ok' : 'no'}">${esc(t(on ? 'stOnline' : 'stOffline'))}</b></div>`
      + (HEALTH.data_version ? `<div class="ss-ver">${esc(t('stVersion'))} · ${esc(String(HEALTH.data_version).slice(0, 8))}</div>` : '');
  }
  function loadStatus(){
    if (!$('sideStatus')) return;
    renderStatus();
    fetch('/health').then(r => r.json()).then(h => { HEALTH = h; renderStatus(); }).catch(() => { HEALTH = { llm_configured: false }; renderStatus(); });
  }

  // ---------------------------------------------------------------- signed-in user
  async function logout(){
    goOverlay(t('loggingOut'));
    try { await fetch('/api/logout', { method: 'POST' }); } catch (_e) { /* the login page still opens */ }
    location.href = '/login';
  }
  async function renderUser(){
    const host = $('userChip'); if (!host) return;
    try {
      const r = await fetch('/api/me');
      if (!r.ok) throw new Error('signed out');
      const me = await r.json();
      const name = me.username || '';
      host.innerHTML = `<span class="av">${esc(name.slice(0, 1).toUpperCase() || '?')}</span><span class="who"><b>${esc(name)}</b>`
        + `<button type="button" id="logoutBtn" data-i18n="logout">${esc(t('logout'))}</button></span>`;
      $('logoutBtn').onclick = logout;
    } catch (_e) {
      host.innerHTML = `<a class="btn btn-back" href="/login" data-i18n="signIn">${esc(t('signIn'))}</a>`;
    }
  }

  // ---------------------------------------------------------------- toast
  let toastTimer = null;
  function toast(msg, err){
    let el = $('toast');
    if (!el){ el = document.createElement('div'); el.className = 'toast'; el.id = 'toast'; el.setAttribute('role', 'status'); el.setAttribute('aria-live', 'polite');
      el.innerHTML = '<i>✓</i><span></span>'; document.body.appendChild(el); }
    el.classList.toggle('err', !!err); el.querySelector('i').textContent = err ? '!' : '✓';
    el.querySelector('span').textContent = msg;
    el.classList.add('on'); clearTimeout(toastTimer); toastTimer = setTimeout(() => el.classList.remove('on'), err ? 5000 : 3000);
  }

  function init(){
    renderNav();
    applyLang(lang);
    const en = $('langEn'), kn = $('langKn');
    if (en) en.onclick = () => applyLang('en');
    if (kn) kn.onclick = () => applyLang('kn');
    const tg = $('sidebarToggle'), bd = $('drawerBackdrop');
    if (tg) tg.onclick = () => document.body.classList.toggle('sidebar-open');
    if (bd) bd.onclick = () => document.body.classList.remove('sidebar-open');
    wireLinks(document);
    renderUser();
    loadStatus();
    if ($('overlay')) setTimeout(ready, 2500);
  }

  window.Shell = { ready, toast, go, lang: () => lang, t, applyLang, logout, wireLinks };
  // kept for the page scripts written against the older nav.js
  window.renderSiteNav = activeId => renderNav(activeId);
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
