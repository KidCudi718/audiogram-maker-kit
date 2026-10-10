"""Per frame compositor: audio reactive ring, breathing photo, drifting bloom, karaoke captions, payoff push in, progress bar.
Rules baked in (do not change without the user asking):
  * No hook or intro. Frame 0 is the full design (background, photo, ring, top bar, footer, name chip, first caption line)
    and the audio starts at frame 0. No fade in, no silent lead in, no crossfade.
  * No fade out. The last word plays in full and the video cuts cleanly.
  * Sharp and vivid: ring bars drawn at 2x then Lanczos downscaled with a tight 1.2px halo, photo scaled from a 2x render,
    solid footer (no frosted blur), payoff uses a solid scrim (no blur), grain 1.0 as dither only, vignette on the scene only.
  * Colour: real RGB to BT.709 conversion in swscale plus bt709 / tv tags so players decode with the right matrix.
  * Export: 1920x1080 30 fps, x264 preset slow, CRF 12, tune film, deblock -1,-1, aq-strength 0.8, yuv420p, AAC 320k 48 kHz,
    +faststart. Audio is the mastered WAV (-14 LUFS / -1 dBTP)."""
import os, json, math, subprocess
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageChops
from .theme import rgb
from . import words as W

FPS = 30; Wd, Ht = 1920, 1080
VF = ("scale=out_color_matrix=bt709:out_range=tv:flags=lanczos+accurate_rnd+full_chroma_int,format=yuv420p,"
      "setparams=range=tv:color_primaries=bt709:color_trc=bt709:colorspace=bt709")

def levels(n, seed=11):
    import random
    random.seed(seed); out = []
    for i in range(n):
        a = 2 * math.pi * i / n
        v = 0.45 + 0.25 * math.sin(3 * a + 0.6) + 0.18 * math.sin(7 * a + 1.9) + 0.12 * math.sin(13 * a)
        out.append(max(0.06, min(1.0, v * random.uniform(0.55, 1.15))))
    return out

def render(clip_dir, source_audio, master_wav, outf, t0=0.0, t1=None, dump=None, crf=12, threads=6, log=print):
    TLJ = json.load(open(os.path.join(clip_dir, "timeline.json"))); G = TLJ["geometry"]; TL = TLJ["timeline"]; PAL = TLJ["palette"]
    ST, DUR = TLJ["start"], TLJ["dur"]; TURNS = [tuple(x) for x in TLJ["turns"]]; SPK = TLJ["speakers"]
    LY = os.path.join(clip_dir, "layers"); LIGHT = TLJ["theme"] == "light"
    T1 = DUR if t1 is None else min(t1, DUR)
    if outf: os.makedirs(os.path.dirname(os.path.abspath(outf)), exist_ok=True)
    cx, cy, P = G["cx"], G["cy"], G["P"]; HS = TLJ["hero_start"]
    # ---------- audio analysis (drives ring and photo) ----------
    wav = os.path.join(clip_dir, "clip.f32")
    if not os.path.exists(wav):
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", str(ST), "-t", str(DUR), "-i", source_audio, "-ac", "1", "-ar", "44100", "-f", "f32le", wav], check=True)
    x = np.fromfile(wav, dtype=np.float32); SR = 44100; n = 200
    NF = int(round(DUR * FPS)) + 1; NW = 2048; win = np.hanning(NW); nb = n // 2 + 1
    edges = np.geomspace(90, 6500, nb + 1); freqs = np.fft.rfftfreq(NW, 1 / SR)
    bidx = [np.where((freqs >= edges[i]) & (freqs < max(edges[i + 1], edges[i] + SR / NW)))[0] for i in range(nb)]
    bidx = [b if len(b) else np.array([np.argmin(abs(freqs - edges[i]))]) for i, b in enumerate(bidx)]
    spec = np.zeros((NF, nb)); rms = np.zeros(NF); pad = np.concatenate([np.zeros(NW), x, np.zeros(NW)])
    for f in range(NF):
        c = min(int(f / FPS * SR) + NW, len(pad) - NW // 2); seg = pad[c - NW // 2:c + NW // 2] * win; p = np.abs(np.fft.rfft(seg)) ** 2
        spec[f] = [10 * np.log10(p[b].mean() + 1e-12) for b in bidx]; rms[f] = np.sqrt(np.mean(seg ** 2) + 1e-12)
    lo = np.percentile(spec, 12, axis=0); hi = np.percentile(spec, 98.5, axis=0); norm = np.clip((spec - lo) / (hi - lo + 1e-9), 0, 1) ** 1.3
    ldb = 20 * np.log10(rms); loud = np.clip((ldb - np.percentile(ldb, 15)) / (np.percentile(ldb, 98) - np.percentile(ldb, 15) + 1e-9), 0, 1)
    shape = np.array(levels(n, 11)); shape = 0.3 + 0.7 * shape / shape.max(); band_of = np.array([min(i, n - i) for i in range(n)])
    lv = np.zeros((NF, n)); v = np.full(n, 0.08); L = np.zeros(NF); l = float(loud[0])
    for f in range(NF):
        tgt = np.clip((0.55 * norm[f, band_of] + 0.45 * loud[f]) * shape * 1.25, 0.06, 1.0); v = tgt.copy() if f == 0 else np.where(tgt > v, v + 0.55 * (tgt - v), v + 0.18 * (tgt - v)); lv[f] = v   # frame 0 already shows the live ring
        l += (0.25 if loud[f] > l else 0.07) * (loud[f] - l); L[f] = l
    # ---------- palette ----------
    R1 = np.array(rgb(PAL["ring1"]), float); R2 = np.array(rgb(PAL["ring2"]), float)
    ACC = rgb(PAL["progress"]); PST = np.array(rgb(PAL["progress_start"]), float); KNOB = rgb(PAL["knob"]) + (255,); TRACK = tuple(PAL["track"])
    SCRIM = rgb(PAL["scrim"]); SCRIM_A = PAL["scrim_a"]
    # ---------- ring ----------
    r0 = P / 2 + 34; ml = 72; BW = 3.0; EXT = int(r0 + ml + 30); SS = 2; RS = 2 * EXT
    yy, xx = np.mgrid[0:RS * SS, 0:RS * SS]; rr = np.hypot(xx / SS - EXT + 0.5 / SS, yy / SS - EXT + 0.5 / SS)
    GRAD = Image.fromarray((R1 + (R2 - R1) * np.clip((rr - r0) / ml, 0, 1)[..., None]).astype(np.uint8), "RGB"); del yy, xx, rr
    def ring(f):
        m = Image.new("L", (RS * SS, RS * SS), 0); d = ImageDraw.Draw(m); c = EXT * SS; w = BW * SS; hw = w / 2
        for i in range(n):
            a = 2 * math.pi * i / n - math.pi / 2; Ln = 4 + ml * lv[f, i]; ca, sa = math.cos(a), math.sin(a)
            x1, y1 = c + r0 * SS * ca, c + r0 * SS * sa; x2, y2 = c + (r0 + Ln) * SS * ca, c + (r0 + Ln) * SS * sa
            d.line([x1, y1, x2, y2], fill=255, width=max(1, round(w))); d.ellipse([x2 - hw, y2 - hw, x2 + hw, y2 + hw], fill=255); d.ellipse([x1 - hw, y1 - hw, x1 + hw, y1 + hw], fill=255)
        im = GRAD.copy(); im.putalpha(m); im = im.resize((RS, RS), Image.LANCZOS)
        g = im.filter(ImageFilter.GaussianBlur(1.2)); g.putalpha(g.getchannel("A").point(lambda v: int(v * 0.35)))
        out = Image.new("RGBA", (RS, RS), (0, 0, 0, 0)); out.alpha_composite(g); out.alpha_composite(im); return out
    # ---------- layers ----------
    load = lambda nm: Image.open(os.path.join(LY, nm))
    BG = load("bg.png").convert("RGB")
    BLOOM_S = load("bloom.png").convert("RGBA").resize((Wd // 2, Ht // 2), Image.LANCZOS)
    PB = (cx - 340, cy - 340, cx + 340, cy + 420)
    PFP = {s: load(f"pfp_{s}.png").convert("RGBA").crop(PB) for s in SPK}
    PFP2 = {s: load(f"pfp_{s}@2x.png").convert("RGBA").crop(tuple(2 * v for v in PB)) for s in SPK}
    UI = {s: load(f"ui_{s}.png").convert("RGBA") for s in SPK}
    CHB = (G["capx"] - 30, G["chipy"] - 30, G["capr"], G["chipy"] + 90)
    CHIP = {s: UI[s].crop(CHB) for s in SPK}
    for s_ in SPK: UI[s_].paste((0, 0, 0, 0), CHB)
    CACHE = {}
    def layer(nm):
        if nm not in CACHE:
            im = load(nm).convert("RGBA"); bb = im.getchannel("A").getbbox(); CACHE[nm] = (im.crop(bb), bb) if bb else (None, None)
        return CACHE[nm]
    PH_START = TLJ["phrase_starts"]
    yy, xx = np.mgrid[0:Ht, 0:Wd].astype(np.float32); d = np.sqrt(((xx - Wd * 0.46) / (Wd * 0.62)) ** 2 + ((yy - Ht * 0.47) / (Ht * 0.62)) ** 2)
    VIG = (1 - PAL["vignette"] * np.clip((d - 0.55) / 0.6, 0, 1) ** 1.6)[..., None].astype(np.float32); del yy, xx, d
    rng = np.random.default_rng(7); GRAIN = [(rng.normal(0, PAL["grain"], (Ht, Wd, 1))).astype(np.float32) for _ in range(6)]; HOLD = 15
    X0, Y0, X1, Y1 = G["pill"]
    ease = lambda a: 1 - (1 - max(0, min(1, a))) ** 3
    def bloom(T):
        ph = 2 * math.pi * T / 10.0; dx = 36 * math.sin(ph); dy = 22 * math.cos(ph); s = 1 + 0.035 * math.sin(ph + 1.2)
        cxs, cys = cx / 2, cy / 2; a = 1 / s
        im = BLOOM_S.transform(BLOOM_S.size, Image.AFFINE, (a, 0, cxs - (cxs + dx / 2) * a, 0, a, cys - (cys + dy / 2) * a), resample=Image.BILINEAR)
        return im.resize((Wd, Ht), Image.BILINEAR)
    def paste(fr, im, bb, alpha=1.0, dy=0):
        if im is None or alpha <= 0.003: return
        if alpha < 0.999: im = im.copy(); im.putalpha(im.getchannel("A").point(lambda v: int(v * alpha)))
        fr.alpha_composite(im, (bb[0], int(round(bb[1] + dy))))
    def state_at(t):
        for e in TL:
            if e["s"] <= t < e["e"]: return e
        return TL[-1] if t >= TL[-1]["s"] else None
    def last_state_of(i):
        es = [e for e in TL if e["phrase"] == i]; return es[-1] if es else None
    def scene(t, f, spk):
        fr = BG.copy().convert("RGBA"); fr.alpha_composite(bloom(t))
        sc = 1 + 0.03 * L[f]; pw, ph = PFP[spk].size; pim = PFP2[spk].resize((round(pw * sc), round(ph * sc)), Image.LANCZOS)
        ox = round(cx - (cx - PB[0]) * sc); oy = round(cy - (cy - PB[1]) * sc); fr.alpha_composite(pim, (ox, oy))
        fr.alpha_composite(ring(f), (cx - EXT, cy - EXT))
        R = (P / 2 + 10) * sc; m = Image.new("L", pim.size, 0); ImageDraw.Draw(m).ellipse([cx - ox - R, cy - oy - R, cx - ox + R, cy - oy + R], fill=255)
        top = pim.copy(); top.putalpha(ImageChops.multiply(pim.getchannel("A"), m)); fr.alpha_composite(top, (ox, oy))
        e = state_at(t)
        if e and not e["hero"]:
            i = e["phrase"]; a = 1.0 if i == 0 else ease((t - PH_START[i]) / 0.2)   # first phrase is on screen at frame 0
            if a < 1 and i > 0:
                pe = last_state_of(i - 1)
                if pe: paste(fr, *layer(pe["png"]), alpha=1 - a, dy=-18 * a)
            paste(fr, *layer(e["png"]), alpha=a, dy=28 * (1 - a))
        elif e and e["hero"] and t - HS < 0.25:
            pe = last_state_of(e["phrase"] - 1); a = ease((t - HS) / 0.25)
            if pe: paste(fr, *layer(pe["png"]), alpha=1 - a, dy=-18 * a)
        return fr
    def hero_treat(fr, h, z):
        if z > 1.0005:
            w2, h2 = round(Wd / z), round(Ht / z); x0 = round(960 - 960 / z); y0 = round(cy - cy / z)
            fr = fr.crop((x0, y0, x0 + w2, y0 + h2)).resize((Wd, Ht), Image.LANCZOS)
        if h > 0: fr.alpha_composite(Image.new("RGBA", (Wd, Ht), SCRIM + (int(255 * SCRIM_A * h),)))
        return fr
    def chrome(fr, spk, prog, chip_a=1.0):
        fr.alpha_composite(UI[spk]); paste(fr, CHIP[spk], CHB, alpha=chip_a)
        px0, px1, py = X0 + 40, X1 - 40, G["progy"]
        tr = Image.new("RGBA", (px1 - px0 + 1, 3), (0, 0, 0, 0)); ImageDraw.Draw(tr).rounded_rectangle([0, 0, px1 - px0, 2], radius=1, fill=TRACK); fr.alpha_composite(tr, (px0, py))
        wpx = (px1 - px0) * max(0, min(1, prog))
        if wpx > 2:
            g = np.linspace(0, 1, int(wpx))[:, None]; col = (PST + (np.array(ACC, float) - PST) * g).astype(np.uint8)
            fr.paste(Image.fromarray(np.repeat(col[None], 3, axis=0), "RGB"), (px0, py))
        dx = px0 + wpx; gl_ = Image.new("RGBA", (40, 40), (0, 0, 0, 0)); ImageDraw.Draw(gl_).ellipse([11, 11, 29, 29], fill=ACC + (170,)); gl_ = gl_.filter(ImageFilter.GaussianBlur(5))
        fr.alpha_composite(gl_, (int(dx - 20), py + 1 - 20)); ImageDraw.Draw(fr).ellipse([dx - 6, py + 1 - 6, dx + 6, py + 1 + 6], fill=KNOB)
        return fr
    def finish(fr, F):
        return np.clip(np.asarray(fr.convert("RGB"), dtype=np.float32) + GRAIN[(F // HOLD) % len(GRAIN)] + 0.5, 0, 255).astype(np.uint8)
    def frame(t):
        f = min(NF - 1, int(round(t * FPS))); spk = W.speaker_at(TURNS, t); fr = scene(t, f, spk)
        if t >= HS:
            hp = (t - HS) / max(0.1, DUR - HS); fr = hero_treat(fr, ease((t - HS) / 0.4), 1 + 0.035 * (0.5 - 0.5 * math.cos(math.pi * min(1, hp))))
            e = state_at(t); a = ease((t - HS - 0.05) / 0.3)
            if e and e["hero"]: paste(fr, *layer(e["png"]), alpha=a, dy=22 * (1 - a))
        fr = Image.fromarray(np.clip(np.asarray(fr.convert("RGB"), dtype=np.float32) * VIG + 0.5, 0, 255).astype(np.uint8), "RGB").convert("RGBA")   # scene only
        return chrome(fr, spk, t / DUR, 1 - ease((t - HS) / 0.25) if t >= HS else 1.0)
    if dump:
        outs = []
        for T in dump:
            p = os.path.join(clip_dir, f"ref_{T:06.2f}.png"); Image.fromarray(finish(frame(T), int(round(T * FPS)))).save(p); outs.append(p)
        return outs
    if master_wav and os.path.exists(master_wav):
        ain = ["-ss", f"{t0:.3f}", "-t", f"{max(0.05, T1 - t0):.3f}", "-i", master_wav]; log("audio: mastered " + master_wav)
    else:
        ain = ["-ss", f"{ST + t0:.3f}", "-t", f"{max(0.05, T1 - t0):.3f}", "-i", source_audio]; log("audio: RAW (no master found)")
    ff = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{Wd}x{Ht}", "-r", str(FPS), "-i", "-",
        *ain, "-map", "0:v", "-map", "1:a", "-af", "apad", "-vf", VF,
        "-c:v", "libx264", "-preset", "slow", "-crf", str(crf), "-tune", "film", "-pix_fmt", "yuv420p", "-threads", str(threads),
        "-x264-params", "deblock=-1,-1:aq-mode=1:aq-strength=0.8:colorprim=bt709:transfer=bt709:colormatrix=bt709:range=tv",
        "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv",
        "-c:a", "aac", "-b:a", "320k", "-ar", "48000", "-ac", "2", "-movflags", "+faststart", "-shortest", outf], stdin=subprocess.PIPE)
    F0, F1 = int(round(t0 * FPS)), int(round(T1 * FPS))
    for F in range(F0, F1):
        ff.stdin.write(finish(frame(F / FPS), F).tobytes())
        if (F - F0) % 150 == 0: log(f"frame {F}/{F1}")
    ff.stdin.close(); ff.wait()
    if ff.returncode: raise SystemExit(f"ffmpeg failed ({ff.returncode})")
    return outf
