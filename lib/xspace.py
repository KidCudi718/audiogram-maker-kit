"""Who spoke when, from X itself: the Space's live captions (replay chat history, message type 45) carry the speaker's
handle and a timestamp. fetch saves them; `speakers` matches them to the transcript and tags every word.
Nothing here guesses: speech with no matching caption stays "unknown" so the bot can ask about just that stretch."""
import json, os, re, statistics, urllib.request
from difflib import SequenceMatcher

def _ie():
    import yt_dlp
    from yt_dlp.extractor.twitter import TwitterSpacesIE
    return TwitterSpacesIE(yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True}))

def _post(url, body):
    r = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"})
    return json.loads(urllib.request.urlopen(r, timeout=30).read())

def fetch_captions(space_id, out_json, log=print):
    """Save X's live captions for a Space replay. Returns the number saved (0 if the Space has none or X changed the API)."""
    try:
        ie = _ie(); d = ie._call_graphql_api("HPEisOmj1epUNLCWTYhUWw/AudioSpaceById", space_id)["audioSpace"]; md = d["metadata"]
        st = ie._call_api(f"live_video_stream/status/{md['media_key']}", md["media_key"])
        if not st.get("chatToken"): log("no speaker captions for this Space (no chat token)"); return 0
        acc = _post("https://proxsee.pscp.tv/api/v2/accessChatPublic", {"chat_token": st["chatToken"]})
        ep = acc.get("replay_endpoint") or acc["endpoint"]; tok = acc.get("replay_access_token") or acc["access_token"]
        msgs, cur = [], ""
        for _ in range(200):
            h = _post(ep + "/chatapi/v1/history", {"access_token": tok, "cursor": cur, "limit": 1000, "since": None, "quick_get": True})
            msgs += h.get("messages") or []; cur = h.get("cursor")
            if not cur: break
    except Exception as ex:
        log(f"no speaker captions for this Space ({type(ex).__name__})"); return 0
    caps = []
    for m in msgs:
        try: b = json.loads(json.loads(m["payload"]).get("body") or "{}")
        except Exception: continue
        if b.get("type") == 45 and b.get("final", True) and b.get("username") and (b.get("body") or "").strip():
            caps.append(dict(handle="@" + b["username"], name=b.get("displayName") or "", twitter_id=b.get("twitter_id"),
                             ts_ms=b["timestamp"], text=b["body"].strip()))
    caps.sort(key=lambda c: c["ts_ms"])
    if not caps: log("no speaker captions for this Space (captions were off)"); return 0
    json.dump({"space_id": space_id, "started_ms": md["started_at"], "offset_s": None, "captions": caps}, open(out_json, "w"), indent=1)
    return len(caps)

def _tok(w): return re.sub(r"[^a-z0-9]", "", w.lower())

def tag(work, log=print):
    """Match captions to transcript words. Writes speakers.json (turns with handles, 'unknown' where no caption matched)
    and fills each caption's start and end in audio time back into x_captions.json."""
    xc_p = os.path.join(work, "x_captions.json"); XC = json.load(open(xc_p))
    tx = json.load(open(os.path.join(work, "transcript.json")))
    W = [dict(s=w["s"], e=w["e"], t=_tok(w["w"])) for sg in tx for w in sg["words"]]; W = [w for w in W if w["t"]]
    toks = [w["t"] for w in W]; idx = {}
    for i in range(len(toks) - 3): idx.setdefault(tuple(toks[i:i + 4]), []).append(i)
    caps = XC["captions"]
    for c in caps: c.pop("start", None); c.pop("end", None)
    raw = [(c["ts_ms"] - XC["started_ms"]) / 1000 for c in caps]
    # 1. this Space's caption delay: caption timestamps mark the end of what was said
    ds = []
    for c, r in zip(caps, raw):
        ct = [x for x in map(_tok, c["text"].split()) if x]
        if len(ct) >= 6:
            hit = idx.get(tuple(ct[-4:]), [])
            if len(hit) == 1: ds.append(r - W[hit[0] + 3]["e"])
    if len(ds) < 3: log("speakers: too few caption matches to line up with the audio; speakers stay unknown"); return None
    off = statistics.median(ds); good = [d for d in ds if abs(d - off) < 3]; off = statistics.median(good)
    # 2. each caption claims the transcript words it matches near its time
    spk = [None] * len(W); starts = [w["s"] for w in W]
    import bisect
    for c, r in zip(caps, raw):
        ct = [x for x in map(_tok, c["text"].split()) if x]
        if not ct: continue
        end = r - off   # long captions are stamped before they finish, so search both sides
        a = bisect.bisect_left(starts, end - (0.5 * len(ct) + 15)); b = bisect.bisect_right(starts, end + 0.5 * len(ct) + 5)
        win = toks[a:b]; sm = SequenceMatcher(None, ct, win, autojunk=False)
        hits = [a + blk.b + k for blk in sm.get_matching_blocks() if blk.size >= 2 for k in range(blk.size)]
        if len(hits) < max(2, 0.3 * len(ct)): continue
        i0, i1 = min(hits), max(hits)
        for i in range(i0, i1 + 1):
            if spk[i] is None: spk[i] = c["handle"]
        c["start"], c["end"] = round(W[i0]["s"], 2), round(W[i1]["e"], 2)
    # 3. untagged words between two stretches of the same speaker (under 10 s) are that speaker; other untagged bits
    #    under 1.5 s belong to the neighbour
    i = 0
    while i < len(W):
        if spk[i] is None:
            j = i
            while j < len(W) and spk[j] is None: j += 1
            prev = spk[i - 1] if i > 0 else None; nxt = spk[j] if j < len(W) else None
            span = W[j - 1]["e"] - W[i]["s"]
            if (prev and prev == nxt and span <= 10) or ((prev or nxt) and span <= 1.5):
                for k in range(i, j): spk[k] = prev or nxt
            i = j
        else: i += 1
    turns = []
    for w, s in zip(W, spk):
        s = s or "unknown"
        if turns and turns[-1][2] == s and w["s"] - turns[-1][1] < 4: turns[-1][1] = round(w["e"], 2)
        else: turns.append([round(w["s"], 2), round(w["e"], 2), s])
    dur = sum(w["e"] - w["s"] for w in W) or 1; tagged = sum(w["e"] - w["s"] for w, s in zip(W, spk) if s)
    XC["offset_s"] = round(off, 2); json.dump(XC, open(xc_p, "w"), indent=1)
    res = dict(offset_s=round(off, 2), offset_matches=len(good), coverage=round(tagged / dur, 3), turns=turns)
    json.dump(res, open(os.path.join(work, "speakers.json"), "w"), indent=1)
    return res

def speaker_at(turns, t):
    for s, e, h in turns:
        if s - 0.05 <= t <= e + 0.05: return h
    return "unknown"
