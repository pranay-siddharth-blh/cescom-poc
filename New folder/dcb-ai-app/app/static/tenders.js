// tenders.html's page script: GeM-format bid drafting (app/tender.py via /api/tender/*) and the Bid Assist-style
// analyser. Same page shell, nav transition and look as the other sections.
// The page shell (header, sidebar, drawer, transitions, language) is nav.js.

const $ = id => document.getElementById(id);
const esc = s => String(s == null ? '' : s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]);
// UX.toast(msg, isError): 'error' and 'warn' show as alerts, 'ok' as a tick
const toast = (msg, kind) => window.Shell ? Shell.toast(msg, kind === 'error' || kind === 'warn') : (window.UX && UX.toast) ? UX.toast(msg, kind === 'error' || kind === 'warn') : alert(msg);

// ------------------------------------------------------------------ tabs
function showTab(which){
  $('tabDraft').hidden = which !== 'draft';
  $('tabAnalyse').hidden = which !== 'analyse';
  $('tabBtnDraft').classList.toggle('active', which === 'draft');
  $('tabBtnAnalyse').classList.toggle('active', which === 'analyse');
  $('tabSeg').style.setProperty('--i', which === 'analyse' ? 1 : 0);
  try { localStorage.setItem('tender-tab', which); } catch (e) { /* storage blocked: fine */ }
}
$('tabBtnDraft').onclick = () => showTab('draft');
$('tabBtnAnalyse').onclick = () => showTab('analyse');
try { if (localStorage.getItem('tender-tab') === 'analyse') showTab('analyse'); } catch (e) { /* ignore */ }

// ------------------------------------------------------------------ items
function addItem(v = {}){
  const tr = document.createElement('tr');
  tr.innerHTML = `<td><input maxlength="300" data-k="name" value="${esc(v.name || '')}" placeholder="Item"></td>
    <td><textarea maxlength="3000" data-k="specification" placeholder="Technical specification, IS standard, rating…">${esc(v.specification || '')}</textarea></td>
    <td><input maxlength="40" data-k="quantity" value="${esc(v.quantity || '')}"></td>
    <td><input maxlength="40" data-k="unit" value="${esc(v.unit || 'Nos')}"></td>
    <td><input maxlength="20" data-k="delivery_days" value="${esc(v.delivery_days || '')}"></td>
    <td><button type="button" class="t-del" title="Remove" aria-label="Remove item">×</button></td>`;
  tr.querySelector('.t-del').onclick = () => tr.remove();
  $('itemRows').appendChild(tr);
}
$('addItem').onclick = () => addItem();
addItem();

function formData(){
  const f = $('draftForm');
  const d = {};
  f.querySelectorAll('input[name], select[name], textarea[name]').forEach(el => {
    d[el.name] = el.type === 'checkbox' ? el.checked : el.value.trim();
  });
  d.items = [...$('itemRows').querySelectorAll('tr')].map(tr => {
    const it = {}; tr.querySelectorAll('[data-k]').forEach(el => { it[el.dataset.k] = el.value.trim(); }); return it;
  }).filter(it => it.name || it.specification);
  return d;
}

async function post(url, body){
  const r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  if (!r.ok){
    const e = await r.json().catch(() => ({}));
    throw new Error(e.error || e.detail || ('Server returned ' + r.status));
  }
  return r;
}

// ------------------------------------------------------------------ plan (live as the value changes)
const fmtRs = v => '₹' + Number(v).toLocaleString('en-IN', { maximumFractionDigits: 0 });
function renderPlan(p, checks){
  $('planPanel').hidden = false;
  $('planHint').textContent = `${p.estimated_value_text} · ${p.kind}`;
  $('planBox').innerHTML = [
    ['GeM procurement mode', p.gem_mode, p.gem_mode_text],
    ['Tender type', p.tender_type, p.method === 'limited' ? 'Limited tenders are capped at ₹5 lakh (KTPP Act)' : 'Advertised / open tender'],
    ['Invited by', p.invite_authority, 'CESC Delegation of Powers, purchases item 1'],
    ['Accepted by', p.accept_authority, 'CESC Delegation of Powers, purchases item 2 / Section A'],
  ].map(([k, v, s]) => `<div class="plan-card"><div class="k">${esc(k)}</div><div class="v">${esc(v)}</div><div class="s">${esc(s)}</div></div>`).join('');
  const hit = p.accept_authority.match(/\((MD|CEE|SEE|EEE|AEEE)\)/);
  $('ladder').innerHTML = '<thead><tr><th>Officer</th><th>Invite tenders up to</th><th>Accept open tenders up to</th><th>Accept limited tenders up to</th></tr></thead><tbody>'
    + p.ladder.map(r => `<tr class="${hit && hit[1] === r.officer ? 'hit' : ''}"><td>${esc(r.name)} (${r.officer})</td><td>${esc(r.invite)}</td><td>${esc(r.accept_open)}</td><td>${esc(r.accept_limited)}</td></tr>`).join('')
    + '<tr><td>Central Purchase Committee (CPC)</td><td>—</td><td>more than ₹3 crore up to ₹10 crore</td><td>—</td></tr>'
    + '<tr><td>Board of Directors</td><td>—</td><td>more than ₹10 crore</td><td>—</td></tr></tbody>';
  const items = (checks || []).concat((p.warnings || []).filter(w => !(checks || []).some(c => c.finding === w)).map(w => ({ severity: 'error', rule: 'DoP / KTPP', finding: w })));
  $('checkList').innerHTML = items.map(c => `<li class="${esc(c.severity)}"><b>${esc(c.rule)}:</b> ${esc(c.finding)}${c.rule_text ? `<small>${esc(c.rule_text)}${c.excerpt ? ' — “…' + esc(c.excerpt) + '…”' : ''}</small>` : ''}</li>`).join('');
  $('planNote').textContent = p.state_note;
}
let planTimer = null, planToken = 0;
function livePlan(){
  clearTimeout(planTimer);
  const token = ++planToken;
  planTimer = setTimeout(async () => {
    const v = $('f_value').value.replace(/,/g, '');
    if (!v || isNaN(Number(v))) return;
    try {
      const r = await post('/api/tender/plan', { estimated_value: Number(v), kind: $('f_kind').value, method: $('f_method').value });
      const p = await r.json();
      if (token === planToken) renderPlan(p, null);   // a draft generated meanwhile keeps its full checks
    } catch (e) { /* the full check runs on Generate */ }
  }, 350);
}
['f_value', 'f_kind', 'f_method'].forEach(id => $(id).addEventListener('input', livePlan));
['f_kind', 'f_method'].forEach(id => $(id).addEventListener('change', livePlan));

// ------------------------------------------------------------------ draft
let lastForm = null;
$('draftForm').addEventListener('submit', async e => {
  e.preventDefault();
  clearTimeout(planTimer); planToken++;
  const btn = $('draftBtn');
  btn.disabled = true; btn.textContent = 'Generating…';
  try {
    lastForm = formData();
    const r = await post('/api/tender/draft', lastForm);
    const d = await r.json();
    renderPlan(d.plan, d.checks);
    $('docPreview').innerHTML = d.html;      // built server-side from escaped values (app/tender.py render_html)
    $('previewPanel').hidden = false;
    $('docxBtn').disabled = false; $('printBtn').disabled = false;
    if (!$('f_emd').value && d.suggested) $('f_emd').placeholder = 'suggested ' + fmtRs(d.suggested.emd_amount);
    const errs = d.checks.filter(c => c.severity === 'error').length;
    toast(errs ? `Bid document drafted — ${errs} issue(s) to fix` : 'Bid document drafted', errs ? 'warn' : 'ok');
    $('planPanel').scrollIntoView({ behavior: 'smooth', block: 'start' });
  } catch (err) {
    toast('Could not draft the tender: ' + err.message, 'error');
  } finally {
    btn.disabled = false; btn.textContent = 'Generate bid document';
  }
});
$('docxBtn').onclick = async () => {
  if (!lastForm) return;
  const btn = $('docxBtn'); btn.disabled = true;
  try {
    const r = await post('/api/tender/docx', lastForm);
    const blob = await r.blob();
    const cd = r.headers.get('Content-Disposition') || '';
    const name = (cd.match(/filename="([^"]+)"/) || [])[1] || 'GeM_bid_draft.docx';
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob); a.download = name; document.body.appendChild(a); a.click();
    setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
  } catch (err) { toast('Download failed: ' + err.message, 'error'); }
  finally { btn.disabled = false; }
};
$('printBtn').onclick = () => window.print();

// ------------------------------------------------------------------ analyse
let pdfB64 = null;
function pickFile(file){
  if (!file) return;
  if (file.type !== 'application/pdf' && !/\.pdf$/i.test(file.name)){ toast('Choose a PDF file', 'error'); return; }
  if (file.size > 11 * 1024 * 1024){ toast('That PDF is larger than 11 MB', 'error'); return; }
  const rd = new FileReader();
  rd.onload = () => { pdfB64 = String(rd.result).split(',')[1]; $('fileName').textContent = file.name; };
  rd.readAsDataURL(file);
}
const dz = $('dropZone');
dz.onclick = () => $('pdfInput').click();
dz.onkeydown = e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); $('pdfInput').click(); } };
$('pdfInput').onchange = e => pickFile(e.target.files[0]);
dz.ondragover = e => { e.preventDefault(); dz.classList.add('over'); };
dz.ondragleave = () => dz.classList.remove('over');
dz.ondrop = e => { e.preventDefault(); dz.classList.remove('over'); pickFile(e.dataTransfer.files[0]); };

$('analyseBtn').onclick = async () => {
  const text = $('a_text').value.trim();
  if (!pdfB64 && text.length < 40){ toast('Choose a PDF or paste the tender text first', 'error'); return; }
  const btn = $('analyseBtn'); btn.disabled = true; btn.textContent = 'Reading…';
  const profile = {};
  if ($('p_turn').value) profile.turnover_lakh = Number($('p_turn').value);
  if ($('p_exp').value) profile.experience_years = Number($('p_exp').value);
  profile.is_mse = $('p_mse').checked; profile.is_startup = $('p_startup').checked;
  const hasProfile = $('p_turn').value || $('p_exp').value || profile.is_mse || profile.is_startup;
  try {
    const r = await post('/api/tender/analyse', { pdf_base64: pdfB64, text: pdfB64 ? '' : text, profile: hasProfile ? profile : null });
    renderAnalysis(await r.json());
  } catch (err) { toast(err.message, 'error'); }
  finally { btn.disabled = false; btn.textContent = 'Analyse'; }
};

function renderAnalysis(a){
  $('analysis').hidden = false;
  $('aSummary').textContent = a.summary;
  $('verdict').innerHTML = a.verdict ? `<span class="verdict ${a.verdict === 'Eligible' ? 'ok' : 'no'}">${esc(a.verdict)}</span>` : '';
  const f = Object.entries(a.fields || {});
  $('aFields').innerHTML = f.length ? f.map(([k, v]) => `<tr><th>${esc(k)}</th><td>${esc(v)}</td></tr>`).join('')
    : '<tr><td>No standard fields were found.</td></tr>';
  $('eligPanel').hidden = !(a.eligibility && a.eligibility.length);
  $('aElig').innerHTML = (a.eligibility || []).map(e => `<tr><th>${esc(e.criterion)}</th><td class="${esc(e.result)}">${esc(e.result.toUpperCase())}</td><td>${esc(e.detail || '')}</td></tr>`).join('');
  $('aComp').innerHTML = (a.compliance && a.compliance.length)
    ? a.compliance.map(c => `<li class="error"><b>${esc(c.rule)}:</b> ${esc(c.finding)}<small>${esc(c.rule_text)} — “…${esc(c.excerpt)}…”</small></li>`).join('')
    : '<li class="ok">No clause matched GeM\'s list of terms that make a bid null and void.</li>';
  $('analysis').scrollIntoView({ behavior: 'smooth', block: 'start' });
}

if (window.Shell) Shell.ready();
