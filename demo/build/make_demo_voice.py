"""Rebuilds the bundled demo voice and its exact word timestamps (no transcription needed).
Voice: Piper TTS (https://github.com/OHF-Voice/piper1-gpl) with en_US-ljspeech-high, trained on the public domain
LJ Speech dataset. Piper itself is only used to generate the file; it is not part of the kit.
Run with a python that has piper-tts and onnx installed:
  python make_demo_voice.py en_US-ljspeech-high.onnx ../demo_script.txt ../
Word timings come straight from Piper's phoneme alignments (word boundaries are the space phonemes)."""
import sys, os, json, wave, re
import numpy as np
from piper import PiperVoice, SynthesisConfig
model, script, outd = sys.argv[1], sys.argv[2], sys.argv[3]
text = open(script).read().strip()
v = PiperVoice.load(model, include_alignments=True); cfg = SynthesisConfig(length_scale=1.06)
GAP = 0.40; LEAD = 0.0; TAIL = 0.6
words_txt = text.split()
audio = []; words = []; t = LEAD; sr = None; wi = 0
for ch in v.synthesize(text, cfg, include_alignments=True):
    sr = ch.sample_rate; a = np.frombuffer(ch.audio_int16_bytes, dtype=np.int16)
    al = ch.phoneme_alignments; tot = sum(int(x.num_samples) for x in al); scale = len(a) / tot
    pos = 0; cur = None
    for x in al:
        n = int(x.num_samples) * scale; p = x.phoneme
        if p in ("^", "$", "_"): pos += n; continue
        if p == " ":
            if cur: words.append(cur); cur = None
            pos += n; continue
        if p in ".,!?;:\u2026":
            pos += n; continue
        if cur is None: cur = [t + pos / sr, t + (pos + n) / sr]
        else: cur[1] = t + (pos + n) / sr
        pos += n
    if cur: words.append(cur)
    audio.append(a); audio.append(np.zeros(int(GAP * sr), dtype=np.int16)); t += len(a) / sr + GAP
audio.append(np.zeros(int(TAIL * sr), dtype=np.int16))
if len(words) != len(words_txt):
    raise SystemExit(f"word count mismatch: {len(words)} timed vs {len(words_txt)} in script")
x = np.concatenate(audio)
wavp = os.path.join(outd, "demo_voice.wav")
with wave.open(wavp, "wb") as w: w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr); w.writeframes(x.tobytes())
seg = [{"start": round(words[0][0], 3), "end": round(words[-1][1], 3), "text": text,
        "words": [{"s": round(s, 3), "e": round(e, 3), "w": " " + wt} for (s, e), wt in zip(words, words_txt)]}]
json.dump(seg, open(os.path.join(outd, "demo_transcript.json"), "w"), indent=1)
print("ok", len(words), "words", round(len(x) / sr, 2), "s")
