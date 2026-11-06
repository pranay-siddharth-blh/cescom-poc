// reports.html's page script (moved out of the HTML unchanged so the content-security policy can forbid
// inline scripts -- see app/protection.py).
// The page shell (header, sidebar, transitions, language) is nav.js; this file only draws the comparison.

const INR_CR = 10000000;
let mode = 'subdivision'; // 'subdivision' | 'tariff'
let options = null; // {subdivisions:[...], tariff_categories:[...]}
let demandChart, efficiencyChart, installationsChart;

function currentOptionsList(){
  return mode === 'subdivision' ? options.subdivisions : options.tariff_categories;
}

// tariff categories carry the booklet's name ("LT-4(a) - Irrigation pump sets up to 10 HP"); the value stays the code
const optionLabel = v => (mode === 'tariff' && options.tariff_labels && options.tariff_labels[v]) || v;
function populateSelects(){
  const list = currentOptionsList();
  const selA = document.getElementById('selectA');
  const selB = document.getElementById('selectB');
  const html = list.map(v => `<option value="${v}">${optionLabel(v)}</option>`).join('');
  selA.innerHTML = html;
  selB.innerHTML = html;
  selA.selectedIndex = 0;
  selB.selectedIndex = list.length > 1 ? 1 : 0;
}

async function fetchSeries(value){
  const resp = await fetch(`/api/reports_series?entity_type=${mode}&value=${encodeURIComponent(value)}`);
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    throw new Error(body.error || ('Server returned ' + resp.status));
  }
  return resp.json();
}

let perCharts = [];
function destroyCharts(){
  [demandChart, efficiencyChart, installationsChart, ...perCharts].forEach(c => c && c.destroy());
  perCharts = [];
}

// The two series over the UNION of their fiscal years (a closed sub-division -- Saligrama, 2015-16 to 2017-18 -- no
// longer cuts the comparison down to its own three years); a year one side has no figures for is a gap, not a zero.
function align(seriesA, seriesB){
  const years = [...new Set([...seriesA.fiscal_years, ...seriesB.fiscal_years])].sort();
  const pick = s => key => years.map(y => { const i = s.fiscal_years.indexOf(y); return i < 0 || !s[key] ? null : s[key][i]; });
  return { years, a: pick(seriesA), b: pick(seriesB) };
}

// Side by side (#1 consumers, #2 amount and %): the latest year both have figures for.
function renderSideTable(seriesA, seriesB){
  const common = seriesA.fiscal_years.filter(y => seriesB.fiscal_years.includes(y));
  const table = document.getElementById('sideTable');
  if (!common.length){ table.innerHTML = '<tr><td>These two have no fiscal year in common.</td></tr>'; document.getElementById('sideYear').textContent = ''; return; }
  const fy = common[common.length - 1];
  document.getElementById('sideYear').textContent = 'FY ' + fy;
  const at = s => key => (s[key] || [])[s.fiscal_years.indexOf(fy)];
  const A = at(seriesA), B = at(seriesB);
  const cr = v => v == null ? '—' : '₹' + (v / INR_CR).toLocaleString('en-IN', { maximumFractionDigits: 2 }) + ' Cr';
  const rs = v => v == null ? '—' : '₹' + Math.round(v).toLocaleString('en-IN');
  const n = v => v == null ? '—' : Math.round(v).toLocaleString('en-IN');
  const p = (num, den) => (num == null || !den) ? '' : `<small>${(num / den * 100).toFixed(1)}%</small>`;
  const pc = v => v == null ? '—' : v.toFixed(2) + '%';
  const tag = v => v == null ? '' : `<small>${v.toFixed(1)}%</small>`;
  const both = A('demand') != null && B('demand') != null ? A('demand') + B('demand') : null;
  const consumers = (A('installations') || 0) + (B('installations') || 0);
  const coll = s => s('collection');
  // every amount with its percentage (#2 consumer population, #3 amount and %): the small figure is the share
  const rows = [
    ['Consumer population (installations) — % of the two', n(A('installations')) + p(A('installations'), consumers), n(B('installations')) + p(B('installations'), consumers)],
    ['  … % of the whole division', pc(A('consumer_share_pct')), pc(B('consumer_share_pct'))],
    ['  … growth on the year before', pc(A('consumer_growth_pct')), pc(B('consumer_growth_pct'))],
    ['Live consumers — % of own consumers', n(A('live_installations')) + p(A('live_installations'), A('installations')), n(B('live_installations')) + p(B('live_installations'), B('installations'))],
    ['Billed consumers — % of own consumers', n(A('billed_installations')) + tag(A('billed_pct')), n(B('billed_installations')) + tag(B('billed_pct'))],
    ['Demand — % of the two', cr(A('demand')) + p(A('demand'), both), cr(B('demand')) + p(B('demand'), both), 'sep'],
    ['  … % of the whole division', pc(A('demand_share_pct')), pc(B('demand_share_pct'))],
    ['Collection — % of own demand', cr(coll(A)) + p(coll(A), A('demand')), cr(coll(B)) + p(coll(B), B('demand'))],
    ['  … cash / cheque — % of own demand', cr(A('cash_collection')) + tag(A('cash_efficiency_pct')), cr(B('cash_collection')) + tag(B('cash_efficiency_pct'))],
    ['  … by adjustment (subsidy) — % of collection', cr(A('adjustment_collection')) + tag(A('adjustment_share_pct')), cr(B('adjustment_collection')) + tag(B('adjustment_share_pct'))],
    ['Billing efficiency (billed ÷ live)', pc(A('billing_efficiency_pct')), pc(B('billing_efficiency_pct')), 'sep'],
    ['AT&C loss', pc(A('atc_loss_pct')), pc(B('atc_loss_pct'))],
    ['Demand per consumer', rs(A('demand_per_installation')), rs(B('demand_per_installation')), 'sep'],
    ['Collection per consumer', rs(A('collection_per_installation')), rs(B('collection_per_installation'))],
    ['Arrears per consumer', rs(A('arrears_per_installation')), rs(B('arrears_per_installation'))],
  ];
  const lab = s => mode === 'tariff' ? optionLabel(s.label) : s.label;
  table.innerHTML = `<thead><tr><th></th><th class="a">${lab(seriesA)}</th><th class="b">${lab(seriesB)}</th></tr></thead><tbody>`
    + rows.map(([l, a, b, cls]) => `<tr class="${cls || ''}"><td>${l}</td><td>${a}</td><td>${b}</td></tr>`).join('') + '</tbody>';
}

// Each comparison chart gets KPCL's chart-type toggle (Bar / Line / Doughnut) in its panel head, with eased animation and
// unit-aware tooltips (ux.js); this page's own Entity A / B colours are kept. The chosen type is remembered per chart.
function renderCharts(seriesA, seriesB){
  destroyCharts();
  renderSideTable(seriesA, seriesB);
  const al = align(seriesA, seriesB);
  const labels = al.years;
  const colors = ['#072577', '#e54825'];   // Entity A: CESC navy, Entity B: CESC orange
  const two = (key, f) => [{ label: seriesA.label, values: al.a(key).map(f || (v => v)) },
                           { label: seriesB.label, values: al.b(key).map(f || (v => v)) }];
  const cr = v => v == null ? null : Math.round(v / INR_CR * 100) / 100;
  const box = id => document.getElementById(id);
  demandChart = UX.chartCard(box('demandBox'), { labels, colors, unit: 'Cr', type: 'bar', title: 'Demand (₹ Cr)', series: two('demand', cr) },
                             { card: false, toggleHost: box('demandHead'), storeKey: 'rep-demand' });
  efficiencyChart = UX.chartCard(box('effBox'), { labels, colors, unit: '%', type: 'line', title: 'Collection efficiency (%)',
                                                  series: two('collection_efficiency_pct') },
                                 { card: false, toggleHost: box('effHead'), storeKey: 'rep-eff' });
  installationsChart = UX.chartCard(box('instBox'), { labels, colors, unit: 'count', type: 'line', title: 'Consumers (installations)',
                                                      series: two('installations') },
                                    { card: false, toggleHost: box('instHead'), storeKey: 'rep-inst' });
  // per consumer (#1)
  [['dpc', 'demand_per_installation', 'Demand per consumer (₹)'], ['cpc', 'collection_per_installation', 'Collection per consumer (₹)'],
   ['apc', 'arrears_per_installation', 'Arrears per consumer (₹)']].forEach(([id, key, title]) => {
    perCharts.push(UX.chartCard(box(id + 'Box'), { labels, colors, unit: 'Rs', type: 'line', title, series: two(key) },
                                { card: false, toggleHost: box(id + 'Head'), storeKey: 'rep-' + id }));
  });
  // the same comparison as percentages (#2 consumer population, #3 amount and %, #5 subsidy, #6 AT&C)
  [['csh', 'consumer_share_pct', "Share of the division's consumers (%)", 'bar'],
   ['cgr', 'consumer_growth_pct', 'Consumer growth, year on year (%)', 'bar'],
   ['dsh', 'demand_share_pct', "Share of the division's demand (%)", 'bar'],
   ['sub', 'adjustment_share_pct', 'Subsidy share of collection (%)', 'line'],
   ['cef', 'cash_efficiency_pct', 'Collection efficiency without adjustment (%)', 'line'],
   ['atc', 'atc_loss_pct', 'AT&C loss (%)', 'line']].forEach(([id, key, title, type]) => {
    if (!box(id + 'Box')) return;
    perCharts.push(UX.chartCard(box(id + 'Box'), { labels, colors, unit: '%', type, title, series: two(key) },
                                { card: false, toggleHost: box(id + 'Head'), storeKey: 'rep-' + id }));
  });
}

async function refresh(){
  const errorBanner = document.getElementById('errorBanner');
  errorBanner.hidden = true;
  const a = document.getElementById('selectA').value;
  const b = document.getElementById('selectB').value;
  if (!a || !b) return;
  document.body.classList.add('is-loading');
  try {
    const [seriesA, seriesB] = await Promise.all([fetchSeries(a), fetchSeries(b)]);
    renderCharts(seriesA, seriesB);
  } catch (err) {
    errorBanner.textContent = 'Could not load comparison: ' + err.message;
    errorBanner.hidden = false;
  } finally { document.body.classList.remove('is-loading'); }
}

function setMode(newMode){
  mode = newMode;
  document.getElementById('modeSubdivision').classList.toggle('active', mode === 'subdivision');
  document.getElementById('modeTariff').classList.toggle('active', mode === 'tariff');
  document.getElementById('modeSeg').style.setProperty('--i', mode === 'tariff' ? 1 : 0);
  populateSelects();
  refresh();
}

async function init(){
  const skeleton = document.getElementById('skeleton');
  const errorBanner = document.getElementById('errorBanner');
  const body = document.getElementById('reportsBody');
  try {
    const resp = await fetch('/api/reports_options');
    if (!resp.ok) throw new Error('Server returned ' + resp.status);
    options = await resp.json();
    populateSelects();
    skeleton.hidden = true;
    body.hidden = false;
    await refresh();
  } catch (err) {
    errorBanner.textContent = 'Could not load report options: ' + err.message;
    errorBanner.hidden = false;
    skeleton.hidden = true;
  }
}

document.getElementById('modeSubdivision').onclick = () => setMode('subdivision');
document.getElementById('modeTariff').onclick = () => setMode('tariff');
document.getElementById('selectA').onchange = refresh;
document.getElementById('selectB').onchange = refresh;

init().finally(() => { if (window.Shell) Shell.ready(); });
