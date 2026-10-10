#!/usr/bin/env python3
"""Audiogram Maker kit: one entry command.

  python3 audiogram.py doctor                                   check ffmpeg, Chrome, python deps, font
  python3 audiogram.py init      --work DIR --brand BRAND.json  create DIR/project.json (episode, people, clips)
  python3 audiogram.py fetch     --work DIR X_SPACE_LINK         download an X Space (x.com/i/spaces/ID, or a post with a Space)
                                                                 to DIR/source.m4a plus DIR/space.json (title, host, participants);
                                                                 any other link or a local file also works
  python3 audiogram.py pfp       --work DIR --key KEY --url PROFILE_IMAGE_URL [--name N --handle H]   speaker photo from X
  python3 audiogram.py transcribe --work DIR [--start S --end E] [--clip ID] [--model small.en]
  python3 audiogram.py speakers  --work DIR                      tag who spoke when from X's live captions (runs after transcribe)
  python3 audiogram.py quote     --work DIR --clip ID            the exact quote to show the user before rendering
  python3 audiogram.py make      --work DIR --clip ID --theme dark|light   master + layers + render + check
  python3 audiogram.py check     --work DIR FILE.mp4             QA stills and specs for any finished file

First run for a new user (no questions first):
  python3 audiogram.py brand-from-url  URL  --out BRAND_DIR       logo, colors, name from their website
  python3 audiogram.py brand-from-logo FILE --out BRAND_DIR [--name NAME]   from a logo (SVG/PNG/JPG/WebP/ICO) or brand kit PDF
  python3 audiogram.py sample --brand BRAND_DIR/brand_profile.json [--theme dark|light] [--still-only]
        about 30 second sample in their brand with the bundled demo voice: writes the preview still first, then the video
  python3 audiogram.py space-preview --work DIR [--space LINK] [--brand BRAND.json] [--space-title T] [--host @h --host-name N --host-pfp F]
        [--cohost @h --cohost-name N --cohost-pfp F] [--theme dark|light]
        one preview still of the user's own Space before any transcript: host photo in the ring, Space title, host and co-host
        in the footer; with no --brand the colours come from the host's photo and the host's name is the wordmark

Everything brand specific comes from the brand profile JSON. Nothing is hard coded."""
import argparse, json, os, re, shutil, subprocess, sys, time, urllib.request
KIT = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, KIT)
from lib import words as W

def log(*a): print(*a, flush=True)

def load_project(work):
    pj = json.load(open(os.path.join(work, "project.json")))
    bp = pj.get("brand_profile") or ""
    if not bp:   # fetch and transcribe work before any brand exists (the setup flow asks about colours while they run)
        return pj, {}, os.path.abspath(work)
    bp = bp if os.path.isabs(bp) else os.path.join(work, bp)
    brand = json.load(open(bp))
    return pj, brand, os.path.dirname(os.path.abspath(bp))

def source_path(work, pj):
    s = pj["episode"].get("source", "source.m4a"); return s if os.path.isabs(s) else os.path.join(work, s)

def mmss(t): return f"{int(t//60)}:{t%60:05.2f}"

def cmd_doctor(a):
    ok = True
    vf = os.path.join(KIT, "VERSION"); log(f"{'kit':14s} {open(vf).read().strip() if os.path.exists(vf) else 'unknown'}")
    for b in ("ffmpeg", "ffprobe"):
        p = shutil.which(b); log(f"{b:14s} {'ok ' + p if p else 'MISSING'}"); ok &= bool(p)
    ch = next((shutil.which(b) for b in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser") if shutil.which(b)), None)
    log(f"{'chrome':14s} {'ok ' + ch if ch else 'MISSING'}"); ok &= bool(ch)
    log(f"{'yt-dlp':14s} {'ok' if shutil.which('yt-dlp') else 'missing (only needed for links)'}")
    for m in ("numpy", "PIL", "faster_whisper", "bs4"):
        try: __import__(m); log(f"{m:14s} ok")
        except Exception: log(f"{m:14s} MISSING"); ok = False
    if ch:
        try:
            out = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True).stdout
            for f in ("loudnorm", "afftdn", "acompressor", "alimiter", "ebur128"):
                if f" {f} " not in out: log(f"ffmpeg filter {f} MISSING"); ok = False
        except Exception: pass
    log(f"{'font':14s} {'ok' if os.path.exists(os.path.join(KIT,'fonts','Inter-Variable.ttf')) else 'MISSING'}")
    dv = all(os.path.exists(os.path.join(KIT, "demo", f)) for f in ("demo_voice.m4a", "demo_transcript.json"))
    log(f"{'demo voice':14s} {'ok' if dv else 'MISSING (sample needs it)'}"); ok &= dv
    log("READY" if ok else "NOT READY: run kit/install.sh"); return 0 if ok else 1

def cmd_init(a):
    os.makedirs(a.work, exist_ok=True); p = os.path.join(a.work, "project.json")
    if os.path.exists(p):
        pj = json.load(open(p))
        if not pj.get("brand_profile"): pj["brand_profile"] = os.path.abspath(a.brand); json.dump(pj, open(p, "w"), indent=1); log("brand set in", p)
        else: log("project.json already exists, leaving it alone")
        return
    sp = os.path.join(a.work, "space.json"); info = json.load(open(sp)) if os.path.exists(sp) else None
    people = {"speaker_id": {"name": "Display Name", "handle": "@handle", "img": ""}}
    if info and info.get("host_handle"):   # the host is known from the Space itself; everyone else is matched later
        people = {info["host_handle"].lstrip("@").lower(): {"name": info.get("host_name") or "", "handle": info["host_handle"], "img": ""}}
    json.dump({"brand_profile": os.path.abspath(a.brand),
               "episode": {"title": (info or {}).get("title") or "Space title",
                           "subtitle": f"X Space · {info['date']}" if info and info.get("date") else "X Space · Month D, YYYY",
                           "source": "source.m4a", "transcript": "transcript.json", "spelling": []},
               "people": people, "clips": {}}, open(p, "w"), indent=1)
    log("wrote", p)

def cmd_pfp(a):
    """Save a speaker's X profile photo (full size) and name/handle into project.json people.<key>."""
    p = os.path.join(a.work, "project.json"); pj = json.load(open(p)); os.makedirs(os.path.join(a.work, "pfp"), exist_ok=True)
    url = re.sub(r"_(normal|bigger|mini|200x200)(\.\w+)$", r"_400x400\2", a.url)
    ext = os.path.splitext(url.split("?")[0])[1] or ".jpg"; dst = os.path.join(a.work, "pfp", a.key + ext)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try: data = urllib.request.urlopen(req, timeout=30).read()
    except Exception:
        req = urllib.request.Request(a.url, headers={"User-Agent": "Mozilla/5.0"}); data = urllib.request.urlopen(req, timeout=30).read()
    open(dst, "wb").write(data)
    from PIL import Image
    w, h = Image.open(dst).size
    person = pj["people"].get(a.key, {}); person["img"] = os.path.join("pfp", a.key + ext)
    if a.name: person["name"] = a.name
    if a.handle: person["handle"] = "@" + a.handle.lstrip("@")
    pj["people"][a.key] = person; pj["people"].pop("speaker_id", None); json.dump(pj, open(p, "w"), indent=1)
    log(f"pfp: {dst} ({w}x{h})" + ("  (small photo: it may look soft)" if min(w, h) < 200 else ""))

SPACE_RE = re.compile(r"(?:https?://)?(?:www\.|mobile\.)?(?:x|twitter)\.com/i/spaces/([A-Za-z0-9]{8,20})", re.I)
STATUS_RE = re.compile(r"(?:https?://)?(?:www\.|mobile\.)?(?:x|twitter)\.com/(?:[A-Za-z0-9_]{1,15}|i(?:/web)?)/status(?:es)?/(\d+)", re.I)

def ytdlp(*args, timeout=None):
    return subprocess.run(["yt-dlp", "--no-warnings", "--retries", "10", "--fragment-retries", "20", *args], capture_output=True, text=True, timeout=timeout)

def clean_title(t):
    """Space titles often carry emoji and decoration ("\U0001F399\ufe0fThe Vest Week Ever\u203c\ufe0f"). Keep the words only."""
    import unicodedata
    t = "".join(ch if (unicodedata.category(ch)[0] in "LNZ" or ch in "&'.,:!?#@$%+-/()") else " " for ch in t)
    t = re.sub(r"\s+", " ", t).strip(" -:|.,"); return t

def merge_project(work, info=None, **ep):
    """Create or update DIR/project.json with what we know about the Space (title, date, host, link)."""
    p = os.path.join(work, "project.json")
    pj = json.load(open(p)) if os.path.exists(p) else {"brand_profile": "", "episode": {"source": "source.m4a", "transcript": "transcript.json",
                                                                                     "spelling": []}, "people": {}, "clips": {}}
    e = pj.setdefault("episode", {})
    if info:
        pj["space"] = {k: info.get(k) for k in ("id", "url", "title", "title_raw", "host_name", "host_handle", "host_pfp_url", "date",
                                               "started_utc", "participants", "people")}
        for x in info.get("people") or []:   # every host and speaker X lists, with real handles and full size photos
            k = x["handle"][1:].lower(); per = pj["people"].setdefault(k, {"name": "", "handle": x["handle"], "img": ""})
            per["name"] = per.get("name") or x["name"]; per["handle"] = x["handle"]; per["role"] = x["role"]; per["pfp_url"] = x["pfp_url"]
        if info.get("title"): e["title"] = info["title"]
        if info.get("date"): e["subtitle"] = f"X Space · {info['date']}"
        if info.get("host_handle"):
            k = info["host_handle"].lstrip("@").lower(); per = pj["people"].setdefault(k, {"name": "", "handle": info["host_handle"], "img": ""})
            per["name"] = per.get("name") or info.get("host_name") or ""; pj["people"].pop("speaker_id", None)
    for k, v in ep.items():
        if v is not None: e[k] = v
    json.dump(pj, open(p, "w"), indent=1); return pj

def x_participants(space_id):
    """Hosts and speakers with their real X handles and photos, from the same public Space lookup yt-dlp uses.
    Maps display names like "SunnyD" to handles without guessing. Empty list if X changes the API."""
    try:
        import yt_dlp
        from yt_dlp.extractor.twitter import TwitterSpacesIE
        ydl = yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True}); ie = TwitterSpacesIE(ydl)
        d = ie._call_graphql_api("HPEisOmj1epUNLCWTYhUWw/AudioSpaceById", space_id)["audioSpace"]
    except Exception as ex:
        log(f"note: could not read the speaker list from X ({ex})"); return []
    out = []
    for role in ("admins", "speakers"):
        for u in (d.get("participants") or {}).get(role) or []:
            if not u.get("twitter_screen_name"): continue
            av = re.sub(r"_(normal|bigger|mini|reasonably_small|200x200)(\.\w+)$", r"_400x400\2", u.get("avatar_url") or "")
            out.append(dict(role="host" if role == "admins" else "speaker", name=u.get("display_name") or "",
                            handle="@" + u["twitter_screen_name"], twitter_id=(u.get("user") or {}).get("rest_id"), pfp_url=av,
                            joined_utc=round(u["start"] / 1000) if u.get("start") else None))
    return out

def space_info(url):
    """Metadata yt-dlp can see for a Space: title, host, start time, listed participants."""
    r = ytdlp("-J", "--skip-download", url, timeout=120)
    if r.returncode != 0: return None, (r.stderr or r.stdout).strip().splitlines()[-1:] or ["unknown error"]
    j = json.loads(r.stdout); desc = j.get("description") or ""
    parts = [p.strip() for p in re.sub(r"^.*?participated by\s*", "", desc).split(",") if p.strip()] if "participated by" in desc else []
    ts = j.get("release_timestamp") or j.get("timestamp")
    when = time.strftime("%b %-d, %Y", time.gmtime(ts)) if ts else ""
    return dict(id=j.get("id"), url=j.get("webpage_url") or url, title=clean_title(j.get("title") or ""), title_raw=j.get("title") or "",
                host_name=j.get("uploader"), host_pfp_url=j.get("thumbnail") or "",
                host_handle=("@" + j["uploader_id"]) if j.get("uploader_id") else None, started_utc=ts, date=when,
                live_status=j.get("live_status"), participants=parts, people=x_participants(j.get("id")) if j.get("id") else []), None

def cmd_fetch(a):
    """X Space links (x.com or twitter.com /i/spaces/<id>, with /peek or query strings, or a post that carries a Space),
    any other yt-dlp link, or a local file. Writes DIR/source.m4a, and DIR/space.json for Spaces."""
    os.makedirs(a.work, exist_ok=True); out = os.path.join(a.work, "source.m4a"); src = a.src.strip().strip("<>")
    if os.path.exists(src):
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", src, "-vn", "-c:a", "aac", "-b:a", "256k", out], check=True)
        log("source:", out); return
    m = SPACE_RE.search(src); info = None
    if m: src = f"https://x.com/i/spaces/{m.group(1)}"
    if m or STATUS_RE.search(src):
        info, err = space_info(src)
        sm = STATUS_RE.search(src) if not m else None
        if info is None and sm:   # a post that only links or quotes the Space: read the Space link from X's public embed data
            try:
                emb = urllib.request.urlopen(urllib.request.Request(f"https://cdn.syndication.twimg.com/tweet-result?id={sm.group(1)}&token=a",
                                             headers={"User-Agent": "Mozilla/5.0"}), timeout=20).read().decode("utf-8", "ignore")
                sid = re.search(r"spaces/([A-Za-z0-9]{8,20})", emb)
                if sid: src = f"https://x.com/i/spaces/{sid.group(1)}"; info, err = space_info(src)
            except Exception: pass
        if info is None and m:
            log("SPACE NOT AVAILABLE: " + " ".join(err) + "\nA Space can only be downloaded when the host recorded it and the replay is"
                " public. If the error is not 'not found', update the downloader once (python3 -m pip install --user -U yt-dlp) and"
                " try again. Otherwise ask the user for the replay link or an audio file."); return 2
        if info is None:
            log("NO SPACE FOUND IN THAT POST: " + " ".join(err) + "\nOpen the post with the X connector, find the x.com/i/spaces/<id> link"
                " in it (or in the quoted post), and run fetch with that link."); return 2
        if info.get("live_status") == "is_live":
            log("SPACE IS STILL LIVE: wait until it ends and the replay is up, then fetch again."); return 2
        src = f"https://x.com/i/spaces/{info['id']}" if info.get("id") else src
        json.dump(info, open(os.path.join(a.work, "space.json"), "w"), indent=1); merge_project(a.work, info)
        log(f"space: {info['title']} | host {info['host_name']} {info['host_handle']} | {info['date']} | "
            f"{len(info['participants'])} listed participants: " + ", ".join(info["participants"]))
        if info.get("people"): log("hosts and speakers from X: " + ", ".join(f"{x['name']} {x['handle']} ({x['role']})" for x in info["people"]))
    for f in os.listdir(a.work):
        if f.startswith("source.") and f != "source.m4a": os.remove(os.path.join(a.work, f))
    t0 = time.time()
    dl_log = os.path.join(a.work, "download.log"); log("downloading the audio (details in download.log)")
    with open(dl_log, "w") as lf:   # the HLS downloader prints a line per chunk: keep that out of the agent's view
        r = subprocess.run(["yt-dlp", "--no-warnings", "--retries", "10", "--fragment-retries", "20", "-N", "8", "-f", "bestaudio/best",
                            "-x", "--audio-format", "m4a", "-o", os.path.join(a.work, "source.%(ext)s"), src], stdout=lf, stderr=subprocess.STDOUT)
    if r.returncode != 0: log("".join(open(dl_log, errors="ignore").readlines()[-5:]))
    if r.returncode != 0 or not os.path.exists(out):
        log("DOWNLOAD FAILED: update the downloader once (python3 -m pip install --user -U yt-dlp, add --break-system-packages if pip asks)"
            " and try again; if it still fails, ask the user for the replay link or an audio file."); return 2
    d = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", out], capture_output=True, text=True).stdout or 0)
    log(f"source: {out} ({d / 60:.1f} min, downloaded in {time.time() - t0:.0f}s)")
    if info and info.get("id"):   # who spoke when: X's own live captions, matched to the transcript by `speakers`
        from lib.xspace import fetch_captions
        n = fetch_captions(info["id"], os.path.join(a.work, "x_captions.json"), log=log)
        if n: log(f"speaker captions from X: {n} (speakers are tagged automatically after transcribe)")

def cmd_transcribe(a):
    from lib.transcribe import transcribe
    pj, brand, _ = load_project(a.work); src = source_path(a.work, pj)
    if a.clip:
        c = pj["clips"][a.clip]; os.makedirs(os.path.join(a.work, "tx"), exist_ok=True)
        out = os.path.join(a.work, "tx", a.clip + ".json"); st = max(0, c["start"] - 4); en = c["end"] + 20
    else:
        out = os.path.join(a.work, pj["episode"].get("transcript", "transcript.json")); st, en = a.start, a.end
    transcribe(src, out, a.model, st, en, log=log)
    if not a.clip and os.path.exists(os.path.join(a.work, "x_captions.json")): cmd_speakers(a)

def cmd_speakers(a):
    """Tag every transcript word with its speaker's X handle from X's captions. Lists the stretches nobody could be matched to."""
    from lib.xspace import tag
    if not os.path.exists(os.path.join(a.work, "x_captions.json")):
        log("no X captions for this Space: map speakers from the transcript and ask the user"); return 0
    t = time.time(); r = tag(a.work, log=log)
    if not r: return 0
    p = os.path.join(a.work, "project.json"); pj = json.load(open(p)); xc = json.load(open(os.path.join(a.work, "x_captions.json")))
    names = {c["handle"]: c["name"] for c in xc["captions"]}
    for h in dict.fromkeys(x[2] for x in r["turns"] if x[2] != "unknown"):
        per = pj["people"].setdefault(h[1:].lower(), {"name": names.get(h, ""), "handle": h, "img": ""})
        per["handle"] = h; per["name"] = per.get("name") or names.get(h, "")
    json.dump(pj, open(p, "w"), indent=1)
    log(f"speakers: caption delay {r['offset_s']}s ({r['offset_matches']} matches), {r['coverage']:.0%} of speech tagged in {time.time() - t:.1f}s")
    gaps = [x for x in r["turns"] if x[2] == "unknown" and x[1] - x[0] >= 3]
    for s_, e_, _ in gaps: log(f"  unknown speaker {mmss(s_)} to {mmss(e_)}")
    if gaps: log("Ask the user only about the unknown stretches you want to clip.")

def fill_turns(work, pj, cid):
    """A clip without turns gets them from speakers.json. Returns False (and says where) if part of it has no known speaker."""
    c = pj["clips"][cid]
    if c.get("turns") or c.get("speaker"): return True
    sp = os.path.join(work, "speakers.json")
    if not os.path.exists(sp): log(f"clip {cid}: no turns and no speakers.json: set turns by hand"); return False
    from lib.xspace import speaker_at
    T = json.load(open(sp))["turns"]; st, en, _ = W.clip_window(c); turns, unk = [], []
    for sg in W.load_segments(work, pj, cid):
        for w in sg["words"]:
            if w["s"] >= st - 0.05 and w["s"] < en - 0.1:
                h = speaker_at(T, (w["s"] + w["e"]) / 2)
                if h == "unknown": unk.append(w["s"]); continue
                k = h[1:].lower()
                if not turns or turns[-1][1] != k: turns.append([round(st if not turns else w["s"], 3), k])
    if unk:
        log(f"clip {cid}: UNKNOWN SPEAKER {mmss(min(unk))} to {mmss(max(unk))}. Ask the user who it is, then set turns by hand."); return False
    if not turns: log(f"clip {cid}: no words in the window"); return False
    c["turns"] = turns; json.dump(pj, open(os.path.join(work, "project.json"), "w"), indent=1); return True

def ensure_photos(work, pj, keys):
    """Every speaker and footer host gets their real X photo from pfp_url (saved by fetch) before anything renders."""
    changed = False
    for k in keys:
        per = pj["people"].get(k) or {}
        if not per.get("img") and per.get("pfp_url"):
            f = _save_pfp(work, k, per["pfp_url"])
            if f: per["img"] = os.path.relpath(f, work); changed = True
    for h in pj["episode"].get("hosts") or []:
        per = pj["people"].get((h.get("handle") or "").lstrip("@").lower()) or {}
        if not h.get("img"):
            if not per.get("img") and per.get("pfp_url"):
                f = _save_pfp(work, h["handle"].lstrip("@").lower(), per["pfp_url"]); per["img"] = os.path.relpath(f, work) if f else ""
            if per.get("img"): h["img"] = os.path.join(work, per["img"]); changed = True
    if changed: json.dump(pj, open(os.path.join(work, "project.json"), "w"), indent=1)

def cmd_quote(a):
    pj, brand, _ = load_project(a.work)
    if not fill_turns(a.work, pj, a.clip): return 3
    c = pj["clips"][a.clip]; rep = {}
    ws = W.load_words(a.work, pj, brand, a.clip, rep); st, en, dur = W.clip_window(c)
    log(f"Clip {a.clip}: {mmss(st)} to {mmss(en)} ({dur:.1f}s)")
    TURNS = W.turns(c); names = [(pj["people"].get(s) or {}).get("name", s) + " " + (pj["people"].get(s) or {}).get("handle", "") for s in dict.fromkeys(s for _, s in TURNS)]
    log("Speaker(s): " + ", ".join(names))
    log("Caption text: " + " ".join(w["w"] for w in ws))
    segs = W.load_segments(a.work, pj, a.clip)
    after = [w["w"].strip() for s in segs for w in s["words"] if en + 0.05 < w["s"] < en + 20]
    log("Next 20s after the end (check the thought is finished): " + " ".join(after))
    cut = [w for sg in segs for w in sg["words"] if st - 0.05 <= w["s"] < en - 0.1 and w["e"] > en + 0.15]
    for w in cut: log(f"WARNING: the end cuts the word '{w['w'].strip()}' (ends at {mmss(w['e'])}): set end to {w['e'] + 0.4:.2f} or later")
    for pat, hit in rep.get("drops", []):
        if not hit: log(f"note: caption drop not matched: {pat}")

def next_name(path):
    if not os.path.exists(path): return path
    b, e = os.path.splitext(path); k = 2
    while os.path.exists(f"{b}_v{k}{e}"): k += 1
    return f"{b}_v{k}{e}"

def cmd_make(a):
    from lib.layers import Layers
    from lib.render import render
    from lib.master_audio import master
    from lib.check import check
    pj, brand, bdir = load_project(a.work)
    if not fill_turns(a.work, pj, a.clip): return 3
    c = pj["clips"][a.clip]; src = source_path(a.work, pj)
    ensure_photos(a.work, pj, [k for _, k in W.turns(c)])
    if not brand: log("NO BRAND YET: run space-preview (colours from the host's photo) or init --brand BRAND.json first"); return 2
    def has_photo(per):
        im = (per or {}).get("img") or ""
        return bool(im) and (bool(re.match(r"^(https?|data):", im)) or os.path.exists(im if os.path.isabs(im) else os.path.join(a.work, im))
                             or os.path.exists(os.path.join(bdir, im)))
    missing = [f"{k} ({pj['people'].get(k, {}).get('name') or k})" for k in dict.fromkeys(sp for _, sp in W.turns(c)) if not has_photo(pj["people"].get(k))]
    missing += [f"footer host {h.get('handle') or h.get('name')}" for h in (pj["episode"].get("hosts") or brand.get("hosts") or []) if not has_photo(h)]
    if missing and not getattr(a, "allow_initials", False):   # never ship an initials badge: every speaker and footer host needs their real profile picture
        log("NEED REAL PROFILE PICTURES (no initials badges): " + ", ".join(missing) + ". Confirm each person's X handle with the user,"
            " then run: pfp --work DIR --key KEY --url PROFILE_IMAGE_URL --name NAME --handle @HANDLE"); return 4
    st, en, dur = W.clip_window(c); theme = a.theme or brand.get("default_theme", "dark")
    bd = os.path.join(a.work, "build", a.clip); os.makedirs(bd, exist_ok=True)
    mw = os.path.join(bd, "master.wav"); t = time.time()
    if not os.path.exists(mw) or a.remaster:
        m = master(src, st, dur, mw); log(f"mastered: {m['before']['I']} -> {m['after']['I']} LUFS, TP {m['after']['TP']}")
    cd = os.path.join(bd, theme)
    if os.path.exists(cd): shutil.rmtree(cd)
    meta, n = Layers(a.work, pj, brand, bdir, a.clip, theme, cd).build(a.jobs); log(f"{n} layers in {time.time()-t:.0f}s")
    from lib.check import check_layout
    lf, _, linfo = check_layout(cd)
    log("layout: " + json.dumps(linfo))
    if lf:   # never show or render a design that breaks the layout rules
        log("LAYOUT FAIL: " + "; ".join(lf)); return 3
    if a.dump:
        for p in render(cd, src, mw, None, dump=[float(x) for x in a.dump.split(",")]): log("still:", p)
        return
    outd = os.path.join(a.work, "out"); os.makedirs(outd, exist_ok=True)
    outf = next_name(os.path.join(outd, f"{a.clip}_{theme}_16x9.mp4"))
    render(cd, src, mw, outf, crf=a.crf, log=log); log(f"rendered {outf} in {time.time()-t:.0f}s")
    r = check(outf, os.path.join(outd, "stills"), os.path.splitext(os.path.basename(outf))[0], meta["caption_text"], clip_dir=cd)
    if not r["ok"]:   # QA gate: a failed clip is renamed so it can never be delivered by mistake
        bad = os.path.splitext(outf)[0] + "_QA_FAILED.mp4"; os.replace(outf, bad); r["file"] = bad; outf = bad
    json.dump(r, open(os.path.splitext(outf)[0] + ".qa.json", "w"), indent=1)
    log(json.dumps(r, indent=1))
    if r["ok"]: log("QA PASS: " + outf)
    else: log("QA FAIL (do not deliver): " + "; ".join(r["fails"])); return 3

def cmd_brand_url(a):
    from lib.brand import from_url
    prof, notes = from_url(a.url, os.path.abspath(a.out)); log(json.dumps({k: prof[k] for k in ("brand_name", "colors", "default_theme", "logo", "link")}, indent=1))
    log("notes: " + "; ".join(notes)); log("profile: " + os.path.join(os.path.abspath(a.out), "brand_profile.json"))

def cmd_brand_logo(a):
    from lib.brand import from_file
    prof, notes = from_file(a.file, os.path.abspath(a.out), a.name); log(json.dumps({k: prof[k] for k in ("brand_name", "colors", "default_theme", "logo")}, indent=1))
    log("notes: " + "; ".join(notes)); log("profile: " + os.path.join(os.path.abspath(a.out), "brand_profile.json"))

def cmd_sample(a):
    brand_p = os.path.abspath(a.brand); brand = json.load(open(brand_p))
    slug = "".join(ch if ch.isalnum() else "-" for ch in brand.get("brand_name", "brand").lower()).strip("-") or "brand"
    work = os.path.abspath(a.work or f"/workspace/audiograms/sample-{slug}"); os.makedirs(work, exist_ok=True)
    demo = os.path.join(KIT, "demo"); tx = json.load(open(os.path.join(demo, "demo_transcript.json")))
    end = round(tx[-1]["words"][-1]["e"] + 0.42, 3)
    json.dump({"brand_profile": brand_p,
               "episode": {"title": brand.get("brand_name", "Your Brand"), "subtitle": "Sample clip",
                           "source": os.path.join(demo, "demo_voice.m4a"), "transcript": os.path.join(demo, "demo_transcript.json"), "spelling": []},
               "people": {"guest": {"name": "Your Guest", "handle": "@yourguest", "img": ""}},
               "clips": {"sample": {"start": 0.0, "end": end, "turns": [[0.0, "guest"]],
                                    "accent_words": ["conversation", "clip", "lands", "frame", "gold"], "drops": [], "fixes": [], "payoff_words": 0}}},
              open(os.path.join(work, "project.json"), "w"), indent=1)
    theme = a.theme or brand.get("default_theme", "dark")
    base = dict(work=work, clip="sample", theme=theme, crf=12, jobs=6, remaster=False, allow_initials=True)   # demo guest has no photo
    if cmd_make(argparse.Namespace(**base, dump="9.2")): log("SAMPLE FAILED: layout check failed, see above"); return 3
    still = os.path.join(work, "build", "sample", theme, "ref_009.20.png")
    os.makedirs(os.path.join(work, "out"), exist_ok=True); dst = os.path.join(work, "out", f"sample_{theme}_still.png"); shutil.copy(still, dst)
    log("STILL READY: " + dst)
    if not a.still_only: return cmd_make(argparse.Namespace(**base, dump=None))

PREVIEW_LINE = "Your best moments will light up here, word by word."

def _save_pfp(work, key, src):
    """A local file or an image URL saved as DIR/pfp/<key>.<ext>. Returns the path, or None."""
    if not src: return None
    os.makedirs(os.path.join(work, "pfp"), exist_ok=True)
    if os.path.exists(src):
        dst = os.path.join(work, "pfp", key + (os.path.splitext(src)[1].lower() or ".jpg"))
        if os.path.abspath(src) != os.path.abspath(dst): shutil.copy(src, dst)
        return dst
    url = re.sub(r"_(normal|bigger|mini|200x200)(\.\w+)$", r"_400x400\2", src)
    dst = os.path.join(work, "pfp", key + (os.path.splitext(url.split("?")[0])[1] or ".jpg"))
    try: open(dst, "wb").write(urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=30).read()); return dst
    except Exception as ex: log(f"could not download the picture for {key}: {ex}"); return None

def cmd_space_preview(a):
    """One preview still of the user's own Space before any transcript exists: the host's real photo in the ring, the
    host's name and handle, the Space title and date in the top bar, host and co-host in the footer, the user's brand
    (or colours from the host's photo with the host's name as a text wordmark) and a placeholder caption."""
    from lib.brand import from_photo
    work = a.work; os.makedirs(work, exist_ok=True); info = None
    if a.space:
        m = SPACE_RE.search(a.space); url = f"https://x.com/i/spaces/{m.group(1)}" if m else a.space.strip()
        info, err = space_info(url)
        if info: json.dump(info, open(os.path.join(work, "space.json"), "w"), indent=1); log(f"space: {info['title']} | {info['host_handle']} | {info['date']}")
        else: log("could not read the Space details (" + " ".join(err) + "): using the flags given")
    elif os.path.exists(os.path.join(work, "space.json")): info = json.load(open(os.path.join(work, "space.json")))
    pj = merge_project(work, info)
    ep = pj["episode"]; info = info or pj.get("space") or {}
    title = a.space_title or (ep.get("title") if ep.get("title") not in (None, "", "Space title") else "") or info.get("title") or ""
    date = a.date or info.get("date") or ""
    sub = f"X Space · {date}" if date else "X Space"
    host = "@" + (a.host or info.get("host_handle") or "").lstrip("@")
    if host == "@": log("NEED THE HOST: pass --host @handle (or --space LINK so it is read from the Space)"); return 2
    hk = host[1:].lower(); hp = pj["people"].get(hk, {})
    hname = a.host_name or hp.get("name") or (info.get("host_name") if (info.get("host_handle") or "").lower() == host.lower() else "") or host[1:]
    hpfp = _save_pfp(work, hk, a.host_pfp) or (os.path.join(work, hp["img"]) if hp.get("img") and not os.path.isabs(hp["img"]) else hp.get("img")) \
        or _save_pfp(work, hk, info.get("host_pfp_url") if (info.get("host_handle") or "").lower() == host.lower() else None)
    if not hpfp or not os.path.exists(hpfp):
        log("NEED THE HOST'S PICTURE: pass --host-pfp FILE_OR_URL (look the handle up with the X tools). Never show initials."); return 2
    hosts = [{"name": hname, "handle": host, "img": os.path.abspath(hpfp)}]
    pj["people"][hk] = {"name": hname, "handle": host, "img": os.path.relpath(hpfp, work)}
    if a.cohost:
        ch = "@" + a.cohost.lstrip("@"); ck = ch[1:].lower(); cp = pj["people"].get(ck, {})
        cpfp = _save_pfp(work, ck, a.cohost_pfp) or (os.path.join(work, cp["img"]) if cp.get("img") else None)
        if not cpfp: log(f"NEED THE CO-HOST'S PICTURE: pass --cohost-pfp FILE_OR_URL for {ch}. Never show initials."); return 2
        hosts.append({"name": a.cohost_name or cp.get("name") or ch[1:], "handle": ch, "img": os.path.abspath(cpfp) if cpfp else ""})
        pj["people"][ck] = {"name": hosts[-1]["name"], "handle": ch, "img": os.path.relpath(cpfp, work) if cpfp else ""}
    if a.brand: brand_p = os.path.abspath(a.brand)
    else:
        bdir = os.path.join(work, "brand_host"); prof, notes = from_photo(hpfp, bdir, hname, host); brand_p = os.path.join(bdir, "brand_profile.json")
        log("brand from the host's photo: " + "; ".join(notes))
    if a.brand or not pj.get("brand_profile"): pj["brand_profile"] = brand_p
    ep.update(title=title or ep.get("title", ""), subtitle=sub, hosts=hosts)
    json.dump(pj, open(os.path.join(work, "project.json"), "w"), indent=1)
    # a tiny stand-alone project: the placeholder line timed word by word over the bundled demo voice (drives the ring)
    pv = os.path.join(work, "preview"); os.makedirs(pv, exist_ok=True); demo = os.path.join(KIT, "demo")
    toks = PREVIEW_LINE.split(" "); step = 0.36
    words = [dict(s=round(0.2 + i * step, 3), e=round(0.2 + i * step + step - 0.04, 3), w=t) for i, t in enumerate(toks)]
    json.dump([{"s": words[0]["s"], "e": words[-1]["e"], "words": words}], open(os.path.join(pv, "preview_transcript.json"), "w"))
    end = round(words[-1]["e"] + 0.5, 3)
    json.dump({"brand_profile": brand_p,
               "episode": {"title": title, "subtitle": sub, "hosts": hosts, "source": os.path.join(demo, "demo_voice.m4a"),
                           "transcript": "preview_transcript.json", "spelling": []},
               "people": {"host": {"name": hname, "handle": host, "img": os.path.abspath(hpfp)}},
               "clips": {"preview": {"start": 0.0, "end": end, "turns": [[0.0, "host"]], "accent_words": [], "drops": [], "fixes": [],
                                     "payoff": False, "phrase_words": [4, 12]}}}, open(os.path.join(pv, "project.json"), "w"), indent=1)
    brand = json.load(open(brand_p)); theme = a.theme or brand.get("default_theme", "dark")
    T = round(words[2]["s"] + 0.12, 2)   # "Your best" spoken, "moments" lit: the first few words read as highlighted
    r = cmd_make(argparse.Namespace(work=pv, clip="preview", theme=theme, crf=12, jobs=a.jobs, remaster=False, dump=str(T)))
    if r: log("PREVIEW FAILED: see above"); return r
    still = os.path.join(pv, "build", "preview", theme, f"ref_{T:06.2f}.png")
    dst = os.path.abspath(a.out) if a.out else os.path.join(work, "out", f"space_preview_{theme}.png")
    os.makedirs(os.path.dirname(dst), exist_ok=True); shutil.copy(still, dst)
    log("PREVIEW READY: " + dst + "  (look at it before sending)")

def cmd_check(a):
    """Re-run the full QA gate on an existing render (layout checks need --clip and --theme)."""
    from lib.check import check
    cd = os.path.join(a.work, "build", a.clip, a.theme) if a.clip and a.theme else None
    txt = json.load(open(os.path.join(cd, "timeline.json")))["caption_text"] if cd else ""
    r = check(a.file, os.path.join(a.work, "out", "stills"), os.path.splitext(os.path.basename(a.file))[0], txt, clip_dir=cd)
    log(json.dumps(r, indent=1)); log("QA PASS" if r["ok"] else "QA FAIL: " + "; ".join(r["fails"]))
    return 0 if r["ok"] else 3

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter); sp = ap.add_subparsers(dest="cmd", required=True)
    sp.add_parser("doctor")
    p = sp.add_parser("init"); p.add_argument("--work", required=True); p.add_argument("--brand", required=True)
    p = sp.add_parser("fetch"); p.add_argument("--work", required=True); p.add_argument("src")
    p = sp.add_parser("pfp"); p.add_argument("--work", required=True); p.add_argument("--key", required=True); p.add_argument("--url", required=True)
    p.add_argument("--name"); p.add_argument("--handle")
    p = sp.add_parser("transcribe"); p.add_argument("--work", required=True); p.add_argument("--start", type=float); p.add_argument("--end", type=float)
    p.add_argument("--clip"); p.add_argument("--model", default="small.en")
    p = sp.add_parser("speakers"); p.add_argument("--work", required=True)
    p = sp.add_parser("quote"); p.add_argument("--work", required=True); p.add_argument("--clip", required=True)
    p = sp.add_parser("make"); p.add_argument("--work", required=True); p.add_argument("--clip", required=True); p.add_argument("--theme", choices=["dark", "light"])
    p.add_argument("--crf", type=int, default=12); p.add_argument("--jobs", type=int, default=6); p.add_argument("--remaster", action="store_true")
    p.add_argument("--dump", help="comma separated seconds: write exact stills instead of a video (fast design preview)")
    p = sp.add_parser("brand-from-url"); p.add_argument("url"); p.add_argument("--out", required=True)
    p = sp.add_parser("brand-from-logo"); p.add_argument("file"); p.add_argument("--out", required=True); p.add_argument("--name")
    p = sp.add_parser("sample"); p.add_argument("--brand", required=True); p.add_argument("--theme", choices=["dark", "light"]); p.add_argument("--work")
    p.add_argument("--still-only", action="store_true")
    p = sp.add_parser("space-preview"); p.add_argument("--work", required=True); p.add_argument("--space", help="X Space or post link: title, date, host read automatically")
    p.add_argument("--brand", help="brand_profile.json; omit to take colours from the host's photo"); p.add_argument("--space-title"); p.add_argument("--date")
    p.add_argument("--host"); p.add_argument("--host-name"); p.add_argument("--host-pfp"); p.add_argument("--cohost"); p.add_argument("--cohost-name")
    p.add_argument("--cohost-pfp"); p.add_argument("--theme", choices=["dark", "light"]); p.add_argument("--out"); p.add_argument("--jobs", type=int, default=6)
    p = sp.add_parser("check"); p.add_argument("--work", required=True); p.add_argument("file"); p.add_argument("--clip")
    p.add_argument("--theme", choices=["dark", "light"])
    a = ap.parse_args()
    if getattr(a, "work", None): a.work = os.path.abspath(a.work)
    sys.exit({"doctor": cmd_doctor, "init": cmd_init, "fetch": cmd_fetch, "pfp": cmd_pfp, "transcribe": cmd_transcribe, "speakers": cmd_speakers, "quote": cmd_quote,
              "make": cmd_make, "check": cmd_check, "brand-from-url": cmd_brand_url,
              "brand-from-logo": cmd_brand_logo, "sample": cmd_sample, "space-preview": cmd_space_preview}[a.cmd](a) or 0)
