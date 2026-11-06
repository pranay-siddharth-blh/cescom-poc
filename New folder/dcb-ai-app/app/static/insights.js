// insights.html: page script (a file, so the security policy can forbid inline scripts).
// Layout follows the RDPR / KPCL Insights page: a filter row (all / needs attention / going well / for information) and
// one KPI box per insight, grouped. The figures come from /api/insights_summary (app/insights_data.py), the same
// read-only mart queries the dashboard uses; the sentences only restate them.
const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const num = (v, d = 1) => v == null ? '—' : Number(v).toLocaleString('en-IN', { minimumFractionDigits: d, maximumFractionDigits: d });
const formatCr = v => v == null ? '—' : '₹' + num(v / 1e7, 2) + ' Cr';
const fyOf = y => y + '-' + String((y + 1) % 100).padStart(2, '0');
const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
const TONE = { good: { accent: '#1f9d57', soft: '#e5f5ec', label: 'Going well' }, bad: { accent: '#d82221', soft: '#fdeceb', label: 'Needs attention' },
               info: { accent: '#e38f00', soft: '#fff6e0', label: 'For information' } };
const IC = {
  up: '<path d="M3 17l6-6 4 4 8-8"/><path d="M15 7h6v6"/>', down: '<path d="M3 7l6 6 4-4 8 8"/><path d="M21 11v6h-6"/>',
  bolt: '<path d="M13 2L4 14h7l-1 8 9-12h-7z"/>', trophy: '<path d="M8 21h8M12 17v4M7 4h10v5a5 5 0 0 1-10 0z"/><path d="M17 5h3v2a3 3 0 0 1-3 3M7 5H4v2a3 3 0 0 0 3 3"/>',
  alert: '<path d="M12 3l9 16H3z"/><path d="M12 10v4M12 17h.01"/>', wallet: '<rect x="3" y="6" width="18" height="13" rx="2"/><path d="M3 10h18"/><circle cx="16.5" cy="14.5" r="1.2"/>',
  gift: '<rect x="3" y="8" width="18" height="13" rx="2"/><path d="M12 8v13M3 12h18"/><path d="M12 8c-2-4-6-4-6-1s6 1 6 1zM12 8c2-4 6-4 6-1s-6 1-6 1z"/>',
  loss: '<path d="M3 3v18h18"/><path d="M7 7l5 5 3-3 5 5"/>',
};
function countUp(el, to, d, ms = 1000){
  if (to == null || isNaN(to)){ el.textContent = '—'; return; }
  if (reduced){ el.textContent = num(to, d); return; }
  const t0 = performance.now();
  const step = now => { const p = Math.min(1, (now - t0) / ms), e = 1 - Math.pow(1 - p, 3); el.textContent = num(to * e, d); if (p < 1) requestAnimationFrame(step); };
  requestAnimationFrame(step);
}

let CARDS = [], FILTER = 'all';
const named = h => h.tariff_name ? `${h.tariff_code} (${h.tariff_name})` : h.tariff_code;

function build(d){
  const cards = [];
  const add = c => cards.push(c);
  if (d.fastest_growing_tariff_3yr){ const h = d.fastest_growing_tariff_3yr;
    add({ group: 'Tariff categories', tone: 'info', icon: 'up', topic: 'Fastest-growing tariff category (3 years)', code: h.tariff_code, value: h.pct_change, d: 1, unit: '% growth',
      text: `<b>${esc(named(h))}</b>: demand grew from <b>${formatCr(h.from_demand)}</b> to <b>${formatCr(h.to_demand)}</b> over the last three fiscal years, division-wide — the fastest of any tariff-booklet category.`,
      ask: `${h.tariff_code} demand trend` }); }
  if (d.largest_yoy_jump){ const h = d.largest_yoy_jump;
    add({ group: 'Tariff categories', tone: 'info', icon: 'bolt', topic: 'Largest single-year jump in demand', code: h.tariff_code, value: h.jump / 1e7, d: 2, unit: '₹ Cr more',
      text: `Demand for <b>${esc(named(h))}</b> went from ${formatCr(h.from_demand)} to ${formatCr(h.to_demand)}${h.pct_change != null ? ` (+${num(h.pct_change, 1)}%)` : ''} between FY ${fyOf(h.from_fiscal_year_start)} and FY ${fyOf(h.to_fiscal_year_start)} — the biggest one-year rupee increase of any category.`,
      ask: `${h.tariff_code} demand in ${fyOf(h.to_fiscal_year_start)}` }); }
  if (d.subsidy){ const h = d.subsidy;
    const top = (h.top || []).map(t => `<b>${esc(t.code)}</b> ${formatCr(t.amount)} (${num(t.share_of_subsidy_pct, 1)}% of it; ${num(t.share_of_category_pct, 1)}% of ${esc(t.code)}'s collection)`).join(', ');
    add({ group: 'Collection & subsidy', tone: 'info', icon: 'gift', topic: `Government subsidy, FY ${h.fiscal_year}`, value: h.share_pct, d: 1, unit: '% of collection',
      text: `${formatCr(h.adjustment_total)} of the division's ${formatCr(h.collection_total)} collection was Government subsidy, booked as adjustment (Gruha Jyothi for LT-1, IP sets for LT-4(a)). Largest: ${top}.`,
      ask: `subsidy share in ${h.fiscal_year}` }); }
  if (d.atc){ const h = d.atc, hi = h.highest, lo = h.lowest;
    add({ group: 'Collection & subsidy', tone: h.division_atc_loss_pct > 10 ? 'bad' : 'good', icon: 'loss', topic: `AT&C loss, FY ${h.fiscal_year}`, value: h.division_atc_loss_pct, d: 2, unit: '% division',
      text: `100 − billing efficiency ${num(h.division_billing_efficiency_pct, 2)}% × collection efficiency ${num(h.division_collection_efficiency_pct, 2)}%.`
        + (hi ? ` Highest: <b>${esc(hi.subdivision_name)}</b> ${num(hi.atc_loss_pct, 2)}%` : '') + (lo ? `; lowest: <b>${esc(lo.subdivision_name)}</b> ${num(lo.atc_loss_pct, 2)}%.` : '.')
        + ' Billing efficiency is installations billed ÷ live, as the sheet prints it; the data has no energy-input figures.',
      ask: `AT&C loss by sub-division in ${h.fiscal_year}` }); }
  if (d.best_efficiency_subdivision){ const h = d.best_efficiency_subdivision;
    add({ group: 'Sub-divisions', tone: 'good', icon: 'trophy', topic: 'Best collection efficiency', code: h.subdivision_name, value: h.efficiency_pct, d: 1, unit: '%',
      text: `<b>${esc(h.subdivision_name)}</b> collected ${num(h.efficiency_pct, 1)}% of what it billed this fiscal year — the best of any sub-division or section.`,
      ask: `What was the collection efficiency in ${h.subdivision_name} in 2025-26?` }); }
  if (d.worst_efficiency_subdivision){ const h = d.worst_efficiency_subdivision;
    add({ group: 'Sub-divisions', tone: 'bad', icon: 'alert', topic: 'Lowest collection efficiency', code: h.subdivision_name, value: h.efficiency_pct, d: 1, unit: '%',
      text: `<b>${esc(h.subdivision_name)}</b> collected ${num(h.efficiency_pct, 1)}% of what it billed this fiscal year — the most room to improve of any sub-division or section.`,
      ask: `show me ${h.subdivision_name.toLowerCase()} numbers` }); }
  if (d.highest_outstanding_growth){ const h = d.highest_outstanding_growth;
    add({ group: 'Sub-divisions', tone: 'bad', icon: 'wallet', topic: 'Fastest-growing outstanding balance', code: h.subdivision_name, value: h.pct_change, d: 1, unit: '% year on year',
      text: `Outstanding balance grew from ${formatCr(h.from_outstanding)} to <b>${formatCr(h.to_outstanding)}</b> for <b>${esc(h.subdivision_name)}</b> — the fastest-growing arrears of any sub-division or section.`,
      ask: `total dues in ${h.subdivision_name} in 2025-26` }); }
  return cards;
}

function card(c, i){
  const tone = TONE[c.tone];
  return `<article class="kpi ins-card ${c.tone}" style="--accent:${tone.accent};--accent-soft:${tone.soft};animation-delay:${Math.min(i, 12) * 50}ms">
    <div class="top"><span class="ico"><svg viewBox="0 0 24 24">${IC[c.icon] || IC.bolt}</svg></span><span class="t">${esc(c.topic)}</span><span class="ins-tone">${esc(tone.label)}</span></div>
    <div class="v">${c.code ? `<span class="code">${esc(c.code)}</span>` : ''}<span data-count="${c.value ?? ''}" data-d="${c.d}">—</span><small>${esc(c.unit)}</small></div>
    <p class="ins-text">${c.text}</p>
    ${c.ask ? `<a class="ins-ask ai-link" href="/chat?q=${encodeURIComponent(c.ask)}">Ask the assistant →</a>` : ''}
  </article>`;
}

function render(){
  const total = CARDS.length, n = k => CARDS.filter(c => c.tone === k).length;
  $('summary').innerHTML = [['all', 'All', total], ['bad', TONE.bad.label, n('bad')], ['good', TONE.good.label, n('good')], ['info', TONE.info.label, n('info')]]
    .map(([k, l, v]) => `<button type="button" class="ins-count ${k}" data-f="${k}" aria-pressed="${FILTER === k}"><b>${v}</b><span>${esc(l)}</span></button>`).join('');
  $('summary').querySelectorAll('button').forEach(b => b.onclick = () => { FILTER = b.dataset.f; render(); });
  const groups = [...new Set(CARDS.map(c => c.group))];
  let i = 0;
  $('groups').innerHTML = groups.map(g => {
    const list = CARDS.filter(c => c.group === g && (FILTER === 'all' || c.tone === FILTER));
    if (!list.length) return '';
    return `<section class="ins-group"><h2>${esc(g)}<span>${list.length}</span></h2><div class="ins-grid">` + list.map(c => card(c, i++)).join('') + '</div></section>';
  }).join('');
  document.querySelectorAll('#groups [data-count]').forEach(el => { const v = el.dataset.count; if (v === '') el.textContent = '—'; else countUp(el, Number(v), Number(el.dataset.d)); });
  if (window.Shell) Shell.wireLinks($('groups'));
}



async function loadInsights(){
  try {
    const r = await fetch('/api/insights_summary');
    if (!r.ok) throw new Error('Server returned ' + r.status);
    const d = await r.json();
    CARDS = build(d);
    const fy = (d.subsidy && d.subsidy.fiscal_year) || (d.atc && d.atc.fiscal_year);
    if (fy) $('subline').innerHTML = `FY <b>${esc(fy)}</b> · Holenarasipura Division · what changed, what is going well and what needs attention.`;
    $('skeleton').hidden = true; $('body').hidden = false;
    render();
  } catch (err) {
    $('errorBanner').textContent = 'Could not load insights: ' + err.message;
    $('errorBanner').hidden = false; $('skeleton').hidden = true;
  } finally { if (window.Shell) Shell.ready(); }
}
loadInsights();
