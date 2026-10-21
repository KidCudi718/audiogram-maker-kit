#!/usr/bin/env python3
"""space_moments: clip and audiogram helpers that run AFTER space_prep (moment scoring, clip speaker turns).

  python3 space_moments.py score --work DIR                # moments_scored.json: heuristic 1-10 (hook, standalone, payoff)
  python3 space_moments.py clip-turns --work DIR --clip ID # set a kit clip's speaker turns from speakers_auto.json
"""
import argparse, json, os, re, sys
KIT = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, KIT)
from space_prep import log, mmss, wjson, join_words
HOOK = re.compile(r"\b(\d[\d,.]*|million|billion|percent|never|always|nobody|everyone|biggest|best|worst|secret|truth|problem|mistake|why|how|what if|here's|the thing|listen|honestly|crazy|insane)\b", re.I)
WEAK_START = re.compile(r"^(and|but|so|because|or|which|that|it|he|she|they|this|um|uh|yeah|like|also)\b", re.I)
def score_moments(work, lo=20, hi=45, top=15):
    vw = json.load(open(os.path.join(work, "voices.json")))
    W = [dict(s=x["s"], e=x["e"], w=x["w"].strip()) for x in vw]; V = [x["voice"] for x in vw]
    starts = [i for i, w in enumerate(W) if i == 0 or re.search(r"[.?!]$", W[i-1]["w"]) or w["s"] - W[i-1]["e"] > 0.8]
    cands = []
    for i in starts:
        if V[i] == "unknown": continue
        for j in range(i, len(W)):
            d = W[j]["e"] - W[i]["s"]
            if d > hi or V[j] != V[i]: break
            if d < lo or not re.search(r"[.?!]$", W[j]["w"]): continue
            if j + 1 < len(W) and W[j+1]["s"] - W[j]["e"] < 0.4 and V[j+1] == V[i]: continue
            txt = join_words([" " + w["w"] for w in W[i:j+1]]); h3 = " ".join(w["w"] for w in W[i:j+1] if w["s"] < W[i]["s"] + 3)
            tail = " ".join(w["w"] for w in W[max(i, j-12):j+1])
            hook = min(4, 1 + 2*bool(HOOK.search(h3)) + bool("?" in h3) + bool(re.match(r"(i|we|you|the|if|when|here)\b", h3, re.I)))
            alone = 3 - bool(WEAK_START.match(txt)) * 2 - (txt.count("?") > 2)
            pay = 1 + bool(HOOK.search(tail)) + bool(re.search(r"\b(so|that's why|the point|which means|bottom line|at the end of the day|you're gonna|you have to|you need)\b", tail, re.I))
            cands.append(dict(voice=V[i], start=round(W[i]["s"], 2), end=round(W[j]["e"] + 0.4, 2), start_mmss=mmss(W[i]["s"]),
                              end_mmss=mmss(W[j]["e"] + 0.4), score=max(1, min(10, hook + alone + pay)),
                              reason=f"hook {hook}/4, standalone {alone}/3, payoff {pay}/3 (heuristic)", quote=txt))
    cands.sort(key=lambda c: (-c["score"], -(c["end"]-c["start"]))); out = []
    for c in cands:
        if all(c["end"] <= o["start"] or c["start"] >= o["end"] for o in out): out.append(c)
        if len(out) >= top: break
    wjson(os.path.join(work, "moments_scored.json"), out); log(f"moments_scored.json: {len(out)} heuristic candidates"); return out

def cmd_clip_turns(a):
    work = a.work; res = json.load(open(os.path.join(work, "speakers_auto.json")))["assignments"]
    pj_p = os.path.join(work, "project.json"); pj = json.load(open(pj_p)); c = pj["clips"][a.clip]
    T = json.load(open(os.path.join(work, "transcript_labeled.json")))["turns"]; turns = []
    for t in T:
        if t["end"] <= c["start"] or t["start"] >= c["end"]: continue
        h = t["candidate_handle"]
        if not h: log(f"UNNAMED voice {t['voice']} at {mmss(t['start'])}: ask the user before rendering"); return 2
        if not turns or turns[-1][1] != h[1:].lower(): turns.append([round(max(t["start"], c["start"]), 2), h[1:].lower()])
    c["turns"] = turns; c["speaker_guess"] = False; wjson(pj_p, pj); log("turns:", turns); return 0

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("score"); p.add_argument("--work", required=True)
    p = sub.add_parser("clip-turns"); p.add_argument("--work", required=True); p.add_argument("--clip", required=True)
    a = ap.parse_args(); sys.exit({"score": lambda a: score_moments(a.work) and 0, "clip-turns": cmd_clip_turns}[a.cmd](a) or 0)
