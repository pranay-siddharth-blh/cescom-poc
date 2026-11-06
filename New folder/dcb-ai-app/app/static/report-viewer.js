// View report: an Excel-style preview of a report workbook, shown before downloading it (ported from the KPCL POC).
// Used by the dashboard (MIS report) and AI mode (Download report). The server renders the very workbook the download
// writes (/api/mis-report/view, /api/answer-report/{id}/view): cells with their number formats, colours, merged cells,
// frozen header row and sheet tabs; charts are drawn with Chart.js from the cells they reference.
//
//   ReportViewer.open({ viewUrl, onDownload, opener })   onDownload() may return a promise
(function () {
  'use strict';
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const kn = () => (document.documentElement.lang || 'en') === 'kn';
  const T = {
    en: { preview: 'Report preview', sheets: n => n + (n === 1 ? ' sheet' : ' sheets'), same: 'exactly as in the Excel file',
          download: 'Download', downloading: 'Downloading…', downloaded: 'Downloaded', close: 'Close preview', loading: 'Preparing the preview…',
          failed: 'The preview could not be prepared. You can still download the report.', gone: 'This report is no longer available. Please ask the question again.',
          noChart: 'Chart (shown in the downloaded file)', review: 'This report is under review and cannot be downloaded.', footer: 'Printed footer', zoomIn: 'Zoom in', zoomOut: 'Zoom out', sheetsNav: 'Sheets' },
    kn: { preview: 'ವರದಿ ಮುನ್ನೋಟ', sheets: n => n + ' ಹಾಳೆಗಳು', same: 'Excel ಫೈಲ್‌ನಲ್ಲಿರುವಂತೆಯೇ',
          download: 'ಡೌನ್‌ಲೋಡ್', downloading: 'ಡೌನ್‌ಲೋಡ್ ಆಗುತ್ತಿದೆ…', downloaded: 'ಡೌನ್‌ಲೋಡ್ ಆಗಿದೆ', close: 'ಮುಚ್ಚಿ', loading: 'ಮುನ್ನೋಟ ಸಿದ್ಧವಾಗುತ್ತಿದೆ…',
          failed: 'ಮುನ್ನೋಟ ಸಿದ್ಧಪಡಿಸಲಾಗಲಿಲ್ಲ. ವರದಿಯನ್ನು ಡೌನ್‌ಲೋಡ್ ಮಾಡಬಹುದು.', gone: 'ಈ ವರದಿ ಲಭ್ಯವಿಲ್ಲ. ದಯವಿಟ್ಟು ಮತ್ತೆ ಕೇಳಿ.',
          noChart: 'ಚಾರ್ಟ್ (ಡೌನ್‌ಲೋಡ್ ಫೈಲ್‌ನಲ್ಲಿ)', review: 'ಈ ವರದಿ ಪರಿಶೀಲನೆಯಲ್ಲಿದೆ.', footer: 'ಮುದ್ರಿತ ಅಡಿಟಿಪ್ಪಣಿ', zoomIn: 'ಹಿಗ್ಗಿಸಿ', zoomOut: 'ಕುಗ್ಗಿಸಿ', sheetsNav: 'ಹಾಳೆಗಳು' },
  };
  const t = (k, ...a) => { const v = (kn() ? T.kn : T.en)[k] ?? T.en[k]; return typeof v === 'function' ? v(...a) : v; };
  const reduced = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  let root = null, state = null, charts = [];
  const ICON_XL = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/><path d="M9 12l4 5M13 12l-4 5"/></svg>';
  const ICON_DL = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3v12"/><path d="M7 10l5 5 5-5"/><path d="M5 21h14"/></svg>';

  function build(){
    root = document.createElement('div');
    root.className = 'rv'; root.hidden = true;
    root.setAttribute('role', 'dialog'); root.setAttribute('aria-modal', 'true'); root.setAttribute('aria-labelledby', 'rvTitle');
    root.innerHTML = `<div class="rv-backdrop" data-close></div>
      <div class="rv-panel">
        <header class="rv-top">
          <span class="rv-ico">${ICON_XL}</span>
          <div class="rv-head"><small class="rv-eyebrow"></small><b id="rvTitle" class="rv-title"></b></div>
          <div class="rv-zoom" role="group"><button type="button" data-z="-1" class="rv-zbtn">−</button><span class="rv-zval">100%</span><button type="button" data-z="1" class="rv-zbtn">+</button></div>
          <button type="button" class="rv-dl">${ICON_DL}<span></span></button>
          <button type="button" class="rv-x" data-close><span aria-hidden="true">×</span></button>
        </header>
        <div class="rv-body" tabindex="0"><div class="rv-sheet"></div><div class="rv-state"></div></div>
        <div class="rv-foot" aria-live="off"></div>
        <nav class="rv-tabs" role="tablist"></nav>
      </div>`;
    document.body.appendChild(root);
    root.querySelectorAll('[data-close]').forEach(b => b.addEventListener('click', close));
    root.querySelector('.rv-dl').addEventListener('click', download);
    root.querySelectorAll('.rv-zbtn').forEach(b => b.addEventListener('click', () => zoom(Number(b.dataset.z))));
    root.addEventListener('keydown', e => {
      if (e.key === 'Escape'){ e.preventDefault(); close(); }
      if (e.key === 'Tab'){   // keep focus inside the dialog
        const f = [...root.querySelectorAll('button:not([disabled]), [tabindex="0"]')].filter(x => x.offsetParent !== null);
        if (!f.length) return;
        if (e.shiftKey && document.activeElement === f[0]){ e.preventDefault(); f[f.length - 1].focus(); }
        else if (!e.shiftKey && document.activeElement === f[f.length - 1]){ e.preventDefault(); f[0].focus(); }
      }
    });
  }

  function labels(){
    root.querySelector('.rv-eyebrow').textContent = t('preview') + (state && state.view ? ' · ' + t('sheets', state.view.sheets.length) + ' · ' + t('same') : '');
    root.querySelector('.rv-dl span').textContent = t('download');
    root.querySelector('.rv-x').setAttribute('aria-label', t('close'));
    root.querySelector('.rv-zbtn[data-z="-1"]').setAttribute('aria-label', t('zoomOut'));
    root.querySelector('.rv-zbtn[data-z="1"]').setAttribute('aria-label', t('zoomIn'));
    root.querySelector('.rv-tabs').setAttribute('aria-label', t('sheetsNav'));
  }

  const ZOOMS = [0.5, 0.67, 0.8, 0.9, 1, 1.1, 1.25, 1.5];
  function zoom(step){
    if (!state) return;
    state.z = Math.max(0, Math.min(ZOOMS.length - 1, state.z + step));
    applyZoom();
  }
  function applyZoom(){
    const z = ZOOMS[state.z], sh = root.querySelector('.rv-sheet');
    sh.style.zoom = z;
    root.querySelector('.rv-zval').textContent = Math.round(z * 100) + '%';
  }

  function setState(kind, text){
    const st = root.querySelector('.rv-state');
    st.className = 'rv-state' + (kind ? ' on ' + kind : '');
    st.innerHTML = kind === 'loading' ? `<span class="rv-spin"></span><span>${esc(text)}</span>` : kind ? `<span>${esc(text)}</span>` : '';
  }

  async function open(opts){
    if (!root) build();
    state = { opts, view: null, z: window.innerWidth < 700 ? 2 : 4, i: 0, opener: opts.opener || document.activeElement };
    root.hidden = false; document.body.classList.add('rv-open');
    requestAnimationFrame(() => root.classList.add('in'));
    root.querySelector('.rv-title').textContent = opts.title || '';
    root.querySelector('.rv-sheet').innerHTML = ''; root.querySelector('.rv-tabs').innerHTML = ''; root.querySelector('.rv-foot').hidden = true;
    const dl = root.querySelector('.rv-dl'); dl.disabled = false; dl.classList.remove('busy', 'done');
    labels(); applyZoom();
    setState('loading', t('loading'));
    root.querySelector('.rv-x').focus();
    try {
      const r = await fetch(opts.viewUrl);
      if (r.status === 409){ const b = await r.json().catch(() => ({})); const err = new Error(b.message || t('review')); err.gone = true; throw err; }
      if (r.status === 404){ const b = await r.json().catch(() => ({})); const err = new Error(b.detail || t('gone')); err.gone = true; throw err; }
      if (!r.ok) throw new Error('HTTP ' + r.status);
      const v = await r.json();
      if (!state || state.opts !== opts) return;   // closed or reopened meanwhile
      state.view = v;
      root.querySelector('.rv-title').textContent = v.name;
      labels(); setState(null);
      tabs(); show(0);
    } catch (e) {
      if (window.console) console.error(e);
      if (!state || state.opts !== opts) return;
      setState('error', e.gone ? e.message : t('failed'));
      if (e.gone) root.querySelector('.rv-dl').disabled = true;
    }
  }

  function tabs(){
    const nav = root.querySelector('.rv-tabs');
    nav.innerHTML = state.view.sheets.map((s, i) => `<button type="button" role="tab" data-i="${i}" aria-selected="false"
      style="--tab:${esc(s.tab_color || '#8a94a6')}">${esc(s.name)}</button>`).join('');
    nav.querySelectorAll('button').forEach(b => {
      b.addEventListener('click', () => show(Number(b.dataset.i)));
      b.addEventListener('keydown', e => {
        const n = state.view.sheets.length;
        if (e.key === 'ArrowRight' || e.key === 'ArrowLeft'){
          e.preventDefault();
          const j = (state.i + (e.key === 'ArrowRight' ? 1 : n - 1)) % n; show(j);
          nav.querySelector(`button[data-i="${j}"]`).focus();
        }
      });
    });
  }

  function show(i){
    state.i = i;
    const s = state.view.sheets[i];
    root.querySelectorAll('.rv-tabs button').forEach(b => { const on = Number(b.dataset.i) === i; b.setAttribute('aria-selected', on); b.tabIndex = on ? 0 : -1;
      if (on) b.scrollIntoView({ block: 'nearest', inline: 'nearest', behavior: reduced ? 'auto' : 'smooth' }); });
    charts.forEach(c => c.destroy()); charts = [];
    const sh = root.querySelector('.rv-sheet');
    sh.style.width = s.width + 'px'; sh.style.height = s.height + 'px';
    sh.innerHTML = s.html + s.charts.map((c, k) => `<figure class="rv-chart" style="left:${c.left + 2}px;top:${c.top + 2}px;width:${c.width}px;height:${c.height}px">
        ${c.title ? `<figcaption>${esc(c.title)}</figcaption>` : ''}<div class="rv-cv">${window.Chart ? `<canvas data-k="${k}"></canvas>` : `<span class="rv-nochart">${esc(t('noChart'))}</span>`}</div></figure>`).join('');
    const foot = root.querySelector('.rv-foot');
    foot.hidden = !s.footer;
    foot.innerHTML = s.footer ? `<b>${esc(t('footer'))}</b><span>${esc(s.footer)}</span>` : '';
    root.querySelector('.rv-body').scrollTo(0, 0);
    if (window.Chart) s.charts.forEach((c, k) => draw(sh.querySelector(`canvas[data-k="${k}"]`), c));
  }

  const PALETTE = ['#072577', '#d82221', '#0f7291', '#2f9e58', '#2271a0', '#e38f00'];   // the Cescom chart palette (chat.js)
  function draw(cv, c){
    const font = { family: 'Calibri, Carlito, "Noto Sans", sans-serif', size: 11 };
    const many = c.labels.length > 60;
    charts.push(new Chart(cv.getContext('2d'), {
      data: { labels: c.labels, datasets: c.datasets.map((d, i) => {
        const col = d.color || PALETTE[i % PALETTE.length];
        return d.type === 'line'
          ? { type: 'line', label: d.label, data: d.data, borderColor: col, backgroundColor: col, borderWidth: many ? 1.4 : 2,
              borderDash: d.dashed ? [6, 4] : [], pointRadius: 0, pointHoverRadius: 3, tension: 0, order: 1 }
          : { type: 'bar', label: d.label, data: d.data, backgroundColor: col, borderWidth: 0, order: 2 };
      }) },
      options: { responsive: true, maintainAspectRatio: false, animation: { duration: reduced ? 0 : 450 },
        interaction: { mode: 'index', intersect: false },
        plugins: { legend: { display: c.legend && c.datasets.length > 1, position: 'bottom', labels: { font, boxWidth: 12, color: '#3a4456' } },
          tooltip: { backgroundColor: '#151b54', callbacks: { label: x => ` ${x.dataset.label}: ${x.raw == null ? '—' : Number(x.raw).toLocaleString('en-US', { maximumFractionDigits: 3 })}` } } },
        scales: { x: { grid: { display: false }, ticks: { font, color: '#5f6b7d', autoSkip: true, maxRotation: 45 } },
          y: { beginAtZero: true, grid: { color: 'rgba(26,47,92,.08)' }, ticks: { font, color: '#5f6b7d', callback: v => Number(v).toLocaleString('en-US') },
               title: { display: !!c.y_title, text: c.y_title, font, color: '#5f6b7d' } } } },
    }));
  }

  async function download(){
    if (!state || !state.opts.onDownload) return;
    const b = root.querySelector('.rv-dl'), span = b.querySelector('span');
    b.disabled = true; b.classList.add('busy'); span.textContent = t('downloading');
    try { await state.opts.onDownload(); b.classList.remove('busy'); b.classList.add('done'); span.textContent = t('downloaded'); }
    catch (e) {
      if (window.console) console.error(e);
      b.classList.remove('busy'); span.textContent = t('download');
      if (e && e.review){ setState('error', e.review); b.disabled = true; return; }
    }
    finally { setTimeout(() => { if (!root.hidden){ b.disabled = false; b.classList.remove('done'); span.textContent = t('download'); } }, 2200); }
  }

  function close(){
    if (!root || root.hidden) return;
    charts.forEach(c => c.destroy()); charts = [];
    root.classList.remove('in'); document.body.classList.remove('rv-open');
    const opener = state && state.opener; state = null;
    setTimeout(() => { root.hidden = true; root.querySelector('.rv-sheet').innerHTML = ''; }, reduced ? 0 : 180);
    if (opener && opener.focus) opener.focus();
  }

  window.ReportViewer = { open, close };
})();
