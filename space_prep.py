#!/usr/bin/env python3
"""space_prep: X Space link in, speaker-labeled transcript out. Nothing else (clips/audiograms: space_moments.py).

  python3 space_prep.py prep <post-or-space-url> [--work DIR] [--crew crew_handles.json] [--blind]
  python3 space_prep.py name --work DIR [--crew ...] [--blind]   # redo naming + outputs from cached data (no API call)

Flow: resolve link -> fetch audio (parallel fragments) + Space info + profile photos -> Deepgram nova-3 diarized
(AssemblyAI backup, lib/cloud_asr.py; raw cached in asr_raw.json) -> naming (naming.py: voiceprints, addressed-by-name,
X speaker list, join time as a soft hint, optional shared-account rule, X captions only as a hint) -> transcript_labeled
.json/.txt/.pdf with photos -> if X captions exist, a quick voiceprint update. --blind hides captions from naming.
Voice A = most talk time. Optional name scrub via AG_SCRUB_REGEX. Writes are atomic.
"""
import argparse, base64, collections, difflib, html, json, os, re, subprocess, sys, threading, time, urllib.request
from collections import Counter, defaultdict
KIT = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, KIT)
K = os.path.join(KIT, "audiogram.py"); ROOT = os.environ.get("AG_WORK_ROOT", "/workspace/audiograms"); VERSION = "3.0"
CREW = os.environ.get("AG_CREW_HANDLES", "")   # optional JSON list of {handle,name,display_name,pfp}
def log(*a): print(*a, flush=True)
def mmss(t): return f"{int(t//60)}:{t%60:04.1f}"
def wjson(p, o, indent=1):
    json.dump(o, open(p + ".tmp", "w"), indent=indent); os.replace(p + ".tmp", p)
def wtext(p, t):
    open(p + ".tmp", "w").write(t); os.replace(p + ".tmp", p)
class Manifest:
    def __init__(self, work):
        self.p = os.path.join(work, "processing_manifest.json")
        self.d = json.load(open(self.p)) if os.path.exists(self.p) else {"stages": {}, "warnings": []}
        self.d["script_version"] = VERSION
    def done(self, st): return st in self.d["stages"]
    def mark(self, st, **kw): self.d["stages"][st] = dict(at=time.strftime("%Y-%m-%d %H:%M:%S"), **kw); self.save()
    def warn(self, m):
        if m not in self.d["warnings"]: self.d["warnings"].append(m); log("WARNING:", m)
        self.save()
    def save(self): wjson(self.p, self.d)
def resolve(url):
    import audiogram as A
    m = A.SPACE_RE.search(url)
    if m: return m.group(1)
    sm = A.STATUS_RE.search(url)
    if not sm: return None
    info, _ = A.space_info(url)
    if info and info.get("id"): return info["id"]
    try:
        emb = urllib.request.urlopen(urllib.request.Request(f"https://cdn.syndication.twimg.com/tweet-result?id={sm.group(1)}&token=a",
                                     headers={"User-Agent": "Mozilla/5.0"}), timeout=20).read().decode("utf-8", "ignore")
        s = re.search(r"spaces/([A-Za-z0-9]{8,20})", emb); return s.group(1) if s else None
    except Exception: return None
def run(args, logf):
    with open(logf, "w") as f: r = subprocess.run(args, stdout=f, stderr=subprocess.STDOUT)
    log(open(logf).read()[-1200:]); return r.returncode
def duration(p):
    try: return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", p],
                                     capture_output=True, text=True).stdout or 0)
    except ValueError: return 0.0
def build_space(work):
    sp = json.load(open(os.path.join(work, "space.json")))
    os.makedirs(os.path.join(work, "img"), exist_ok=True)
    people = sp.get("people") or []; host = sp.get("host_handle")
    for p in people:
        h = p["handle"].lstrip("@"); dst = os.path.join(work, "img", h + ".jpg")
        if p.get("pfp_url") and not os.path.exists(dst):
            try:
                data = urllib.request.urlopen(urllib.request.Request(p["pfp_url"], headers={"User-Agent": "Mozilla/5.0"}), timeout=20).read()
                open(dst + ".tmp", "wb").write(data); os.replace(dst + ".tmp", dst)
            except Exception as ex: log(f"photo failed for @{h}: {ex}")
        p["img"] = dst if os.path.exists(dst) else ""
        if p.get("role") in ("host", "cohost", "speaker"):
            p["role"] = "host" if p["handle"].lower() == (host or "").lower() else ("cohost" if p["role"] in ("host", "cohost") else "speaker")
    sp["people"] = people
    sp["length_s"] = round(duration(os.path.join(work, "source.m4a")), 1); sp["length"] = mmss(sp["length_s"]).split(".")[0]
    sp["hosts"] = [p["handle"] for p in people if p["role"] == "host"] or ([host] if host else [])
    sp["cohosts"] = [p["handle"] for p in people if p["role"] == "cohost"]
    wjson(os.path.join(work, "space.json"), sp); return sp
def join_words(ws):
    t = ""
    for w in ws:
        if not t or w.startswith((" ", "'")) or re.match(r"^[.,!?;:%)]", w): t += w
        else: t += " " + w
    return re.sub(r"\s+", " ", t).strip()
def turns_from(recs):
    T = []
    for r in recs:
        if T and T[-1]["voice"] == r["voice"] and r["s"] - T[-1]["end"] < 2.5:
            T[-1]["end"] = r["e"]; T[-1]["_w"].append(r["w"]); T[-1]["_c"].append(r["caption"])
        else: T.append(dict(voice=r["voice"], start=r["s"], end=r["e"], _w=[r["w"]], _c=[r["caption"]]))
    for t in T:
        t["text"] = join_words(t.pop("_w")); c = t.pop("_c"); cc = Counter(x for x in c if x)
        top = cc.most_common(1)[0] if cc else None
        t["caption_handle"] = top[0] if top and top[1] / len(c) >= 0.6 else None   # only turns X actually captioned
        t["caption_coverage"] = round(sum(cc.values()) / len(c), 2)
        t["provenance"] = "x_caption" if t["caption_handle"] else ("diarization" if t["voice"] != "unknown" else "unknown")
    return T
STOP = {"the", "and", "mr", "mrs", "doc", "eth", "sol", "btc", "nft", "crypto", "all", "things", "big", "real", "official", "app",
        "markets", "here", "just", "good", "what", "that", "this", "with", "bot", "bots", "on", "thee"}
def aliases(p):
    raw = re.sub(r"[^\w\s.]", " ", p.get("name", "")) + " " + re.sub(r"(?<=[a-z])(?=[A-Z])", " ", p["handle"].lstrip("@").replace("_", " "))
    toks = {t.lower().strip(".") for t in re.split(r"[\s.]+", raw) if len(t) >= 3 and not t.isdigit()}
    dn = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", re.sub(r"[^\w\s]", " ", p.get("name", ""))).split()
    if dn: toks.add(dn[0].lower())
    return {t for t in toks if len(t) >= 3 and t not in STOP}
def match_name(word, al):
    w = word.lower().strip(".,!?'\"")
    if len(w) < 3: return False
    return any(w == a or (len(w) >= 4 and len(a) >= 4 and w[0] == a[0] and difflib.SequenceMatcher(None, w, a).ratio() >= 0.75) for a in al)
ADDR = re.compile(r"\b(go ahead|hey|yo|welcome|what's up|whats up|thank you|thanks|appreciate (?:you|it)|come up|over to you|you there|"
                  r"can you hear|floor is yours|you're up)\b", re.I)
SELF = re.compile(r"\b(i'm|i am|this is|my name is|name's)\s+(\w+)", re.I)
def clues(T, people):
    al = {p["handle"]: aliases(p) for p in people}; ev = defaultdict(list)   # voice -> [(handle, source, weight, t, quote)]
    for i, t in enumerate(T):
        if t["voice"] == "unknown": continue
        ws = re.findall(r"[\w']+", t["text"])
        for h, a in al.items():
            hits = [w for w in ws if match_name(w, a)]
            if not hits: continue
            q = t["text"]; pos = q.lower().find(hits[0].lower()); snip = q[max(0, pos-60):pos+60]
            for m in SELF.finditer(q):
                if match_name(m.group(2), a): ev[t["voice"]].append((h, "self_intro", 3, t["start"], snip))
            if ADDR.search(q[max(0, pos-40):pos+40]):
                nxt = next((u for u in T[i+1:i+6] if u["voice"] not in (t["voice"], "unknown") and u["end"]-u["start"] > 2), None)
                prv = next((u for u in reversed(T[max(0, i-3):i]) if u["voice"] not in (t["voice"], "unknown")), None)
                if re.search(r"thank|thanks|appreciate", q[max(0, pos-40):pos+40], re.I):
                    if prv: ev[prv["voice"]].append((h, "addressed_then_spoke", 1, t["start"], f"(thanked after speaking) {snip}"))
                elif nxt and nxt["start"] - t["end"] < 20: ev[nxt["voice"]].append((h, "addressed_then_spoke", 2, t["start"], snip))
    return ev

# ---------- steps ----------
def people_pool(sp, crew_path):
    """Listed Space people plus the crew list (crew names are name candidates, not listed speakers)."""
    pool = {p["handle"]: dict(p) for p in sp["people"]}; disp = {p["handle"]: p["name"] for p in sp["people"]}
    pfp = {p["handle"]: p.get("img", "") for p in sp["people"]}
    if crew_path and os.path.exists(crew_path):
        for c in json.load(open(crew_path)):
            pool.setdefault(c["handle"], dict(handle=c["handle"], role="crew"))["name"] = f"{c['name']} {c['display_name']}"
            disp[c["handle"]] = c["name"]; pfp[c["handle"]] = pfp.get(c["handle"]) or c.get("pfp", "")
    return pool, disp, pfp

def caption_hint(work, words, blind):
    """Per word: the handle X captioned at that time (None when no captions, or --blind)."""
    if blind or not os.path.exists(os.path.join(work, "x_captions.json")): return [None] * len(words)
    run([sys.executable, K, "speakers", "--work", work], os.path.join(work, "speakers.log"))
    from lib.xspace import speaker_at
    turns = json.load(open(os.path.join(work, "speakers.json")))["turns"]
    return [(lambda h: None if h in (None, "unknown") else h)(speaker_at(turns, (w["s"] + w["e"]) / 2)) for w in words]

def name_voices(work, sp, T, recs, pool, log=log):
    import naming as N, soundfile as sf
    ev = clues(T, list(pool.values())); per = defaultdict(Counter)
    for r in recs:
        if r.get("caption"): per[r["voice"]][r["caption"]] += r["e"] - r["s"]
    for v, cc in per.items():
        tot = sum(cc.values())
        for h, d in cc.items():
            if d >= 3 and d / tot >= 0.5: ev[v].append((h, "x_caption", min(10, 3 + int(d // 5)), 0, f"X captions tag {d:.0f}s ({d/tot:.0%})"))
    audio, sr = sf.read(N.wav16(work), dtype="float32")
    res, warns, *_ = N.auto_name(T, recs, list(pool.values()), ev, sp.get("started_utc"), audio, sr, log=log)
    left = list(dict.fromkeys(t["voice"] for t in T if t["voice"] not in res and t["voice"] != "unknown"))
    first = {t["voice"]: t["start"] for t in reversed(T)}
    q = ("Who is " + ", ".join(f"{v} (first heard {mmss(first[v])})" for v in left) + "?") if left else None
    wjson(os.path.join(work, "speakers_auto.json"), dict(assignments=res, warnings=warns, question=q))
    return res, warns, q

def write_outputs(work, sp, T, res, disp, pfp, q):
    from naming import scrub
    talk = Counter()
    for t in T: talk[t["voice"]] += t["end"] - t["start"]
    conf = lambda o: "none" if not o else ("high" if o["score"] >= 8 else "medium" if o["score"] >= 4 else "low")
    turns = []
    for t in T:
        o = res.get(t["voice"]); h = o["handle"] if o else None
        turns.append(dict(start=round(t["start"], 2), end=round(t["end"], 2), voice=t["voice"], candidate_handle=h, confidence=conf(o),
                          attribution_source=(o["sources"][0] if o else "unknown"), verified=False, text=scrub(t["text"])))
    voices = [dict(voice=v, talk_s=round(s), handle=(res.get(v) or {}).get("handle"), confidence=conf(res.get(v)),
                   sources=(res.get(v) or {}).get("sources", []), evidence=[scrub(e) for e in (res.get(v) or {}).get("evidence", [])])
              for v, s in talk.most_common() if v != "unknown"]
    label = lambda x: (f"{disp.get(x['candidate_handle'], '')} {x['candidate_handle']}".strip() if x["candidate_handle"] else x["voice"])
    hosts = ", ".join(f"{p['name']} {p['handle']}" for p in sp["people"] if p.get("role") in ("host", "cohost"))
    wjson(os.path.join(work, "transcript_labeled.json"), dict(space=sp.get("title"), date=sp.get("date"), length=sp.get("length"),
          hosts=hosts, speakers_confirmed=False, question=q, voices=voices, turns=turns))
    wtext(os.path.join(work, "transcript_labeled.txt"), f"{sp.get('title')} | {sp.get('date')} | {sp.get('length')} | hosts {hosts}\n"
          "Speakers auto-named, not confirmed by the user.\n\n" + "".join(f"[{mmss(x['start'])}] {label(x)}: {x['text']}\n" for x in turns))
    def pic(h):
        f = pfp.get(h) if h else None
        if f and os.path.exists(f): return f'<img src="data:image/jpeg;base64,{base64.b64encode(open(f, "rb").read()).decode()}">'
        return '<div class="ph">?</div>'
    css = ("body{font-family:Helvetica,Arial;max-width:820px;margin:30px auto;color:#111}img,.ph{width:44px;height:44px;border-radius:50%;"
           "object-fit:cover;background:#bbb;flex:none;text-align:center;line-height:44px;color:#fff;font-weight:bold}.t{display:flex;gap:12px;"
           "margin:12px 0}.n{font-weight:bold}.n span{color:#777;font-weight:normal;font-size:13px}.warn{background:#fff3cd;border:2px solid "
           "#d39e00;padding:10px 14px;font-weight:bold}.sp{display:inline-flex;align-items:center;gap:8px;margin:6px 14px 6px 0;font-size:13px;width:360px}")
    o = [f'<html><head><meta charset="utf-8"><style>{css}</style></head><body><h1>{html.escape(sp.get("title", ""))}</h1>',
         f'<p>{sp.get("date")} · {sp.get("length")} · Hosts {html.escape(hosts)}</p>',
         '<p class="warn">Speakers auto-named (voiceprints, names said on air, speaker list). Not confirmed by the user.</p><h2>Voices</h2>']
    for v in voices:
        who = f"{html.escape(disp.get(v['handle'], ''))} {v['handle']}" if v["handle"] else "unknown"
        o.append(f'<div class="sp">{pic(v["handle"])}<div><b>{v["voice"]}</b> · {mmss(v["talk_s"]).split(".")[0]}<br>{who}<br>{v["confidence"]}</div></div>')
    if q: o.append(f"<p><b>Question:</b> {html.escape(q)}</p>")
    o.append("<h2>Transcript</h2>")
    for x in turns:
        o.append(f'<div class="t">{pic(x["candidate_handle"])}<div><div class="n">{html.escape(label(x))} <span>{mmss(x["start"])} · '
                 f'{x["confidence"]}</span></div>{html.escape(x["text"])}</div></div>')
    hp = os.path.join(work, "build", "transcript_labeled.html"); os.makedirs(os.path.dirname(hp), exist_ok=True); wtext(hp, "\n".join(o) + "</body></html>")
    subprocess.run(["google-chrome", "--headless", "--no-sandbox", "--disable-gpu", "--no-pdf-header-footer",
                    "--print-to-pdf=" + os.path.join(work, "transcript_labeled.pdf"), hp], capture_output=True, timeout=120)

def finish(work, sp, a, M):
    """Naming + outputs from cached transcript (voices.json)."""
    t0 = time.time(); recs = json.load(open(os.path.join(work, "voices.json")))
    T = turns_from(recs); pool, disp, pfp = people_pool(sp, a.crew)
    res, warns, q = name_voices(work, sp, T, recs, pool)
    for w in warns: M.warn(w)
    M.mark("name", seconds=round(time.time() - t0)); write_outputs(work, sp, T, res, disp, pfp, q); M.mark("outputs")
    for v in sorted({t["voice"] for t in T} - {"unknown"}):
        o = res.get(v); log(f"{v}: {(disp.get(o['handle'], '') + ' ' + o['handle']) if o else 'unknown'} {o['sources'] if o else ''}")
    log("QUESTION: " + (q or "none")); log("PDF: " + os.path.join(work, "transcript_labeled.pdf"))

def cmd_prep(a):
    sid = resolve(a.src)
    if not sid: log("NOT A SPACE: no x.com/i/spaces link found in that post."); return 2
    work = a.work or os.path.join(ROOT, sid); os.makedirs(work, exist_ok=True); M = Manifest(work); M.d["space_id"] = sid
    t0 = time.time()
    if not (os.path.exists(os.path.join(work, "source.m4a")) and os.path.exists(os.path.join(work, "space.json"))):
        if run([sys.executable, K, "fetch", "--work", work, f"https://x.com/i/spaces/{sid}"], os.path.join(work, "fetch.log")): return 2
    M.mark("fetch", seconds=round(time.time() - t0))
    box = {}; th = threading.Thread(target=lambda: box.update(sp=build_space(work))); th.start()   # photos while Deepgram runs
    import lib_deepgram as DG
    from lib.cloud_asr import ASRError
    t0 = time.time()
    try: dg = DG.transcribe(work, log=log)
    except ASRError as ex: M.warn(str(ex)); return 2
    th.join(); sp = box["sp"]
    M.mark("transcribe", engine=dg["engine"], seconds=round(time.time() - t0), audio_min=round(dg["duration"] / 60, 1))
    log(f"{sp.get('title')} | {sp.get('date')} | {sp['length']} | hosts {sp['hosts']} {sp['cohosts']}")
    words = dg["words"]
    if not words: M.warn("no words transcribed"); return 2
    capw = caption_hint(work, words, a.blind)
    assert len(capw) == len(words) == len(dg["voices"]), "alignment length mismatch"
    wjson(os.path.join(work, "voices.json"), [dict(s=w["s"], e=w["e"], w=w["w"], voice=v, caption=c)
                                              for w, v, c in zip(words, dg["voices"], capw)], indent=None)
    finish(work, sp, a, M)
    if any(capw): learn_prints(work); M.mark("voiceprints")
    return 0

def cmd_name(a):
    work = os.path.abspath(a.work); sp = json.load(open(os.path.join(work, "space.json"))); finish(work, sp, a, Manifest(work)); return 0

def learn_prints(work):
    """Quick final step when X captions exist: update shared voiceprints (never the shared account)."""
    try:
        import naming as N, soundfile as sf
        audio, sr = sf.read(N.wav16(work), dtype="float32"); N.learn_from_captions(work, audio, sr, log=log)
    except Exception as ex: log(f"voiceprint learning skipped: {ex}")

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); sub = ap.add_subparsers(dest="cmd", required=True)
    for c in ("prep", "name"):
        p = sub.add_parser(c); p.add_argument("--crew", default=CREW); p.add_argument("--blind", action="store_true", help="hide X captions from naming")
        p.add_argument("src") if c == "prep" else p.add_argument("--work", required=True)
        if c == "prep": p.add_argument("--work")
    a = ap.parse_args(); sys.exit({"prep": cmd_prep, "name": cmd_name}[a.cmd](a) or 0)
