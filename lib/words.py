"""Words, caption edits, speaker turns, phrases and karaoke states for one clip.
Caption edits change the captions only. The audio is never edited."""
import json, os, re

def clip_window(clip):
    st = float(clip["start"]); en = float(clip["end"]); return st, en, round(en - st, 3)

def turns(clip):
    st, _, _ = clip_window(clip)
    tt = clip.get("turns") or [[st, clip["speaker"]]]
    return sorted([(float(t) - st, s) for t, s in tt])

def speaker_at(TURNS, t):
    cur = TURNS[0][1]
    for s, k in TURNS:
        if t >= s - 1e-6: cur = k
    return cur

def apply_fixes(ws, fixes):
    """fixes: [["old words", "new words"], ...]. Same word count keeps each word's timing."""
    toks = [w["w"] for w in ws]
    for old, new in fixes:
        o = old.split(" "); n = new.split(" ") if new else []
        i = 0
        while i <= len(toks) - len(o):
            if toks[i:i + len(o)] == o:
                seg = ws[i:i + len(o)]
                if len(n) == len(o):
                    for w, t in zip(seg, n): w["w"] = t
                else:
                    s, e = seg[0]["s"], seg[-1]["e"]; k = max(1, len(n)); d = (e - s) / k
                    ws[i:i + len(o)] = [dict(s=s + j * d, e=s + (j + 1) * d, w=t) for j, t in enumerate(n)]
                toks = [w["w"] for w in ws]; i += max(1, len(n))
            else: i += 1
    return ws

def apply_drops(ws, drops):
    """drops: token patterns like "just [like] having". Bracketed tokens leave the captions."""
    used = []
    for pat in drops:
        pt = pat.split(" "); core = [t[1:-1] if t.startswith("[") and t.endswith("]") else t for t in pt]
        kill = [t.startswith("[") and t.endswith("]") for t in pt]; i = 0; hit = False
        while i <= len(ws) - len(pt):
            if [w["w"] for w in ws[i:i + len(pt)]] == core:
                ws[i:i + len(pt)] = [w for w, k in zip(ws[i:i + len(pt)], kill) if not k]; i += sum(1 for k in kill if not k); hit = True
            else: i += 1
        used.append((pat, hit))
    return ws, used

def load_segments(project_dir, project, clip_id):
    p = os.path.join(project_dir, "tx", clip_id + ".json")
    if os.path.exists(p): return json.load(open(p))
    return json.load(open(os.path.join(project_dir, project["episode"].get("transcript", "transcript.json"))))

def load_words(project_dir, project, brand, clip_id, report=None):
    clip = project["clips"][clip_id]; ST, EN, DUR = clip_window(clip); TURNS = turns(clip)
    ws = []
    for s in load_segments(project_dir, project, clip_id):
        for w in s["words"]:
            if w["s"] >= ST - 0.05 and w["e"] <= EN + 0.15 and w["s"] < EN - 0.1:
                t = w["w"].strip()
                if t: ws.append(dict(s=w["s"] - ST, e=min(w["e"], EN) - ST, w=t))
    out = []
    for w in ws:   # transcript repeats
        if out and w["s"] < out[-1]["e"] - 0.05 and w["w"] == out[-1]["w"]: continue
        out.append(w)
    ws = out
    terms = brand.get("spelling", []) + project["episode"].get("spelling", [])
    for w in ws:
        t = w["w"]
        for term in terms:   # fix brand / name capitalisation, e.g. "lumen" -> "Lumen"
            t = re.sub(r"(?i)^" + re.escape(term) + r"(?=$|[^A-Za-z0-9])", term, t)
        t = t.replace("\u2014", ",").replace("\u2013", "-")   # never an em dash in captions
        w["w"] = t
    ws, used = apply_drops(ws, clip.get("drops", []))
    ws = apply_fixes(ws, clip.get("fixes", []))
    ws = [w for w in ws if w["w"]]
    for w in ws: w["spk"] = speaker_at(TURNS, (w["s"] + w["e"]) / 2 if w["e"] - w["s"] < 1.5 else w["s"] + 0.1)
    if report is not None: report["drops"] = used
    return ws

SOFT = set("a an the and or but so to of in on at for with from by as that is are was were be i we you they it my our your their this these those have has had".split())

DANGLE = SOFT | set("will would can could should shall may might must just very into about than if not do does did am been "
                     "being because when which who what where how".split())

def _split_score(ph, j, n):
    w = ph[j - 1]["w"]; nx = ph[j]["w"].lower().strip(",.")
    sc = -0.45 * abs(j - n / 2)
    if re.search(r"[,;:]$", w): sc += 3
    elif nx in SOFT | {"like", "because", "but", "so", "when", "which", "who"} and w.lower().strip(",.") not in SOFT: sc += 1.2
    if w.lower().strip(",.") in SOFT: sc -= 1.5
    return sc

def _split(ph, mn, mx):
    if len(ph) <= mx: return [ph]
    js = list(range(mn, len(ph) - mn + 1)) or list(range(1, len(ph)))
    ok = [j for j in js if ph[j - 1]["w"].lower().strip(",.") not in DANGLE or re.search(r"[,;:.?!]$", ph[j - 1]["w"])]
    js = ok or js   # never end a caption on a dangling word ("will", "the", "from") when a natural break exists
    j = max(js, key=lambda j: _split_score(ph, j, len(ph)))
    return _split(ph[:j], mn, mx) + _split(ph[j:], mn, mx)

def phrases(ws, mn=4, mx=8):
    sents = []; cur = []
    for k, w in enumerate(ws):
        cur.append(w); nxt = ws[k + 1] if k + 1 < len(ws) else None
        if nxt is None or re.search(r"[.?!]$", w["w"]) or nxt["spk"] != w["spk"] or nxt["s"] - w["e"] > 1.2:
            sents.append(cur); cur = []
    i = 0
    while i < len(sents):
        sct = sents[i]
        if len(sct) < mn:
            opts = []
            if i > 0 and len(sents[i - 1]) + len(sct) <= mx and sents[i - 1][-1]["spk"] == sct[0]["spk"]: opts.append(i - 1)
            if i + 1 < len(sents) and len(sents[i + 1]) + len(sct) <= mx and sents[i + 1][0]["spk"] == sct[-1]["spk"]: opts.append(i)
            if opts:
                a = min(opts, key=lambda a: len(sents[a]) + len(sents[a + 1])); sents[a:a + 2] = [sents[a] + sents[a + 1]]; i = max(0, a - 1); continue
        i += 1
    out = []
    for sct in sents: out += _split(sct, mn, mx)
    return out

def times(phs, DUR):
    tt = []
    for i, c in enumerate(phs):
        s = c[0]["s"] if i else 0.0   # first phrase is on screen at frame 0
        if i + 1 < len(phs):
            n = phs[i + 1][0]["s"]; e = n if n - c[-1]["e"] < 1.2 else c[-1]["e"] + 0.5
        else: e = DUR
        tt.append((round(s, 3), round(e, 3)))
    return tt

def karaoke_states(ph, s, e):
    """[(t0,t1,cur,spoken)]: each word lit from its start to the next word's start (or its end + 0.25 s)."""
    out = []; t = s
    for i, w in enumerate(ph):
        ws_ = max(s, w["s"]); nxt = ph[i + 1]["s"] if i + 1 < len(ph) else None
        if ws_ > t + 1e-3: out.append((t, ws_, None, i))
        we = min(e, nxt if (nxt is not None and nxt - w["e"] < 0.35) else w["e"] + 0.25)
        if nxt is not None: we = min(we, nxt)
        we = max(we, ws_ + 1 / 30)
        out.append((ws_, we, i, i)); t = we
    if t < e - 1e-3: out.append((t, e, None, len(ph)))
    return [(round(a, 3), round(b, 3), c, d) for a, b, c, d in out if b > a]

def norm_tok(w): return re.sub(r"[^\w'$%]", "", w).lower()

def key_index(ph, acc_keys):
    """Only the clip's chosen accent words get the accent colour. No automatic picks."""
    for i, w in enumerate(ph):
        if norm_tok(w["w"]) in acc_keys: return i
    return None

def payoff_index(phs):
    for i in range(len(phs) - 1, -1, -1):
        if re.search(r"[.?!]$", phs[i][-1]["w"]): return i
    return len(phs) - 1

def build_phrases(ws, clip, mn=4, mx=8):
    phs = phrases(ws, mn, mx)
    n = int(clip.get("payoff_words", 0) or 0)   # last N words become the payoff line
    if not n:   # default: the whole last sentence, so the payoff never starts mid sentence (if it is 12 words or less)
        k = len(ws)
        while k > 0 and not (k < len(ws) and re.search(r"[.?!]$", ws[k - 1]["w"])): k -= 1
        if 0 < len(ws) - k <= 12 and k > 0: n = len(ws) - k
    if n and n < len(ws): phs = phrases(ws[:-n], mn, mx) + [ws[-n:]]
    return phs
