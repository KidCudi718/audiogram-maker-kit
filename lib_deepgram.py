"""Whole-Space cloud transcription for space_prep (Deepgram nova-3 diarized; AssemblyAI backup). Caches the raw
response (asr_raw.json) so reruns never pay twice. Returns words with per-word 'Voice A..' (A = most talk time)."""
import json, os, time, collections
from lib.cloud_asr import transcribe_file, compact

def transcribe(work, log=print):
    raw_p = os.path.join(work, "asr_raw.json"); legacy = os.path.join(work, "deepgram.json")
    if os.path.exists(raw_p): r = json.load(open(raw_p))
    elif os.path.exists(legacy):   # earlier Deepgram run: rebuild from its raw JSON without a new call
        j = json.load(open(legacy)); ws = j["results"]["channels"][0]["alternatives"][0]["words"]
        r = dict(engine="deepgram nova-3", duration=j["metadata"]["duration"],
                 words=[dict(s=w["start"], e=w["end"], w=" " + (w.get("punctuated_word") or w["word"]), spk=str(w.get("speaker", "?"))) for w in ws],
                 segs=[dict(start=u["start"], end=u["end"], text=u["transcript"], words=[dict(s=w["start"], e=w["end"], w=" " + (w.get("punctuated_word") or w["word"])) for w in u["words"]]) for u in j["results"].get("utterances") or []])
    else:
        up = os.path.join(work, "build", "asr_upload.ogg"); os.makedirs(os.path.dirname(up), exist_ok=True)
        if not os.path.exists(up): compact(os.path.join(work, "source.m4a"), up + ".tmp.ogg"); os.replace(up + ".tmp.ogg", up)
        t = time.time(); r = transcribe_file(up, log=log); r.pop("raw", None)
        json.dump(r, open(raw_p + ".tmp", "w")); os.replace(raw_p + ".tmp", raw_p)
        log(f"{r['engine']}: {r['duration']/60:.1f} min in {time.time()-t:.0f}s")
    talk = collections.Counter()
    for w in r["words"]: talk[w["spk"]] += w["e"] - w["s"]
    name = {s: ("Voice " + chr(65 + i)) if i < 26 else f"Voice {i+1}" for i, (s, _) in enumerate(talk.most_common())}
    segs = r["segs"] or [dict(start=w["s"], end=w["e"], text=w["w"].strip(), words=[w]) for w in r["words"]]
    p = os.path.join(work, "transcript.json"); json.dump(segs, open(p + ".tmp", "w")); os.replace(p + ".tmp", p)
    words = [dict(s=round(w["s"], 2), e=round(w["e"], 2), w=w["w"]) for w in r["words"]]
    return dict(words=words, voices=[name[w["spk"]] for w in r["words"]], duration=r["duration"], engine=r["engine"])
