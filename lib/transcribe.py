"""Word level transcript with faster-whisper. Output shape:
[{"start","end","text","words":[{"s","e","w"}]}]  (times in seconds of the SOURCE file)."""
import json, os, subprocess, tempfile, time, wave
import numpy as np

def to_wav16k(src, out, start=None, end=None):
    cmd = ["ffmpeg", "-v", "error", "-y"]
    if start is not None: cmd += ["-ss", f"{start:.3f}"]
    if end is not None: cmd += ["-t", f"{end - (start or 0):.3f}"]
    subprocess.run(cmd + ["-i", src, "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", out], check=True)
    return out

def transcribe(src, out_json, model="small.en", start=None, end=None, threads=None, log=print):
    from faster_whisper import WhisperModel
    tmp = tempfile.mktemp(suffix=".wav"); to_wav16k(src, tmp, start, end)
    wf = wave.open(tmp); audio = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0; os.remove(tmp)
    m = WhisperModel(model, device="cpu", compute_type="int8", cpu_threads=threads or os.cpu_count() or 4)
    off = start or 0.0; t = time.time()
    segs, info = m.transcribe(audio, word_timestamps=True, vad_filter=True, beam_size=1 if model.startswith("small") else 5)
    out = []
    for s in segs:
        out.append({"start": s.start + off, "end": s.end + off, "text": s.text.strip(),
                    "words": [{"s": w.start + off, "e": w.end + off, "w": w.word} for w in s.words]})
        log(f"  {s.end + off:7.1f}s")
    json.dump(out, open(out_json, "w"))
    base = os.path.splitext(out_json)[0]
    with open(base + ".txt", "w") as f:
        for d in out: f.write(f"[{int(d['start']//60):02d}:{d['start']%60:05.2f}] {d['text']}\n")
    def ts(x):
        h = int(x // 3600); m_ = int(x % 3600 // 60); s_ = x % 60
        return f"{h:02d}:{m_:02d}:{int(s_):02d},{int((s_%1)*1000):03d}"
    with open(base + ".srt", "w") as f:
        for i, d in enumerate(out, 1): f.write(f"{i}\n{ts(d['start'])} --> {ts(d['end'])}\n{d['text']}\n\n")
    log(f"transcribed {len(out)} segments in {time.time()-t:.0f}s -> {out_json}")
    return out
