"""QA gate for every render. A clip that fails any check is NOT delivered (audiogram.py renames it *_QA_FAILED.mp4).
Video checks (finished file):
  H.264 1920x1080 30 fps yuv420p, BT.709 tags, tv range, AAC 48 kHz; loudness -14 LUFS +/-1, true peak <= -1 dBTP,
  no clipped samples; frame 0 is the full design (caption, ring, photo on screen, no fade in); no fade at the end;
  duration matches the clip and ends on a finished sentence; no em dash in captions.
Layout checks (from the layers and real element boxes measured in Chrome):
  logo readable against its background (and never a solid block); caption, accent and payoff text contrast >= 4.5:1
  against the actual background pixels; every text box inside the 5 percent title safe area; no text overflow;
  no overlaps (logo vs title, hosts vs link, chip vs captions, captions vs ring / progress / footer, payoff vs top
  bar / footer); the payoff takeover clears the speaker and ring before its text shows, and starts only when the
  first payoff word is spoken.
Stills at 0, 0.1, 0.25, 0.5, 1.0 s, the midpoint, the last 2 s and the very last frame, plus a contact sheet."""
import json, math, os, re, subprocess
import numpy as np
from PIL import Image, ImageDraw, ImageStat
from .master_audio import measure

def probe(f):
    j = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", f], capture_output=True, text=True).stdout)
    v = next(s for s in j["streams"] if s["codec_type"] == "video"); a = next((s for s in j["streams"] if s["codec_type"] == "audio"), None)
    return v, a, float(j["format"]["duration"])

def grab(f, t, out, sseof=False):
    cmd = ["ffmpeg", "-v", "error", "-y"] + (["-sseof", "-0.25"] if sseof else ["-ss", f"{t:.3f}"]) + ["-i", f]
    cmd += (["-update", "1"] if sseof else ["-frames:v", "1"]) + [out]
    subprocess.run(cmd, check=True); return out

# ---------------- colour maths ----------------
def _rl(x):
    x = np.asarray(x, np.float32) / 255; x = np.where(x <= 0.03928, x / 12.92, ((x + 0.055) / 1.055) ** 2.4)
    return 0.2126 * x[..., 0] + 0.7152 * x[..., 1] + 0.0722 * x[..., 2]

def _hex(h):
    h = h.lstrip("#"); return np.array([int(h[i:i + 2], 16) for i in (0, 2, 4)], np.float32)

def worst_contrast(color_hex, bg_pixels):
    """Lowest contrast of a text colour against the background pixels it can sit on (2nd/98th percentile)."""
    L = _rl(bg_pixels.reshape(-1, 3)); lt = float(_rl(_hex(color_hex)[None])[0])
    lo, hi = np.percentile(L, 2), np.percentile(L, 98)
    return round(float(min((max(lt, b) + 0.05) / (min(lt, b) + 0.05) for b in (lo, hi))), 2)

def text_backgrounds(LY, G):
    """Real background pixels behind the captions (scene) and behind the payoff (clean background, nothing else)."""
    bg = Image.open(os.path.join(LY, "bg.png")).convert("RGBA"); bloom = Image.open(os.path.join(LY, "bloom.png")).convert("RGBA")
    scene = bg.copy(); scene.alpha_composite(bloom); S = np.asarray(scene.convert("RGB"), np.float32)
    cl = os.path.join(LY, "bg_clean.png")
    H = np.asarray((Image.open(cl) if os.path.exists(cl) else bg).convert("RGB"), np.float32)
    cap_px = S[G["capy"]:G["capy"] + G.get("capmaxh", 500), G["capx"]:G["capr"]]
    hero_px = H[G["herocy"] - 200:G["herocy"] + 200, 260:1660]
    return S, cap_px, hero_px

# ---------------- layout gate ----------------
def _inter(a, b, pad=0):
    return not (a[2] + pad <= b[0] or b[2] + pad <= a[0] or a[3] + pad <= b[1] or b[3] + pad <= a[1])

def _inside(a, box, tol=1):
    return a[0] >= box[0] - tol and a[1] >= box[1] - tol and a[2] <= box[2] + tol and a[3] <= box[3] + tol

def check_layout(clip_dir):
    """Static design checks on the built layers. Returns (fails, notes, info)."""
    from .render import payoff_timing
    TL = json.load(open(os.path.join(clip_dir, "timeline.json"))); G = TL["geometry"]; P = TL["palette"]; LY = os.path.join(clip_dir, "layers")
    fails, notes, info = [], [], {}
    safe = G.get("safe", [96, 54, 1824, 1026])
    S, cap_px, hero_px = text_backgrounds(LY, G)
    # 0. the watermark must stay a whisper: at most about 3.5 levels of brightness change anywhere
    cl = os.path.join(LY, "bg_clean.png")
    if os.path.exists(cl):
        dm = np.abs(_rl(np.asarray(Image.open(os.path.join(LY, "bg.png")).convert("RGB"), np.float32)) ** (1 / 2.2)
                    - _rl(np.asarray(Image.open(cl).convert("RGB"), np.float32)) ** (1 / 2.2)) * 255
        info["watermark_max_delta"] = round(float(np.percentile(dm, 99.9)), 2)
        if info["watermark_max_delta"] > 3.5: fails.append(f"watermark too visible ({info['watermark_max_delta']} levels, max 3.5)")
    # 1. text contrast on the real background (active word, spoken words, accent word; payoff the same)
    cr = {}
    for k in ("c", "s", "key"):
        cr[f"caption_{k}"] = worst_contrast(P[k], cap_px); cr[f"payoff_{k}"] = worst_contrast(P[k], hero_px)
    info["contrast"] = cr
    for k, v in cr.items():
        if v < 4.5: fails.append(f"text contrast {k} {v}:1 is below 4.5:1")
    up = {k: min(worst_contrast(P[k], cap_px), worst_contrast(P[k], hero_px)) for k in ("u", "hu", "nx")}
    info["upcoming_words_contrast"] = up   # quieter on purpose, but clearly readable
    if min(up["u"], up["hu"]) < 3.0: fails.append(f"upcoming words too faint ({min(up['u'], up['hu'])}:1, need 3:1)")
    if up["nx"] < 1.8: fails.append(f"next line preview too faint ({up['nx']}:1)")
    # 2. top bar, chip, footer: real boxes measured in Chrome
    for spk, m in (TL.get("ui_layout") or {}).items():
        if "error" in m: fails.append(f"could not measure layout for {spk}"); continue
        ui = Image.open(os.path.join(LY, f"ui_{spk}.png")).convert("RGBA")
        if m.get("overflow"): fails.append(f"text does not fit even at the smallest size: {m['overflow']}")
        texts = []
        for k in (".top .logo", ".top .lgrp", ".top .logoi", ".top > .wm", ".tt", ".chip", ".ftxt", ".lk", ".code", ".h"):
            for r in m.get(k, []):
                if r[2] - r[0] > 0 and r[3] - r[1] > 0: texts.append((k, r))
        for k, r in texts:
            if not _inside(r, safe): fails.append(f"{k} outside the title safe area {[round(v) for v in r]}")
        logo_r = next((r for k, r in texts if k in (".top .logo", ".top .lgrp", ".top > .wm")), None)
        for r in m.get(".tt", []):
            if logo_r and _inter(logo_r, r, 16): fails.append("logo and title overlap or touch")
        for a in m.get(".hosts", []):
            for b in m.get(".cta", []):
                if (a[2] - a[0]) > 0 and (b[2] - b[0]) > 0 and _inter(a, b, 12): fails.append("footer hosts and link overlap")
        for k in (".hosts", ".cta"):
            for r in m.get(k, []):
                for pr in m.get(".pill", []):
                    if r[2] - r[0] > 0 and not _inside(r, pr, 2): fails.append(f"footer {k} spills out of the footer bar")
        # logo legibility and solid block test on the real pixels
        lr = next((r for k, r in texts if k in (".top .logo", ".top .logoi", ".top > .wm")), None)
        if lr:
            x0, y0, x1, y1 = [int(round(v)) for v in lr]
            crop = np.asarray(ui.crop((x0, y0, x1, y1)), np.float32); under = S[y0:y1, x0:x1]
            msk = crop[..., 3] > 128
            if msk.sum() < 40: fails.append("logo is missing or too small to see")
            else:
                Lp = _rl(crop[..., :3][msk]); Lb = _rl(under[msk])
                c = (np.maximum(Lp, Lb) + 0.05) / (np.minimum(Lp, Lb) + 0.05); leg = float((c >= 2.0).mean())
                ys, xs = np.nonzero(msk); fill = msk.sum() / ((np.ptp(ys) + 1) * (np.ptp(xs) + 1)); std = float(Lp.std())
                info.setdefault("logo", {})[spk] = dict(legible=round(leg, 2), fill=round(float(fill), 2), tone_spread=round(std, 3), style=TL.get("logo_style"))
                need = 0.5 if TL.get("logo_style") == "icon" else 0.55
                if leg < need: fails.append(f"logo hard to read on this background ({leg:.0%} of it stands out, need {need:.0%})")
                if fill > 0.9 and std < 0.03 and TL.get("logo_style") != "text": fails.append("logo renders as a solid block")
        chip = (m.get(".chip") or [None])[0]
        info.setdefault("chip", {})[spk] = chip
    # 3. captions and payoff boxes over the whole timeline
    chip_bottom = max([c[3] for c in info.get("chip", {}).values() if c] or [G["chipy"] + 60])
    ring_right = G["cx"] + G["P"] / 2 + 34 + 72 + 4
    cap_zone = (max(G["capx"] - 4, ring_right), chip_bottom + 8, min(G["capr"] + 4, safe[2]), G["progy"] - 10)
    hero_zone = (safe[0], 132, safe[2], G["progy"] - 10)
    worst = {}
    for e in TL["timeline"]:
        im = Image.open(os.path.join(LY, e["png"])); bb = im.getchannel("A").point(lambda v: 255 if v > 24 else 0).getbbox()
        if not bb: fails.append(f"caption layer {e['png']} is empty"); continue
        zone = hero_zone if e["hero"] else cap_zone
        if not _inside(bb, zone, 0):
            worst[e["png"]] = (bb, "payoff" if e["hero"] else "caption")
    for png, (bb, kind) in list(worst.items())[:5]:
        fails.append(f"{kind} text leaves its area or overlaps another element: {png} box {list(bb)}")
    # 4. payoff timing: starts with the first payoff word; speaker, ring, glow, chip and last caption are fully gone
    #    (opacity 0) before the payoff text begins
    if TL.get("hero_phrase"):
        fs, fe, ts = payoff_timing(TL); info["payoff_timing"] = dict(fade_start=fs, fade_end=fe, text_start=ts, hero_start=TL["hero_start"])
        first_word_start = min((e["s"] for e in TL["timeline"] if e["hero"] and e["cur"] is not None), default=TL["hero_start"])
        if TL["hero_start"] > first_word_start + 0.02: fails.append("payoff starts after its first word")
        if TL["hero_start"] < first_word_start - 0.05: fails.append("payoff starts before the payoff line is spoken")
        if fe > ts - 1 / 30 + 1e-6: fails.append("payoff text starts before the speaker has fully faded out")
        prev_end = max([e["e"] for e in TL["timeline"] if not e["hero"] and e["cur"] is not None and e["s"] < TL["hero_start"]] or [0])
        if fs < min(prev_end, TL["hero_start"] - 0.17) - 1e-3: fails.append("speaker starts fading while the last caption word is still lit")
    # 5. frame 0 is the full design: first caption on screen from t = 0 with its first word lit
    if not TL["timeline"] or TL["timeline"][0]["s"] > 0.001 or TL["timeline"][0]["phrase"] != 0: fails.append("frame 0 has no caption")
    elif TL["timeline"][0]["cur"] is None: fails.append("first word is not lit at frame 0")
    return fails, notes, info

# ---------------- full gate ----------------
def check(f, still_dir, tag, caption_text="", clip_dir=None):
    os.makedirs(still_dir, exist_ok=True)
    v, a, dur = probe(f); fails = []; notes = []
    spec = dict(codec=v["codec_name"], size=f'{v["width"]}x{v["height"]}', fps=v.get("r_frame_rate"), pix=v.get("pix_fmt"),
                matrix=v.get("color_space"), primaries=v.get("color_primaries"), trc=v.get("color_transfer"), range=v.get("color_range"),
                audio=(a or {}).get("codec_name"), ar=(a or {}).get("sample_rate"), dur=round(dur, 3))
    if spec["codec"] != "h264": fails.append("video is not H.264")
    if spec["size"] != "1920x1080": fails.append("not 1920x1080")
    if spec["fps"] != "30/1": fails.append("not 30 fps")
    if spec["pix"] != "yuv420p": fails.append("pixel format is not yuv420p")
    if not (spec["matrix"] == spec["primaries"] == spec["trc"] == "bt709"): fails.append("missing BT.709 colour tags")
    if spec["range"] != "tv": fails.append("colour range not tv")
    if spec["audio"] != "aac" or spec["ar"] != "48000": fails.append("audio is not AAC 48 kHz")
    loud = measure(["-i", f])
    if abs(loud["I"] + 14) > 1.0: fails.append(f"loudness {loud['I']} LUFS, want -14 +/- 1")
    if loud["TP"] > -0.9: fails.append(f"true peak {loud['TP']} dBTP, want -1 or lower")
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", f, "-vn", "-ac", "2", "-f", "f32le", "-"], capture_output=True).stdout
    x = np.frombuffer(raw, np.float32); clipped = int((np.abs(x) >= 0.9999).sum()) if len(x) else 0
    if clipped: fails.append(f"{clipped} clipped audio samples")
    pts = [("f0", 0.0), ("t0.10", 0.10), ("t0.25", 0.25), ("t0.50", 0.50), ("t1.00", 1.0), ("mid", dur / 2),
           ("end-2.0", max(0, dur - 2.0)), ("end-1.0", max(0, dur - 1.0))]
    paths = []
    for name, t in pts: paths.append((name, grab(f, t, os.path.join(still_dir, f"{tag}_{name}.png"))))
    paths.append(("last", grab(f, 0, os.path.join(still_dir, f"{tag}_last.png"), sseof=True)))
    P = {k: np.asarray(Image.open(p).convert("L"), np.float32) for k, p in paths}
    # frame 0 is the full design: not blank, same look as 0.1 s, caption area has text
    if P["f0"].std() < 12: fails.append("frame 0 looks blank (possible intro or fade in)")
    if abs(P["f0"].mean() - P["t0.10"].mean()) > 6 or abs(P["f0"].mean() - P["t1.00"].mean()) > 18: fails.append("frame 0 differs from 0.1 s / 1 s (possible fade in)")
    if P["f0"][372:620, 820:1810].std() < 8: fails.append("frame 0 has no visible caption")
    # no fade at the end
    if P["last"].std() < 12: fails.append("last frame looks blank (possible fade out)")
    if abs(P["last"].mean() - P["end-1.0"].mean()) > 10: fails.append("last second changes brightness (possible fade out)")
    if "\u2014" in caption_text: fails.append("em dash in captions")
    caps = [w for w in re.findall(r"\b[A-Z]{4,}\b", caption_text)]
    if caps: notes.append("all caps words in captions (fine if acronyms): " + ", ".join(sorted(set(caps))))
    layout = {}
    if clip_dir and json.load(open(os.path.join(clip_dir, "timeline.json"))).get("hero_phrase"):
        from .render import payoff_timing
        TLv = json.load(open(os.path.join(clip_dir, "timeline.json"))); fs, fe, ts = payoff_timing(TLv)
        Fc = math.ceil(fe * 30 - 1e-6)   # first frame with the speaker fully gone; it must come before the payoff text
        if Fc >= ts * 30: fails.append("no fully clear frame between the speaker fade and the payoff text")
        pth = grab(f, max(0, Fc / 30 - 0.005), os.path.join(still_dir, f"{tag}_payoff_clear.png"))
        fr = np.asarray(Image.open(pth).convert("L"), np.float32)
        bgc = os.path.join(clip_dir, "layers", "bg_clean.png")
        ref = np.asarray(Image.open(bgc if os.path.exists(bgc) else os.path.join(clip_dir, "layers", "bg.png")).convert("L"), np.float32)
        G_ = TLv["geometry"]; r = int(G_["P"] / 2 + 120)
        for nm, (x0, y0, x1, y1) in dict(speaker=(G_["cx"] - r, G_["cy"] - r, G_["cx"] + r, G_["cy"] + r),
                                         captions=(G_["capx"], G_["chipy"], G_["capr"], G_["progy"] - 12)).items():
            d = np.abs(fr[y0:y1, x0:x1] - ref[y0:y1, x0:x1]); d = d - np.median(d)   # vignette and grain shift levels evenly
            if np.percentile(d, 99.5) > 12: fails.append(f"{nm} still visible just before the payoff text (t={Fc / 30:.3f}s)")
        paths.append(("payoff_clear", pth))
    if clip_dir:
        TL = json.load(open(os.path.join(clip_dir, "timeline.json")))
        if abs(dur - TL["dur"]) > 0.12: fails.append(f"video is {dur:.2f}s but the clip is {TL['dur']:.2f}s")
        words = caption_text.split()
        if words and not re.search(r"[.?!][\"')]*$", words[-1]): fails.append("clip does not end on a finished sentence (last word: " + words[-1] + ")")
        last_cap = max((e["e"] for e in TL["timeline"]), default=dur)
        lf, ln, layout = check_layout(clip_dir); fails += lf; notes += ln
    # contact sheet
    th = [Image.open(p).resize((480, 270)) for _, p in paths]; sheet = Image.new("RGB", (480 * 3 + 40, (270 + 30) * 3 + 10), (20, 20, 20))
    d = ImageDraw.Draw(sheet)
    for i, ((name, _), im) in enumerate(zip(paths, th)):
        xx = 10 + (i % 3) * 490; yy = 10 + (i // 3) * 300; sheet.paste(im, (xx, yy)); d.text((xx, yy + 274), name, fill=(230, 230, 230))
    sheet_p = os.path.join(still_dir, f"{tag}_sheet.png"); sheet.save(sheet_p)
    return dict(file=f, ok=not fails, fails=fails, notes=notes, spec=spec, loudness=loud, clipped_samples=clipped, layout=layout,
                stills=dict(paths), sheet=sheet_p)
