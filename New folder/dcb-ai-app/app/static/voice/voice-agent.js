/* Voice mode engine: the hands-free loop behind the Cescom voice panel (ported from RDPR). A port of the Meghalaya voice agent's
   voiceAgent.js as integrated into the KPCL voice instance (same flow, same speech-detection settings and assets),
   wired to this app's endpoints:

     listen (Silero VAD in the browser, or a loudness fallback)
       -> POST /api/voice/transcribe   (16 kHz WAV body)
       -> GET  /api/voice/filler       (spoken while the answer is worked out; never added to the chat)
       -> ask(question)                (the chat's own ask() with mode "voice": the answer, chart, table and report
                                        buttons appear in the chat exactly as for a typed question)
       -> POST /api/voice/speak/stream per sentence (next one fetched while this one plays; the browser's own voice
                                        if the server voice is unavailable)
       -> listen again

   Barge-in: while a reply plays, the speech detector keeps running; real speech stops the reply and becomes the
   next question. Hold-to-talk: no automatic turn detection; hold the orb or Space, release to send.
   Framework-free: the UI subscribes with on() and reads `levels` every frame. Every async step checks the session
   id, so stop() mid-await can never revive a finished session. */
import { StreamClip, ttsBody } from './pcm-stream.js';
import { encodeWavBytes, isEchoOf, isRealTranscript, levelFromFrame, speechTextFor, splitSpokenSentences } from './voice-text.js';

const SPEAK_TIMEOUT_MS = 20000;
const ASR_TIMEOUT_MS = 20000;
const HEARD_WINDOW_MS = 2000;            // "I heard: ..." stays this long with "That's not what I said" before asking
const STILL_WORKING_MS = 8000;           // say "Still working on it." once if the answer takes longer
const ANSWER_TIMEOUT_MS = 60000;         // stop waiting (the answer still lands on screen when ready)
const IDLE_OFF_MS = 3 * 60 * 1000;       // microphone off after this long without a question
const MAX_RECORD_MS = 30000;             // one question: at most 30 s of recording
const VAD_RATE = 16000;
const ENERGY_SILENCE_MS = 900;           // fallback: quiet after speech that ends a turn
const ENERGY_FLOOR = 0.02;               // fallback: loudness that counts as speech
const ENERGY_MIN_SPEECH_MS = 500;        // fallback: shorter bursts are a pop, a cough or a key
const BARGE_MIN_SAMPLES = 0.7 * 16000;   // talking over a reply: shorter than 0.7 s is a cough or a word to a colleague
const BARGE_DUCK = 0.25;                 // the reply's volume while we check whether someone is really asking something
const SETTLE_MS = 300;                   // after a reply, let the room go quiet before listening (no echo tail)
const PTT_MIN_MS = 350;                  // hold-to-talk: a shorter press is a tap, not a question
let streamRoute = true;                  // false once the server says it has no streamed route

// Silero VAD (the Meghalaya agent's tuned values, same vendored files in /static/vendor/vad).
const VAD_BASE = '/static/vendor/vad/';
const VAD_OPTIONS = {
  model: 'legacy',
  positiveSpeechThreshold: 0.6,          // 0.3 let distant office chatter through as questions; 0.5 still woke on noise
  negativeSpeechThreshold: 0.35,
  redemptionMs: 800,
  minSpeechMs: 600,                      // a short background phrase is not a question
  preSpeechPadMs: 800,                   // keeps the first words of a barge-in
};

let vadScripts = null;
const dbg = (...a) => { if (window.__voiceDebug) console.log('[voice]', Math.round(performance.now()), ...a); };
let vadBroken = false;

function loadScript(src) {
  return new Promise((resolve, reject) => {
    const s = document.createElement('script');
    s.src = src;
    s.onload = resolve;
    s.onerror = () => reject(new Error('failed to load ' + src));
    document.head.appendChild(s);
  });
}

async function wasmAllowed() {
  if (typeof WebAssembly === 'undefined') return false;
  try { await WebAssembly.compile(new Uint8Array([0, 97, 115, 109, 1, 0, 0, 0])); return true; } catch (e) { return false; }
}

function loadVad() {
  if (!vadScripts) {
    vadScripts = loadScript(VAD_BASE + 'ort.wasm.min.js')
      .then(() => loadScript(VAD_BASE + 'bundle.min.js'))
      .then(() => {
        if (!window.ort || !window.vad || !window.vad.MicVAD) throw new Error('VAD globals missing');
        return window.vad;
      });
    vadScripts.catch(() => { vadScripts = null; });
  }
  return vadScripts;
}

/** Start fetching the speech detector early (hover / focus on the voice button). */
export function prefetchVad() {
  if (vadBroken || vadScripts || typeof window === 'undefined') return;
  wasmAllowed().then(ok => { if (ok && !vadScripts) loadVad().catch(() => {}); });
}

export function micErrorMessage(e) {
  switch (e && e.name) {
    case 'NotAllowedError':
    case 'SecurityError':
      return 'Microphone access was blocked. Allow it from the icon at the left of the address bar, then try again.';
    case 'NotFoundError':
    case 'OverconstrainedError':
      return 'No microphone was found. Connect one, then try again.';
    case 'NotReadableError':
      return 'The microphone is being used by another application. Close it and try again.';
    default:
      return 'Could not start the microphone' + (e && e.name ? ` (${e.name})` : '') + '. Please try again.';
  }
}

/* Soft UI sounds: 'start' when the microphone opens, 'send' when a question is captured. Synthesised, no files. */
function earcon(ctx, kind) {
  if (!ctx || ctx.state === 'closed') return;
  const notes = kind === 'start' ? [[523.25, 0], [783.99, 0.09]] : [[659.25, 0]];
  const t0 = ctx.currentTime + 0.01;
  notes.forEach(([f, at]) => {
    const osc = ctx.createOscillator();
    const g = ctx.createGain();
    osc.type = 'sine';
    osc.frequency.value = f;
    g.gain.setValueAtTime(0, t0 + at);
    g.gain.linearRampToValueAtTime(0.05, t0 + at + 0.015);
    g.gain.exponentialRampToValueAtTime(0.0001, t0 + at + 0.16);
    osc.connect(g);
    g.connect(ctx.destination);
    osc.start(t0 + at);
    osc.stop(t0 + at + 0.18);
    osc.onended = () => { try { osc.disconnect(); g.disconnect(); } catch (e) { /* already */ } };
  });
}

// On-device voices only, so the answer text never leaves the officer's machine on the fallback path.
function localVoice() {
  const synth = window.speechSynthesis;
  const pick = () => {
    const local = synth.getVoices().filter(v => v.localService);
    return local.find(v => v.lang === 'en-IN') || local.find(v => (v.lang || '').startsWith('en')) || null;
  };
  if (synth.getVoices().length) return Promise.resolve(pick());
  return new Promise(resolve => {
    const done = () => resolve(pick());
    synth.addEventListener('voiceschanged', done, { once: true });
    setTimeout(done, 1500);
  });
}

const utterances = new Set();          // held so the browser can't garbage-collect them mid-speech

export class VoiceAgent {
  /**
   * @param o.ask            (question) => Promise<{spoken_answer, answer} | null>
   * @param o.getDeviceVoice () => boolean, skip the server voice
   * @param o.getSpeed       () => number, speaking speed (1 = normal)
   * @param o.getMode        () => 'auto' | 'ptt', hands-free or hold-to-talk
   * @param o.getSounds      () => boolean
   */
  constructor(o) {
    this.o = o;
    this.sid = 0;
    this.active = false;
    this.session = 's' + Math.random().toString(36).slice(2, 10);
    this.snap = { state: 'off', muted: false, error: null, userText: '', replyText: '', engine: null, ptt: false, holding: false,
                  heard: '', confirm: null, sttDown: false, notice: '' };
    this.pendingConfirm = null;          // { options, sid } while a "Did you mean ...?" waits for an answer
    this.heardCtl = null;                // the open "I heard" window: { resolve }
    this.lastActivity = 0;
    this.idleTimer = 0;
    this.asrFailures = 0;
    this.speechOutDown = false;
    this.levels = { input: 0, output: 0 };
    this.subs = new Set();
    this.ctx = null; this.stream = null; this.inSrc = null; this.inAnalyser = null; this.outGain = null; this.outAnalyser = null;
    this.vad = null; this.vadMode = 'off'; this.carryover = false;
    this.speakingText = ''; this.lastSaid = null; this.spokeUntil = 0; this.noiseFloor = 0;
    this.bargeTimer = 0; this.settleTimer = 0;
    this.recorder = null; this.energy = null; this.raf = 0;
    this.pttRec = null; this.pttStartedAt = 0;
    this.speakCtrl = null; this.clip = null; this.filler = null; this.browserSpeaking = false;
    this.turnSeq = 0;
    this.wakeLock = null;
    this.tick = this.tick.bind(this);
    // A hidden tab turns the microphone off (nobody is watching what it hears).
    this.onVisible = () => {
      if (document.visibilityState === 'hidden' && this.active) this.autoOff('Voice was turned off because this tab was hidden. Press the voice button to start again.');
      else this.keepAwake();
    };
  }

  /* Microphone off on its own (idle, hidden tab): stop and leave a notice for the page to show. */
  autoOff(message) {
    this.stop();
    this.set({ notice: message });
  }

  activity() { this.lastActivity = performance.now(); }

  startIdleWatch(sid) {
    clearInterval(this.idleTimer);
    this.activity();
    this.idleTimer = setInterval(() => {
      if (!this.live(sid)) { clearInterval(this.idleTimer); return; }
      const quiet = ['listening', 'typing'].includes(this.snap.state) && !this.snap.holding;
      if (quiet && performance.now() - this.lastActivity > IDLE_OFF_MS) {
        this.autoOff('Voice was turned off after 3 minutes without a question. Press the voice button to start again.');
      }
    }, 15000);
  }

  /* Speech-to-text is down: release the microphone and offer typing instead (answers are still spoken). */
  sttDown(message) {
    if (!this.active) return;
    this.vadPause();
    this.dropRecorder();
    this.dropPtt();
    if (this.stream) this.stream.getAudioTracks().forEach(t => { t.enabled = false; });
    this.set({ state: 'typing', sttDown: true, error: message || "Speech recognition isn't available right now. Please type your question instead." });
  }

  /* "Try voice again": back to listening (the page checks the health first). */
  resumeVoice() {
    if (!this.active) return;
    this.asrFailures = 0;
    if (this.stream) this.stream.getAudioTracks().forEach(t => { t.enabled = !this.snap.muted; });
    this.set({ sttDown: false, error: null });
    this.listen();
  }

  // ── "I heard: ..." window ───────────────────────────────────────────────────
  /* Shows the transcript for HEARD_WINDOW_MS; resolves true to ask, false if the officer said it was wrong. */
  heardWindow(text, sid) {
    return new Promise(resolve => {
      const done = ok => { if (this.heardCtl) { clearTimeout(this.heardCtl.timer); this.heardCtl = null; resolve(ok); } };
      this.heardCtl = { resolve: done, timer: setTimeout(() => done(true), HEARD_WINDOW_MS) };
      this.set({ state: 'heard', heard: text, userText: text, replyText: '', heardUntil: Date.now() + HEARD_WINDOW_MS });
      if (!this.live(sid)) done(false);
    });
  }
  notWhatISaid() { if (this.heardCtl) this.heardCtl.resolve(false); }
  askNow() { if (this.heardCtl) this.heardCtl.resolve(true); }

  // ── "Did you mean ...?" ─────────────────────────────────────────────────────
  async askConfirm(confirm, sid) {
    this.pendingConfirm = { options: confirm.options, sid };
    this.set({ state: 'confirm', confirm, heard: '', replyText: '' });
    await this.speak(confirm.prompt, sid);
    if (this.live(sid) && this.pendingConfirm) { this.set({ state: 'confirm' }); this.listen(sid, true); }
  }

  /* An option tapped on screen. */
  chooseOption(i) {
    const p = this.pendingConfirm;
    if (!p || !p.options[i] || !this.live(p.sid)) return;
    this.pendingConfirm = null;
    this.set({ confirm: null });
    if (this.snap.state === 'speaking') this.interrupt();
    this.vadPause();
    this.dropRecorder();
    this.answer(p.options[i].question, p.sid, ++this.turnSeq);
  }

  cancelConfirm() {
    this.pendingConfirm = null;
    this.set({ confirm: null });
  }

  /* A spoken reply to "Did you mean ...?": an option's name or year, "first" / "second", "yes" (= first), or "no".
     Returns the option index, -1 for "no", or null when it doesn't answer the question (then it is a new question). */
  resolveConfirm(text) {
    const p = this.pendingConfirm;
    const t = ' ' + String(text || '').toLowerCase().replace(/[^a-z0-9-]+/g, ' ') + ' ';
    if (!p) return null;
    const hits = p.options.map(o => {
      const words = o.label.toLowerCase().match(/[a-z]{4,}|\d{4}-\d{2}/g) || [];
      return words.filter(w => !['sub', 'division', 'subdivision', 'section', 'district', 'tariff', 'category'].includes(w)
                               && t.includes(' ' + w + ' ')).length;
    });
    if (hits[0] !== hits[1]) return hits[0] > hits[1] ? 0 : 1;
    if (/ (first|one|former|yes|yeah|correct|right) /.test(t)) return 0;
    if (/ (second|two|latter|other) /.test(t)) return 1;
    if (/ (no|neither|none|cancel|wrong) /.test(t)) return -1;
    return null;
  }

  opt(name, fallback) { const f = this.o[name]; return f ? f() : fallback; }
  isPtt() { return this.opt('getMode', 'auto') === 'ptt'; }
  sound(kind) { if (this.opt('getSounds', true)) earcon(this.ctx, kind); }
  on(fn) { this.subs.add(fn); fn(this.snap); return () => this.subs.delete(fn); }
  set(patch) { this.snap = { ...this.snap, ...patch }; this.subs.forEach(fn => fn(this.snap)); }
  live(sid) { return this.active && sid === this.sid; }

  // ── lifecycle ──────────────────────────────────────────────────────────────
  async start() {
    if (this.active) return;
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia || !AC) {
      this.set({ state: 'error', error: window.isSecureContext === false
        ? 'Voice needs a secure address. Open this page on this computer (http://127.0.0.1) or over HTTPS.'
        : 'Voice mode is not supported in this browser. Please use Chrome or Edge.' });
      return;
    }
    const sid = ++this.sid;
    this.active = true;
    this.pendingConfirm = null;
    this.asrFailures = 0;
    this.set({ state: 'starting', error: null, userText: '', replyText: '', muted: false, holding: false, ptt: this.isPtt(),
               heard: '', confirm: null, sttDown: false, notice: '' });
    this.ctx = new AC();                         // before the first await, while the click still counts as a gesture
    this.ctx.resume().catch(() => {});
    let stream;
    try {
      // The browser asks Allow / Block here the first time. echoCancellation: the agent must not hear itself.
      stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
    } catch (e) {
      if (sid === this.sid) { this.teardown(); this.set({ state: 'error', error: micErrorMessage(e) }); }
      return;
    }
    if (!this.live(sid)) { stream.getTracks().forEach(t => t.stop()); return; }
    this.stream = stream;
    stream.getAudioTracks().forEach(t => {
      t.onended = () => {
        if (!this.live(sid) || this.stream !== stream) return;
        this.teardown();
        this.set({ state: 'error', error: 'The microphone was disconnected. Connect it again, then try again.' });
      };
    });
    this.keepAwake();
    document.addEventListener('visibilitychange', this.onVisible);
    const ctx = this.ctx;
    this.inSrc = ctx.createMediaStreamSource(stream);
    this.inAnalyser = ctx.createAnalyser();
    this.inAnalyser.fftSize = 1024;
    this.inSrc.connect(this.inAnalyser);
    this.outGain = ctx.createGain();
    this.outAnalyser = ctx.createAnalyser();
    this.outAnalyser.fftSize = 1024;
    this.outGain.connect(this.outAnalyser);
    this.outAnalyser.connect(ctx.destination);
    this.raf = requestAnimationFrame(this.tick);
    this.sound('start');
    this.startIdleWatch(sid);
    // Listen only once the speech detector is ready (about a second; the files are local). Listening earlier on the
    // loudness detector took the start chime, a key press or a fan as a "question" (the speech model then wrote a stray word).
    const inst = await this.createVad(stream, sid);
    if (!this.live(sid) || this.stream !== stream) { if (inst) inst.destroy().catch(() => {}); return; }
    if (inst) this.vad = inst;
    this.set({ engine: inst ? 'vad' : 'energy' });
    this.listen(sid);
  }

  keepAwake() {
    if (!this.active || this.wakeLock || !navigator.wakeLock || document.visibilityState !== 'visible') return;
    navigator.wakeLock.request('screen').then(lock => {
      if (!this.active) { lock.release().catch(() => {}); return; }
      this.wakeLock = lock;
      lock.addEventListener('release', () => { if (this.wakeLock === lock) this.wakeLock = null; });
    }).catch(() => {});
  }

  stop() {
    if (!this.active && this.snap.state === 'off') return;
    this.sid++;
    this.teardown();
    this.set({ state: 'off', error: null });
  }

  teardown() {
    this.active = false;
    this.turnSeq++;
    clearInterval(this.idleTimer);
    clearTimeout(this.recCap);
    clearTimeout(this.bargeTimer);
    clearTimeout(this.settleTimer);
    if (this.heardCtl) this.heardCtl.resolve(false);
    this.pendingConfirm = null;
    this.lastSaid = null;
    this.vadMode = 'off';
    this.carryover = false;
    this.energy = null;
    if (this.vad) { this.vad.destroy().catch(() => {}); this.vad = null; }
    this.dropRecorder();
    this.dropPtt();
    document.removeEventListener('visibilitychange', this.onVisible);
    if (this.wakeLock) { this.wakeLock.release().catch(() => {}); this.wakeLock = null; }
    if (this.speakCtrl) this.speakCtrl.abort();
    if (this.clip) this.clip.stop();
    this.stopFiller();
    if (window.speechSynthesis) window.speechSynthesis.cancel();
    this.browserSpeaking = false;
    cancelAnimationFrame(this.raf);
    try { if (this.inSrc) this.inSrc.disconnect(); } catch (e) { /* already */ }
    if (this.stream) this.stream.getTracks().forEach(t => t.stop());
    this.stream = null; this.inSrc = null; this.inAnalyser = null; this.outGain = null; this.outAnalyser = null;
    if (this.ctx && this.ctx.state !== 'closed') this.ctx.close().catch(() => {});
    this.ctx = null;
    this.levels.input = 0; this.levels.output = 0;
  }

  setMuted(muted) {
    this.activity();
    this.set({ muted });
    if (this.stream) this.stream.getAudioTracks().forEach(t => { t.enabled = !muted; });
  }

  /** Tap while speaking: end the reply and listen. */
  interrupt() {
    if (this.snap.state !== 'speaking') return;
    if (this.speakCtrl) this.speakCtrl.abort();
    if (window.speechSynthesis) window.speechSynthesis.cancel();
    if (this.clip) this.clip.stop();
  }

  setMode() {
    const ptt = this.isPtt();
    if (ptt === this.snap.ptt) return;
    this.set({ ptt, holding: false });
    if (this.active && this.snap.state === 'listening') {
      this.vadPause();
      this.dropRecorder();
      this.dropPtt();
      this.listen();
    }
  }

  // ── hold-to-talk ───────────────────────────────────────────────────────────
  pttDown() {
    if (!this.active || !this.isPtt() || this.pttRec || !this.stream) return;
    const s = this.snap.state;
    if (s === 'speaking') this.interrupt();
    else if (s !== 'listening') return;
    if (this.snap.muted) this.setMuted(false);
    const rec = this.makeRecorder();
    if (!rec) return;
    this.pttRec = rec;
    this.pttStartedAt = performance.now();
    this.activity();
    rec.start();
    clearTimeout(this.recCap);
    this.recCap = setTimeout(() => this.pttUp(), MAX_RECORD_MS);    // one question is at most 30 s
    this.set({ state: 'listening', holding: true, replyText: '' });
  }

  pttUp() {
    const rec = this.pttRec;
    if (!rec) return;
    clearTimeout(this.recCap);
    const sid = this.sid;
    const short = performance.now() - this.pttStartedAt < PTT_MIN_MS;
    this.pttRec = null;
    this.set({ holding: false });
    rec.onstop = () => {
      if (!this.live(sid) || short) return;
      const type = (rec.mimeType || 'audio/webm').split(';')[0];
      this.onEnergyTurnEnd(new Blob(rec.chunks, { type }), sid);
    };
    try { rec.stop(); } catch (e) { /* already */ }
  }

  dropPtt() {
    const rec = this.pttRec;
    this.pttRec = null;
    if (rec) { rec.onstop = null; try { rec.stop(); } catch (e) { /* already */ } }
    if (this.snap.holding) this.set({ holding: false });
  }

  dropRecorder() {
    const rec = this.recorder;
    this.recorder = null;
    this.energy = null;
    if (rec && rec.state !== 'inactive') { try { rec.stop(); } catch (e) { /* already */ } }
  }

  makeRecorder() {
    const prefer = ['audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus', 'audio/mp4'];
    const picked = window.MediaRecorder && MediaRecorder.isTypeSupported ? prefer.find(t => MediaRecorder.isTypeSupported(t)) : null;
    let rec;
    try { rec = picked ? new MediaRecorder(this.stream, { mimeType: picked }) : new MediaRecorder(this.stream); } catch (e) {
      this.teardown();
      this.set({ state: 'error', error: 'This browser cannot record audio for voice mode.' });
      return null;
    }
    rec.chunks = [];
    rec.ondataavailable = ev => { if (ev.data.size > 0) rec.chunks.push(ev.data); };
    return rec;
  }

  /** A question typed while voice mode is on: answered and spoken like a spoken one. */
  askText(text) {
    const q = String(text || '').trim();
    if (!this.active || !q || this.snap.state === 'starting') return;
    if (this.snap.state === 'speaking') this.interrupt();
    if (this.heardCtl) this.heardCtl.resolve(false);
    this.cancelConfirm();
    this.vadPause();
    this.carryover = false;
    this.dropRecorder();
    this.dropPtt();
    this.answer(q, this.sid, ++this.turnSeq);
  }

  // ── meters (one animation loop: orb levels + the loudness fallback) ──────────
  tick(now) {
    if (!this.active) return;
    let input = 0, output = 0, rms = 0;
    if (this.inAnalyser) {
      const f = new Float32Array(this.inAnalyser.fftSize);
      this.inAnalyser.getFloatTimeDomainData(f);
      ({ rms, level: input } = levelFromFrame(f));
    }
    if (this.outAnalyser && this.clip) {
      const f = new Float32Array(this.outAnalyser.fftSize);
      this.outAnalyser.getFloatTimeDomainData(f);
      output = Math.min(1, levelFromFrame(f).level * 1.6);
    } else if (this.browserSpeaking) {
      output = 0.35 + 0.25 * Math.sin(now / 140) * Math.sin(now / 390);   // the OS voice can't be metered
    }
    if (this.snap.muted || (this.snap.ptt && !this.snap.holding)) input = 0;
    const ease = (prev, v) => prev + (v - prev) * (v > prev ? 0.5 : 0.15);
    this.levels.input = ease(this.levels.input, input);
    this.levels.output = ease(this.levels.output, output);
    const e = this.energy;
    if (e && !e.spoken) this.noiseFloor = this.noiseFloor ? this.noiseFloor * 0.97 + rms * 0.03 : rms;   // the room
    if (e && !this.snap.muted) {
      if (rms >= Math.max(ENERGY_FLOOR, 3 * (this.noiseFloor || 0))) {
        if (!e.spoken) { e.spoken = true; e.startedAt = now; }
        e.quietSince = null;
      } else if (e.mode === 'turn' && e.spoken && now - e.startedAt < ENERGY_MIN_SPEECH_MS) {
        e.spoken = false;                                         // a click or a cough: not the start of a question
      } else if (e.mode === 'turn' && e.spoken && now - e.startedAt >= ENERGY_MIN_SPEECH_MS) {
        if (e.quietSince === null) e.quietSince = now;
        else if (now - e.quietSince >= ENERGY_SILENCE_MS) { this.energy = null; e.fire(); }
      }
    }
    this.raf = requestAnimationFrame(this.tick);
  }

  // ── Silero VAD ─────────────────────────────────────────────────────────────
  async createVad(stream, sid) {
    if (vadBroken) return null;
    let inst = null;
    try {
      if (!window.AudioWorkletNode) throw new Error('AudioWorklet not supported');
      if (!(await wasmAllowed())) throw new Error('WebAssembly blocked');
      const lib = await loadVad();
      window.ort.env.wasm.numThreads = 1;      // no cross-origin isolation, so no threads
      window.ort.env.wasm.proxy = false;       // no blob: worker
      inst = await lib.MicVAD.new({
        ...VAD_OPTIONS,
        baseAssetPath: VAD_BASE,
        onnxWASMBasePath: VAD_BASE,
        startOnLoad: false,
        getStream: async () => stream,         // one echo-cancelled stream for the whole session
        pauseStream: async () => {},
        resumeStream: async () => stream,
        onSpeechEnd: audio => { dbg('speech end', this.vadMode, (audio.length / VAD_RATE).toFixed(2) + 's'); this.onVadSpeechEnd(inst, audio); },
        onSpeechRealStart: () => { dbg('speech start', this.vadMode); this.activity(); this.onVadSpeechStart(inst); },
        onVADMisfire: () => { dbg('misfire', this.vadMode); this.carryover = false; if (this.vadMode === 'bargein-hold') this.resumeAfterBarge(); },
      });
      await inst.start();
      await inst.pause();
      return inst;
    } catch (e) {
      if (this.live(sid) && this.stream === stream) vadBroken = true;
      console.warn('voice mode: speech detector unavailable, using loudness detection -', e && e.message ? e.message : e);
      if (inst) inst.destroy().catch(() => {});
      return null;
    }
  }

  vadStart(mode) {
    if (!this.vad) return;
    dbg('vad start', mode);
    this.vadMode = mode;
    this.vad.start().catch(e => console.warn('voice mode: VAD start failed', e));
  }

  vadPause() {
    dbg('vad pause', this.vadMode);
    this.vadMode = 'off';
    if (this.vad) this.vad.pause().catch(() => {});
  }

  onVadSpeechEnd(inst, audio) {
    if (!this.active || inst !== this.vad) return;
    if (this.vadMode === 'bargein-hold') { this.checkBargeIn(audio); return; }
    if (this.vadMode !== 'listen') return;
    this.vadPause();
    this.activity();
    if (audio.length > (MAX_RECORD_MS / 1000) * VAD_RATE) {           // one question is at most 30 s
      const sid = this.sid;
      this.speak('That was longer than thirty seconds. Please ask one short question at a time.', sid)
        .then(() => { if (this.live(sid)) this.listen(sid, !!this.pendingConfirm); });
      return;
    }
    const bargeIn = this.carryover;
    this.carryover = false;
    // when the words began: the detector ends a turn after 0.8 s of quiet, and the clip includes 0.8 s before speech
    const startedAt = performance.now() - (audio.length / VAD_RATE) * 1000 + VAD_OPTIONS.preSpeechPadMs;
    this.processTurn(new Blob([encodeWavBytes(audio, 16000)], { type: 'audio/wav' }), { bargeIn, startedAt });
  }

  /* Someone may be talking over the reply. Don't stop yet: the microphone also hears the reply itself (and the room),
     so turn the reply down and decide when they stop talking (checkBargeIn). */
  onVadSpeechStart(inst) {
    if (!this.active || inst !== this.vad || this.vadMode !== 'bargein') return;
    this.vadMode = 'bargein-hold';
    this.duck(true);
    clearTimeout(this.bargeTimer);
    this.bargeTimer = setTimeout(() => this.duck(false), 6000);  // never leave a reply turned down for long
  }

  duck(on) {
    const g = this.outGain;
    if (!g || !this.ctx) return;
    if (!on) clearTimeout(this.bargeTimer);
    g.gain.cancelScheduledValues(this.ctx.currentTime);
    g.gain.setTargetAtTime(on ? BARGE_DUCK : 1, this.ctx.currentTime, on ? 0.04 : 0.08);
  }

  resumeAfterBarge() {
    this.duck(false);
    if (this.vadMode === 'bargein-hold' || this.vadMode === 'bargein-check') {
      this.vadMode = ['speaking', 'thinking'].includes(this.snap.state) ? 'bargein' : 'off';
    }
  }

  /* The words said over the reply: a real question (or "stop") ends the reply and is answered; the reply's own echo,
     a cough, or a word to a colleague is ignored and the reply carries on at full volume. */
  async checkBargeIn(audio) {
    const sid = this.sid, turn = this.turnSeq;
    if (audio.length < BARGE_MIN_SAMPLES) { this.resumeAfterBarge(); return; }
    this.vadMode = 'bargein-check';
    const replyText = [this.speakingText, this.fillerText, this.lastSaid && this.lastSaid.text].filter(Boolean).join(' . ');
    const { status, data } = await this.transcribe(new Blob([encodeWavBytes(audio, VAD_RATE)], { type: 'audio/wav' }), true);
    if (!this.live(sid) || turn !== this.turnSeq) return;
    const text = status === 200 && data && !data.rejected ? String(data.text || '').trim() : '';
    const stopWord = /^\s*(stop|wait|cancel|enough|ok(ay)?,? stop|that's enough)\b/i.test(text);
    const tooShort = text.split(/\s+/).filter(Boolean).length < 2;
    if (!stopWord && (tooShort || !isRealTranscript(text) || isEchoOf(text, replyText))) {
      if (this.vadMode === 'bargein-check' || this.vadMode === 'bargein-hold') this.resumeAfterBarge();
      else this.duck(false);
      return;
    }
    const myTurn = ++this.turnSeq;                               // the reply's own "listen again" stands down
    this.duck(false);
    this.vadPause();
    this.stopFiller();
    if (this.snap.state === 'speaking') this.interrupt();
    if (stopWord && text.split(/\s+/).length <= 3) { this.listen(sid); return; }
    await this.handleTranscript(status, data, sid, myTurn);
  }

  // ── listening ──────────────────────────────────────────────────────────────
  listen(sid = this.sid, keepConfirm = false) {
    if (!this.live(sid)) return;
    if (this.snap.sttDown) { this.set({ state: 'typing' }); return; }
    this.set({ state: keepConfirm && this.pendingConfirm ? 'confirm' : 'listening', ptt: this.isPtt() });
    if (this.isPtt()) { this.vadPause(); return; }
    if (this.vad) {
      if (this.vadMode === 'listen') return;                     // still hearing words said over the reply
      const wait = Math.max(0, (this.spokeUntil || 0) + SETTLE_MS - performance.now());
      clearTimeout(this.settleTimer);
      if (!wait) { this.vadStart('listen'); return; }
      this.vadPause();
      this.settleTimer = setTimeout(() => {
        if (this.live(sid) && this.vadMode === 'off' && ['listening', 'confirm'].includes(this.snap.state)) this.vadStart('listen');
      }, wait);
      return;
    }
    if (this.recorder && this.recorder.state !== 'inactive') return;
    const rec = this.makeRecorder();
    if (!rec) return;
    rec.onstop = () => {
      if (!this.live(sid) || this.recorder !== rec) return;
      this.recorder = null;
      const type = (rec.mimeType || 'audio/webm').split(';')[0];
      this.onEnergyTurnEnd(new Blob(rec.chunks, { type }), sid);
    };
    this.recorder = rec;
    rec.start();
    clearTimeout(this.recCap);
    this.recCap = setTimeout(() => { if (this.recorder === rec && rec.state !== 'inactive') rec.stop(); }, MAX_RECORD_MS);
    this.energy = { mode: 'turn', spoken: false, startedAt: 0, quietSince: null,
                    fire: () => { if (rec.state !== 'inactive') rec.stop(); } };
  }

  async onEnergyTurnEnd(blob, sid) {
    if (!blob || blob.size < 1200) { this.listen(sid); return; }
    this.set({ state: 'thinking' });
    let wav = blob;
    try { wav = await this.toWav(blob); } catch (e) { console.warn('voice mode: WAV conversion failed', e); }
    if (this.live(sid)) this.processTurn(wav, {});
  }

  /* Recorder output -> 16 kHz mono WAV (the speech model doesn't take webm/opus). */
  async toWav(blob) {
    const decoded = await this.ctx.decodeAudioData(await blob.arrayBuffer());
    const chans = decoded.numberOfChannels;
    const mono = new Float32Array(decoded.length);
    for (let c = 0; c < chans; c++) {
      const d = decoded.getChannelData(c);
      for (let i = 0; i < d.length; i++) mono[i] += d[i] / chans;
    }
    const ratio = decoded.sampleRate / 16000;
    const n = Math.max(1, Math.floor(mono.length / ratio));
    const pcm = new Float32Array(n);
    for (let i = 0; i < n; i++) {
      const pos = i * ratio, i0 = Math.floor(pos), frac = pos - i0;
      const a = mono[i0] || 0, b = mono[i0 + 1] !== undefined ? mono[i0 + 1] : a;
      pcm[i] = a + (b - a) * frac;
    }
    return new Blob([encodeWavBytes(pcm, 16000)], { type: 'audio/wav' });
  }

  async processTurn(wav, { bargeIn, startedAt } = {}) {
    const sid = this.sid;
    if (!this.live(sid)) return;
    const turn = ++this.turnSeq;
    this.activity();
    this.set({ state: 'thinking', replyText: '', heard: '' });
    const { status, data } = await this.transcribe(wav, false, bargeIn);
    if (!this.live(sid) || turn !== this.turnSeq) return;
    await this.handleTranscript(status, data, sid, turn, { bargeIn, startedAt });
  }

  /* One recording -> {status, data}. status 0 = no answer from the server (network / timeout). */
  async transcribe(wav, overReply = false, bargeIn = false) {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), ASR_TIMEOUT_MS);
    const expect = overReply ? 'bargein' : this.pendingConfirm ? 'confirm' : '';
    try {
      const res = await fetch('/api/voice/transcribe?language=en&session_id=' + encodeURIComponent(this.session)
                              + (bargeIn ? '&barge_in=1' : '') + (expect ? '&expect=' + expect : ''),
                              { method: 'POST', body: wav, headers: { 'Content-Type': 'audio/wav' }, signal: ctrl.signal });
      return { status: res.status, data: await res.json().catch(() => null) };
    } catch (e) {
      console.warn('voice mode: transcription failed', e);
      return { status: 0, data: null };
    } finally { clearTimeout(timer); }
  }

  // ── one turn: transcript -> checks -> (confirm | "I heard") -> filler -> ask -> speak ──────────────
  async handleTranscript(status, data, sid, turn, { bargeIn = false, startedAt = performance.now() } = {}) {
    if (!this.live(sid)) return;
    const listenAgain = () => { if (this.live(sid) && turn === this.turnSeq) this.listen(sid, !!this.pendingConfirm); };
    if (status === 503 || status === 0 || !data) {
      // Twice in a row, or the service says it's down: offer typing instead (the visible fallback).
      if (++this.asrFailures >= 2 || status === 503) {
        await this.speak("I can't hear questions right now. Please type your question instead.", sid);
        this.sttDown();
      } else {
        await this.speak("Sorry, I didn't catch that. Please say it again.", sid);
        listenAgain();
      }
      return;
    }
    this.asrFailures = 0;
    if (status !== 200) {                                       // 413 / 400
      await this.speak((data && data.detail) || 'Please ask one short question at a time.', sid);
      listenAgain();
      return;
    }
    if (data.rejected) {                                        // too_long / language: say why; noise: nothing
      if (data.rejected === 'too_short' && data.heard) this.notify(`I only heard “${data.heard.trim().slice(0, 40)}”. Please ask your full question.`);
      if (data.message) await this.speak(data.message, sid);
      listenAgain();
      return;
    }
    const text = String(data.text || '').trim();
    if (!isRealTranscript(text)) { listenAgain(); return; }
    // The last reply heard back through the speakers (its words, in order), begun while it played or just after:
    // not a question.
    if (this.lastSaid && (bargeIn || startedAt - this.lastSaid.at < 3000) && isEchoOf(text, this.lastSaid.text)) {
      listenAgain();
      return;
    }
    this.sound('send');
    // An answer to "Did you mean ...?"
    if (this.pendingConfirm) {
      const pick = this.resolveConfirm(text);
      const p = this.pendingConfirm;
      if (pick !== null) {
        this.pendingConfirm = null;
        this.set({ confirm: null });
        if (pick < 0) {
          await this.speak('Okay. Please ask your question again.', sid);
          listenAgain();
          return;
        }
        await this.answer(p.options[pick].question, sid, turn);
        return;
      }
      this.cancelConfirm();                                     // not an answer to it: a new question
    }
    if (data.confirm && data.confirm.options && data.confirm.options.length >= 2) {
      await this.askConfirm(data.confirm, sid);
      return;
    }
    const ok = await this.heardWindow(text, sid);
    if (!this.live(sid) || turn !== this.turnSeq) return;
    if (!ok) {
      await this.speak('Sorry about that. Please say it again.', sid);
      listenAgain();
      return;
    }
    await this.answer(text, sid, turn);
  }

  notify(message) {
    this.set({ notice: '' });
    this.set({ notice: message });
  }

  async answer(question, sid, turn) {
    const listenAgain = () => { if (this.live(sid) && turn === this.turnSeq) this.listen(sid); };
    this.activity();
    this.set({ state: 'thinking', replyText: '', userText: question, heard: '' });
    this.playFiller(sid, turn);
    // A new question may come at any moment from here on (while looking up, or over the reply): same check as barge-in.
    if (!this.isPtt() && this.vad && this.vadMode === 'off') this.vadStart('bargein');
    let saidStill = false;
    const still = setTimeout(() => {                            // a long wait gets one "Still working on it."
      if (this.live(sid) && turn === this.turnSeq && this.snap.state === 'thinking' && !this.filler) {
        saidStill = true;
        this.sayAside('Still working on it.', sid, turn);
      }
    }, STILL_WORKING_MS);
    let response = null, timedOut = false;
    try {
      await this.chatIdle(sid, turn);
      if (!this.live(sid) || turn !== this.turnSeq) return;
      response = await Promise.race([
        this.o.ask(question),
        new Promise(r => setTimeout(() => { timedOut = true; r(null); }, ANSWER_TIMEOUT_MS)),
      ]);
    } catch (e) { response = null; }
    clearTimeout(still);
    if (!this.live(sid) || turn !== this.turnSeq) return;
    if (!response) {
      await this.speak(timedOut ? 'This is taking longer than usual. The answer will appear on your screen when it is ready.'
                                : "Sorry, I couldn't get that answer just now. Please ask again, or type your question.", sid);
      listenAgain();
      return;
    }
    const said = speechTextFor(response);      // speak short, show long: the chat has the full answer
    if (said) await this.speak(said, sid);
    this.activity();
    listenAgain();
    return saidStill;
  }

  /* The chat takes one question at a time: wait (up to 20 s) for it to finish typing out the previous answer. */
  async chatIdle(sid, turn) {
    const t0 = performance.now();
    while (this.opt('isBusy', false) && performance.now() - t0 < 20000 && this.live(sid) && turn === this.turnSeq) {
      await new Promise(r => setTimeout(r, 120));
    }
  }

  /* A short phrase while thinking ("Still working on it."), without changing state; dropped if the answer lands. */
  sayAside(text, sid, turn) {
    this.fillerText = text;
    const stillThinking = () => this.live(sid) && turn === this.turnSeq && this.snap.state === 'thinking';
    const ctrl = new AbortController();
    this.fetchClip(text, ctrl.signal).then(async out => {
      if (!stillThinking()) { if (out.stream) out.stream.abort(); return; }
      if (out.stream || out.blob) {
        const clip = out.stream ? await this.playStream(out.stream) : await this.playClip(out.blob);
        if (!clip) return;
        if (!stillThinking()) { clip.stop(); return; }
        this.stopFiller();
        this.filler = clip;
        clip.done.then(() => { if (this.filler === clip) this.filler = null; });
      } else if (out.spokenText) {
        this.speakInBrowser(out.spokenText, ctrl.signal);
        this.filler = { stop: () => { ctrl.abort(); if (window.speechSynthesis) window.speechSynthesis.cancel(); } };
      }
    }).catch(() => {});
  }

  // ── filler ("Let me check that."), spoken only ──────────────────────────────
  playFiller(sid, turn) {
    const stillThinking = () => this.live(sid) && turn === this.turnSeq && this.snap.state === 'thinking';
    fetch('/api/voice/filler?session_id=' + encodeURIComponent(this.session))
      .then(r => (r.ok ? r.json() : null))
      .then(async d => {
        if (!d || !d.filler || !stillThinking()) return;
        this.fillerText = d.filler;
        const ctrl = new AbortController();
        const pending = { stop: () => ctrl.abort() };
        this.stopFiller();
        this.filler = pending;
        const out = await this.fetchClip(d.filler, ctrl.signal);
        if (this.filler === pending) this.filler = null;
        if (!stillThinking()) { if (out.stream) out.stream.abort(); return; }
        if (out.stream || out.blob) {
          const clip = out.stream ? await this.playStream(out.stream) : await this.playClip(out.blob);
          if (!clip) return;
          if (!stillThinking()) { clip.stop(); return; }
          this.stopFiller();
          this.filler = clip;
          clip.done.then(() => { if (this.filler === clip) this.filler = null; });
        } else if (out.spokenText) {
          this.speakInBrowser(out.spokenText, ctrl.signal);
          this.filler = { stop: () => { ctrl.abort(); if (window.speechSynthesis) window.speechSynthesis.cancel(); } };
        }
      })
      .catch(() => {});
  }

  stopFiller() {
    if (this.filler) { const f = this.filler; this.filler = null; f.stop(); }
  }

  // ── speaking ───────────────────────────────────────────────────────────────
  /* One sentence's audio -> { stream } | { blob } | { spokenText } | { skipped }. */
  async fetchClip(text, parent) {
    if (this.opt('getDeviceVoice', false) || this.speechOutDown) return { spokenText: text };
    const speed = this.opt('getSpeed', 1);
    if (streamRoute) {
      const clip = new StreamClip(text, { signal: parent, speed });
      const r = await clip.ready;
      if (r.ok) return { stream: clip };
      if (parent.aborted) return { skipped: true };
      if (r.status === 404 || r.status === 405) streamRoute = false;
      else return { spokenText: r.spokenText || text };
    }
    const ctrl = new AbortController();
    const onParent = () => ctrl.abort();
    parent.addEventListener('abort', onParent, { once: true });
    const timer = setTimeout(() => ctrl.abort(), SPEAK_TIMEOUT_MS);
    try {
      const res = await fetch('/api/voice/speak', { method: 'POST', headers: { 'Content-Type': 'application/json' },
                                                    body: JSON.stringify(ttsBody(text, { speed })), signal: ctrl.signal });
      if (res.ok) { const blob = await res.blob(); if (blob && blob.size) return { blob }; }
      const d = res.ok ? {} : await res.json().catch(() => ({}));
      return { spokenText: d.spoken_text || text };
    } catch (e) {
      if (parent.aborted) return { skipped: true };
      return { spokenText: text };
    } finally {
      clearTimeout(timer);
      parent.removeEventListener('abort', onParent);
    }
  }

  async playClip(blob) {
    const ctx = this.ctx;
    if (!ctx || !this.outGain) return null;
    try {
      if (ctx.state === 'suspended') await ctx.resume().catch(() => {});
      const buffer = await ctx.decodeAudioData(await blob.arrayBuffer());
      if (!this.active || ctx !== this.ctx) return null;
      const src = ctx.createBufferSource();
      src.buffer = buffer;
      src.connect(this.outGain);
      let finish, stopped = false;
      const done = new Promise(r => { finish = r; });
      src.onended = () => { try { src.disconnect(); } catch (e) { /* already */ } finish(stopped ? 'stopped' : 'ended'); };
      src.start();
      return { done, stop() { if (stopped) return; stopped = true; try { src.stop(); } catch (e) { finish('stopped'); } } };
    } catch (e) {
      console.warn('voice mode: could not play the clip', e);
      return null;
    }
  }

  async playStream(stream) {
    const ctx = this.ctx;
    if (!ctx || !this.outGain) { stream.abort(); return null; }
    if (ctx.state === 'suspended') await ctx.resume().catch(() => {});
    if (!this.active || ctx !== this.ctx) { stream.abort(); return null; }
    return stream.play(ctx, this.outGain);
  }

  async speakInBrowser(text, signal) {
    const synth = window.speechSynthesis;
    if (!synth || !this.active || signal.aborted) return;
    const voice = await localVoice();
    if (!voice || !this.active || signal.aborted) return;
    this.browserSpeaking = true;
    await new Promise(resolve => {
      const u = new SpeechSynthesisUtterance(text);
      u.voice = voice;
      u.lang = voice.lang;
      u.rate = this.opt('getSpeed', 1) || 1;
      utterances.add(u);
      const done = () => { utterances.delete(u); signal.removeEventListener('abort', done); resolve(); };
      u.onend = done;
      u.onerror = done;
      signal.addEventListener('abort', done, { once: true });
      synth.speak(u);
    });
    this.browserSpeaking = false;
  }

  /* Speaks a reply sentence by sentence: sentence 1 is fetched at once and N+1 while N plays. Interrupt, barge-in
     and stop end the whole reply. */
  async speak(text, sid = this.sid) {
    if (!this.live(sid)) return 'stopped';
    this.stopFiller();
    if (window.speechSynthesis) window.speechSynthesis.cancel();
    const parts = splitSpokenSentences(text);
    this.speakingText = text;
    if (!parts.length) return 'ended';
    this.set({ state: 'speaking', replyText: '' });
    const ctrl = new AbortController();
    this.speakCtrl = ctrl;
    // from the first moment of the reply, so words said while its audio is still on the way are not cut in half
    if (!this.isPtt() && this.vad && this.vadMode === 'off') this.vadStart('bargein');
    try {
      let next = this.fetchClip(parts[0], ctrl.signal);
      for (let i = 0; i < parts.length; i++) {
        const r = await next;
        if (!this.live(sid) || ctrl.signal.aborted || r.skipped) return 'stopped';
        next = i + 1 < parts.length ? this.fetchClip(parts[i + 1], ctrl.signal) : null;
        if (this.snap.state === 'speaking') this.set({ replyText: parts[i] });
        if (!r.blob && !r.stream) {
          await this.speakInBrowser(r.spokenText, ctrl.signal);
          if (ctrl.signal.aborted) return 'stopped';
          continue;
        }
        const clip = r.stream ? await this.playStream(r.stream) : await this.playClip(r.blob);
        if (!clip) {
          await this.speakInBrowser(parts[i], ctrl.signal);
          if (ctrl.signal.aborted) return 'stopped';
          continue;
        }
        if (!this.live(sid) || ctrl.signal.aborted) { clip.stop(); return 'stopped'; }
        this.clip = clip;
        // Talking over the reply is checked for the whole reply (not restarted per sentence). Hold-to-talk interrupts
        // by pressing; without the speech detector, by tapping (loudness alone can't tell a voice from the reply).
        if (!this.isPtt() && this.vad && this.vadMode === 'off') this.vadStart('bargein');
        const how = await clip.done;
        if (this.clip === clip) this.clip = null;
        if (how === 'stopped') return 'stopped';
      }
      return 'ended';
    } finally {
      ctrl.abort();
      if (this.speakCtrl === ctrl) this.speakCtrl = null;
      this.spokeUntil = performance.now();
      this.lastSaid = { text, at: this.spokeUntil };
      if (this.vadMode === 'bargein') { this.vadPause(); this.duck(false); }
      else if (this.vadMode === 'bargein-hold') {                // still talking as the reply ended: that is the next question
        this.duck(false);
        this.vadMode = 'listen';
        this.carryover = true;
      }                                                          // 'bargein-check': checkBargeIn decides
    }
  }
}
