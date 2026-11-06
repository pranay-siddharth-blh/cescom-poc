// Immersive / responsive helpers ported from the KPCL / RDPR frontends, in the CESC website palette (#072577 navy, #e54825,
// #2271a0, #e38f00 amber, #0f7291 teal, #7c49ab violet, #d82221 red):
//   UX.chartCard(host, spec, opts)   a chart card with a 3-way chart-type toggle (bar / line / doughnut -- one slice
//                                    per year / category, one ring per series), gradient bars, eased animation, unit-aware
//                                    tooltips and ticks; an SVG fallback when Chart.js (CDN) did not load
//   UX.countUp(el, to, fmt, ms)      numbers that count up (ease-out), instantly with reduced motion
//   UX.spark(canvas, values, color)  a sparkline
//   UX.toast(msg, err)               a short status message
//   UX.onScroll(selector)            panels that fade in as they scroll into view
// spec = { labels, series: [{ label, values }], unit, title, type }; unit 'Rs' | 'Cr' | '%' | 'count' | 'units' | ...
(function(){
  'use strict';
  const reduced = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const PALETTE = ['#072577', '#e54825', '#2271a0', '#e38f00', '#0f7291', '#7c49ab', '#1725a5', '#d82221', '#5b667a', '#ffcd00'];
  const hc = () => document.body.classList.contains('hc');
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const kn = () => (document.documentElement.lang || 'en') === 'kn';
  const L = {
    en: { bar: 'Bar', line: 'Line', doughnut: 'Doughnut', noData: 'no data', chartType: 'Chart type', share: 'of total' },
    kn: { bar: 'ಬಾರ್', line: 'ರೇಖೆ', doughnut: 'ಡೋನಟ್', noData: 'ಮಾಹಿತಿ ಇಲ್ಲ', chartType: 'ಚಾರ್ಟ್ ಪ್ರಕಾರ', share: 'ಒಟ್ಟಿನಲ್ಲಿ' },
  };
  const tr = k => (kn() ? L.kn : L.en)[k];
  const ICONS = {
    bar: '<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>',
    line: '<path d="M3 17l6-6 4 4 8-8"/>',
    doughnut: '<circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="3.5"/><path d="M12 4v4.5"/>',
  };
  // Slice colours for doughnuts (one per category / year): navy and red families, then slate and green -- Cescom's own.
  const SLICES = ['#072577', '#2271a0', '#e38f00', '#0f7291', '#7c49ab', '#e54825', '#1725a5', '#ffcd00', '#151b54',
                  '#d82221', '#5b9bd5', '#f2b34a', '#4fb3c7', '#a88bd1', '#f08a70', '#8a94a6'];

  // ---------------------------------------------------------------- number formats
  function rupees(v, short){
    if (v == null || isNaN(v)) return tr('noData');
    const a = Math.abs(v), sign = v < 0 ? '-' : '';
    if (a >= 1e7) return `${sign}₹${(a / 1e7).toLocaleString('en-IN', { maximumFractionDigits: 2 })} ${short ? 'Cr' : 'crore'}`;
    if (a >= 1e5) return `${sign}₹${(a / 1e5).toLocaleString('en-IN', { maximumFractionDigits: 2 })} ${short ? 'L' : 'lakh'}`;
    return `${sign}₹${a.toLocaleString('en-IN', { maximumFractionDigits: 2 })}`;
  }
  function fmt(v, unit, short){
    if (v == null || isNaN(v)) return tr('noData');
    switch (unit){
      case 'Rs': return rupees(v, short);
      case 'Cr': return '₹' + Number(v).toLocaleString('en-IN', { maximumFractionDigits: 2 }) + ' Cr';
      case '%': return Number(v).toLocaleString('en-IN', { maximumFractionDigits: 2 }) + '%';
      case 'Rs/unit': return '₹' + Number(v).toFixed(2) + (short ? '' : ' per unit');
      case 'units': return Number(v).toLocaleString('en-IN', { maximumFractionDigits: 0 }) + (short ? '' : ' units');
      case 'kW': return Number(v).toLocaleString('en-IN', { maximumFractionDigits: 2 }) + ' kW';
      case 'count': return Number(v).toLocaleString('en-IN', { maximumFractionDigits: 0 });
      default: return Number(v).toLocaleString('en-IN', { maximumFractionDigits: 2 });
    }
  }

  // ---------------------------------------------------------------- count-up
  function countUp(el, to, format, ms){
    if (!el) return;
    const f = format || (v => Number(v).toLocaleString('en-IN', { maximumFractionDigits: 0 }));
    if (to == null || isNaN(to)){ el.textContent = '—'; return; }
    if (reduced){ el.textContent = f(to); return; }
    const t0 = performance.now(), dur = ms || 1100;
    el.classList.add('ux-num');
    const step = now => { const p = Math.min(1, (now - t0) / dur), e = 1 - Math.pow(1 - p, 3);
      el.textContent = f(to * e); if (p < 1) requestAnimationFrame(step); else el.textContent = f(to); };
    requestAnimationFrame(step);
  }

  // ---------------------------------------------------------------- chart types a spec can take
  // Every chart offers the same three: bar, line and doughnut. A doughnut has one slice per category / year; with
  // several series (LT-1 vs HT-2, Entity A vs B) each series is its own ring, slices coloured alike across rings.
  function typesFor(){ return ['bar', 'line', 'doughnut']; }
  const isRatio = spec => /^(%|Rs\/unit|ratio)$/.test(spec.unit || '') ||
    /%|ratio|rate|efficien|average|per unit/i.test((spec.title || '') + ' ' + (spec.series[0].label || ''));

  // ---------------------------------------------------------------- Chart.js drawing
  function chartConfig(ctx, spec, type){
    const isHc = hc(), ink = isHc ? '#000' : '#5b667a', grid = isHc ? '#000' : 'rgba(7,37,119,.07)';
    const font = { family: getComputedStyle(document.body).fontFamily, size: 11 };
    const multi = spec.series.length > 1, dough = type === 'doughnut', bar = type === 'bar';
    const h = ctx.canvas.clientHeight || 250;
    const ratio = isRatio(spec);
    const datasets = spec.series.map((s, i) => {
      const col = isHc ? ['#000', '#555', '#999'][i % 3] : ((spec.colors && spec.colors[i]) || PALETTE[i % PALETTE.length]);
      let bg = col;
      if (bar && !multi && !isHc){ const g = ctx.createLinearGradient(0, 0, 0, h); g.addColorStop(0, '#1e3fb0'); g.addColorStop(1, '#072577'); bg = g; }
      if (type === 'line' && !multi && !isHc){
        const g = ctx.createLinearGradient(0, 0, 0, h);
        g.addColorStop(0, col + '38'); g.addColorStop(1, 'rgba(255,255,255,0)'); bg = g;
      }
      if (dough){
        // slices can't be negative; the tooltip still reports the real figure
        return { label: s.label || spec.title || '', data: s.values.map(v => v == null ? 0 : Math.max(0, v)), raw: s.values,
                 backgroundColor: spec.labels.map((_, j) => isHc ? ['#000', '#555', '#999', '#ccc'][j % 4] : SLICES[j % SLICES.length]),
                 borderColor: '#fff', borderWidth: 2, hoverOffset: 8 };
      }
      return {
        label: s.label || spec.title || '', data: s.values,
        backgroundColor: bg,
        borderColor: bar ? 'transparent' : col,
        borderWidth: bar ? 0 : 2.2, borderRadius: bar ? 6 : 0, maxBarThickness: 44, minBarLength: bar ? 2 : 0,
        pointBackgroundColor: col, pointBorderColor: '#fff', pointBorderWidth: 1.5,
        pointRadius: bar ? 0 : (s.values.length > 24 ? 0 : 3.5), pointHoverRadius: 6,
        fill: type === 'line' && !multi, tension: .3, spanGaps: true,
      };
    });
    const doughLabel = x => {
      const real = x.dataset.raw ? x.dataset.raw[x.dataIndex] : x.raw;
      const tot = x.dataset.data.reduce((a, b) => a + (b || 0), 0);
      const share = !ratio && tot > 0 ? ` (${(x.raw / tot * 100).toFixed(1)}% ${tr('share')})` : '';
      return ' ' + (multi ? x.dataset.label + ' · ' : '') + x.label + ': ' + fmt(real, spec.unit) + share;
    };
    // Bar / line: every amount also as a percentage -- its share between the series at that point (A vs B), or of the
    // whole for a category chart, and the change on the year before for a fiscal-year axis (points for a % / ratio).
    const fyAxis = spec.labels.length > 1 && spec.labels.every(l => /^(FY\s*)?\d{4}-\d{2}$/.test(String(l)));
    const lineLabel = x => {
      let s = ' ' + (x.dataset.label || '') + ': ' + fmt(x.raw, spec.unit);
      const v = Number(x.raw);
      if (x.raw == null || !isFinite(v)) return s;
      if (!ratio && v >= 0) {
        const tot = multi ? x.chart.data.datasets.reduce((a, d) => a + Math.max(0, Number(d.data[x.dataIndex]) || 0), 0)
                          : (fyAxis ? 0 : x.dataset.data.reduce((a, b) => a + Math.max(0, Number(b) || 0), 0));
        if (tot > 0) s += ` (${(v / tot * 100).toFixed(1)}%${multi ? '' : ' ' + tr('share')})`;
      }
      if (fyAxis && x.dataIndex > 0) {
        const p = Number(x.dataset.data[x.dataIndex - 1]);
        if (x.dataset.data[x.dataIndex - 1] != null && isFinite(p)) {
          if (ratio) s += ` · ${v - p >= 0 ? '▲' : '▼'} ${Math.abs(v - p).toFixed(2)} pts`;
          else if (p) s += ` · ${v - p >= 0 ? '▲' : '▼'} ${Math.abs((v - p) / Math.abs(p) * 100).toFixed(1)}% YoY`;
        }
      }
      return s;
    };
    // a soft guide line under the pointer (RDPR)
    const guide = { id: 'guide', afterDatasetsDraw(ch){
      const act = ch.tooltip && ch.tooltip.getActiveElements(); if (dough || !act || !act.length) return;
      const el = act[0].element, { top, bottom, left, right } = ch.chartArea, g = ch.ctx, h = ch.options.indexAxis === 'y';
      g.save(); g.strokeStyle = isHc ? '#000' : 'rgba(7,37,119,.28)'; g.setLineDash([4, 4]); g.lineWidth = 1;
      g.beginPath(); if (h){ g.moveTo(left, el.y); g.lineTo(right, el.y); } else { g.moveTo(el.x, top); g.lineTo(el.x, bottom); }
      g.stroke(); g.restore(); } };
    const narrow = (ctx.canvas.parentElement && ctx.canvas.parentElement.clientWidth || window.innerWidth) < 560;
    const horiz = !dough && isHorizontal(spec, type);
    const catAxis = horiz ? 'y' : 'x', valAxis = horiz ? 'x' : 'y';
    const width = (ctx.canvas.parentElement && ctx.canvas.parentElement.clientWidth) || 600;
    const crowded = fyAxis && width / Math.max(1, spec.labels.length) < 58;
    const maxLen = horiz ? (width < 480 ? 16 : 26) : (width / Math.max(1, spec.labels.length) < 70 ? 10 : 18);
    const catLabel = l => {
      let t = String(l ?? '');
      if (fyAxis){ t = t.replace(/^FY\s*/, ''); return crowded ? t.slice(2) : t; }
      return t.length > maxLen ? t.slice(0, maxLen - 1).trimEnd() + '…' : t;
    };
    const allVals = spec.series.flatMap(x => x.values).filter(v => v != null && isFinite(v));
    const vMin = allVals.length ? Math.min(...allVals) : 0, vMax = allVals.length ? Math.max(...allVals) : 0;
    const zeroBase = !(type === 'line' && ratio && vMin > 0 && (vMax - vMin) < vMax * 0.5);
    const lowEdge = zeroBase ? undefined : Math.max(0, vMin - (vMax - vMin) * 0.25);
    return {
      type: dough ? 'doughnut' : type,
      data: { labels: spec.labels, datasets },
      plugins: [guide],
      options: {
        responsive: true, maintainAspectRatio: false, cutout: dough ? (multi ? '38%' : '62%') : undefined, radius: dough ? '74%' : undefined, layout: { padding: dough ? 6 : 0 },
        animation: { duration: reduced ? 0 : 750, easing: 'easeOutQuart', delay: c => reduced || c.type !== 'data' || c.mode !== 'default' ? 0 : c.dataIndex * 22 },
        interaction: dough ? { mode: 'nearest', intersect: true } : { mode: 'index', intersect: false },
        plugins: {
          legend: { display: dough || multi, position: dough && !narrow ? 'right' : 'bottom',
                    labels: { color: ink, boxWidth: 10, boxHeight: 10, usePointStyle: true, pointStyle: 'rectRounded', font, padding: dough ? 6 : 12 } },
          tooltip: { backgroundColor: isHc ? '#000' : '#151b54', padding: 10, cornerRadius: 8, titleFont: { ...font, weight: '700' }, bodyFont: font,
            callbacks: dough ? { title: () => '', label: doughLabel } : { title: items => items.length ? String(items[0].label) : '', label: lineLabel } },
        },
        indexAxis: horiz ? 'y' : 'x',
        scales: dough ? {} : {
          [catAxis]: { ticks: { color: ink, font, autoSkip: true, autoSkipPadding: 10, maxRotation: horiz || fyAxis ? 0 : 30, minRotation: 0,
                                callback: function(v){ return catLabel(this.getLabelForValue(v)); } },
                       grid: { display: false }, border: { color: isHc ? '#000' : '#d0d7e6' } },
          [valAxis]: { beginAtZero: zeroBase, suggestedMin: zeroBase ? undefined : lowEdge, grace: '6%', border: { display: false },
                       grid: { color: grid }, ticks: { color: ink, font, maxTicksLimit: horiz ? 5 : 6, callback: v => fmt(v, spec.unit, true) } },
        },
      },
    };
  }

  // Axis layout (RDPR / KPCL reading): a category chart with many or long names is drawn as horizontal bars, so every
  // name reads left to right; a fiscal-year axis is never tilted (shortened to 15-16 when crowded); long names are cut
  // to fit with the full name in the tooltip; a line of percentages zooms to its own range instead of hugging zero.
  function isHorizontal(spec, type){
    const fy = spec.labels.length > 1 && spec.labels.every(l => /^(FY\s*)?\d{4}-\d{2}$/.test(String(l)));
    if (type !== 'bar' || fy) return false;
    const longest = Math.max(...spec.labels.map(l => String(l).length));
    return spec.labels.length > 6 || longest > 14;
  }

  // SVG fallback doughnut (no CDN): the first series, one slice per label, with a small legend.
  function svgDoughnut(spec){
    const vals = spec.series[0].values.map(v => v == null ? 0 : Math.max(0, v)), tot = vals.reduce((a, b) => a + b, 0) || 1;
    const cx = 125, cy = 125, R = 105, r = 60;
    let a0 = -Math.PI / 2, s = `<svg viewBox="0 0 520 250" role="img" aria-label="${esc(spec.title || '')}">`;
    vals.forEach((v, i) => {
      const a1 = a0 + v / tot * Math.PI * 2, big = a1 - a0 > Math.PI ? 1 : 0, c = SLICES[i % SLICES.length];
      const p = (rad, ang) => `${cx + rad * Math.cos(ang)},${cy + rad * Math.sin(ang)}`;
      if (v > 0) s += `<path d="M${p(R, a0)} A${R},${R} 0 ${big} 1 ${p(R, a1)} L${p(r, a1)} A${r},${r} 0 ${big} 0 ${p(r, a0)} Z" fill="${c}" stroke="#fff" stroke-width="2">`
        + `<title>${esc(spec.labels[i])}: ${esc(fmt(spec.series[0].values[i], spec.unit))}</title></path>`;
      a0 = a1;
    });
    const rows = Math.min(vals.length, 12);
    spec.labels.slice(0, rows).forEach((l, i) => {
      s += `<rect x="270" y="${14 + i * 19}" width="10" height="10" rx="2" fill="${SLICES[i % SLICES.length]}"/>`
        + `<text x="286" y="${23 + i * 19}">${esc(String(l).slice(0, 34))}</text>`;
    });
    return s + '</svg>';
  }

  // SVG fallback (no CDN): bars or a line, same palette.
  function svgChart(spec, type){
    if (type === 'doughnut') return svgDoughnut(spec);
    const vals = spec.series[0].values, n = spec.labels.length, W = 820, H = 250, Lm = 70, B = 56, T = 12, R = 10, pw = W - Lm - R, ph = H - T - B;
    const v0 = vals.map(v => v == null ? 0 : v), mx = Math.max(...v0, 0), mn = Math.min(0, ...v0);
    const p = Math.pow(10, Math.floor(Math.log10(Math.max(Math.abs(mx), 1e-9)))), max = mx > 0 ? Math.ceil(mx / p) * p : 1;
    const y = v => T + ph - (v - mn) / ((max - mn) || 1) * ph, stp = pw / Math.max(n, 1);
    let s = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="${esc(spec.title || '')}">`;
    for (let i = 0; i <= 4; i++){ const v = mn + (max - mn) * i / 4, yy = y(v);
      s += `<line x1="${Lm}" x2="${W - R}" y1="${yy}" y2="${yy}" stroke="#072577" opacity=".08"/><text x="${Lm - 6}" y="${yy + 4}" text-anchor="end">${esc(fmt(v, spec.unit, true))}</text>`; }
    if (type === 'line'){
      const pts = v0.map((v, i) => (Lm + stp * i + stp / 2) + ',' + y(v)).join(' ');
      s += `<polyline fill="none" stroke="#072577" stroke-width="2" points="${pts}"/>`;
      v0.forEach((v, i) => s += `<circle cx="${Lm + stp * i + stp / 2}" cy="${y(v)}" r="3.5" fill="#e38f00" stroke="#fff"><title>${esc(spec.labels[i])}: ${esc(fmt(vals[i], spec.unit))}</title></circle>`);
    } else {
      v0.forEach((v, i) => { const bw = Math.max(2, Math.min(44, stp * .7)), x = Lm + stp * i + (stp - bw) / 2;
        s += `<rect x="${x}" y="${y(Math.max(v, 0))}" width="${bw}" height="${Math.abs(y(v) - y(0))}" rx="4" fill="${vals[i] == null ? '#d0d7e6' : '#072577'}"><title>${esc(spec.labels[i])}: ${esc(fmt(vals[i], spec.unit))}</title></rect>`; });
    }
    const every = Math.ceil(n / 14);
    spec.labels.forEach((l, i) => { if (i % every) return;
      s += `<text transform="translate(${Lm + stp * i + stp / 2},${T + ph + 12}) rotate(35)">${esc(String(l).slice(0, 16))}</text>`; });
    return s + '</svg>';
  }

  function remember(key, val){ try { if (val === undefined) return localStorage.getItem(key); localStorage.setItem(key, val); } catch (_e) { return null; } }

  // ---------------------------------------------------------------- the chart card
  // opts: { titleText, storeKey (remember the chosen type), card: false (draw into host without a card frame),
  //         toggleHost (put the toggle there instead of the card head) }
  function chartCard(host, spec, opts){
    opts = opts || {};
    if (!spec || !spec.labels || !spec.labels.length || !spec.series || !spec.series.length) return null;
    let card = host, head = null, canvasWrap;
    if (opts.card === false){
      host.innerHTML = '';
      canvasWrap = document.createElement('div'); canvasWrap.className = 'ux-canvas'; canvasWrap.style.height = '100%';
      host.appendChild(canvasWrap);
    } else {
      card = document.createElement('div'); card.className = 'ux-card ux-reveal';
      head = document.createElement('div'); head.className = 'ux-head';
      const title = document.createElement('div'); title.className = 'ux-title'; { const tt = String(opts.titleText || spec.title || ''); title.textContent = tt.charAt(0).toUpperCase() + tt.slice(1); }
      head.appendChild(title);
      canvasWrap = document.createElement('div'); canvasWrap.className = 'ux-canvas';
      card.append(head, canvasWrap); host.appendChild(card);
    }
    const types = typesFor(spec);
    if (opts.toggleHost) opts.toggleHost.querySelectorAll(':scope > .ux-toggle').forEach(x => x.remove());   // a redraw replaces it
    const tog = document.createElement('div'); tog.className = 'ux-toggle'; tog.setAttribute('role', 'group'); tog.setAttribute('aria-label', tr('chartType'));
    const thumb = document.createElement('span'); thumb.className = 'ux-thumb'; thumb.setAttribute('aria-hidden', 'true'); tog.appendChild(thumb);
    const place = b => { if (!b || !b.offsetWidth) return; thumb.style.width = b.offsetWidth + 'px'; thumb.style.transform = `translateX(${b.offsetLeft}px)`; };
    (opts.toggleHost || head || host).appendChild(tog);
    let inst = null;
    const draw = type => {
      if (inst){ inst.destroy(); inst = null; }
      canvasWrap.innerHTML = ''; canvasWrap.classList.remove('swap'); void canvasWrap.offsetWidth; canvasWrap.classList.add('swap');
      if (!window.Chart){ canvasWrap.innerHTML = svgChart(spec, type); return; }
      if (opts.card !== false){
        const rows = isHorizontal(spec, type) ? spec.labels.length * Math.max(1, Math.min(spec.series.length, 3)) : 0;
        canvasWrap.style.height = rows ? Math.min(560, Math.max(250, rows * 26 + 50)) + 'px' : '';
      }
      const cv = document.createElement('canvas'); canvasWrap.appendChild(cv);
      inst = new Chart(cv.getContext('2d'), chartConfig(cv.getContext('2d'), spec, type));
      // several series as rings: say which ring is which (Chart.js draws the first series outermost)
      const old = canvasWrap.parentElement && canvasWrap.parentElement.querySelector(':scope > .ux-rings');
      if (old) old.remove();
      if (opts.card === false) canvasWrap.style.height = (type === 'doughnut' && spec.series.length > 1) ? 'calc(100% - 24px)' : '100%';
      if (type === 'doughnut' && spec.series.length > 1){
        const cap = document.createElement('div'); cap.className = 'ux-rings';
        const names = kn() ? ['ಹೊರ ಉಂಗುರ', 'ಮಧ್ಯ ಉಂಗುರ', 'ಒಳ ಉಂಗುರ'] : ['Outer ring', 'Middle ring', 'Inner ring'];
        const n = spec.series.length;
        cap.textContent = spec.series.map((s, i) => `${i === 0 ? names[0] : i === n - 1 ? names[2] : names[1]}: ${s.label}`).join(' · ');
        canvasWrap.insertAdjacentElement('afterend', cap);
      }
    };
    const saved = opts.storeKey ? remember('dcb-chart-' + opts.storeKey) : null;
    let initial = types.includes(saved) ? saved : (types.includes(spec.type) ? spec.type : 'bar');
    types.forEach(ty => {
      const b = document.createElement('button'); b.type = 'button'; b.dataset.type = ty;
      b.innerHTML = `<svg viewBox="0 0 24 24" aria-hidden="true">${ICONS[ty]}</svg><span>${esc(tr(ty))}</span>`;
      b.title = tr(ty);
      b.setAttribute('aria-pressed', ty === initial ? 'true' : 'false');
      b.onclick = () => {
        tog.querySelectorAll('button').forEach(x => x.setAttribute('aria-pressed', 'false'));
        b.setAttribute('aria-pressed', 'true'); place(b); draw(ty);
        if (opts.storeKey) remember('dcb-chart-' + opts.storeKey, ty);
      };
      tog.appendChild(b);
    });
    draw(initial);
    // the thumb sits under the chosen type: placed once the toggle has its size, and again when it resizes
    const placeNow = () => { tog.classList.add('no-anim'); place(tog.querySelector('button[aria-pressed="true"]')); requestAnimationFrame(() => tog.classList.remove('no-anim')); };
    requestAnimationFrame(placeNow);
    if ('ResizeObserver' in window){ const ro = new ResizeObserver(placeNow); ro.observe(tog); }
    return { card, redraw: () => draw(tog.querySelector('[aria-pressed="true"]').dataset.type), destroy: () => { if (inst) inst.destroy(); } };
  }

  // ---------------------------------------------------------------- sparkline
  function spark(canvas, values, color){
    if (!canvas || !window.Chart || !values || values.length < 2) return null;
    const ctx = canvas.getContext('2d'), g = ctx.createLinearGradient(0, 0, 0, canvas.clientHeight || 38);
    g.addColorStop(0, (color || '#072577') + '40'); g.addColorStop(1, 'rgba(255,255,255,0)');
    return new Chart(ctx, { type: 'line',
      data: { labels: values.map((_, i) => i), datasets: [{ data: values, borderColor: color || '#072577', backgroundColor: g, fill: true,
        borderWidth: 2, tension: .35, pointRadius: values.map((_, i) => i === values.length - 1 ? 3 : 0), pointBackgroundColor: color || '#072577' }] },
      options: { responsive: true, maintainAspectRatio: false, animation: { duration: reduced ? 0 : 900 },
        plugins: { legend: { display: false }, tooltip: { enabled: false } }, scales: { x: { display: false }, y: { display: false } } } });
  }

  // ---------------------------------------------------------------- toast
  let toastEl = null, toastTimer = null;
  function toast(msg, err){
    if (!toastEl){ toastEl = document.createElement('div'); toastEl.className = 'ux-toast'; toastEl.setAttribute('role', 'status');
      toastEl.setAttribute('aria-live', 'polite'); toastEl.innerHTML = '<b></b><span></span>'; document.body.appendChild(toastEl); }
    toastEl.classList.toggle('err', !!err);
    toastEl.firstElementChild.textContent = err ? '!' : '✓';
    toastEl.lastElementChild.textContent = msg;
    toastEl.classList.add('on'); clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toastEl.classList.remove('on'), err ? 5000 : 3000);
  }

  // ---------------------------------------------------------------- reveal on scroll
  function onScroll(selector){
    const els = document.querySelectorAll(selector);
    if (reduced || !('IntersectionObserver' in window)){ els.forEach(e => e.classList.add('in')); return; }
    const io = new IntersectionObserver(entries => entries.forEach(en => { if (en.isIntersecting){ en.target.classList.add('in'); io.unobserve(en.target); } }),
                                        { threshold: .08 });
    els.forEach(e => { e.classList.add('ux-onscroll'); io.observe(e); });
  }

  window.UX = { chartCard, countUp, spark, toast, onScroll, fmt, reduced, PALETTE };
})();
