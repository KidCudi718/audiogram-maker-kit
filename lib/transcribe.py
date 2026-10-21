"""Word level transcript via cloud ASR (Deepgram nova-3, AssemblyAI backup; see lib/cloud_asr.py). No local model.
Output shape: [{"start","end","text","words":[{"s","e","w"}]}]  (times in seconds of the SOURCE file). Also writes
.txt and .srt next to the JSON. Signature kept for audiogram.py (model/threads/chunk/batch_size are ignored)."""
import json, os, tempfile, time
from lib.cloud_asr import transcribe_file, compact

def write_all(out, out_json):
    json.dump(out, open(out_json + ".tmp", "w")); os.replace(out_json + ".tmp", out_json)
    base = os.path.splitext(out_json)[0]
    with open(base + ".txt", "w") as f:
        for d in out: f.write(f"[{int(d['start']//60):02d}:{d['start']%60:05.2f}] {d['text']}\n")
    def ts(x):
        h = int(x // 3600); m_ = int(x % 3600 // 60); s_ = x % 60
        return f"{h:02d}:{m_:02d}:{int(s_):02d},{int((s_%1)*1000):03d}"
    with open(base + ".srt", "w") as f:
        for i, d in enumerate(out, 1): f.write(f"{i}\n{ts(d['start'])} --> {ts(d['end'])}\n{d['text']}\n\n")

def transcribe(src, out_json, model=None, start=None, end=None, threads=None, log=print, chunk=None, batch_size=None, on_chunk=None):
    t = time.time(); tmp = tempfile.mktemp(suffix=".ogg"); compact(src, tmp, start, end)
    try: r = transcribe_file(tmp, log=log)
    finally: os.remove(tmp)
    off = start or 0.0
    segs = r["segs"] or [dict(start=w["s"], end=w["e"], text=w["w"].strip(), words=[w]) for w in r["words"]]
    out = [dict(start=round(s["start"] + off, 2), end=round(s["end"] + off, 2), text=s["text"],
                words=[dict(s=round(w["s"] + off, 2), e=round(w["e"] + off, 2), w=w["w"]) for w in s["words"]]) for s in segs]
    write_all(out, out_json); log(f"CHUNK READY all ({r['engine']})")
    if on_chunk:
        try: on_chunk()
        except Exception as ex: log(f"note: speaker step failed ({ex})")
    log(f"transcribed {len(out)} segments with {r['engine']} in {time.time()-t:.0f}s -> {out_json}")
    return out
