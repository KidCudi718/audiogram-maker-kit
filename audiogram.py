#!/usr/bin/env python3
"""Audiogram Maker kit: one entry command.

  python3 audiogram.py doctor                                   check ffmpeg, Chrome, python deps, font
  python3 audiogram.py init      --work DIR --brand BRAND.json  create DIR/project.json (episode, people, clips)
  python3 audiogram.py fetch     --work DIR URL_OR_FILE         download (yt-dlp) or copy the recording to DIR/source.m4a
  python3 audiogram.py transcribe --work DIR [--start S --end E] [--clip ID] [--model small.en]
  python3 audiogram.py quote     --work DIR --clip ID            the exact quote to show the user before rendering
  python3 audiogram.py make      --work DIR --clip ID --theme dark|light   master + layers + render + check
  python3 audiogram.py check     --work DIR FILE.mp4             QA stills and specs for any finished file

First run for a new user (no questions first):
  python3 audiogram.py brand-from-url  URL  --out BRAND_DIR       logo, colors, name from their website
  python3 audiogram.py brand-from-logo FILE --out BRAND_DIR [--name NAME]   from a logo (SVG/PNG/JPG) or brand kit PDF
  python3 audiogram.py sample --brand BRAND_DIR/brand_profile.json [--theme dark|light] [--still-only]
        30 s sample in their brand with the bundled demo voice: writes the preview still first, then the video

Everything brand specific comes from the brand profile JSON. Nothing is hard coded."""
import argparse, json, os, shutil, subprocess, sys, time
KIT = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, KIT)
from lib import words as W

def log(*a): print(*a, flush=True)

def load_project(work):
    pj = json.load(open(os.path.join(work, "project.json")))
    bp = pj["brand_profile"]; bp = bp if os.path.isabs(bp) else os.path.join(work, bp)
    brand = json.load(open(bp))
    return pj, brand, os.path.dirname(os.path.abspath(bp))

def source_path(work, pj):
    s = pj["episode"].get("source", "source.m4a"); return s if os.path.isabs(s) else os.path.join(work, s)

def mmss(t): return f"{int(t//60)}:{t%60:05.2f}"

def cmd_doctor(a):
    ok = True
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
    if os.path.exists(p): log("project.json already exists, leaving it alone"); return
    json.dump({"brand_profile": os.path.abspath(a.brand),
               "episode": {"title": "Episode or Space title", "subtitle": "X Space · Month D, YYYY", "source": "source.m4a",
                           "transcript": "transcript.json", "spelling": []},
               "people": {"speaker_id": {"name": "Display Name", "handle": "@handle", "img": "pfp/handle.jpg"}},
               "clips": {}}, open(p, "w"), indent=1)
    log("wrote", p)

def cmd_fetch(a):
    os.makedirs(a.work, exist_ok=True); out = os.path.join(a.work, "source.m4a")
    if os.path.exists(a.src):
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", a.src, "-vn", "-c:a", "aac", "-b:a", "256k", out], check=True)
    else:
        subprocess.run(["yt-dlp", "-f", "bestaudio/best", "-x", "--audio-format", "m4a", "-o", os.path.join(a.work, "source.%(ext)s"), a.src], check=True)
    log("source:", out)

def cmd_transcribe(a):
    from lib.transcribe import transcribe
    pj, brand, _ = load_project(a.work); src = source_path(a.work, pj)
    if a.clip:
        c = pj["clips"][a.clip]; os.makedirs(os.path.join(a.work, "tx"), exist_ok=True)
        out = os.path.join(a.work, "tx", a.clip + ".json"); st = max(0, c["start"] - 4); en = c["end"] + 20
    else:
        out = os.path.join(a.work, pj["episode"].get("transcript", "transcript.json")); st, en = a.start, a.end
    transcribe(src, out, a.model, st, en, log=log)

def cmd_quote(a):
    pj, brand, _ = load_project(a.work); c = pj["clips"][a.clip]; rep = {}
    ws = W.load_words(a.work, pj, brand, a.clip, rep); st, en, dur = W.clip_window(c)
    log(f"Clip {a.clip}: {mmss(st)} to {mmss(en)} ({dur:.1f}s)")
    TURNS = W.turns(c); names = [pj["people"][s]["name"] for s in dict.fromkeys(s for _, s in TURNS)]
    log("Speaker(s): " + ", ".join(names))
    log("Caption text: " + " ".join(w["w"] for w in ws))
    segs = W.load_segments(a.work, pj, a.clip)
    after = [w["w"].strip() for s in segs for w in s["words"] if en + 0.05 < w["s"] < en + 20]
    log("Next 20s after the end (check the thought is finished): " + " ".join(after))
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
    pj, brand, bdir = load_project(a.work); c = pj["clips"][a.clip]; src = source_path(a.work, pj)
    st, en, dur = W.clip_window(c); theme = a.theme or brand.get("default_theme", "dark")
    bd = os.path.join(a.work, "build", a.clip); os.makedirs(bd, exist_ok=True)
    mw = os.path.join(bd, "master.wav"); t = time.time()
    if not os.path.exists(mw) or a.remaster:
        m = master(src, st, dur, mw); log(f"mastered: {m['before']['I']} -> {m['after']['I']} LUFS, TP {m['after']['TP']}")
    cd = os.path.join(bd, theme)
    if os.path.exists(cd): shutil.rmtree(cd)
    meta, n = Layers(a.work, pj, brand, bdir, a.clip, theme, cd).build(a.jobs); log(f"{n} layers in {time.time()-t:.0f}s")
    if a.dump:
        for p in render(cd, src, mw, None, dump=[float(x) for x in a.dump.split(",")]): log("still:", p)
        return
    outd = os.path.join(a.work, "out"); os.makedirs(outd, exist_ok=True)
    outf = next_name(os.path.join(outd, f"{a.clip}_{theme}_16x9.mp4"))
    render(cd, src, mw, outf, crf=a.crf, log=log); log(f"rendered {outf} in {time.time()-t:.0f}s")
    r = check(outf, os.path.join(outd, "stills"), os.path.splitext(os.path.basename(outf))[0], meta["caption_text"])
    json.dump(r, open(os.path.splitext(outf)[0] + ".qa.json", "w"), indent=1)
    log(json.dumps(r, indent=1)); log("QA PASS" if r["ok"] else "QA FAIL: " + "; ".join(r["fails"]))

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
               "episode": {"title": f"{brand.get('brand_name', 'Your Show')} Live", "subtitle": "Sample clip",
                           "source": os.path.join(demo, "demo_voice.m4a"), "transcript": os.path.join(demo, "demo_transcript.json"), "spelling": []},
               "people": {"guest": {"name": "Your Guest", "handle": "@yourguest", "img": ""}},
               "clips": {"sample": {"start": 0.0, "end": end, "turns": [[0.0, "guest"]],
                                    "accent_words": ["conversation", "clip", "lands", "frame", "gold"], "drops": [], "fixes": [], "payoff_words": 0}}},
              open(os.path.join(work, "project.json"), "w"), indent=1)
    theme = a.theme or brand.get("default_theme", "dark")
    base = dict(work=work, clip="sample", theme=theme, crf=12, jobs=6, remaster=False)
    cmd_make(argparse.Namespace(**base, dump="9.2")); still = os.path.join(work, "build", "sample", theme, "ref_009.20.png")
    os.makedirs(os.path.join(work, "out"), exist_ok=True); dst = os.path.join(work, "out", f"sample_{theme}_still.png"); shutil.copy(still, dst)
    log("STILL READY: " + dst)
    if not a.still_only: cmd_make(argparse.Namespace(**base, dump=None))

def cmd_check(a):
    from lib.check import check
    r = check(a.file, os.path.join(a.work, "out", "stills"), os.path.splitext(os.path.basename(a.file))[0])
    log(json.dumps(r, indent=1))

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter); sp = ap.add_subparsers(dest="cmd", required=True)
    sp.add_parser("doctor")
    p = sp.add_parser("init"); p.add_argument("--work", required=True); p.add_argument("--brand", required=True)
    p = sp.add_parser("fetch"); p.add_argument("--work", required=True); p.add_argument("src")
    p = sp.add_parser("transcribe"); p.add_argument("--work", required=True); p.add_argument("--start", type=float); p.add_argument("--end", type=float)
    p.add_argument("--clip"); p.add_argument("--model", default="small.en")
    p = sp.add_parser("quote"); p.add_argument("--work", required=True); p.add_argument("--clip", required=True)
    p = sp.add_parser("make"); p.add_argument("--work", required=True); p.add_argument("--clip", required=True); p.add_argument("--theme", choices=["dark", "light"])
    p.add_argument("--crf", type=int, default=12); p.add_argument("--jobs", type=int, default=6); p.add_argument("--remaster", action="store_true")
    p.add_argument("--dump", help="comma separated seconds: write exact stills instead of a video (fast design preview)")
    p = sp.add_parser("brand-from-url"); p.add_argument("url"); p.add_argument("--out", required=True)
    p = sp.add_parser("brand-from-logo"); p.add_argument("file"); p.add_argument("--out", required=True); p.add_argument("--name")
    p = sp.add_parser("sample"); p.add_argument("--brand", required=True); p.add_argument("--theme", choices=["dark", "light"]); p.add_argument("--work")
    p.add_argument("--still-only", action="store_true")
    p = sp.add_parser("check"); p.add_argument("--work", required=True); p.add_argument("file")
    a = ap.parse_args()
    if getattr(a, "work", None): a.work = os.path.abspath(a.work)
    sys.exit({"doctor": cmd_doctor, "init": cmd_init, "fetch": cmd_fetch, "transcribe": cmd_transcribe, "quote": cmd_quote,
              "make": cmd_make, "check": cmd_check, "brand-from-url": cmd_brand_url,
              "brand-from-logo": cmd_brand_logo, "sample": cmd_sample}[a.cmd](a) or 0)
