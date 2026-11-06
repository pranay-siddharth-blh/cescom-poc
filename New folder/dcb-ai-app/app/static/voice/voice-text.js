// Pure helpers for voice mode (no DOM). Adapted from the Meghalaya voice agent's voiceText.js (via the KPCL voice instance).

/** Sentence boundaries for pipelined speech. Splits only at . ! ? followed by a space and a capital, digit or quote,
    so "64.95 percent" or "Rs. 5" stay whole. */
export function splitSpokenSentences(text) {
  const s = String(text == null ? '' : text).trim();
  if (!s) return [];
  const parts = s.split(/(?<=[.!?])\s+(?=["'A-Z0-9])/).map(p => p.trim()).filter(Boolean);
  return parts.length ? parts : [s];
}

/** True when an ASR transcript holds real words, not transcribed noise (the model is asked for English, so a
    reply with no Latin letter or digit is noise, e.g. a chime heard as Chinese characters). */
export function isRealTranscript(t) {
  return !!t && /[A-Za-z0-9]/.test(String(t)) && !/^\s*(oh|uh|um|hmm|ah)[.!?]?\s*$/i.test(String(t));
}

/** True when `heard` is the agent's own reply coming back through the microphone: it repeats a run of at least four
    words of `said`, in order, covering most of what was heard. A follow-up question ("Hassan collection in March 2025?") shares
    words with the reply but not a long run of them, so it is never mistaken for an echo. */
export function isEchoOf(heard, said) {
  const words = s => String(s || '').toLowerCase().replace(/(\d),(\d)/g, '$1$2').match(/[a-z0-9]+/g) || [];
  const h = words(heard), s = words(said);
  if (!h.length || !s.length) return false;
  let best = 0;
  const prev = new Array(s.length + 1).fill(0);
  for (let i = 1; i <= h.length; i++) {
    let diag = 0;
    for (let j = 1; j <= s.length; j++) {
      const keep = prev[j];
      prev[j] = h[i - 1] === s[j - 1] ? diag + 1 : 0;
      if (prev[j] > best) best = prev[j];
      diag = keep;
    }
  }
  return best >= Math.min(4, h.length) && best >= 0.6 * h.length && h.length >= 3;
}

/** Float32 mono PCM (-1..1) -> 16-bit PCM WAV bytes. */
export function encodeWavBytes(samples, rate) {
  const n = samples.length;
  const buf = new ArrayBuffer(44 + n * 2);
  const view = new DataView(buf);
  const str = (off, s) => { for (let i = 0; i < s.length; i++) view.setUint8(off + i, s.charCodeAt(i)); };
  str(0, 'RIFF'); view.setUint32(4, 36 + n * 2, true); str(8, 'WAVE');
  str(12, 'fmt '); view.setUint32(16, 16, true); view.setUint16(20, 1, true);
  view.setUint16(22, 1, true); view.setUint32(24, rate, true);
  view.setUint32(28, rate * 2, true); view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  str(36, 'data'); view.setUint32(40, n * 2, true);
  for (let i = 0; i < n; i++) {
    const v = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(44 + i * 2, v < 0 ? v * 0x8000 : v * 0x7FFF, true);
  }
  return buf;
}

/** RMS of a time-domain frame, mapped to a 0..1 display level. */
export function levelFromFrame(frame) {
  let sum = 0;
  for (let i = 0; i < frame.length; i++) sum += frame[i] * frame[i];
  const rms = Math.sqrt(sum / (frame.length || 1));
  return { rms, level: Math.min(1, rms * 6) };
}

/** What to say for an answer: the server's spoken_answer, else the answer text without its markup. */
export function speechTextFor(response) {
  if (!response) return '';
  return String(response.spoken_answer || String(response.answer || '')
    .replace(/\*\*/g, '').replace(/^\s*[•\-]\s*/gm, '').replace(/\s*\n+\s*/g, '. ').replace(/(\.\s*){2,}/g, '. '))
    .trim().slice(0, 1500);
}

export const VOICE_SPEEDS = [0.85, 1, 1.15];
