"""Automatic speaker naming for space_prep (no manual table).
Evidence per (voice, person): self_intro, addressed_then_spoke / host handoff, join-time proximity, talk-time vs role,
and ECAPA voiceprints ($AG_VOICEPRINTS/<handle>.npy, default ~/.config/audiogram-maker/voiceprints; starts empty).
Join-time gating: X's joined_utc is the person's LATEST join. A voice that speaks before that time only excludes the person
when the join data looks trustworthy (no host or co-host would be excluded from their own first turns); otherwise join
times are used as soft evidence and a warning is logged. Assignment is one-to-one (Hungarian). A person may own a second
voice only if the two never overlap in time and their embeddings are >= MERGE_SIM similar (split voice merge)."""
import json, os, re, collections
import numpy as np
# Optional shared host account (env AG_SHARED_ACCOUNT=@handle, AG_SHARED_USERS=@a,@b). Off by default.
SHARED = os.environ.get("AG_SHARED_ACCOUNT") or None
SHARED_USERS = {h.strip() for h in os.environ.get("AG_SHARED_USERS", "").split(",") if h.strip()}
VP = os.environ.get("AG_VOICEPRINTS") or os.path.expanduser("~/.config/audiogram-maker/voiceprints"); MERGE_SIM = 0.72; MIN_SCORE = 2.0
# Optional name scrub: env AG_SCRUB_REGEX (names never written) replaced by AG_SCRUB_REPL. Off by default.
_RX = os.environ.get("AG_SCRUB_REGEX"); BANNED = re.compile(_RX, re.I) if _RX else None
def scrub(s, repl=None):
    if not BANNED: return s
    return BANNED.sub(repl or os.environ.get("AG_SCRUB_REPL", "[name removed]"), s)

def wav16(work):
    import subprocess
    wav = os.path.join(work, "build", "mono16k.wav"); os.makedirs(os.path.dirname(wav), exist_ok=True)
    if not os.path.exists(wav):
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", os.path.join(work, "source.m4a"), "-ac", "1", "-ar", "16000", wav + ".tmp.wav"], check=True)
        os.replace(wav + ".tmp.wav", wav)
    return wav

_enc = None
def encoder():
    global _enc
    if _enc is None:
        from speechbrain.inference.speaker import EncoderClassifier
        _enc = EncoderClassifier.from_hparams(source="speechbrain/spkrec-ecapa-voxceleb", savedir=os.path.expanduser("~/.cache/sb_ecapa"), run_opts={"device": "cpu"})
    return _enc
def embed(audio, sr, spans, cap=60):
    import torch
    spans = sorted(spans, key=lambda x: -(x[1]-x[0]))[:cap]; spans = [(s, min(e, s+6)) for s, e in spans if e - s >= 1.2]
    if not spans: return None
    out = []
    for s, e in spans:
        x = torch.from_numpy(audio[int(s*sr):int(e*sr)].copy()).unsqueeze(0)
        with torch.no_grad(): out.append(encoder().encode_batch(x).squeeze().numpy())
    v = np.mean([o/np.linalg.norm(o) for o in out], 0); return v / np.linalg.norm(v)

def units(recs, voice=None, key="voice"):
    U = []; cur = None
    for r in recs:
        if voice is not None and r[key] != voice: cur = None; continue
        if cur and r["s"] - cur[1] < 0.4 and r[key] == cur[2]: cur[1] = r["e"]
        else: cur = [r["s"], r["e"], r[key]]; U.append(cur)
    return [(s, e) for s, e, _ in U]

def build_voiceprints(work, ref_json, audio, sr, log=print):
    """Per-person voiceprints from a hand-reviewed transcript (list of {start, spk 'Name (@handle...)', auto})."""
    os.makedirs(VP, exist_ok=True); x = json.load(open(ref_json)); spans = collections.defaultdict(list)
    for i, t in enumerate(x):
        m = re.search(r"\(@(\w+)", t["spk"])
        if t.get("auto") or not m: continue
        e = x[i+1]["start"] if i + 1 < len(x) else t["start"] + 30
        for s in np.arange(t["start"] + 0.5, e - 3, 4.0): spans["@" + m.group(1)].append((float(s), float(s) + 4))
    made = []
    for h, sp in spans.items():
        v = embed(audio, sr, sp, cap=40)
        if v is not None: np.save(os.path.join(VP, h.lstrip("@") + ".npy"), v); made.append(h)
    log(f"voiceprints saved: {made}"); return made

def load_voiceprints(handles):
    return {h: np.load(os.path.join(VP, h.lstrip("@") + ".npy")) for h in handles if os.path.exists(os.path.join(VP, h.lstrip("@") + ".npy"))}

def auto_name(T, recs, people, ev, start_utc, audio, sr, use_voiceprints=True, log=print):
    """T turns, recs per-word records, people [{handle,name,role,joined_utc}], ev clues {voice:[(h,kind,w,t,q)]}.
    Returns {voice: dict(handle, score, sources, evidence)} and the warnings."""
    from scipy.optimize import linear_sum_assignment
    warns = []; voices = [v for v in dict.fromkeys(t["voice"] for t in T) if v != "unknown"]
    talk = collections.Counter(); first = {}
    for t in T: talk[t["voice"]] += t["end"] - t["start"]; first.setdefault(t["voice"], t["start"])
    H = [p["handle"] for p in people]; S = np.zeros((len(voices), len(H))); why = collections.defaultdict(list)
    vi = {v: i for i, v in enumerate(voices)}; hi = {h: j for j, h in enumerate(H)}
    for v, items in ev.items():
        for h, k, w, tt, q in items:
            if v in vi and h in hi: S[vi[v], hi[h]] += w; why[(v, h)].append(f"{k} at {int(tt//60)}:{tt%60:04.1f}: \"{q.strip()[:90]}\"")
    # join times (latest join on X)
    join = {p["handle"]: (p["joined_utc"] - start_utc) for p in people if p.get("joined_utc") and start_utc}
    hosts = [p["handle"] for p in people if p.get("role") in ("host", "cohost")]
    early = min(first.values()) if first else 0
    trust = not any(join.get(h, 0) > early + 120 for h in hosts)
    if not trust: warns.append("X join times are latest re-joins (a host 'joined' after the Space was well underway): used as soft evidence only")
    for v in voices:
        for h, j in join.items():
            if first[v] < j - 5 and trust: S[vi[v], hi[h]] = -99; why[(v, h)].append("excluded: speaks before joining")
            elif 0 <= first[v] - j < 90: S[vi[v], hi[h]] += 1; why[(v, h)].append(f"first speech {first[v]-j:.0f}s after joining")
    # talk time vs role: the two most talkative voices lean to hosts/co-hosts
    for v, _ in talk.most_common(3):
        if v in vi:
            for h in hosts: S[vi[v], hi[h]] += 0.5; why[(v, h)].append("top talk time, host role")
    # voiceprints
    cents = {}
    for v in voices: cents[v] = embed(audio, sr, units(recs, v))
    if use_voiceprints:
        vp = load_voiceprints(H)
        for v in voices:
            if cents[v] is None: continue
            for h, p in vp.items():
                sim = float(cents[v] @ p)
                if sim >= 0.45: S[vi[v], hi[h]] += round((sim - 0.4) * 20, 1); why[(v, h)].append(f"voiceprint match {sim:.2f}")
    r, c = linear_sum_assignment(-S); out = {}
    for i, j in zip(r, c):
        if S[i, j] >= MIN_SCORE:
            srcs = sorted({re.sub(r" at .*|:.*| \d.*", "", w).replace("first speech", "join_time").replace("top talk time, host role", "talk_role").replace("voiceprint match", "voiceprint") for w in why[(voices[i], H[j])] if not w.startswith("excluded")})
            out[voices[i]] = dict(handle=H[j], score=round(float(S[i, j]), 1), sources=srcs, evidence=why[(voices[i], H[j])][:4])
    # optional shared host account: a voice matching a shared user who is not listed, while the shared account is,
    # is labelled as the shared account. Never build a print for the shared account.
    listed = {p["handle"] for p in people if p.get("role") in ("host", "cohost", "speaker")}
    if SHARED and SHARED in listed:
        for v, o in out.items():
            if o["handle"] in SHARED_USERS and o["handle"] not in listed:
                o["evidence"] = [f"matches {o['handle']}, who is not listed; shared account {SHARED} is"] + o["evidence"]
                o["handle"] = SHARED; o["sources"] = sorted(set(o["sources"]) | {"shared_account_rule"})
    # merge split voices: an unassigned voice joins an assigned one if similar and never overlapping
    spans = {v: [(t["start"], t["end"]) for t in T if t["voice"] == v] for v in voices}
    for v in voices:
        if v in out or cents[v] is None: continue
        best = max(((float(cents[v] @ cents[u]), u) for u in out if cents[u] is not None), default=(0, None))
        if best[0] >= MERGE_SIM and not any(a < d and c_ < b for a, b in spans[v] for c_, d in spans[best[1]]):
            o = out[best[1]]; out[v] = dict(handle=o["handle"], score=o["score"], sources=["merged_split_voice"],
                                            evidence=[f"same person as {best[1]} (embedding similarity {best[0]:.2f}, never overlapping)"])
    return out, warns, S, voices, H

# ---------- learning voiceprints from X captions ----------
def _idx():
    p = os.path.join(VP, "index.json"); return json.load(open(p)) if os.path.exists(p) else {}
def save_print(h, v, n, src, log=print):
    """Weighted average with any existing print (weight = seconds of speech)."""
    os.makedirs(VP, exist_ok=True); idx = _idx(); f = os.path.join(VP, h.lstrip("@") + ".npy"); e = idx.get(h, {})
    if os.path.exists(f):
        old_n = e.get("seconds", 60.0); v = np.load(f) * old_n + v * n; v /= np.linalg.norm(v); n = old_n + n
    np.save(f + ".tmp.npy", v); os.replace(f + ".tmp.npy", f)
    idx[h] = dict(seconds=round(n), sources=sorted(set(e.get("sources", []) + [src]))); p = os.path.join(VP, "index.json")
    json.dump(idx, open(p + ".tmp", "w"), indent=1); os.replace(p + ".tmp", p); log(f"voiceprint {h}: {round(n)}s from {idx[h]['sources']}")

def learn_from_captions(work, audio, sr, skip=None, min_s=30.0, log=print):
    """Caption-attributed clean segments -> per-handle prints. Uses speakers.json (captions matched to word timing) when
    present, else raw caption times (approximate: only the middle of single-speaker caption spans is used)."""
    spans = collections.defaultdict(list); src = os.path.basename(os.path.abspath(work))
    sp_p = os.path.join(work, "speakers.json")
    if os.path.exists(sp_p) and json.load(open(sp_p)).get("coverage", 0) >= 0.5:
        T = json.load(open(sp_p))["turns"]
        for i, (s, e, h) in enumerate(T):
            if h == "unknown" or e - s < 2.5: continue
            spans[h].append((s + 0.3, e - 0.3))
    else:
        xc = json.load(open(os.path.join(work, "x_captions.json"))); caps = sorted(xc["captions"], key=lambda c: c["ts_ms"])
        st = xc.get("started_ms"); off = xc.get("offset_s") or 0; prev_end, prev_h = 0, None
        for c in caps:
            end = (c["ts_ms"] - st) / 1000 - off; dur = len(c["text"].split()) / 2.7; beg = max(prev_end, end - dur)
            if end - beg >= 4 and (prev_h == c["handle"] or beg > prev_end + 0.5): spans[c["handle"]].append((beg + 1.0, end - 1.0))
            prev_end, prev_h = end, c["handle"]
    for h, sp in spans.items():
        tot = sum(e - s for s, e in sp)
        if (skip and h in skip) or (SHARED and h == SHARED) or tot < min_s: continue
        # clean: drop segments far from this speaker's own centroid
        segs = [(s, min(e, s + 6)) for s, e in sorted(sp, key=lambda x: -(x[1]-x[0]))[:40]]
        embs = []
        for s, e in segs:
            x = embed(audio, sr, [(s, e)], cap=1)
            if x is not None: embs.append(x)
        if len(embs) < 3: continue
        E = np.array(embs); c = E.mean(0); c /= np.linalg.norm(c); keep = E[E @ c >= 0.5]
        if len(keep) < 3: continue
        v = keep.mean(0); v /= np.linalg.norm(v); save_print(h, v, min(tot, 6.0 * len(keep)), src, log=log)
