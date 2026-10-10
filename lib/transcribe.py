"""Word level transcript with faster-whisper. Output shape:
[{"start","end","text","words":[{"s","e","w"}]}]  (times in seconds of the SOURCE file).

v1.2: the audio is transcribed in chunks (default 10 minutes, cut at the quietest moment near each boundary so no word
is split). After every chunk the transcript files are rewritten and a line
  CHUNK READY mm:ss-mm:ss
is logged, so moments can be read and proposed while the rest of the Space is still being transcribed.
batch_size > 1 uses faster-whisper's BatchedInferencePipeline. It is NOT the default: on a CPU box (small.en, int8) it
was only about 20 percent faster and dropped or looped words on real Space audio (8 to 70 percent fewer words in tests),
which breaks word-by-word captions. Sequential decoding with VAD is the tested default."""
import json, os, subprocess, tempfile, time, wave
import numpy as np

SR = 16000

def to_wav16k(src, out, start=None, end=None):
    cmd = ["ffmpeg", "-v", "error", "-y"]
    if start is not None: cmd += ["-ss", f"{start:.3f}"]
    if end is not None: cmd += ["-t", f"{end - (start or 0):.3f}"]
    subprocess.run(cmd + ["-i", src, "-ac", "1", "-ar", str(SR), "-c:a", "pcm_s16le", out], check=True)
    return out

def mmss(t): return f"{int(t // 60):02d}:{int(t % 60):02d}"

def cut_points(audio, chunk, search=15.0):
    """Chunk boundaries (in samples) at the quietest 50 ms near every `chunk` seconds."""
    n = len(audio); pts = [0]; hop = int(0.05 * SR)
    while n - pts[-1] > (chunk + search) * SR:
        nominal = pts[-1] + int(chunk * SR); a, b = nominal - int(search * SR), nominal + int(search * SR)
        seg = audio[a:b][: (b - a) // hop * hop].reshape(-1, hop)
        pts.append(a + int(np.argmin((seg ** 2).mean(1))) * hop + hop // 2)
    return pts + [n]

def write_all(out, out_json):
    json.dump(out, open(out_json, "w"))
    base = os.path.splitext(out_json)[0]
    with open(base + ".txt", "w") as f:
        for d in out: f.write(f"[{int(d['start']//60):02d}:{d['start']%60:05.2f}] {d['text']}\n")
    def ts(x):
        h = int(x // 3600); m_ = int(x % 3600 // 60); s_ = x % 60
        return f"{h:02d}:{m_:02d}:{int(s_):02d},{int((s_%1)*1000):03d}"
    with open(base + ".srt", "w") as f:
        for i, d in enumerate(out, 1): f.write(f"{i}\n{ts(d['start'])} --> {ts(d['end'])}\n{d['text']}\n\n")

def transcribe(src, out_json, model="small.en", start=None, end=None, threads=None, log=print, chunk=600, batch_size=1,
               on_chunk=None):
    from faster_whisper import WhisperModel, BatchedInferencePipeline
    tmp = tempfile.mktemp(suffix=".wav"); to_wav16k(src, tmp, start, end)
    wf = wave.open(tmp); audio = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0; os.remove(tmp)
    m = WhisperModel(model, device="cpu", compute_type="int8", cpu_threads=threads or os.cpu_count() or 4)
    pipe = BatchedInferencePipeline(model=m) if batch_size and batch_size > 1 else None
    off = start or 0.0; t = time.time(); out = []
    pts = cut_points(audio, chunk) if chunk else [0, len(audio)]
    log(f"transcribing {len(audio) / SR / 60:.1f} min in {len(pts) - 1} chunk(s), {'batched x' + str(batch_size) if pipe else 'sequential'}")
    for a, b in zip(pts, pts[1:]):
        c0 = off + a / SR; piece = audio[a:b]
        if pipe: segs, _ = pipe.transcribe(piece, word_timestamps=True, vad_filter=True, batch_size=batch_size, beam_size=1 if model.startswith("small") else 5)
        else: segs, _ = m.transcribe(piece, word_timestamps=True, vad_filter=True, beam_size=1 if model.startswith("small") else 5)
        for s in segs:
            if not s.words: continue
            out.append({"start": s.start + c0, "end": s.end + c0, "text": s.text.strip(),
                        "words": [{"s": w.start + c0, "e": w.end + c0, "w": w.word} for w in s.words]})
        out.sort(key=lambda d: d["start"]); write_all(out, out_json)
        log(f"CHUNK READY {mmss(c0)}-{mmss(off + b / SR)} ({time.time() - t:.0f}s so far)")
        if on_chunk:
            try: on_chunk()
            except Exception as ex: log(f"note: per-chunk step failed ({ex})")
    write_all(out, out_json)
    log(f"transcribed {len(out)} segments in {time.time()-t:.0f}s -> {out_json}")
    return out
