"""Cloud transcription with speaker diarization. Deepgram nova-3 first; AssemblyAI (speaker_labels) only if Deepgram
errors. Keys from env DEEPGRAM_API_KEY / ASSEMBLYAI_API_KEY (never logged). With no key at all, falls back to local
faster-whisper small.en (slower, no speaker diarization: every word is one voice). Get a free Deepgram key for speed.
transcribe_file(path) -> dict(engine, duration, words=[{s,e,w,spk}], segs=[{start,end,text,words}], raw)"""
import json, os, subprocess, time
DG_URL = "https://api.deepgram.com/v1/listen?model=nova-3&diarize=true&smart_format=true&punctuate=true&utterances=true&language=en"

class ASRError(RuntimeError): pass

def compact(src, out, start=None, end=None):
    """Low-bitrate mono Opus for fast upload (~11 MB/hour)."""
    cmd = ["ffmpeg", "-v", "error", "-y"]
    if start is not None: cmd += ["-ss", f"{start:.3f}"]
    if end is not None: cmd += ["-t", f"{end - (start or 0):.3f}"]
    subprocess.run(cmd + ["-i", src, "-ac", "1", "-ar", "16000", "-c:a", "libopus", "-b:a", "24k", "-application", "voip", out], check=True)
    return out

def _deepgram(path, log):
    import requests
    key = os.environ.get("DEEPGRAM_API_KEY")
    if not key: raise ASRError("no DEEPGRAM_API_KEY")
    with open(path, "rb") as fh:
        r = requests.post(DG_URL, headers={"Authorization": f"Token {key}", "Content-Type": "audio/ogg"}, data=fh, timeout=900)
    if r.status_code != 200: raise ASRError(f"Deepgram HTTP {r.status_code}")
    j = r.json(); ws = j["results"]["channels"][0]["alternatives"][0]["words"]
    words = [dict(s=w["start"], e=w["end"], w=" " + (w.get("punctuated_word") or w["word"]), spk=str(w.get("speaker", "?"))) for w in ws]
    segs = [dict(start=u["start"], end=u["end"], text=u["transcript"], words=[dict(s=w["start"], e=w["end"], w=" " + (w.get("punctuated_word") or w["word"])) for w in u["words"]])
            for u in j["results"].get("utterances") or []]
    return dict(engine="deepgram nova-3", duration=j["metadata"]["duration"], words=words, segs=segs, raw=j)

def _assemblyai(path, log):
    import requests
    key = os.environ.get("ASSEMBLYAI_API_KEY")
    if not key: raise ASRError("no ASSEMBLYAI_API_KEY")
    h = {"authorization": key}
    with open(path, "rb") as fh: up = requests.post("https://api.assemblyai.com/v2/upload", headers=h, data=fh, timeout=900)
    if up.status_code != 200: raise ASRError(f"AssemblyAI upload HTTP {up.status_code}")
    r = requests.post("https://api.assemblyai.com/v2/transcript", headers=h, json=dict(audio_url=up.json()["upload_url"], speaker_labels=True,
                      punctuate=True, format_text=True, language_code="en"), timeout=60)
    if r.status_code != 200: raise ASRError(f"AssemblyAI HTTP {r.status_code}")
    tid = r.json()["id"]
    while True:
        j = requests.get(f"https://api.assemblyai.com/v2/transcript/{tid}", headers=h, timeout=60).json()
        if j["status"] == "completed": break
        if j["status"] == "error": raise ASRError(f"AssemblyAI error: {j.get('error')}")
        time.sleep(3)
    words = [dict(s=w["start"]/1000, e=w["end"]/1000, w=" " + w["text"], spk=str(w.get("speaker") or "?")) for w in j["words"]]
    segs = [dict(start=u["start"]/1000, end=u["end"]/1000, text=u["text"], words=[dict(s=w["start"]/1000, e=w["end"]/1000, w=" " + w["text"]) for w in u["words"]])
            for u in j.get("utterances") or []]
    return dict(engine="assemblyai speaker_labels", duration=j.get("audio_duration") or 0, words=words, segs=segs, raw=j)

def _local(path, log):
    try: from faster_whisper import WhisperModel
    except ImportError: raise ASRError("no DEEPGRAM_API_KEY and faster-whisper is not installed. Get a free key at https://console.deepgram.com/signup and set DEEPGRAM_API_KEY, or run install.sh")
    log("no DEEPGRAM_API_KEY: using local faster-whisper (slow, no speaker separation). A free Deepgram key makes this much faster.")
    segs_i, info = WhisperModel("small.en", device="cpu", compute_type="int8").transcribe(path, word_timestamps=True, vad_filter=True)
    segs, words = [], []
    for sg in segs_i:
        ws = [dict(s=w.start, e=w.end, w=w.word) for w in (sg.words or [])]
        words += [dict(w, spk="0") for w in ws]; segs.append(dict(start=sg.start, end=sg.end, text=sg.text.strip(), words=ws))
    return dict(engine="local faster-whisper small.en", duration=info.duration, words=words, segs=segs, raw={})

def transcribe_file(path, log=print):
    if not os.environ.get("DEEPGRAM_API_KEY") and not os.environ.get("ASSEMBLYAI_API_KEY"): return _local(path, log)
    try: return _deepgram(path, log)
    except Exception as ex:
        log(f"Deepgram failed ({ex if isinstance(ex, ASRError) else type(ex).__name__}); trying AssemblyAI")
    try: return _assemblyai(path, log)
    except Exception as ex:
        raise ASRError("TRANSCRIPTION FAILED: Deepgram errored and AssemblyAI is unavailable "
                       f"({ex if isinstance(ex, ASRError) else type(ex).__name__}). Set ASSEMBLYAI_API_KEY or retry Deepgram.")
