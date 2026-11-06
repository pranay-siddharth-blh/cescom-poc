/* Cescom voice panel (ported from RDPR) (plain JavaScript; the engine is voice-agent.js). Modelled on the Meghalaya agent's VoiceMode,
   as integrated into the KPCL voice instance:

   * the round voice button beside "Ask" starts voice mode; the browser asks Allow / Block for the microphone the first
     time, and voice starts as soon as it is allowed;
   * full panel: the orb over the blurred chat, status, captions, and round controls (mute, settings, back to chat,
     end); docked bar (chevron down): the orb takes the composer's place and the chat stays visible, with every spoken
     answer streaming into it exactly like a typed one (chart, table, View / Download report). Every press of the voice
     button opens the full panel, however voice was left last time (docking is for this session's reading only);
   * settings: officer voice (server) or device voice, speaking speed, hands-free or hold-to-talk, captions, sounds;
   * keyboard: Esc ends, M mutes, Space holds to talk (hold-to-talk) or interrupts a reply. */
import { VoiceAgent, prefetchVad } from './voice-agent.js';
import { VOICE_SPEEDS } from './voice-text.js';

const PREFS_KEY = 'dcb-voice-prefs';
const DEFAULT_PREFS = { voice: 'server', speed: 1, mode: 'auto', captions: true, sounds: true, docked: false };
const L = {
  en: {
    launch: 'Talk to the assistant', title: 'Voice conversation', eyebrow: 'CESC DCB Assistant',
    starting: 'Starting…', startingSub: 'Please allow the microphone when your browser asks.',
    listening: 'Listening', listeningSub: 'Ask about the division, a sub-division or a tariff category, and a year.', muted: 'Microphone is off',
    holdSub: 'Hold the orb or the Space bar, and speak.', holding: 'Listening — release to send',
    thinking: 'Looking up the figures', speaking: 'Speaking', speakingSub: 'Start talking, or tap the orb, to interrupt.',
    you: 'You asked', reply: 'Assistant', mute: 'Turn microphone off (M)', unmute: 'Turn microphone on (M)',
    settings: 'Voice settings', dock: 'Back to the chat (voice stays on)', expand: 'Open the voice panel', end: 'End voice (Esc)',
    endShort: 'End', retry: 'Try again', basic: 'basic speech detection',
    sheetTitle: 'Voice settings', sheetSub: 'Spoken answers are short; the full answer, chart and report appear in the chat.',
    voice: 'Voice', officer: 'Officer voice', device: 'Device voice', speed: 'Speaking speed', how: 'How you talk',
    auto: 'Hands-free', ptt: 'Hold to talk', captions: 'Show captions', sounds: 'Start and send sounds', done: 'Done',
    hint: 'The microphone is used only while voice mode is on. Shortcuts: ', unavailable: 'Voice input is not available on this server.',
    heardTitle: 'I heard', notWhat: "That's not what I said", askNow: 'Ask now', confirmTitle: 'Please confirm',
    neither: 'Neither', typeTitle: 'Type your question', typePh: 'Type a question…', ask: 'Ask', tryVoice: 'Try voice again',
    micOn: 'Microphone on', micOff: 'Microphone off', speechIn: 'Speech in', speechOut: 'Voice', ready: 'ready',
    down: 'unavailable', checking: 'checking',
  },
  kn: {
    launch: 'ಸಹಾಯಕರೊಂದಿಗೆ ಮಾತನಾಡಿ', title: 'ಧ್ವನಿ ಸಂಭಾಷಣೆ', eyebrow: 'ಸೆಸ್ಕ್ ಡಿಸಿಬಿ ಸಹಾಯಕ',
    starting: 'ಪ್ರಾರಂಭವಾಗುತ್ತಿದೆ…', startingSub: 'ಬ್ರೌಸರ್ ಕೇಳಿದಾಗ ಮೈಕ್ರೊಫೋನ್‌ಗೆ ಅನುಮತಿ ನೀಡಿ.',
    listening: 'ಆಲಿಸಲಾಗುತ್ತಿದೆ', listeningSub: 'ಇಂಗ್ಲಿಷ್‌ನಲ್ಲಿ ವಿಭಾಗ, ಉಪವಿಭಾಗ ಅಥವಾ ಸುಂಕ ವರ್ಗ ಮತ್ತು ವರ್ಷದ ಬಗ್ಗೆ ಕೇಳಿ.', muted: 'ಮೈಕ್ರೊಫೋನ್ ಆಫ್ ಆಗಿದೆ',
    holdSub: 'ವೃತ್ತ ಅಥವಾ Space ಒತ್ತಿ ಹಿಡಿದು ಮಾತನಾಡಿ.', holding: 'ಆಲಿಸಲಾಗುತ್ತಿದೆ — ಕಳುಹಿಸಲು ಬಿಡಿ',
    thinking: 'ಅಂಕಿಅಂಶಗಳನ್ನು ಹುಡುಕಲಾಗುತ್ತಿದೆ', speaking: 'ಮಾತನಾಡಲಾಗುತ್ತಿದೆ', speakingSub: 'ತಡೆಯಲು ಮಾತನಾಡಿ ಅಥವಾ ವೃತ್ತವನ್ನು ಒತ್ತಿ.',
    you: 'ನಿಮ್ಮ ಪ್ರಶ್ನೆ', reply: 'ಸಹಾಯಕ', mute: 'ಮೈಕ್ರೊಫೋನ್ ಆಫ್ (M)', unmute: 'ಮೈಕ್ರೊಫೋನ್ ಆನ್ (M)',
    settings: 'ಧ್ವನಿ ಸೆಟ್ಟಿಂಗ್‌ಗಳು', dock: 'ಸಂಭಾಷಣೆಗೆ ಹಿಂತಿರುಗಿ', expand: 'ಧ್ವನಿ ಫಲಕ ತೆರೆಯಿರಿ', end: 'ಧ್ವನಿ ಮುಗಿಸಿ (Esc)',
    endShort: 'ಮುಗಿಸಿ', retry: 'ಮತ್ತೆ ಪ್ರಯತ್ನಿಸಿ', basic: 'ಮೂಲ ಧ್ವನಿ ಪತ್ತೆ',
    sheetTitle: 'ಧ್ವನಿ ಸೆಟ್ಟಿಂಗ್‌ಗಳು', sheetSub: 'ಮಾತಿನ ಉತ್ತರಗಳು ಚಿಕ್ಕವು; ಪೂರ್ಣ ಉತ್ತರ, ಚಾರ್ಟ್ ಮತ್ತು ವರದಿ ಸಂಭಾಷಣೆಯಲ್ಲಿ ಇರುತ್ತವೆ.',
    voice: 'ಧ್ವನಿ', officer: 'ಅಧಿಕಾರಿ ಧ್ವನಿ', device: 'ಸಾಧನದ ಧ್ವನಿ', speed: 'ಮಾತಿನ ವೇಗ', how: 'ನೀವು ಹೇಗೆ ಮಾತನಾಡುತ್ತೀರಿ',
    auto: 'ಕೈ ಬಳಸದೆ', ptt: 'ಒತ್ತಿ ಮಾತನಾಡಿ', captions: 'ಉಪಶೀರ್ಷಿಕೆ ತೋರಿಸಿ', sounds: 'ಪ್ರಾರಂಭ ಮತ್ತು ಕಳುಹಿಸುವ ಧ್ವನಿ', done: 'ಸರಿ',
    hint: 'ಧ್ವನಿ ಮೋಡ್ ಆನ್ ಇರುವಾಗ ಮಾತ್ರ ಮೈಕ್ರೊಫೋನ್ ಬಳಸಲಾಗುತ್ತದೆ. ಶಾರ್ಟ್‌ಕಟ್‌ಗಳು: ', unavailable: 'ಈ ಸರ್ವರ್‌ನಲ್ಲಿ ಧ್ವನಿ ಇನ್‌ಪುಟ್ ಲಭ್ಯವಿಲ್ಲ.',
    heardTitle: 'ನಾನು ಕೇಳಿದ್ದು', notWhat: 'ನಾನು ಹೇಳಿದ್ದು ಅದಲ್ಲ', askNow: 'ಈಗ ಕೇಳಿ', confirmTitle: 'ದಯವಿಟ್ಟು ಖಚಿತಪಡಿಸಿ',
    neither: 'ಎರಡೂ ಅಲ್ಲ', typeTitle: 'ನಿಮ್ಮ ಪ್ರಶ್ನೆಯನ್ನು ಟೈಪ್ ಮಾಡಿ', typePh: 'ಪ್ರಶ್ನೆ ಟೈಪ್ ಮಾಡಿ…', ask: 'ಕೇಳಿ', tryVoice: 'ಮತ್ತೆ ಧ್ವನಿ ಪ್ರಯತ್ನಿಸಿ',
    micOn: 'ಮೈಕ್ರೊಫೋನ್ ಆನ್', micOff: 'ಮೈಕ್ರೊಫೋನ್ ಆಫ್', speechIn: 'ಧ್ವನಿ ಇನ್‌ಪುಟ್', speechOut: 'ಧ್ವನಿ', ready: 'ಸಿದ್ಧ',
    down: 'ಲಭ್ಯವಿಲ್ಲ', checking: 'ಪರಿಶೀಲನೆ',
  },
};
const lang = () => (window.DCBChat && window.DCBChat.lang && window.DCBChat.lang() === 'kn') ? 'kn' : 'en';
const tr = k => L[lang()][k] || L.en[k];

const ICON = {
  mic: '<svg viewBox="0 0 24 24"><rect x="9" y="2" width="6" height="12" rx="3"/><path d="M19 10v1a7 7 0 0 1-14 0v-1"/><path d="M12 18v4"/></svg>',
  micOff: '<svg viewBox="0 0 24 24"><path d="M2 2l20 20"/><path d="M9 9v2a3 3 0 0 0 5.1 2.1M15 9.3V5a3 3 0 0 0-5.9-.8"/><path d="M19 10v1a7 7 0 0 1-.9 3.4M5 10v1a7 7 0 0 0 11.1 5.7"/><path d="M12 18v4"/></svg>',
  gear: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/></svg>',
  down: '<svg viewBox="0 0 24 24"><path d="m6 9 6 6 6-6"/></svg>',
  expand: '<svg viewBox="0 0 24 24"><path d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7"/></svg>',
  close: '<svg viewBox="0 0 24 24"><path d="M18 6 6 18M6 6l12 12"/></svg>',
  cc: '<svg viewBox="0 0 24 24"><rect x="2" y="5" width="20" height="14" rx="3"/><path d="M10 10.2a2.2 2.2 0 1 0 0 3.6M17 10.2a2.2 2.2 0 1 0 0 3.6"/></svg>',
};

function readPrefs() {
  try { return { ...DEFAULT_PREFS, ...JSON.parse(localStorage.getItem(PREFS_KEY) || '{}') }; } catch (e) { return { ...DEFAULT_PREFS }; }
}
function writePrefs(p) { try { localStorage.setItem(PREFS_KEY, JSON.stringify(p)); } catch (e) { /* storage blocked */ } }
const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

let prefs = readPrefs();
let config = { speech_in: true, speech_out: true, speed: true };
fetch('/api/voice/config').then(r => (r.ok ? r.json() : null)).then(c => { if (c) config = c; }).catch(() => {});

const agent = new VoiceAgent({
  ask: q => (window.DCBChat ? window.DCBChat.ask(q, { mode: 'voice' }) : Promise.resolve(null)),
  isBusy: () => !!(window.DCBChat && window.DCBChat.isBusy && window.DCBChat.isBusy()),
  getDeviceVoice: () => prefs.voice === 'device' || !config.speech_out,
  getSpeed: () => prefs.speed || 1,
  getMode: () => prefs.mode,
  getSounds: () => prefs.sounds,
});

// ------------------------------------------------------------------ DOM
const root = document.createElement('div');
root.className = 'vm';
root.innerHTML = `
  <div class="vm-full" role="dialog" aria-modal="true" aria-labelledby="vmTitle">
    <div class="vm-top">
      <button type="button" class="vm-icon" data-act="dock">${ICON.down}</button>
      <div class="vm-title" id="vmTitle"><small data-l="eyebrow"></small><span data-l="title"></span></div>
      <span class="sp"></span>
      <span class="vm-mic" aria-live="polite"></span>
      <span class="vm-health" title=""></span>
      <button type="button" class="vm-icon" data-act="captions">${ICON.cc}</button>
      <button type="button" class="vm-icon" data-act="settings">${ICON.gear}</button>
    </div>
    <button type="button" class="vm-orb vm-orb-big" data-act="orb" aria-label="Voice"><span class="vm-orb-core"></span></button>
    <div class="vm-status" role="status" aria-live="polite"><span class="vm-status-main"></span><small class="vm-status-sub"></small></div>
    <div class="vm-cap" aria-live="polite"></div>
    <div class="vm-heard" hidden><div class="vm-heard-bar"><i></i></div>
      <div class="vm-row-btns"><button type="button" class="vm-chip warn" data-act="notwhat"></button>
      <button type="button" class="vm-chip" data-act="asknow"></button></div></div>
    <div class="vm-confirm" hidden><div class="vm-row-btns vm-opts"></div></div>
    <form class="vm-typebox" hidden><input type="text" maxlength="300" autocomplete="off" aria-label="Type a question">
      <button type="submit" class="vm-chip solid" data-l="ask"></button>
      <button type="button" class="vm-chip" data-act="tryvoice"></button></form>
    <button type="button" class="vm-retry" data-act="retry"></button>
    <div class="vm-controls">
      <button type="button" class="vm-ctl" data-act="mute"></button>
      <button type="button" class="vm-ctl" data-act="dock2">${ICON.down}</button>
      <button type="button" class="vm-ctl end" data-act="end">${ICON.close}</button>
    </div>
  </div>`;
document.body.appendChild(root);

const dock = document.createElement('div');
dock.className = 'vm-dock';
dock.innerHTML = `
  <button type="button" class="vm-orb vm-orb-small" data-act="expand"><span class="vm-orb-core"></span></button>
  <div class="vm-dock-info" aria-live="polite"><span class="vm-dock-state"></span><span class="vm-mic vm-mic-small"></span><span class="vm-dock-cap"></span></div>
  <div class="vm-dock-act"></div>
  <button type="button" class="vm-dbtn" data-act="mute"></button>
  <button type="button" class="vm-dbtn" data-act="expand">${ICON.expand}</button>
  <button type="button" class="vm-dend" data-act="end"></button>`;
const composer = document.querySelector('footer .composer');
composer.appendChild(dock);
// The dock lives in the footer, but its visibility follows the panel state on the root element.
const syncDockVisibility = () => { dock.style.display = root.classList.contains('open') && root.classList.contains('docked') ? 'flex' : 'none'; };

const launch = document.createElement('button');
launch.type = 'button';
launch.id = 'voiceBtn';
launch.className = 'vm-launch';
launch.innerHTML = '<i></i><i></i><i></i><i></i>';
document.getElementById('send').before(launch);

const $ = sel => root.querySelector(sel);
const orbs = () => [root.querySelector('.vm-orb-big'), dock.querySelector('.vm-orb-small')];

// ------------------------------------------------------------------ state -> view
let lastUser = '', lastReply = '';

function statusFor(s) {
  switch (s.state) {
    case 'starting': return [tr('starting'), tr('startingSub')];
    case 'listening':
      if (s.ptt) return [s.holding ? tr('holding') : tr('listening'), tr('holdSub')];
      return s.muted ? [tr('muted'), ''] : [tr('listening'), tr('listeningSub')];
    case 'thinking': return [tr('thinking'), ''];
    case 'heard': return [tr('heardTitle'), ''];
    case 'confirm': return [(s.confirm && s.confirm.prompt) || tr('confirmTitle'), ''];
    case 'typing': return [s.error || tr('typeTitle'), ''];
    case 'speaking': return s.confirm && s.confirm.prompt ? [s.confirm.prompt, ''] : [tr('speaking'), s.ptt ? tr('holdSub') : tr('speakingSub')];
    case 'error': return [s.error || 'Something went wrong', ''];
    default: return ['', ''];
  }
}

let lastNotice = '';
function toast(message) {
  const t = document.createElement('div');
  t.className = 'vm-toast';
  t.setAttribute('role', 'status');
  t.textContent = message;
  document.body.appendChild(t);
  setTimeout(() => t.classList.add('gone'), 7000);
  setTimeout(() => t.remove(), 7600);
}

function renderExtras(s) {
  // "I heard": the transcript, a 2-second bar, and the two choices
  const heard = $('.vm-heard');
  heard.hidden = s.state !== 'heard';
  if (s.state === 'heard') {
    const bar = heard.querySelector('.vm-heard-bar i');
    bar.style.animation = 'none'; void bar.offsetWidth; bar.style.animation = '';
  }
  // "Did you mean ...?": one button per option, plus "Neither"
  const conf = $('.vm-confirm');
  const opts = s.confirm && s.confirm.options ? s.confirm.options : null;
  conf.hidden = !(s.state === 'confirm' || (s.state === 'speaking' && opts));
  const optHtml = opts ? opts.map((o, i) => `<button type="button" class="vm-chip solid" data-act="opt${i}">${esc(o.label)}</button>`).join('')
    + `<button type="button" class="vm-chip" data-act="neither">${esc(tr('neither'))}</button>` : '';
  if (conf.dataset.html !== optHtml) { conf.querySelector('.vm-opts').innerHTML = optHtml; conf.dataset.html = optHtml; }
  // type-instead (speech-to-text down)
  const typebox = $('.vm-typebox');
  typebox.hidden = !s.sttDown;
  // dock: the same choices, compact
  const act = dock.querySelector('.vm-dock-act');
  let dockHtml = '';
  if (s.state === 'heard') dockHtml = `<button type="button" class="vm-chip warn" data-act="notwhat">${esc(tr('notWhat'))}</button>`;
  else if (opts && (s.state === 'confirm' || s.state === 'speaking')) dockHtml = optHtml;
  else if (s.sttDown) dockHtml = `<form class="vm-typebox vm-typebox-dock"><input type="text" maxlength="300" autocomplete="off" placeholder="${esc(tr('typePh'))}" aria-label="Type a question"><button type="submit" class="vm-chip solid">${esc(tr('ask'))}</button><button type="button" class="vm-chip" data-act="tryvoice">${esc(tr('tryVoice'))}</button></form>`;
  if (act.dataset.html !== dockHtml) { act.innerHTML = dockHtml; act.dataset.html = dockHtml; }
  // microphone indicator: on while the microphone is capturing, off when muted or released
  const on = agent.active && !!agent.stream && !s.muted && !s.sttDown && s.state !== 'error';
  [root, dock].forEach(host => host.querySelectorAll('.vm-mic').forEach(m => {
    m.classList.toggle('on', on);
    m.textContent = on ? tr('micOn') : tr('micOff');
  }));
  if (s.notice && s.notice !== lastNotice) { lastNotice = s.notice; toast(s.notice); }
  if (!s.notice) lastNotice = '';
}

function render(s) {
  root.classList.toggle('is-muted', !!s.muted);
  ['starting', 'listening', 'thinking', 'speaking', 'error', 'off', 'heard', 'confirm', 'typing'].forEach(st => {
    root.classList.toggle('st-' + st, s.state === st);
    dock.classList.toggle('st-' + st, s.state === st);
  });
  if (s.userText) lastUser = s.userText;
  if (s.replyText) lastReply = s.replyText;
  else if (s.state !== 'speaking') lastReply = '';      // a new turn: never caption it with the previous reply
  const [main, sub] = statusFor(s);
  $('.vm-status-main').textContent = main;
  $('.vm-status-sub').textContent = sub;
  const cap = $('.vm-cap');
  let capHtml = '';
  if (s.state === 'speaking' && lastReply) capHtml = `<span class="who">${esc(tr('reply'))}</span>${esc(lastReply)}`;
  else if (s.state === 'heard' && s.heard) capHtml = `<span class="who">${esc(tr('heardTitle'))}</span>${esc(s.heard)}`;
  else if ((s.state === 'thinking' || s.state === 'speaking') && lastUser) capHtml = `<span class="who">${esc(tr('you'))}</span>${esc(lastUser)}`;
  cap.innerHTML = capHtml;
  cap.classList.toggle('off', !prefs.captions);
  dock.querySelector('.vm-dock-state').textContent = main;
  dock.querySelector('.vm-dock-cap').textContent = s.state === 'speaking' ? lastReply : s.state === 'heard' ? s.heard
    : s.state === 'thinking' ? lastUser : sub;
  root.querySelectorAll('[data-act="mute"]').forEach(b => { b.innerHTML = s.muted ? ICON.micOff : ICON.mic; b.classList.toggle('on', !!s.muted);
    b.title = s.muted ? tr('unmute') : tr('mute'); b.setAttribute('aria-label', b.title); });
  dock.querySelectorAll('[data-act="mute"]').forEach(b => { b.innerHTML = s.muted ? ICON.micOff : ICON.mic; b.classList.toggle('on', !!s.muted);
    b.title = s.muted ? tr('unmute') : tr('mute'); b.setAttribute('aria-label', b.title); });
  $('.vm-retry').textContent = tr('retry');
  renderExtras(s);
  if (s.state === 'off') close(false);
}

function applyLabels() {
  root.querySelectorAll('[data-l]').forEach(el => { el.textContent = tr(el.dataset.l); });
  const set = (sel, key, host = root) => host.querySelectorAll(sel).forEach(b => { b.title = tr(key); b.setAttribute('aria-label', tr(key)); });
  set('[data-act="dock"]', 'dock'); set('[data-act="dock2"]', 'dock'); set('[data-act="settings"]', 'settings');
  set('[data-act="end"]', 'end'); set('[data-act="captions"]', 'captions');
  set('[data-act="expand"]', 'expand', dock); set('[data-act="end"]', 'end', dock);
  dock.querySelector('.vm-dend').textContent = tr('endShort');
  $('[data-act="notwhat"]').textContent = tr('notWhat');
  $('[data-act="asknow"]').textContent = tr('askNow');
  $('[data-act="tryvoice"]').textContent = tr('tryVoice');
  $('.vm-typebox input').placeholder = tr('typePh');
  renderHealth();
  launch.title = tr('launch'); launch.setAttribute('aria-label', tr('launch'));
  $('[data-act="captions"]').classList.toggle('on', !!prefs.captions);
}

// Orb levels, every frame while voice is on.
let raf = 0;
function animate() {
  const s = agent.snap;
  const lvl = s.state === 'speaking' ? agent.levels.output : (s.state === 'listening' || s.state === 'confirm') ? agent.levels.input : 0;
  orbs().forEach(o => { if (o) o.style.setProperty('--lvl', lvl.toFixed(3)); });
  raf = requestAnimationFrame(animate);
}

// ------------------------------------------------------------------ speech service health
let health = null, healthTimer = 0;
function renderHealth() {
  const el = $('.vm-health');
  const dot = (name, h) => `<span class="vm-hdot ${h ? (h.ok ? 'ok' : 'bad') : ''}" title="${esc(name + ': ' + (h ? (h.ok ? tr('ready') : tr('down') + ' — ' + h.detail) : tr('checking')))}"></span>${esc(name)}`;
  el.innerHTML = dot(tr('speechIn'), health && health.speech_in) + ' ' + dot(tr('speechOut'), health && health.speech_out);
}
async function checkHealth(refresh = false) {
  try {
    const r = await fetch('/api/voice/health' + (refresh ? '?refresh=true' : ''));
    health = r.ok ? await r.json() : null;
  } catch (e) { health = null; }
  renderHealth();
  if (!health) return health;
  agent.speechOutDown = !(health.speech_out && health.speech_out.ok);    // the browser voice speaks instead
  if (agent.active && health.speech_in && !health.speech_in.ok && !agent.snap.sttDown) agent.sttDown();
  return health;
}

// ------------------------------------------------------------------ open / close
function layout(docked) {
  prefs.docked = docked; writePrefs(prefs);
  root.classList.toggle('docked', docked);
  root.classList.toggle('full', !docked);
  document.body.classList.toggle('vm-docked', docked && root.classList.contains('open'));
  syncDockVisibility();
}

function open() {
  // The voice button always brings back the full panel (the orb): voice already on but docked -> expand it in place.
  if (root.classList.contains('open')) { layout(false); return; }
  if (config && config.speech_in === false) {
    root.classList.add('open');
    layout(false);
    agent.set({ state: 'error', error: tr('unavailable') });
    return;
  }
  lastUser = ''; lastReply = '';
  root.classList.add('open');
  layout(false);                                  // never reopens docked, even if it was docked when voice last ended
  cancelAnimationFrame(raf); raf = requestAnimationFrame(animate);
  agent.start();
  checkHealth();
  clearInterval(healthTimer);
  healthTimer = setInterval(() => checkHealth(), 3 * 60 * 1000);
}

function close(stopAgent = true) {
  if (stopAgent) agent.stop();
  clearInterval(healthTimer);
  root.classList.remove('open', 'docked', 'full');
  document.body.classList.remove('vm-docked');
  syncDockVisibility();
  cancelAnimationFrame(raf);
  orbs().forEach(o => { if (o) o.style.setProperty('--lvl', '0'); });
}

// ------------------------------------------------------------------ settings sheet
function openSettings() {
  const back = document.createElement('div');
  back.className = 'vm-sheet-back';
  const seg = (name, opts) => `<div class="vm-seg" role="radiogroup">${opts.map(([v, label]) =>
    `<button type="button" data-k="${name}" data-v="${v}" aria-pressed="${String(prefs[name]) === String(v)}">${esc(label)}</button>`).join('')}</div>`;
  back.innerHTML = `<div class="vm-sheet" role="dialog" aria-modal="true" aria-labelledby="vmSheetT">
    <h3 id="vmSheetT">${esc(tr('sheetTitle'))}</h3><p class="sub">${esc(tr('sheetSub'))}</p>
    <div class="vm-label">${esc(tr('voice'))}</div>${seg('voice', [['server', tr('officer')], ['device', tr('device')]])}
    ${config.speed !== false ? `<div class="vm-label">${esc(tr('speed'))}</div>${seg('speed', VOICE_SPEEDS.map(v => [v, v + '×']))}` : ''}
    <div class="vm-label">${esc(tr('how'))}</div>${seg('mode', [['auto', tr('auto')], ['ptt', tr('ptt')]])}
    <label class="vm-row"><span>${esc(tr('captions'))}</span><input type="checkbox" data-c="captions" ${prefs.captions ? 'checked' : ''}></label>
    <label class="vm-row"><span>${esc(tr('sounds'))}</span><input type="checkbox" data-c="sounds" ${prefs.sounds ? 'checked' : ''}></label>
    <p class="hint">${esc(tr('hint'))}<span class="vm-kbd">Space</span> <span class="vm-kbd">M</span> <span class="vm-kbd">Esc</span></p>
    <div class="vm-sheet-actions"><button type="button" data-done>${esc(tr('done'))}</button></div></div>`;
  document.body.appendChild(back);
  const closeSheet = () => { back.remove(); document.removeEventListener('keydown', onKey, true); };
  const onKey = e => { if (e.key === 'Escape') { e.stopPropagation(); closeSheet(); } };
  document.addEventListener('keydown', onKey, true);
  back.addEventListener('mousedown', e => { if (e.target === back) closeSheet(); });
  back.querySelector('[data-done]').onclick = closeSheet;
  back.querySelectorAll('[data-k]').forEach(b => b.onclick = () => {
    const k = b.dataset.k;
    prefs[k] = k === 'speed' ? Number(b.dataset.v) : b.dataset.v;
    writePrefs(prefs);
    back.querySelectorAll(`[data-k="${k}"]`).forEach(x => x.setAttribute('aria-pressed', String(x === b)));
    if (k === 'mode') agent.setMode();
  });
  back.querySelectorAll('[data-c]').forEach(c => c.onchange = () => {
    prefs[c.dataset.c] = c.checked; writePrefs(prefs); applyLabels(); render(agent.snap);
  });
  back.querySelector('[data-done]').focus();
}

// ------------------------------------------------------------------ events
function act(name, ev) {
  switch (name) {
    case 'dock': case 'dock2': layout(true); break;
    case 'expand': layout(false); break;
    case 'end': close(true); break;
    case 'mute': agent.setMuted(!agent.snap.muted); break;
    case 'settings': openSettings(); break;
    case 'captions': prefs.captions = !prefs.captions; writePrefs(prefs); applyLabels(); render(agent.snap); break;
    case 'retry': agent.stop(); root.classList.add('open'); agent.start(); break;
    case 'notwhat': agent.notWhatISaid(); break;
    case 'asknow': agent.askNow(); break;
    case 'opt0': agent.chooseOption(0); break;
    case 'opt1': agent.chooseOption(1); break;
    case 'neither':
      agent.cancelConfirm();
      if (agent.snap.state === 'speaking') agent.interrupt();
      agent.listen();
      break;
    case 'tryvoice':
      checkHealth(true).then(h => {
        if (h && h.speech_in && h.speech_in.ok) agent.resumeVoice();
        else toast(tr('unavailable'));
      });
      break;
    case 'orb':
      if (agent.snap.ptt) return;                 // hold-to-talk uses pointer down / up below
      if (agent.snap.state === 'speaking') agent.interrupt();
      break;
    default:
  }
  if (ev) ev.stopPropagation();
}
// The type-instead box (panel and dock): typed questions are answered and spoken.
[root, dock].forEach(host => host.addEventListener('submit', e => {
  const form = e.target.closest('.vm-typebox');
  if (!form) return;
  e.preventDefault();
  const input = form.querySelector('input');
  const q = input.value.trim();
  if (q) { input.value = ''; agent.askText(q); }
}));
[root, dock].forEach(host => host.addEventListener('click', e => {
  const b = e.target.closest('[data-act]');
  if (b && host.contains(b)) act(b.dataset.act, e);
}));
const bigOrb = root.querySelector('.vm-orb-big');
bigOrb.addEventListener('pointerdown', () => { if (agent.snap.ptt) agent.pttDown(); });
['pointerup', 'pointerleave', 'pointercancel'].forEach(t => bigOrb.addEventListener(t, () => { if (agent.snap.ptt) agent.pttUp(); }));

launch.addEventListener('click', open);
launch.addEventListener('pointerenter', prefetchVad);
launch.addEventListener('focus', prefetchVad);

const typing = el => el && (el.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName));
document.addEventListener('keydown', e => {
  if (!root.classList.contains('open') || document.querySelector('.vm-sheet-back')) return;
  if (e.key === 'Escape') { e.preventDefault(); close(true); return; }
  if (typing(document.activeElement)) return;
  if (e.key === 'm' || e.key === 'M') { e.preventDefault(); agent.setMuted(!agent.snap.muted); }
  else if (e.code === 'Space') {
    e.preventDefault();
    if (e.repeat) return;
    if (agent.snap.ptt) agent.pttDown(); else if (agent.snap.state === 'speaking') agent.interrupt();
  }
});
document.addEventListener('keyup', e => { if (e.code === 'Space' && root.classList.contains('open') && agent.snap.ptt) agent.pttUp(); });
// A typed question while voice is docked is answered and spoken too.
const box = document.getElementById('box');
box.addEventListener('keydown', e => {
  if (e.key === 'Enter' && root.classList.contains('open') && agent.active && box.value.trim()) {
    e.preventDefault(); e.stopImmediatePropagation();
    const q = box.value; box.value = '';
    agent.askText(q);
  }
}, true);

agent.on(render);
document.querySelectorAll('#langEn, #langKn').forEach(b => b.addEventListener('click', () => setTimeout(() => { applyLabels(); render(agent.snap); }, 0)));
applyLabels();
syncDockVisibility();
window.addEventListener('pagehide', () => agent.stop());
