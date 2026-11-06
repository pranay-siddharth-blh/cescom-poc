// chat.html: page script (a file, so the security policy can forbid inline scripts).
// Layout and behaviour follow the RDPR / KPCL assistant (AI mode). A question goes to POST /api/ask and the server
// streams (app/main.py::ask_stream): status (one per real pipeline stage) -> sql -> chart {chart_data} -> answer_chunk
// ... -> table {table} -> done {answerable, truncated, report_id, technical, timing_ms, route}.
// Charts are drawn by ux.js from the rows the query returned (never from model-typed numbers).
// ------------------------------------------------------------------ strings
// The language switch translates the page's own labels. Questions may be typed in English or Kannada (app/kannada.py
// rewrites a Kannada question for the routes); figures and their wording come back in English.
const I18N = {
  en: {
    splashTitle: 'DCB Assistant', splashSub: 'Entering AI mode',
    govLine1: 'Government of Karnataka', govLine2: 'Chamundeshwari Electricity Supply Corporation Limited, Mysuru',
    appBar: '<b>DCB Assistant · Demand, Collection &amp; Balance</b><span class="extra"><span class="sep">|</span>Holenarasipura Division<span class="sep">|</span>FY 2015-16 – FY 2025-26</span>',
    aiBadge: 'AI MODE', menu: 'Menu', newChat: 'New conversation', dashLink: 'Dashboard', dashSub: 'KPIs · sub-divisions · insights', goDash: 'Opening the dashboard',
    groups: ['Collection and demand', 'Tariff categories', 'Subsidy, AT&C and meters', 'Summaries and reference'],
    placeholder: 'Ask about demand, collection, arrears, tariffs, subsidy, AT&C, sub-divisions…', send: 'Ask',
    footnote: 'Every figure comes from a checked, read-only query on the CESC Holenarasipura Division DCB data.',
    eyebrow: 'Chamundeshwari Electricity Supply Corporation', welcomeTitle: 'DCB Assistant',
    welcomeSub: 'Ask in plain English or ಕನ್ನಡ about demand, collection and balance across the Holenarasipura Division. Every answer comes from the DCB statements themselves — with a chart, the data table and an Excel report.',
    stats: [['11', 'fiscal years · 2015-16 to 2025-26'], ['5', 'sub-divisions and sections'], ['22', 'tariff categories (CESC booklet)'], ['EN · ಕನ್ನಡ', 'ask in either language']],
    cardsLabel: 'Suggested questions',
    modelReady: 'Model ready', modelOffline: 'Offline mode', modelDown: 'Server unreachable', modelChecking: '',
    stages: { thinking: 'Reading the question', understanding: 'Reading the question', querying: 'Looking up the figures', writing_sql: 'Drafting the query',
              checking_sql: 'Checking the query', running_query: 'Running the query', repairing: 'Correcting the query',
              verifying: 'Double-checking the answer', writing_answer: 'Preparing the answer' },
    elapsed: s => s + ' s', answeredIn: s => 'Answered in ' + s + ' s', spokenQuestion: 'Asked by voice', askedAs: 'Taking this as:',
    notAnswerable: 'Not answered from the data', truncated: 'Results truncated', copy: 'Copy', copied: 'Copied',
    dlReport: 'Download report', viewReport: 'View report', viewReportTitle: 'View this report before downloading it', dlReportBusy: 'Preparing report…',
    dlReportDone: 'Report downloaded', dlReportFail: 'Report expired — ask again', dlReportReview: 'Under review', dlReportTitle: 'Download this answer as an Excel report',
    dataTable: n => 'Data table · ' + n + ' rows', csv: 'Download CSV',
    technical: 'Technical details (SQL, route and timing)', noText: '(no answer text received)',
    timedOut: 'This is taking longer than expected. Please try again in a moment.',
    failed: 'Sorry, I couldn\'t answer that right now. Please try again in a moment.', partial: 'Partial answer received:',
    textSize: 'Text size', highContrast: 'High contrast', on: 'On', off: 'Off',
  },
  kn: {
    splashTitle: 'ಡಿಸಿಬಿ ಸಹಾಯಕ', splashSub: 'AI ಮೋಡ್ ಪ್ರವೇಶಿಸಲಾಗುತ್ತಿದೆ',
    govLine1: 'ಕರ್ನಾಟಕ ಸರ್ಕಾರ', govLine2: 'ಚಾಮುಂಡೇಶ್ವರಿ ವಿದ್ಯುತ್ ಸರಬರಾಜು ನಿಗಮ ನಿಯಮಿತ, ಮೈಸೂರು',
    appBar: '<b>ಡಿಸಿಬಿ ಸಹಾಯಕ · ಬೇಡಿಕೆ, ಸಂಗ್ರಹಣೆ ಮತ್ತು ಬಾಕಿ</b><span class="extra"><span class="sep">|</span>ಹೊಳೆನರಸೀಪುರ ವಿಭಾಗ<span class="sep">|</span>2015-16 – 2025-26</span>',
    aiBadge: 'AI ಮೋಡ್', menu: 'ಮೆನು', newChat: 'ಹೊಸ ಸಂಭಾಷಣೆ', dashLink: 'ಡ್ಯಾಶ್‌ಬೋರ್ಡ್', dashSub: 'ಸೂಚಕಗಳು · ಉಪ-ವಿಭಾಗಗಳು · ಒಳನೋಟಗಳು', goDash: 'ಡ್ಯಾಶ್‌ಬೋರ್ಡ್ ತೆರೆಯಲಾಗುತ್ತಿದೆ',
    groups: ['ಸಂಗ್ರಹಣೆ ಮತ್ತು ಬೇಡಿಕೆ', 'ಸುಂಕ ವರ್ಗಗಳು', 'ಸಹಾಯಧನ, AT&C ಮತ್ತು ಮೀಟರ್‌ಗಳು', 'ಸಾರಾಂಶ ಮತ್ತು ಉಲ್ಲೇಖ'],
    placeholder: 'ಬೇಡಿಕೆ, ಸಂಗ್ರಹಣೆ, ಬಾಕಿ, ಸುಂಕ, ಉಪ-ವಿಭಾಗಗಳ ಬಗ್ಗೆ ಇಂಗ್ಲಿಷ್ ಅಥವಾ ಕನ್ನಡದಲ್ಲಿ ಕೇಳಿ…', send: 'ಕೇಳಿ',
    footnote: 'ಪ್ರತಿ ಅಂಕಿಅಂಶವೂ ಪರಿಶೀಲಿಸಿದ, ಓದಲು-ಮಾತ್ರ ಪ್ರಶ್ನೆಯಿಂದ ಬರುತ್ತದೆ. ಅಂಕಿಅಂಶಗಳ ವಿವರ ಇಂಗ್ಲಿಷ್‌ನಲ್ಲಿರುತ್ತದೆ.',
    eyebrow: 'ಚಾಮುಂಡೇಶ್ವರಿ ವಿದ್ಯುತ್ ಸರಬರಾಜು ನಿಗಮ', welcomeTitle: 'ಡಿಸಿಬಿ ಸಹಾಯಕ',
    welcomeSub: 'ಹೊಳೆನರಸೀಪುರ ವಿಭಾಗದ ಬೇಡಿಕೆ, ಸಂಗ್ರಹಣೆ ಮತ್ತು ಬಾಕಿಯ ಬಗ್ಗೆ ಇಂಗ್ಲಿಷ್ ಅಥವಾ ಕನ್ನಡದಲ್ಲಿ ಕೇಳಿ. ಪ್ರತಿ ಉತ್ತರ ಡಿಸಿಬಿ ಹೇಳಿಕೆಗಳಿಂದಲೇ ಬರುತ್ತದೆ — ಚಾರ್ಟ್, ದತ್ತಾಂಶ ಕೋಷ್ಟಕ ಮತ್ತು Excel ವರದಿಯೊಂದಿಗೆ.',
    stats: [['11', 'ಆರ್ಥಿಕ ವರ್ಷಗಳು · 2015-16 ರಿಂದ 2025-26'], ['5', 'ಉಪ-ವಿಭಾಗಗಳು ಮತ್ತು ಶಾಖೆಗಳು'], ['22', 'ಸುಂಕ ವರ್ಗಗಳು (ಸಿಇಎಸ್‌ಸಿ ಕೈಪಿಡಿ)'], ['EN · ಕನ್ನಡ', 'ಯಾವುದೇ ಭಾಷೆಯಲ್ಲಿ ಕೇಳಿ']],
    cardsLabel: 'ಸೂಚಿತ ಪ್ರಶ್ನೆಗಳು',
    modelReady: 'ಮಾದರಿ ಸಿದ್ಧ', modelOffline: 'ಆಫ್‌ಲೈನ್ ಮೋಡ್', modelDown: 'ಸರ್ವರ್ ತಲುಪಲಾಗುತ್ತಿಲ್ಲ', modelChecking: '',
    stages: { thinking: 'ಪ್ರಶ್ನೆ ಓದಲಾಗುತ್ತಿದೆ', understanding: 'ಪ್ರಶ್ನೆ ಓದಲಾಗುತ್ತಿದೆ', querying: 'ಅಂಕಿಅಂಶಗಳನ್ನು ಹುಡುಕಲಾಗುತ್ತಿದೆ', writing_sql: 'ಪ್ರಶ್ನೆ ರಚಿಸಲಾಗುತ್ತಿದೆ',
              checking_sql: 'ಪ್ರಶ್ನೆ ಪರಿಶೀಲಿಸಲಾಗುತ್ತಿದೆ', running_query: 'ಪ್ರಶ್ನೆ ನಡೆಸಲಾಗುತ್ತಿದೆ', repairing: 'ಪ್ರಶ್ನೆ ಸರಿಪಡಿಸಲಾಗುತ್ತಿದೆ',
              verifying: 'ಉತ್ತರವನ್ನು ಮರುಪರಿಶೀಲಿಸಲಾಗುತ್ತಿದೆ', writing_answer: 'ಉತ್ತರ ಸಿದ್ಧಪಡಿಸಲಾಗುತ್ತಿದೆ' },
    elapsed: s => s + ' ಸೆ', answeredIn: s => s + ' ಸೆ ನಲ್ಲಿ ಉತ್ತರಿಸಲಾಗಿದೆ', spokenQuestion: 'ಧ್ವನಿಯ ಮೂಲಕ ಕೇಳಲಾಗಿದೆ', askedAs: 'ಇದನ್ನು ಹೀಗೆ ತೆಗೆದುಕೊಳ್ಳಲಾಗಿದೆ:',
    notAnswerable: 'ದತ್ತಾಂಶದಿಂದ ಉತ್ತರಿಸಲಾಗಿಲ್ಲ', truncated: 'ಫಲಿತಾಂಶಗಳನ್ನು ಮೊಟಕುಗೊಳಿಸಲಾಗಿದೆ', copy: 'ನಕಲಿಸಿ', copied: 'ನಕಲಿಸಲಾಗಿದೆ',
    dlReport: 'ವರದಿ ಡೌನ್‌ಲೋಡ್', viewReport: 'ವರದಿ ವೀಕ್ಷಿಸಿ', viewReportTitle: 'ಡೌನ್‌ಲೋಡ್ ಮಾಡುವ ಮೊದಲು ವರದಿಯನ್ನು ನೋಡಿ', dlReportBusy: 'ವರದಿ ಸಿದ್ಧವಾಗುತ್ತಿದೆ…',
    dlReportDone: 'ವರದಿ ಡೌನ್‌ಲೋಡ್ ಆಗಿದೆ', dlReportFail: 'ವರದಿ ಅವಧಿ ಮುಗಿದಿದೆ — ಮತ್ತೆ ಕೇಳಿ', dlReportReview: 'ಪರಿಶೀಲನೆಯಲ್ಲಿದೆ', dlReportTitle: 'ಈ ಉತ್ತರವನ್ನು Excel ವರದಿಯಾಗಿ ಡೌನ್‌ಲೋಡ್ ಮಾಡಿ',
    dataTable: n => 'ದತ್ತಾಂಶ ಕೋಷ್ಟಕ · ' + n + ' ಸಾಲುಗಳು', csv: 'CSV ಡೌನ್‌ಲೋಡ್',
    technical: 'ತಾಂತ್ರಿಕ ವಿವರಗಳು (SQL ಮತ್ತು ಸಮಯ)', noText: '(ಯಾವುದೇ ಉತ್ತರ ಪಠ್ಯ ಬಂದಿಲ್ಲ)',
    timedOut: 'ನಿರೀಕ್ಷೆಗಿಂತ ಹೆಚ್ಚು ಸಮಯ ತೆಗೆದುಕೊಳ್ಳುತ್ತಿದೆ. ಸ್ವಲ್ಪ ಸಮಯದ ನಂತರ ಮತ್ತೆ ಪ್ರಯತ್ನಿಸಿ.',
    failed: 'ಕ್ಷಮಿಸಿ, ಈಗ ಉತ್ತರಿಸಲಾಗಲಿಲ್ಲ. ಸ್ವಲ್ಪ ಸಮಯದ ನಂತರ ಮತ್ತೆ ಪ್ರಯತ್ನಿಸಿ.', partial: 'ಭಾಗಶಃ ಉತ್ತರ:',
    textSize: 'ಅಕ್ಷರ ಗಾತ್ರ', highContrast: 'ಹೆಚ್ಚಿನ ವ್ಯತ್ಯಾಸ', on: 'ಆನ್', off: 'ಆಫ್',
  },
};
let lang = 'en';
const t = (k, v) => { const e = I18N[lang][k] ?? I18N.en[k]; return typeof e === 'function' ? e(v) : e; };

// ------------------------------------------------------------------ questions
// One list feeds the sidebar (grouped) and the six welcome cards. The English (or Kannada) question is what is sent.
// Every one was answered by the live assistant on 2026-10-05 (13 deterministic routes, booklet, KB, refusal); keep
// that true when editing.
const ICONS = {
  gauge: '<path d="M12 14l4-4"/><path d="M3.3 17a9 9 0 1 1 17.4 0"/><circle cx="12" cy="14" r="1.2"/>',
  line: '<path d="M3 3v18h18"/><path d="M7 15l4-5 3 3 5-7"/>',
  bars: '<path d="M3 3v18h18"/><path d="M8 17v-5M13 17V8M18 17V5"/>',
  down: '<path d="M3 7l6 6 4-4 8 8"/><path d="M21 11v6h-6"/>',
  book: '<path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20V3H6.5A2.5 2.5 0 0 0 4 5.5z"/><path d="M4 19.5V21h16"/>',
  shield: '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="M9 12l2 2 4-4"/>',
  alert: '<path d="M12 3l9 16H3z"/><path d="M12 10v4M12 17h.01"/>',
  bolt: '<path d="M13 2L4 14h7l-1 8 9-12h-7z"/>',
  gift: '<rect x="3" y="8" width="18" height="13" rx="2"/><path d="M12 8v13M3 12h18"/>',
  meter: '<circle cx="12" cy="12" r="9"/><path d="M12 12l4-3"/><path d="M7 16h10"/>',
  target: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1"/>',
  lang: '<path d="M4 5h9M8.5 3v2M6 5c0 4 3 7 6 8"/><path d="M11 5c-.5 3-3 6.5-6 8"/><path d="M13 21l4-9 4 9M14.5 18h5"/>',
};
const QUESTIONS = [
  { g: 0, q: 'What was the collection efficiency in Arkalgud in 2024-25?', en: 'Collection efficiency of a sub-division', kn: 'ಉಪ-ವಿಭಾಗದ ಸಂಗ್ರಹಣೆ ದಕ್ಷತೆ', i: 'gauge', card: 1 },
  { g: 0, q: 'total demand of the division in 2025-26', en: 'Division demand this year', kn: 'ಈ ವರ್ಷದ ವಿಭಾಗದ ಬೇಡಿಕೆ', i: 'bars' },
  { g: 0, q: 'collection efficiency trend', en: 'Collection efficiency, year by year', kn: 'ವರ್ಷವಾರು ಸಂಗ್ರಹಣೆ ದಕ್ಷತೆ', i: 'line', card: 2 },
  { g: 0, q: 'which sub-division collects the best in 2024-25', en: 'Sub-divisions ranked', kn: 'ಉಪ-ವಿಭಾಗಗಳ ಶ್ರೇಣಿ', i: 'bars', card: 3 },
  { g: 0, q: 'compare this year and last year collection', en: 'This year against last year', kn: 'ಈ ವರ್ಷ ಮತ್ತು ಕಳೆದ ವರ್ಷ', i: 'line' },
  { g: 0, q: 'which area has the most pending dues', en: 'Where arrears are highest', kn: 'ಹೆಚ್ಚು ಬಾಕಿ ಎಲ್ಲಿದೆ', i: 'alert' },
  { g: 1, q: 'LT-1 vs HT-2 demand', en: 'LT-1 against HT-2, every year', kn: 'LT-1 ಮತ್ತು HT-2, ಪ್ರತಿ ವರ್ಷ', i: 'bars', card: 4 },
  { g: 1, q: 'top 3 tariff categories by demand', en: 'Largest tariff categories', kn: 'ದೊಡ್ಡ ಸುಂಕ ವರ್ಗಗಳು', i: 'bars' },
  { g: 1, q: 'LT vs HT demand in 2024-25', en: 'LT against HT', kn: 'LT ಮತ್ತು HT', i: 'target' },
  { g: 1, q: 'domestic vs commercial demand in 2025-26', en: 'Domestic against commercial', kn: 'ಗೃಹ ಮತ್ತು ವಾಣಿಜ್ಯ', i: 'target' },
  { g: 1, q: 'What is the LT-3 tariff rate?', en: 'A rate from the CESC tariff booklet', kn: 'ಸುಂಕ ಕೈಪಿಡಿಯ ದರ', i: 'book', card: 5 },
  { g: 1, q: 'Which category is a homestay?', en: 'Which category a connection is', kn: 'ಸಂಪರ್ಕದ ವರ್ಗ', i: 'book' },
  { g: 2, q: 'subsidy share trend', en: 'Government subsidy share, year by year', kn: 'ವರ್ಷವಾರು ಸರ್ಕಾರಿ ಸಹಾಯಧನ', i: 'gift', card: 6 },
  { g: 2, q: 'AT&C loss by sub-division in 2025-26', en: 'AT&C loss by sub-division', kn: 'ಉಪ-ವಿಭಾಗವಾರು AT&C ನಷ್ಟ', i: 'bolt' },
  { g: 2, q: 'MNR installations in 2024-25', en: 'Meters not recording (MNR)', kn: 'MNR ಸ್ಥಾಪನೆಗಳು', i: 'meter' },
  { g: 2, q: 'FPPCA collected in 2024-25', en: 'FPPCA collected', kn: 'FPPCA ಸಂಗ್ರಹ', i: 'down' },
  { g: 3, q: 'show me arkalgud numbers', en: 'A sub-division at a glance', kn: 'ಉಪ-ವಿಭಾಗದ ಸಾರಾಂಶ', i: 'target' },
  { g: 3, q: 'What does DCB stand for?', en: 'Explain a term', kn: 'ಪದದ ವಿವರಣೆ', i: 'book' },
  { g: 3, q: 'ಅರಕಲಗೂಡಿನಲ್ಲಿ 2024-25 ರ ಸಂಗ್ರಹಣೆ ದಕ್ಷತೆ ಎಷ್ಟು?', en: 'A question in Kannada', kn: 'ಕನ್ನಡದಲ್ಲಿ ಪ್ರಶ್ನೆ', i: 'lang' },
  { g: 3, q: 'demand for LT-9', en: 'Safeguard: an unknown category', kn: 'ಸುರಕ್ಷತೆ: ಅಜ್ಞಾತ ವರ್ಗ', i: 'shield' },
];
const svg = name => `<svg viewBox="0 0 24 24" aria-hidden="true">${ICONS[name]}</svg>`;

// ------------------------------------------------------------------ elements
const chat = document.getElementById('chat');
const scroller = document.getElementById('chatScroll');
const box = document.getElementById('box');
const sendBtn = document.getElementById('send');
const govBrand = document.getElementById('govBrand');
let busy = false;

const esc = s => String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const nearBottom = () => scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 160;
const REDUCED = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
const toBottom = (force, smooth) => { if (force || nearBottom()) scroller.scrollTo({ top: scroller.scrollHeight, behavior: smooth && !REDUCED ? 'smooth' : 'auto' }); };

// Answers are plain text. Markup allowed: **bold**, blank-line paragraphs, lines starting with "•" or "-" as a list,
// and a Markdown table (the tariff-booklet route). Everything is escaped first, so nothing in an answer can inject HTML.
function formatAnswer(text){
  const bold = s => esc(s).replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
  const cells = l => l.trim().replace(/^\||\|$/g, '').split('|').map(c => c.trim());
  return String(text).replace(/^\s+/, '').split(/\n{2,}/).map(block => {
    let html = '', list = [], para = [], rows = [];
    const flushList = () => { if (list.length){ html += '<ul>' + list.map(l => '<li>' + bold(l) + '</li>').join('') + '</ul>'; list = []; } };
    const flushPara = () => { if (para.length){ const note = /^Note:\s/.test(para[0]);
      html += (note ? '<p class="note">' : '<p>') + para.map((l, i) => i === 0 && note ? '<span class="note-tag">Note</span>' + bold(l.replace(/^Note:\s*/, '')) : bold(l)).join('<br>') + '</p>'; para = []; } };
    const flushTable = () => {
      if (!rows.length) return;
      const body = rows.filter(r => !r.every(c => /^:?-{2,}:?$/.test(c)));
      const [head, ...rest] = body;
      html += '<div class="md-table"><table><thead><tr>' + (head || []).map(c => `<th>${bold(c)}</th>`).join('') + '</tr></thead><tbody>'
        + rest.map(r => '<tr>' + r.map(c => `<td>${bold(c)}</td>`).join('') + '</tr>').join('') + '</tbody></table></div>';
      rows = [];
    };
    block.split('\n').forEach(l => {
      if (/^\s*\|.*\|\s*$/.test(l)){ flushPara(); flushList(); rows.push(cells(l)); return; }
      flushTable();
      const m = l.match(/^\s*[•\-]\s+(.*)$/);
      if (m){ flushPara(); list.push(m[1]); } else if (l.trim() || para.length){ flushList(); para.push(l); }
    });
    flushPara(); flushList(); flushTable();
    return html;
  }).join('');
}

// ------------------------------------------------------------------ sidebar + welcome
function renderSidebar(){
  const host = document.getElementById('sideGroups'); host.innerHTML = '';
  t('groups').forEach((name, gi) => {
    const g = document.createElement('div'); g.className = 'side-group';
    g.innerHTML = `<h3>${esc(name)}</h3><div class="side-list"></div>`;
    QUESTIONS.filter(x => x.g === gi).forEach(x => {
      const b = document.createElement('button'); b.type = 'button'; b.className = 'side-item'; b.title = x[lang];
      b.innerHTML = svg(x.i) + `<span>${esc(x.q)}</span>`;
      b.onclick = () => { document.body.classList.remove('sidebar-open'); ask(x.q); };
      g.lastElementChild.appendChild(b);
    });
    host.appendChild(g);
  });
}
function renderWelcome(){
  chat.innerHTML = '';
  const w = document.createElement('div'); w.className = 'welcome'; w.id = 'welcome';
  w.innerHTML = `<div class="eyebrow">${esc(t('eyebrow'))}</div><h2>${esc(t('welcomeTitle'))}</h2>
    <div class="ornament"><i></i><b></b><i></i></div>
    <div class="cards-label">${esc(t('cardsLabel'))}</div><div class="welcome-cards"></div>`;
  const cards = w.querySelector('.welcome-cards');
  QUESTIONS.filter(x => x.card).sort((a, b) => a.card - b.card).forEach(x => {
    const b = document.createElement('button'); b.type = 'button'; b.className = 'welcome-card';
    b.innerHTML = `<span class="top">${svg(x.i)}<span class="title">${esc(x[lang])}</span></span><span class="q">${esc(x.q)}</span>`;
    b.onclick = () => ask(x.q);
    cards.appendChild(b);
  });
  chat.appendChild(w);
}

// ------------------------------------------------------------------ messages
function addMessage(who){
  const m = document.createElement('div'); m.className = 'msg ' + who;
  const av = document.createElement('div'); av.className = 'av';
  av.innerHTML = who === 'bot' ? '<img src="/static/cesc_logo_clean.png" alt="">'
    : '<svg viewBox="0 0 24 24"><circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/></svg>';
  const b = document.createElement('div'); b.className = 'bubble';
  m.append(av, b); chat.appendChild(m); toBottom(true, true);
  return b;
}

// The data table under an answer ({columns, rows, units, raw} from app/answer.py), with CSV download: text a
// spreadsheet would read as a formula is written with a leading apostrophe; plain numbers stay numbers.
function renderTable(host, table){
  if (!table || !Array.isArray(table.columns) || !Array.isArray(table.rows) || !table.rows.length) return;
  const d = document.createElement('details'); d.className = 'panel reveal';
  const s = document.createElement('summary');
  s.innerHTML = `<span>${esc(t('dataTable', table.rows.length.toLocaleString('en-IN')))}</span>`;
  const csv = document.createElement('span'); csv.className = 'pill pill-btn csv'; csv.setAttribute('role', 'button'); csv.tabIndex = 0;
  csv.innerHTML = '<svg viewBox="0 0 24 24"><path d="M12 3v12"/><path d="M7 10l5 5 5-5"/><path d="M5 21h14"/></svg>' + esc(t('csv'));
  const dl = e => {
    e.preventDefault(); e.stopPropagation();
    const safe = v => { const x = String(v ?? ''); return /^[=+\-@\t\r]/.test(x) && !/^[+-]?[\d,]*\.?\d+%?$/.test(x) ? "'" + x : x; };
    const q = v => '"' + safe(v).replace(/"/g, '""') + '"';
    const text = [table.columns.map(q).join(',')].concat((table.raw || table.rows).map(r => r.map(q).join(','))).join('\r\n');
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob(['﻿' + text], { type: 'text/csv;charset=utf-8' }));
    a.download = 'dcb_answer.csv'; document.body.appendChild(a); a.click();
    setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 500);
  };
  csv.onclick = dl; csv.onkeydown = e => { if (e.key === 'Enter' || e.key === ' ') dl(e); };
  s.appendChild(csv); d.appendChild(s);
  const wrap = document.createElement('div'); wrap.className = 'table-wrap';
  const num = i => table.units && table.units[i];
  let h = '<table><thead><tr>' + table.columns.map((x, i) => `<th class="${num(i) ? 'num' : ''}">${esc(x)}</th>`).join('') + '</tr></thead><tbody>';
  table.rows.forEach(r => { h += '<tr>' + r.map((v, i) => `<td class="${num(i) ? 'num' : ''}">${esc(v === '' || v == null ? '—' : v)}</td>`).join('') + '</tr>'; });
  wrap.innerHTML = h + '</tbody></table>'; d.appendChild(wrap);
  host.appendChild(d);
}

// Chart data is built by the server from the query result (app/answer.py), never by the model: one or more series over
// the same labels, with the answer's unit. ux.js draws the card with the Bar / Line / Doughnut toggle.
function renderChart(host, c){
  if (!c || !Array.isArray(c.values) || c.values.length < 2 || !window.UX) return;
  const series = Array.isArray(c.series) && c.series.length ? c.series : [{ label: c.series_label || '', values: c.values }];
  const title = series.length > 1 ? series.map(s => s.label).filter(Boolean).join(' vs ') : (c.series_label || (series[0] && series[0].label) || '');
  UX.chartCard(host, { labels: c.labels, series, unit: c.unit || '', type: c.type, title });
}

// ------------------------------------------------------------------ "View report" / "Download report" (Excel)
// The server kept the answer under report_id and builds the workbook from it; a 409 means a check inside the workbook
// failed and it is withheld ("under review").
function viewButton(id){
  const b = document.createElement('button'); b.type = 'button'; b.className = 'pill pill-btn view-btn'; b.title = t('viewReportTitle');
  b.innerHTML = '<svg viewBox="0 0 24 24"><path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/></svg>' + esc(t('viewReport'));
  b.onclick = () => window.ReportViewer && ReportViewer.open({ viewUrl: '/api/answer-report/' + encodeURIComponent(id) + '/view', opener: b, onDownload: () => downloadReport(id) });
  return b;
}
async function downloadReport(id){
  const r = await fetch('/api/answer-report/' + encodeURIComponent(id));
  if (r.status === 409){ const b = await r.json().catch(() => ({})); const e = new Error('under review'); e.review = b.message || ''; throw e; }
  if (!r.ok) throw new Error('HTTP ' + r.status);
  const blob = await r.blob();
  const m = /filename="?([^";]+)"?/.exec(r.headers.get('Content-Disposition') || '');
  const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = m ? m[1] : 'DCB_answer.xlsx';
  document.body.appendChild(a); a.click();
  setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 4000);
}
function reportButton(id){
  const b = document.createElement('button'); b.type = 'button'; b.className = 'pill pill-btn report-btn'; b.title = t('dlReportTitle');
  const ic = '<svg viewBox="0 0 24 24"><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/><path d="M12 11v6"/><path d="M9.5 14.5 12 17l2.5-2.5"/></svg>';
  const spin = '<svg viewBox="0 0 24 24"><path d="M21 12a9 9 0 1 1-6.2-8.6"/></svg>';
  const label = (icon, text, xl) => { b.innerHTML = icon + esc(text) + (xl ? '<span class="xl">XLSX</span>' : ''); };
  label(ic, t('dlReport'), true);
  let working = false;
  b.onclick = async () => {
    if (working) return; working = true;
    b.classList.remove('fail'); b.classList.add('busy'); label(spin, t('dlReportBusy'));
    try {
      await downloadReport(id);
      b.classList.remove('busy'); label(ic, t('dlReportDone'), true);
      if (window.UX) UX.toast(t('dlReportDone'));
      setTimeout(() => label(ic, t('dlReport'), true), 2200);
    } catch (e) {
      b.classList.remove('busy'); b.classList.add('fail');
      if (e && e.review){ label(ic, t('dlReportReview')); b.title = e.review; } else label(ic, t('dlReportFail'));
      if (window.UX) UX.toast(e && e.review ? t('dlReportReview') : t('dlReportFail'), true);
    } finally { working = false; }
  };
  return b;
}

// ------------------------------------------------------------------ meta row
function renderMeta(bubble, done, sql, secs, answerText){
  const meta = document.createElement('div'); meta.className = 'meta reveal';
  if (done && done.answerable === false){
    bubble.classList.add('refused');
    const p = document.createElement('span'); p.className = 'pill nf'; p.textContent = t('notAnswerable'); meta.appendChild(p);
  }
  if (done && done.truncated){ const p = document.createElement('span'); p.className = 'pill trunc'; p.textContent = t('truncated'); meta.appendChild(p); }
  const time = document.createElement('span'); time.textContent = t('answeredIn', secs); meta.appendChild(time);
  const sp = document.createElement('span'); sp.className = 'spacer'; meta.appendChild(sp);
  if (done && done.report_id && done.answerable !== false){ if (window.ReportViewer) meta.appendChild(viewButton(done.report_id)); meta.appendChild(reportButton(done.report_id)); }
  if (answerText){
    const cp = document.createElement('button'); cp.type = 'button'; cp.className = 'pill pill-btn';
    const ic = '<svg viewBox="0 0 24 24"><rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/></svg>';
    cp.innerHTML = ic + esc(t('copy'));
    cp.onclick = async () => {
      try { await navigator.clipboard.writeText(answerText.replace(/\*\*/g, '')); cp.innerHTML = ic + esc(t('copied'));
            setTimeout(() => { cp.innerHTML = ic + esc(t('copy')); }, 1400); } catch (_e) { /* clipboard blocked */ }
    };
    meta.appendChild(cp);
  }
  bubble.appendChild(meta);
  // SQL, route and timings: only when the server runs with DCB_SHOW_TECHNICAL_DETAILS=true.
  if (done && done.technical && (sql || (done.timing_ms && Object.keys(done.timing_ms).length))){
    const d = document.createElement('details'); d.className = 'panel';
    d.innerHTML = `<summary>${esc(t('technical'))}</summary>`;
    const tm = done.timing_ms || {};
    const parts = Object.keys(tm).filter(k => k.endsWith('_ms')).map(k => k.replace(/_ms$/, '') + ': ' + tm[k] + 'ms');
    if (parts.length || done.route){ const x = document.createElement('div'); x.className = 'timing'; x.textContent = (done.route ? 'route: ' + done.route + ' · ' : '') + parts.join(' · '); d.appendChild(x); }
    if (sql){ const pre = document.createElement('pre'); pre.textContent = sql; d.appendChild(pre); }
    bubble.appendChild(d);
  }
}

// ------------------------------------------------------------------ ask (SSE)
const CLIENT_TIMEOUT_MS = 200000;   // the gateway's own timeout is long; the server answers "busy" after 20 s in the queue
// The last question answered from the data (as the server took it), sent with the next one so a follow-up ("and
// Konanur?", "what about 2023-24?", "and the demand?") is answered as the whole question it stands for
// (app/followup.py). Cleared by New conversation.
let lastAsked = '';
// Voice mode: ask(text, { mode: 'voice' }) resolves as soon as the server's spoken form arrives ({ spoken_answer,
// answerable }), so the voice can start while the text is still typing out; null when nothing was answered. Typed
// questions call it the same way and ignore the promise.
function ask(text, opts){
  let settle = null;
  const settled = new Promise(r => { settle = r; });
  askRun(text, opts || {}, settle);
  return settled;
}
async function askRun(text, opts, settleFn){
  const voiceTurn = opts.mode === 'voice';
  let settle = settleFn;
  const resolveOnce = v => { if (settle){ const f = settle; settle = null; f(v); } };
  text = (text || '').trim();
  if (!text || busy){ resolveOnce(null); return; }
  busy = true; sendBtn.disabled = true;
  const w = document.getElementById('welcome'); if (w) w.remove();
  const ub = addMessage('user'); ub.textContent = text; if (!voiceTurn) box.value = '';
  if (voiceTurn){ ub.classList.add('voice-q'); ub.title = t('spokenQuestion'); }

  const bot = addMessage('bot');
  const progress = document.createElement('div'); progress.className = 'progress';
  progress.innerHTML = '<div class="progress-bar"></div><div class="progress-row"><div class="stages"></div><div class="elapsed"></div></div>';
  const stagesEl = progress.querySelector('.stages'), elapsedEl = progress.querySelector('.elapsed');
  const answerEl = document.createElement('div'); answerEl.className = 'answer';
  bot.append(progress, answerEl);
  govBrand.classList.add('thinking');

  const t0 = performance.now();
  const secs = () => ((performance.now() - t0) / 1000).toFixed(1);
  const tick = setInterval(() => { elapsedEl.textContent = t('elapsed', Math.floor((performance.now() - t0) / 1000)); }, 500);
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), CLIENT_TIMEOUT_MS);
  let acc = '', sql = '', done = null, table = null, chartData = null, streaming = false;
  const seen = new Set();

  const showStage = stage => {
    const label = (t('stages') || {})[stage]; if (!label || seen.has(label)) return; seen.add(label);
    stagesEl.querySelectorAll('.stage.now').forEach(s => { s.classList.remove('now'); s.classList.add('done'); });
    const s = document.createElement('span'); s.className = 'stage now'; s.innerHTML = '<i></i>'; s.appendChild(document.createTextNode(label));
    stagesEl.appendChild(s); toBottom();
  };
  const endProgress = () => { clearInterval(tick); if (progress.isConnected) progress.remove(); govBrand.classList.remove('thinking'); };

  const frame = f => {
    let ev = '', data = '';
    f.split('\n').forEach(l => { if (l.startsWith('event:')) ev = l.slice(6).trim(); else if (l.startsWith('data:')) data += l.slice(5).trim(); });
    if (!ev || !data) return;
    let d; try { d = JSON.parse(data); } catch (_e) { return; }
    if (ev === 'status'){ if (d.stage !== 'answering') showStage(d.stage); }
    else if (ev === 'followup' && d.asked_as){
      const note = document.createElement('div'); note.className = 'asked-as';
      note.textContent = t('askedAs') + ' '; const b = document.createElement('b'); b.textContent = d.asked_as; note.appendChild(b);
      bot.insertBefore(note, progress.isConnected ? progress : answerEl); toBottom();
    }
    else if (ev === 'sql') sql = d.sql || '';
    else if (ev === 'chart') chartData = d.chart_data || null;
    else if (ev === 'answer_chunk'){
      if (!streaming){ streaming = true; endProgress(); answerEl.classList.add('streaming'); }
      acc += d.text; answerEl.innerHTML = formatAnswer(acc); toBottom();
    }
    else if (ev === 'table') table = d.table;
    else if (ev === 'done') done = d;
    else if (ev === 'spoken') resolveOnce({ spoken_answer: d.spoken_answer || '', answerable: d.answerable });
  };

  try {
    const r = await fetch('/api/ask', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(Object.assign({ question: text }, voiceTurn ? { mode: 'voice' } : {}, lastAsked ? { previous: lastAsked } : {})), signal: ctrl.signal });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const reader = r.body.getReader(), dec = new TextDecoder(); let buf = '';
    while (true){
      const { value, done: end } = await reader.read(); if (end) break;
      buf += dec.decode(value, { stream: true });
      let i; while ((i = buf.indexOf('\n\n')) >= 0){ frame(buf.slice(0, i)); buf = buf.slice(i + 2); }
    }
    buf += dec.decode(); if (buf.trim()) frame(buf);
    clearTimeout(timer); endProgress();
    answerEl.classList.remove('streaming');
    if (!acc) answerEl.textContent = t('noText'); else answerEl.innerHTML = formatAnswer(acc);
    if (!done || done.answerable !== false) renderChart(bot, chartData);
    renderTable(bot, table);
    renderMeta(bot, done, sql, secs(), acc.trim());
    bot.closest('.msg').classList.add('settled');
    if (done && done.context) lastAsked = done.asked_as || text;   // only answers from the data; a refusal keeps the earlier context
    resolveOnce(acc ? { spoken_answer: (done && done.spoken_answer) || '', answer: acc, answerable: done ? done.answerable : true } : null);
  } catch (e) {
    clearTimeout(timer); endProgress();
    answerEl.classList.remove('streaming');
    if (window.console) console.error(e);   // never show raw error text to users
    bot.classList.add('err');
    answerEl.innerHTML = formatAnswer((e && e.name === 'AbortError' ? t('timedOut') : t('failed')) + (acc ? '\n\n' + t('partial') + '\n' + acc : ''));
  }
  resolveOnce(null);
  requestAnimationFrame(() => toBottom(false, true));
  busy = false; sendBtn.disabled = false; if (!voiceTurn) box.focus();
}

// ------------------------------------------------------------------ model status (kept quiet, in the footnote)
let status = 'checking';
function renderStatus(){
  const el = document.getElementById('modelStatus');
  el.className = 'model-status' + (status === 'ready' ? ' ok' : status === 'offline' ? ' off' : status === 'down' ? ' bad' : '');
  document.getElementById('modelStatusText').textContent = t({ ready: 'modelReady', offline: 'modelOffline', down: 'modelDown', checking: 'modelChecking' }[status]);
}
fetch('/health').then(r => r.json()).then(h => { status = h.llm_configured ? 'ready' : 'offline'; renderStatus(); }).catch(() => { status = 'down'; renderStatus(); });

// ------------------------------------------------------------------ language (remembered per browser)
function save(k, v){ try { localStorage.setItem(k, v); } catch (_e) {} }
function load(k){ try { return localStorage.getItem(k); } catch (_e) { return null; } }
function applyLang(l){
  lang = l === 'kn' ? 'kn' : 'en'; document.documentElement.lang = lang;
  document.querySelectorAll('[data-i18n]').forEach(el => { el.textContent = t(el.dataset.i18n); });
  document.querySelectorAll('[data-i18n-html]').forEach(el => { el.innerHTML = t(el.dataset.i18nHtml); });
  document.querySelectorAll('[data-i18n-placeholder]').forEach(el => { el.placeholder = t(el.dataset.i18nPlaceholder); });
  document.getElementById('langEn').setAttribute('aria-pressed', lang === 'en');
  document.getElementById('langKn').setAttribute('aria-pressed', lang === 'kn');
  renderStatus(); renderSidebar();
  if (document.getElementById('welcome')) renderWelcome();
  save('dcb-lang', lang);
}
document.getElementById('langEn').onclick = () => applyLang('en');
document.getElementById('langKn').onclick = () => applyLang('kn');

// ------------------------------------------------------------------ wiring
sendBtn.onclick = () => ask(box.value);
box.addEventListener('keydown', e => { if (e.key === 'Enter' && !e.isComposing) ask(box.value); });
document.addEventListener('keydown', e => {
  if (e.key === '/' && document.activeElement !== box && !/INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName)){ e.preventDefault(); box.focus(); }
});
document.getElementById('dashLink').addEventListener('click', e => {
  e.preventDefault();
  const href = e.currentTarget.getAttribute('href');
  const ov = document.createElement('div'); ov.className = 'go-overlay';
  ov.innerHTML = '<div class="splash-logos"><span class="splash-halo"><img src="/static/cescom_official_logo.png" alt=""></span>'
    + '<span class="splash-halo"><img src="/static/cesc_logo_clean.png" alt=""></span></div><div class="splash-text"><b></b><small>CESC · Holenarasipura Division</small></div>';
  ov.querySelector('b').textContent = t('goDash');
  document.body.appendChild(ov);
  try { sessionStorage.setItem('cesc-splash', '1'); } catch (_e) {}
  setTimeout(() => { location.href = href; }, REDUCED ? 100 : 700);
});
// Voice mode: the voice panel (voice/voice-ui.js) asks through the same ask() as typing, so every spoken answer is
// streamed into the chat exactly like a typed one (chart, table, View / Download report).
window.DCBChat = { ask, isBusy: () => busy, lang: () => lang };
document.getElementById('newchat').onclick = () => { if (busy) return; lastAsked = ''; renderWelcome(); document.body.classList.remove('sidebar-open'); box.focus(); };
document.getElementById('sidebarToggle').onclick = () => document.body.classList.toggle('sidebar-open');
document.getElementById('drawerBackdrop').onclick = () => document.body.classList.remove('sidebar-open');

// The splash plays once per browser session (the dashboard pages share the flag).
(function(){
  const sp = document.getElementById('splash');
  let seen = null; try { seen = sessionStorage.getItem('cesc-splash') || sessionStorage.getItem('cesc-chat-splash'); } catch (_e) {}
  if (seen && !new URLSearchParams(location.search).get('q')){ sp.remove(); return; }
  try { sessionStorage.setItem('cesc-chat-splash', '1'); } catch (_e) {}
  setTimeout(() => { sp.classList.add('gone'); setTimeout(() => sp.remove(), 550); }, REDUCED ? 150 : seen ? 600 : 1300);
})();

renderWelcome();
applyLang(load('dcb-lang') === 'kn' ? 'kn' : 'en');
// ?q=... (links from the dashboard and Insights) asks straight away
{ const pre = new URLSearchParams(location.search).get('q'); if (pre) ask(pre.slice(0, 500)); }
box.focus();
