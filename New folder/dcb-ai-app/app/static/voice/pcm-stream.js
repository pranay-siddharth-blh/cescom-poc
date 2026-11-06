/* Streamed speech playback. POST /api/voice/speak/stream returns raw s16le mono PCM (rate in X-Audio-Sample-Rate)
   as the model decodes it, so speech starts after the first chunk instead of after the whole clip.

   A StreamClip starts its fetch at once (so the next sentence can be fetched while this one plays), buffers what
   arrives, and once play() is called schedules every chunk back-to-back on the Web Audio clock: no gaps, and no
   <audio> element. Adapted from the Meghalaya voice agent's pcmStream.js (via the KPCL voice instance). */

const FIRST_CHUNK_TIMEOUT_MS = 20000;   // then the browser's own voice takes over
const LEAD_S = 0.05;                    // schedule this far ahead of "now"

/** Bytes -> Float32 samples, carrying an odd trailing byte into the next chunk. */
export function pcm16ToFloat32(bytes, carry) {
  let buf = bytes;
  if (carry && carry.length) {
    buf = new Uint8Array(carry.length + bytes.length);
    buf.set(carry, 0);
    buf.set(bytes, carry.length);
  }
  const n = buf.length >> 1;
  const view = new DataView(buf.buffer, buf.byteOffset, n * 2);
  const out = new Float32Array(n);
  for (let i = 0; i < n; i++) out[i] = view.getInt16(i * 2, true) / 32768;
  return { samples: out, carry: buf.length & 1 ? buf.slice(buf.length - 1) : null };
}

/** The speak request body: speed only when not 1, so the default request is minimal. */
export function ttsBody(text, { speed } = {}) {
  const body = { text };
  if (speed && speed !== 1) body.speed = speed;
  return body;
}

export class StreamClip {
  /** Await `ready`: { ok: true } once audio is flowing, or { ok: false, status, spokenText } (404 = no stream route,
      use /api/voice/speak; anything else = use the browser voice with spokenText). */
  constructor(text, { signal, speed } = {}) {
    this.text = text;
    this.ctrl = new AbortController();
    this.chunks = [];
    this.ended = false;
    this.rate = 24000;
    this.onChunk = null;
    if (signal) {
      if (signal.aborted) this.ctrl.abort();
      else signal.addEventListener('abort', () => this.ctrl.abort(), { once: true });
    }
    this.ready = this._run(ttsBody(text, { speed }));
  }

  async _run(body) {
    const timer = setTimeout(() => this.ctrl.abort(), FIRST_CHUNK_TIMEOUT_MS);
    let res;
    try {
      res = await fetch('/api/voice/speak/stream', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal: this.ctrl.signal,
      });
    } catch (e) {
      clearTimeout(timer);
      this.ended = true;
      return { ok: false, status: 0, spokenText: this.text, aborted: this.ctrl.signal.aborted };
    }
    if (!res.ok || !res.body) {
      clearTimeout(timer);
      this.ended = true;
      const d = await res.json().catch(() => ({}));
      return { ok: false, status: res.status, spokenText: d.spoken_text || this.text };
    }
    this.rate = parseInt(res.headers.get('X-Audio-Sample-Rate'), 10) || 24000;
    const reader = res.body.getReader();
    let carry = null, first = true;
    const pump = async () => {
      try {
        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          if (first) { clearTimeout(timer); first = false; }
          const r = pcm16ToFloat32(value, carry);
          carry = r.carry;
          if (r.samples.length) { this.chunks.push(r.samples); if (this.onChunk) this.onChunk(); }
        }
      } catch (e) { /* aborted or cut short: play what arrived */ }
      clearTimeout(timer);
      this.ended = true;
      if (this.onChunk) this.onChunk();
    };
    const firstAudio = new Promise(resolve => { this.onChunk = resolve; });
    pump();
    await firstAudio;
    this.onChunk = null;
    if (!this.chunks.length) return { ok: false, status: 200, spokenText: this.text, aborted: this.ctrl.signal.aborted };
    return { ok: true };
  }

  /** Play through `destination`. Returns { done: Promise<'ended'|'stopped'>, stop() }. */
  play(ctx, destination) {
    const dest = destination || ctx.destination;
    const sources = new Set();
    let at = ctx.currentTime + LEAD_S, stopped = false, finish;
    const done = new Promise(r => { finish = r; });
    const maybeEnd = () => { if (!stopped && this.ended && !this.chunks.length && !sources.size) finish('ended'); };
    const schedule = () => {
      if (stopped) return;
      while (this.chunks.length) {
        const s = this.chunks.shift();
        const buf = ctx.createBuffer(1, s.length, this.rate);
        buf.copyToChannel(s, 0);
        const src = ctx.createBufferSource();
        src.buffer = buf;
        src.connect(dest);
        if (at < ctx.currentTime + 0.01) at = ctx.currentTime + LEAD_S;   // fell behind: restart cleanly
        src.start(at);
        at += buf.duration;
        sources.add(src);
        src.onended = () => { sources.delete(src); try { src.disconnect(); } catch (e) { /* already */ } maybeEnd(); };
      }
      maybeEnd();
    };
    this.onChunk = schedule;
    schedule();
    return {
      done,
      stop: () => {
        if (stopped) return;
        stopped = true;
        this.onChunk = null;
        this.ctrl.abort();
        sources.forEach(src => { try { src.stop(); } catch (e) { /* not started */ } });
        sources.clear();
        finish('stopped');
      },
    };
  }

  abort() { this.ctrl.abort(); }
}
