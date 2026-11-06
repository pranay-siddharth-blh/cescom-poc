// about.html: page script (a file, so the security policy can forbid inline scripts). The page shell is nav.js.
// One search over three tabs, nothing listed twice:
//   Terms             the glossary (/api/glossary), with the wording CESC confirmed (DC / MNR, FPPCA) and without the
//                     database-internal entries; the locked semantic layer on disk is never changed
//   Tariff categories one card per category of the CESC tariff booklet FY2025-26 to FY2027-28: who it is for and its
//                     FY 2025-26 rates at a glance, the full description, three-year rates and notes on demand; then
//                     the older / special codes the DCB sheets print and what they are today
//   Booklet rules     the booklet's general rules
const $ = id => document.getElementById(id);
const esc = s => String(s == null ? '' : s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]);

// Display wording: confirmed meanings (CLAUDE.md §13.10, KI-15) and shorter, plainer definitions. null hides an entry.
const TERM_FIX = {
  'DCB': 'Demand-Collection-Balance: for each tariff category and sub-division, the installations, what was billed (demand), what was paid (collection) and what is still owed (balance).',
  'DC/MNR': 'DC is a direct connection: supply given without a meter while a faulty meter awaits replacement (KERC: a new meter within 3 days). MNR is a meter not recording. The sheets print them separately from 2018-19.',
  'FAC': 'Fuel and power purchase cost adjustment (FPPCA, printed as FAC): a pass-through charge for changes in the cost of the power bought.',
  'ARR': 'Average Revenue Realized: revenue per unit of energy actually realised.',
  'P&G Surcharge': 'A tariff component billed from 2025-26 only; earlier years have none.',
  'Sub Total / LT-TOTAL / HT-TOTAL / GRAND TOTAL': null,
};
const FAMILY_SUMMARIES = new Set(['LT-3', 'LT-4', 'LT-6', 'HT-2']);   // repeated by the category cards below them

const shortRate = s => String(s || '')
  .replace(/ per kW per month of sanctioned load/gi, '/kW/month').replace(/ per kVA per month/gi, '/kVA/month')
  .replace(/ per HP per month/gi, '/HP/month').replace(/ per unit \(kWh\)/gi, '/unit').replace(/ per unit/gi, '/unit')
  .replace(/\s*\(.*?\)\s*$/, '');

let DATA = null, TAB = 'terms', Q = '';
// spaces, hyphens and dots are ignored, so "homestay" finds "home stay" and "lt4a" finds "LT-4(a)"
const squash = s => String(s || '').toLowerCase().replace(/[^a-z0-9ಀ-೿%]+/g, '');
const hit = text => !Q || String(text).toLowerCase().includes(Q) || squash(text).includes(squash(Q));
const mark = text => {
  const t = esc(text);
  if (!Q) return t;
  const i = t.toLowerCase().indexOf(esc(Q).toLowerCase());
  return i < 0 ? t : t.slice(0, i) + '<mark>' + t.slice(i, i + esc(Q).length) + '</mark>' + t.slice(i + esc(Q).length);
};

function terms(){
  return Object.entries(DATA.glossary)
    .map(([k, v]) => [k, k in TERM_FIX ? TERM_FIX[k] : v])
    .filter(([, v]) => v);
}
function cats(){ return DATA.booklet ? DATA.booklet.categories : []; }
function oldCodes(){
  const codes = new Set(cats().map(c => c.code));
  return Object.entries(DATA.tariff_code_glossary).filter(([k]) => !codes.has(k) && !FAMILY_SUMMARIES.has(k));
}
function rules(){ return DATA.booklet ? DATA.booklet.rules : []; }

function render(){
  const t = terms().filter(([k, v]) => hit(k + ' ' + v));
  $('termList').innerHTML = t.map(([k, v]) => `<div class="term"><b>${mark(k)}</b><p>${mark(v)}</p></div>`).join('');

  const cs = cats().filter(c => hit([c.code, c.name, c.short, c.applies_to, ...(c.notes || [])].join(' ')));
  $('catList').innerHTML = cs.map(c => {
    const fy = (c.rate_rows || []).slice(0, 2).map(r => `<span class="rate${/energy/i.test(r[0]) ? ' gold' : ''}">${esc(r[0].replace(/ charge$/i, ''))} ${esc(shortRate(r[1]))}</span>`).join('');
    return `<details class="cat"${Q ? ' open' : ''}>
      <summary><span class="ttl"><span class="vtag${c.voltage === 'HT' ? ' ht' : ''}">${esc(c.voltage)}</span>${mark(c.code)} · ${mark(c.name)}<span class="more">Details ▾</span></span>
        <span class="who">${mark(c.short || '')}</span>${fy ? `<span class="rates">${fy}</span>` : ''}</summary>
      <div class="body"><p>${mark(c.applies_to)}</p>
        <table><thead><tr>${c.rate_columns.map(h => `<th>${esc(h)}</th>`).join('')}</tr></thead>
          <tbody>${c.rate_rows.map(r => `<tr>${r.map(v => `<td>${esc(v)}</td>`).join('')}</tr>`).join('')}</tbody></table>
        ${c.notes && c.notes.length ? `<ul>${c.notes.map(n => `<li>${mark(n)}</li>`).join('')}</ul>` : ''}
        <p style="color:var(--faint);font-size:.68rem;margin-top:8px">Booklet page ${esc(c.page)}</p></div>
    </details>`;
  }).join('');
  const oc = oldCodes().filter(([k, v]) => hit(k + ' ' + v));
  $('oldCodes').innerHTML = oc.map(([k, v]) => `<div class="term"><b>${mark(k)}</b><p>${mark(v)}</p></div>`).join('');

  const rs = rules().filter(r => hit(r.title + ' ' + r.text));
  $('ruleList').innerHTML = rs.map(r => `<div class="term"><b>${mark(r.title)}<small>p.${esc(r.page)}</small></b><p>${mark(r.text)}</p></div>`).join('');

  $('nTerms').textContent = t.length; $('nTariffs').textContent = cs.length; $('nRules').textContent = rs.length;
  const shown = { terms: t.length, tariffs: cs.length + oc.length, rules: rs.length }[TAB];
  $('noMatch').hidden = shown > 0;
  document.querySelector('#tabTariffs .sub-h').hidden = !oc.length;
}

function setTab(tab){
  TAB = tab;
  const btns = [...document.querySelectorAll('#aboutTabs button')];
  btns.forEach((b, i) => { const on = b.dataset.tab === tab; b.classList.toggle('active', on); if (on) $('aboutTabs').style.setProperty('--i', i); });
  $('tabTerms').hidden = tab !== 'terms'; $('tabTariffs').hidden = tab !== 'tariffs'; $('tabRules').hidden = tab !== 'rules';
  render();
}

async function init(){
  try {
    const resp = await fetch('/api/glossary');
    if (!resp.ok) throw new Error('Server returned ' + resp.status);
    DATA = await resp.json();
    if (DATA.booklet){
      const s = DATA.booklet.source;
      $('bookletSrc').textContent = `${s.title}, ${s.order}. ${s.caveat}`;
    }
    $('glossarySkeleton').hidden = true;
    document.querySelectorAll('#aboutTabs button').forEach(b => b.onclick = () => setTab(b.dataset.tab));
    let timer = null;
    $('aboutFilter').oninput = e => { clearTimeout(timer); timer = setTimeout(() => {
      Q = e.target.value.trim().toLowerCase();
      render();
      // the open tab has nothing for this search but another has: show that one
      const counts = { terms: +$('nTerms').textContent, tariffs: +$('nTariffs').textContent, rules: +$('nRules').textContent };
      if (Q && !counts[TAB]){ const other = Object.keys(counts).find(k => counts[k]); if (other) setTab(other); }
    }, 120); };
    setTab('terms');
  } catch (err) {
    $('glossaryError').textContent = 'Could not load the glossary: ' + err.message;
    $('glossaryError').hidden = false;
    $('glossarySkeleton').hidden = true;
  }
}

init().finally(() => { if (window.Shell) Shell.ready(); });
