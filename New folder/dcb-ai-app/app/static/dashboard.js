// dashboard.html: page script (a file, so the security policy can forbid inline scripts).
// The original Cescom dashboard (CLAUDE.md §9.8-9.12, §13.10, §14): three KPI cards with Demand trend and Collection
// efficiency under them, the Sub-divisions panel on the right (click one for its own dashboard, ?subdivision=; click it
// again, the back button or Dashboard to return), Top tariff categories beside Live installations, and the DCB
// indicators. Only the look is the RDPR / KPCL one (count-ups, sparklines, chart toggle, animated bars).
// Every figure comes from /api/dashboard_summary or /api/dashboard_summary/{sub-division} (app/dashboard_data.py).
const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const INR_CR = 1e7;
const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
const num = (v, d = 1) => v == null || isNaN(v) ? '—' : Number(v).toLocaleString('en-IN', { minimumFractionDigits: d, maximumFractionDigits: d });
const cr = v => v == null ? null : v / INR_CR;
const formatCr = v => v == null ? '—' : '₹' + num(cr(v), 2) + ' Cr';
const formatInt = v => v == null ? '—' : Math.round(v).toLocaleString('en-IN');
function formatRs(v){
  if (v == null) return '—';
  const a = Math.abs(v), s = v < 0 ? '-' : '';
  if (a >= 1e7) return s + '₹' + num(a / 1e7, 2) + ' Cr';
  if (a >= 1e5) return s + '₹' + num(a / 1e5, 2) + ' L';
  return s + '₹' + a.toLocaleString('en-IN', { maximumFractionDigits: 0 });
}
const pct = (v, d = 2) => v == null ? '—' : num(v, d) + '%';
const barPct = (v, denom) => !denom || v == null ? 0 : Math.max(0, Math.min(100, v / denom * 100));
// Sub-division colours: the CESC portal's own blues, amber, teal and violet.
const SUB_COLORS = ['#072577', '#2271a0', '#e38f00', '#0f7291', '#7c49ab', '#e54825', '#1725a5'];

// counts a figure up to its value; on a redraw it glides from the figure already shown
function countUp(el, to, d = 1, ms = 1100){
  if (!el) return;
  const tok = (el.__count = (el.__count || 0) + 1);
  if (to == null || isNaN(to)){ el.textContent = '—'; el.__v = null; return; }
  const from = el.__v == null ? 0 : el.__v; el.__v = to;
  if (reduced || from === to){ el.textContent = num(to, d); return; }
  const t0 = performance.now();
  const step = now => { if (el.__count !== tok) return;
    const p = Math.min(1, (now - t0) / ms), e = 1 - Math.pow(1 - p, 3);
    el.textContent = num(from + (to - from) * e, d); if (p < 1) requestAnimationFrame(step); };
  requestAnimationFrame(step);
}
function delta(pctChange, goodUp = true){
  if (pctChange == null || isNaN(pctChange)) return '';
  const up = pctChange >= 0, good = goodUp ? up : !up, cls = Math.abs(pctChange) < 0.05 ? 'flat' : good ? 'up' : 'down';
  return `<span class="chip ${cls}">${up ? '▲' : '▼'} ${num(Math.abs(pctChange), 1)}%</span>`;
}

const charts = {};
function chart(id, cfg){
  if (!window.Chart || !$(id)) return null;
  if (charts[id]) charts[id].destroy();
  charts[id] = new Chart($(id).getContext('2d'), cfg);
  return charts[id];
}
function spark(id, values, color, fillTop){
  const cv = $(id); if (!cv || !window.Chart || !values || values.length < 2) return;
  const ctx = cv.getContext('2d'), g = ctx.createLinearGradient(0, 0, 0, cv.clientHeight || 40);
  g.addColorStop(0, fillTop); g.addColorStop(1, 'rgba(255,255,255,0)');
  chart(id, { type: 'line', data: { labels: values.map((_, i) => i), datasets: [{ data: values, borderColor: color, backgroundColor: g, fill: true,
    borderWidth: 2, tension: .35, spanGaps: true, pointRadius: values.map((_, i) => i === values.length - 1 ? 3 : 0), pointBackgroundColor: color }] },
    options: { responsive: true, maintainAspectRatio: false, animation: { duration: reduced ? 0 : 900 }, plugins: { legend: { display: false }, tooltip: { enabled: false } },
      scales: { x: { display: false }, y: { display: false } } } });
}

// ------------------------------------------------------------------ state
// divisionData: the whole-division summary, fetched once (its sub-division list drives the Sub-divisions panel);
// currentSubdivision: null for the division, else the exact sub-division drilled into.
let divisionData = null, currentSubdivision = null;

async function loadDashboard(){
  try {
    const resp = await fetch('/api/dashboard_summary');
    if (resp.status === 401){ location.href = '/login'; return; }
    if (!resp.ok) throw new Error('Server returned ' + resp.status);
    divisionData = await resp.json();
    // shown before drawing, so every canvas is measured at its real size
    $('skeleton').hidden = true; $('dashBody').hidden = false;
    const requested = new URLSearchParams(location.search).get('subdivision');
    if (requested) await showSubdivisionView(requested, false); else showGeneralView(false);
  } catch (err) {
    $('errorBanner').textContent = 'Could not load the dashboard: ' + err.message;
    $('errorBanner').hidden = false; $('skeleton').hidden = true;
  } finally {
    if (window.Shell) Shell.ready();
  }
}

function showGeneralView(pushState){
  currentSubdivision = null;
  $('eyebrow').textContent = 'Dashboard · Holenarasipura Division';
  $('dashTitle').textContent = 'Dashboard';
  $('fyLine').innerHTML = `Demand, Collection &amp; Balance — Holenarasipura Division · FY <b>${esc(divisionData.fiscal_year)}</b>`;
  $('backToGeneralBtn').hidden = true;
  $('askAiBtn').href = '/chat?q=' + encodeURIComponent('latest figures');
  renderScopedPanels(divisionData);
  renderSubdivisionsList();
  if (pushState !== false){ const u = new URL(location); u.searchParams.delete('subdivision'); history.pushState({ subdivision: null }, '', u); }
}

async function showSubdivisionView(name, pushState){
  document.body.classList.add('is-loading');
  try {
    const resp = await fetch('/api/dashboard_summary/' + encodeURIComponent(name));
    if (resp.status === 401){ location.href = '/login'; return; }
    if (!resp.ok){ const b = await resp.json().catch(() => ({})); throw new Error(b.error || ('Server returned ' + resp.status)); }
    const data = await resp.json();
    currentSubdivision = data.subdivision_name;
    const isActive = data.last_fiscal_year === divisionData.fiscal_year;
    $('eyebrow').textContent = 'Sub-division dashboard · Demand, Collection & Balance';
    $('dashTitle').textContent = data.subdivision_name;
    $('fyLine').innerHTML = isActive ? `Sub-division view · FY <b>${esc(data.fiscal_year)}</b>`
      : `Sub-division view · Closed after ${esc(data.last_fiscal_year)} — showing FY <b>${esc(data.fiscal_year)}</b> (last active year)`;
    $('backToGeneralBtn').hidden = false;
    $('askAiBtn').href = '/chat?q=' + encodeURIComponent(`show me ${data.subdivision_name.toLowerCase()} numbers`);
    renderScopedPanels(data);
    renderSubdivisionsList();
    $('errorBanner').hidden = true;
    if (pushState !== false){ const u = new URL(location); u.searchParams.set('subdivision', data.subdivision_name); history.pushState({ subdivision: data.subdivision_name }, '', u); }
  } catch (err) {
    $('errorBanner').textContent = 'Could not load ' + name + ': ' + err.message;
    $('errorBanner').hidden = false;
    showGeneralView(true);
  } finally { document.body.classList.remove('is-loading'); }
}

window.addEventListener('popstate', () => {
  if (!divisionData) return;
  const name = new URLSearchParams(location.search).get('subdivision');
  if (name) showSubdivisionView(name, false); else showGeneralView(false);
});


// First screen: on screens at least 1100 px wide and 640 px tall, the top area (KPI cards, Demand trend, Collection
// efficiency, Sub-divisions) is sized to end at the bottom of the window, so nothing in it is cut off; the charts flex
// to fill it and the rest of the dashboard follows below. Smaller screens keep the natural flow.
function fitTop(){
  const area = document.querySelector('.top-area'), main = $('main');
  if (!area || $('dashBody').hidden) return;
  if (innerWidth < 1100 || innerHeight < 640){ area.classList.remove('fit'); area.style.height = ''; return; }
  const top = area.getBoundingClientRect().top - main.getBoundingClientRect().top + main.scrollTop;
  area.style.height = Math.max(440, Math.floor(main.clientHeight - top - 18)) + 'px';
  area.classList.add('fit');
}
let fitTimer = null;
window.addEventListener('resize', () => { clearTimeout(fitTimer); fitTimer = setTimeout(fitTop, 80); });

// ------------------------------------------------------------------ the panels both views share (same JSON shape)
function renderScopedPanels(data){
  fitTop();
  const k = data.kpis, yearly = data.yearly_demand || [];
  countUp($('kpiDemand'), cr(k.total_demand), 2);
  countUp($('kpiCollection'), cr(k.total_collection), 2);
  countUp($('kpiBalance'), cr(k.outstanding_balance), 2);
  $('kpiDemandDelta').innerHTML = delta(k.total_demand_yoy_pct, true);
  $('kpiCollectionDelta').innerHTML = delta(k.total_collection_yoy_pct, true);
  $('kpiBalanceDelta').innerHTML = delta(k.outstanding_balance_yoy_pct, false);
  // amount and %: each KPI also says what share of the year's demand it is
  const eff = k.collection_efficiency_pct;
  const live = data.indicators && data.indicators.current ? data.indicators.current.live_installation_pct : null;
  $('kpiDemandFoot').textContent = k.total_installations ? `${formatInt(k.total_installations)} installations` + (live != null ? ` · ${num(live, 1)}% live` : '') : '';
  $('kpiCollectionFoot').textContent = eff != null ? `${num(eff, 1)}% of demand collected` : '';
  $('kpiBalanceFoot').textContent = eff != null ? `${num(100 - eff, 1)}% of demand not yet collected` : '';
  const col = key => yearly.map(y => y[key] == null ? null : y[key] / INR_CR);
  spark('sparkDemand', col('demand_total'), '#ffcd00', 'rgba(255,205,0,.32)');
  spark('sparkCollection', col('collection_grand_total'), '#1f9d57', 'rgba(31,157,87,.2)');
  spark('sparkBalance', col('closing_balance_total'), '#d82221', 'rgba(216,34,33,.18)');
  $('effAsof').textContent = 'FY ' + data.fiscal_year;
  renderDemandChart(yearly);
  renderEfficiencyDonut(eff);
  renderTariffs(data.top_tariff_categories || []);
  renderLiveChart(data.yearly_live_installations || []);
  renderIndicators(data.indicators);
  requestAnimationFrame(fitTop);   // the heading may have changed height (a sub-division's longer title)
}

// The Sub-divisions panel: every active sub-division / section; a row's bar is collected % + outstanding % of THAT
// row's own demand (always the full width). Click a row for its dashboard; click it again to go back.
function renderSubdivisionsList(){
  const host = $('subdivisionRows');
  host.innerHTML = divisionData.subdivisions.map((s, i) => {
    const collected = s.collection_grand_total || 0, demand = s.demand_total || 0, outstanding = Math.max(0, demand - collected);
    const cp = barPct(collected, demand), op = barPct(outstanding, demand), color = SUB_COLORS[i % SUB_COLORS.length];
    const sub = s.is_active ? `${formatCr(collected)} collected (${num(cp, 1)}%) · ${formatCr(outstanding)} outstanding (${num(op, 1)}%)`
                            : `${formatCr(collected)} collected · FY ${esc(s.last_fiscal_year)} (last active year)`;
    const effCls = s.efficiency_pct == null ? '' : s.efficiency_pct >= 95 ? 'pct-good' : s.efficiency_pct >= 90 ? 'pct-warn' : 'pct-bad';
    return `<button type="button" class="sd-row${s.subdivision_name === currentSubdivision ? ' sel' : ''}" data-s="${esc(s.subdivision_name)}" style="animation:rise .45s ease both;animation-delay:${i * 50}ms">
      <span class="sd-top"><span class="sd-ic" style="background:${color}">${esc(s.subdivision_name.slice(0, 2).toUpperCase())}</span>
        <span class="sd-main"><span class="sd-name">${esc(s.subdivision_name)}${s.is_active ? '' : '<span class="badge-closed">Closed</span>'}</span><span class="sd-sub">${sub}</span></span>
        <span class="sd-eff ${effCls}">${pct(s.efficiency_pct, 1)}</span></span>
      ${demand > 0 ? `<span class="split"><b class="c" data-w="${cp}"></b><b class="o" data-w="${op}"></b></span>` : '<span class="split nodata"></span>'}
    </button>`;
  }).join('');
  host.querySelectorAll('.sd-row').forEach(b => b.onclick = () => {
    if (currentSubdivision === b.dataset.s) showGeneralView(); else showSubdivisionView(b.dataset.s);
  });
  requestAnimationFrame(() => requestAnimationFrame(() => host.querySelectorAll('[data-w]').forEach(b => { b.style.width = b.dataset.w + '%'; })));
}

// Demand trend and Live installations: chart cards with the Bar / Line / Doughnut toggle in the panel head.
let demandCard = null, liveCard = null;
function renderDemandChart(yearly){
  if (demandCard) demandCard.destroy();
  $('demandHint').textContent = (currentSubdivision ? `${yearly.length} fiscal years, ${currentSubdivision}` : `All ${yearly.length} fiscal years, division total`) + ' · ₹ Cr';
  demandCard = UX.chartCard($('demandBox'), {
    labels: yearly.map(y => y.fiscal_year), unit: 'Cr', type: 'bar', title: 'Demand (₹ Cr)',
    series: [{ label: 'Demand (₹ Cr)', values: yearly.map(y => y.demand_total == null ? null : Math.round(y.demand_total / INR_CR * 100) / 100) }],
  }, { card: false, toggleHost: $('demandHead'), storeKey: 'dash-demand' });
}
function renderLiveChart(yearly){
  if (liveCard) liveCard.destroy();
  $('liveHint').textContent = currentSubdivision ? `${yearly.length} fiscal years, ${currentSubdivision}` : `All ${yearly.length} fiscal years, division total`;
  liveCard = UX.chartCard($('liveBox'), {
    labels: yearly.map(y => y.fiscal_year), unit: 'count', type: 'bar', title: 'Live installations',
    series: [{ label: 'Live installations', values: yearly.map(y => y.live_installations == null ? null : Math.round(y.live_installations)) }],
  }, { card: false, toggleHost: $('liveHead'), storeKey: 'dash-live' });
}
function renderEfficiencyDonut(eff){
  countUp($('donutPct'), eff, 1);
  const collected = eff ?? 0;
  chart('efficiencyDonut', { type: 'doughnut',
    data: { labels: ['Collected', 'Outstanding'], datasets: [{ data: [collected, Math.max(0, 100 - collected)], backgroundColor: ['#072577', '#e2e6f0'], borderWidth: 0, hoverOffset: 6 }] },
    options: { responsive: true, maintainAspectRatio: false, cutout: '74%', animation: { duration: reduced ? 0 : 1100, easing: 'easeOutQuart', animateRotate: true },
      plugins: { legend: { display: false }, tooltip: { backgroundColor: '#151b54', callbacks: { label: x => ` ${x.label}: ${num(x.raw, 1)}%` } } } } });
}

// Top tariff categories (booklet names), with the share of demand and of consumers.
function renderTariffs(list){
  $('tariffRows').innerHTML = list.map((t, i) => `<div class="tr-row" style="animation:rise .45s ease both;animation-delay:${i * 50}ms">
      <span class="tr-ic">${esc(t.tariff_code.slice(0, 2).toUpperCase())}</span>
      <span class="tr-main"><span class="tr-title">${esc(t.tariff_code)}${t.tariff_name ? ` <span>· ${esc(t.tariff_name)}</span>` : ''}</span>
        <span class="tr-sub">${formatCr(t.demand)} demand${t.installations != null ? ` · ${formatInt(t.installations)} consumers` + (t.installation_share_pct != null ? ` (${t.installation_share_pct < 0.1 ? '<0.1' : num(t.installation_share_pct, 1)}%)` : '') : ''}</span></span>
      <span class="chip up">${t.share_pct == null ? '—' : num(t.share_pct, 1) + '%'}</span></div>`).join('')
    || '<p class="ind-note">No tariff figures for this selection.</p>';
}

// DCB indicators: the sheet's own percentages beside the amounts, FPPCA, and DC / MNR meters (values from the mart,
// where each percentage reproduces the printed one -- build check).
function ptsDelta(cur, prev, goodUp){
  if (cur == null || prev == null) return '';
  const d = cur - prev, cls = Math.abs(d) < 0.005 || goodUp === null ? 'flat' : ((d > 0) === goodUp ? 'good' : 'bad');
  return `<span class="ind-delta ${cls}">${d >= 0 ? '▲' : '▼'} ${num(Math.abs(d), 2)} pts</span>`;
}
function renderIndicators(ind){
  const host = $('indPct');
  if (!ind || !ind.current){ host.innerHTML = '<p class="ind-note">No indicator figures for this selection.</p>'; $('indPer').innerHTML = '';
    $('indFppca').innerHTML = ''; $('indMeters').innerHTML = ''; $('indMetersNote').textContent = ''; return; }
  const c = ind.current, p = ind.previous || {};
  $('indYear').textContent = `FY ${ind.fiscal_year}` + (ind.previous_fiscal_year ? ` · change vs ${ind.previous_fiscal_year}` : '');
  const rows = [
    ['Collection efficiency', 'collection_efficiency_pct', true, ''],
    ['Collection efficiency without adjustment (cash)', 'collection_efficiency_without_adj_pct', true, ''],
    ['Government subsidy (collected by adjustment)', 'adjustment_share_pct', null, 'gold'],
    ['Billing efficiency (billed ÷ live)', 'billing_efficiency_pct', true, 'green'],
    ['AT&C loss', 'atc_loss_pct', false, 'red'],
    ['Live installations', 'live_installation_pct', true, 'green'],
    ['Installations billed', 'billed_installation_pct', true, 'green'],
    ['Metered consumption', 'metered_consumption_pct', true, ''],
    ['Assessed (unmetered) consumption', 'assessed_consumption_pct', false, 'red'],
    ['DC/MNR installations (as printed)', 'dc_mnr_installation_pct', false, 'red'],
  ];
  host.innerHTML = rows.map(([label, col, goodUp, colour]) => `<div class="ind-item"><span class="ind-label">${esc(label)}</span>
      <span class="ind-val">${pct(c[col])}${ptsDelta(c[col], p[col], goodUp)}</span>
      <div class="ind-track"><div class="ind-fill ${colour}" data-w="${Math.max(0, Math.min(100, c[col] || 0))}"></div></div></div>`).join('');
  $('indPer').innerHTML = `<div>Demand per installation<b>${formatRs(c.demand_per_installation_rs)}</b></div>
    <div>Collection per installation<b>${formatRs(c.collection_per_installation_rs)}</b></div>
    <div>Arrears per installation<b>${formatRs(c.arrears_per_installation_rs)}</b></div>`;
  requestAnimationFrame(() => requestAnimationFrame(() => host.querySelectorAll('.ind-fill').forEach(b => { b.style.width = b.dataset.w + '%'; })));
  const refund = c.demand_fac != null && c.demand_fac < 0;
  $('indFppca').innerHTML = `
    <div class="ind-stat${refund ? ' warn' : ''}"><div class="k">Billed</div><div class="v">${formatRs(c.demand_fac)}</div>
      <div class="s">${pct(c.fac_share_of_demand_pct)} of demand${refund ? ' · refund year' : ''}</div></div>
    <div class="ind-stat"><div class="k">Collected</div><div class="v">${formatRs(c.collection_fac_total)}</div>
      <div class="s">${pct(c.fac_collection_efficiency_pct, 1)} of FPPCA billed</div></div>
    <div class="ind-stat"><div class="k">Outstanding (year end)</div><div class="v">${formatRs(c.closing_balance_fac)}</div>
      <div class="s">${p.closing_balance_fac != null ? 'was ' + formatRs(p.closing_balance_fac) + ' a year earlier' : ''}</div></div>
    <div class="ind-stat"><div class="k">Per unit consumed</div><div class="v">${c.fac_per_unit_rs == null ? '—' : '₹' + c.fac_per_unit_rs.toFixed(2)}</div>
      <div class="s">${p.fac_per_unit_rs != null ? 'was ₹' + p.fac_per_unit_rs.toFixed(2) : ''}</div></div>`;
  const sep = c.mnr_installations != null;
  $('indMeters').innerHTML = `
    <div class="ind-stat"><div class="k">MNR installations</div><div class="v">${sep ? formatInt(c.mnr_installations) : '—'}</div>
      <div class="s">${sep ? pct(c.mnr_installation_pct) + ' of installations' : 'printed separately from 2018-19'}</div></div>
    <div class="ind-stat"><div class="k">DC installations</div><div class="v">${formatInt(sep ? c.dc_installations : c.dc_mnr_installations)}</div>
      <div class="s">${pct(c.dc_mnr_installation_pct)} of installations</div></div>
    <div class="ind-stat"><div class="k">Assessed consumption</div><div class="v">${c.consumption_assessed == null ? '—' : formatInt(c.consumption_assessed) + ' units'}</div>
      <div class="s">${pct(c.assessed_consumption_pct)} of all consumption</div></div>
    <div class="ind-stat"><div class="k">Installations billed</div><div class="v">${formatInt(c.billed_installations)}</div>
      <div class="s">of ${formatInt(c.total_installations)} (${pct(c.billed_installation_pct, 1)})</div></div>`;
  $('indMetersNote').textContent = (sep ? '' : 'Up to 2017-18 DC and MNR are printed together; the DC figure shown is that combined column. ')
    + 'DC = direct connection: supply given without a meter while a faulty meter awaits replacement (KERC: new meter within 3 days).';
}

// ------------------------------------------------------------------ MIS report (Excel) of what is on screen
// View opens the very workbook the download writes (report-viewer.js); a 409 means a check inside it failed and it is
// withheld ("under review"), never handed out wrong.
const misQuery = () => currentSubdivision ? '?subdivision=' + encodeURIComponent(currentSubdivision) : '';
async function downloadMis(){
  const r = await fetch('/api/mis-report' + misQuery());
  if (r.status === 409){ const b = await r.json().catch(() => ({})); const e = new Error('under review'); e.review = b.message || 'Report under review'; throw e; }
  if (r.status === 401){ location.href = '/login'; return; }
  if (!r.ok) throw new Error('HTTP ' + r.status);
  const blob = await r.blob();
  const m = (r.headers.get('Content-Disposition') || '').match(/filename="([^"]+)"/);
  const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = m ? m[1] : 'DCB_MIS.xlsx';
  document.body.appendChild(a); a.click(); setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 800);
  if (window.Shell) Shell.toast('MIS report downloaded · ' + a.download);
}
$('misView').onclick = () => {
  if (!window.ReportViewer) return;
  ReportViewer.open({ viewUrl: '/api/mis-report/view' + misQuery(), opener: $('misView'), onDownload: downloadMis,
                      title: (currentSubdivision || 'Holenarasipura Division') + ' — MIS report' });
};
$('misReportBtn').onclick = async () => {
  const b = $('misReportBtn'); if (b.classList.contains('busy')) return;
  b.classList.add('busy'); b.disabled = true;
  try { await downloadMis(); }
  catch (e) { if (window.Shell) Shell.toast(e && e.review ? e.review : 'The MIS report could not be prepared. Please try again.', true); }
  finally { b.classList.remove('busy'); b.disabled = false; }
};
$('backToGeneralBtn').onclick = () => showGeneralView();

loadDashboard();
// FastAPI's /docs exists only with DCB_SHOW_TECHNICAL_DETAILS=true; show its link only then.
fetch('/health').then(r => r.json()).then(h => { if (h && h.technical) $('apiDocsLink').hidden = false; }).catch(() => {});
