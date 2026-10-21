"""Per frame compositor: audio reactive ring, breathing photo, drifting bloom, karaoke captions, payoff takeover, progress bar.
Rules baked in (do not change without the user asking):
  * No hook or intro. Frame 0 is the full design (background, photo, ring, top bar, footer, name chip, first caption line)
    and the audio starts at frame 0. No fade in, no silent lead in, no crossfade.
  * No fade out. The last word plays in full and the video cuts cleanly.
  * Sharp and vivid: ring bars drawn at 2x then Lanczos downscaled with a tight 1.2px halo, photo scaled from a 2x render,
    solid footer (no frosted blur), grain 1.0 as dither only, vignette on the scene only.
  * Colour: real RGB to BT.709 conversion in swscale plus bt709 / tv tags so players decode with the right matrix.
  * First caption word is lit from frame 0. Upcoming words are quieter but stay readable (3.4:1 or more).
  * Payoff takeover: the speaker photo, ring, glow, name chip, last caption and watermark fade fully to 0 in the 0.35 s
    before the payoff (never while a caption word is still lit), then the payoff line fades in 0.05 s after its first
    word starts, on a clean background with nothing behind it.
  * Export (v1.2 default, "share"): ONE encode straight to the small shareable file: 1920x1080 30 fps, x264 preset slow,
    CRF 23, maxrate 3M, bufsize 6M, yuv420p, BT.709, AAC 160k 48 kHz, +faststart (about 6 to 7 MB for 35 s).
    quality="master" keeps the old archival encode (CRF 12, tune film, AAC 320k) for users who ask for it.
    Audio is the mastered WAV (-14 LUFS / -1 dBTP).
  * Speed: frames are drawn by several worker processes in parallel and fed to x264 in order. Same pixels, faster."""
import os, json, math, subprocess
from collections import deque
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

_PRODUCE = None
def _work(F): return _PRODUCE(F)

# Payoff takeover: the speaker (photo, ring, glow), name chip, last caption and watermark fade fully to 0 BEFORE the
# payoff text starts; the payoff then fades in on a clean background with nothing behind it.
SPK_LEAD = 0.35; SPK_FADE = 0.30; HERO_TEXT_DELAY = 0.05; HERO_TEXT_FADE = 0.30

def payoff_timing(TLJ):
    """(fade_start, fade_end, text_start). The fade never starts before the previous word has finished, and it
    always ends before the payoff text begins."""
    HS = TLJ["hero_start"]
    prev_end = max([e["e"] for e in TLJ["timeline"] if not e["hero"] and e["cur"] is not None and e["s"] < HS] or [0.0])
    fs = max(HS - SPK_LEAD, min(prev_end, HS - 0.17)); fs = max(0.0, fs)
    fe = min(fs + SPK_FADE, HS + HERO_TEXT_DELAY - 1 / 30)
    ts = round(HS + HERO_TEXT_DELAY, 3)
    return round(fs, 3), min(round(fe, 3), math.floor((ts - 1 / 30) * 1000) / 1000), ts   # rounding never lets the fade end late

ENC = {"share": dict(v=["-preset", "slow", "-crf", "23", "-maxrate", "3M", "-bufsize", "6M"], a="160k"),
       "master": dict(v=["-preset", "slow", "-crf", "12", "-tune", "film"], a="320k")}

def render(clip_dir, source_audio, master_wav, outf, t0=0.0, t1=None, dump=None, crf=None, threads=0, workers=None, log=print, quality="share"):
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
    BGC = load("bg_clean.png").convert("RGB") if os.path.exists(os.path.join(clip_dir, "layers", "bg_clean.png")) else BG   # payoff: no watermark
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
    FS, FE, TS = payoff_timing(TLJ) if TLJ.get("hero_phrase") else (DUR + 1, DUR + 2, DUR + 3)
    def spk_alpha(t): return 1.0 if t <= FS else max(0.0, 1 - ease((t - FS) / max(1e-3, FE - FS)))
    def scene(t, f, spk):
        k = spk_alpha(t)
        if k <= 0.003: return BGC.copy().convert("RGBA")          # nothing behind the payoff, not even the watermark
        base = BG if k >= 0.999 else Image.blend(BGC, BG, k)
        fr = base.copy().convert("RGBA"); sp = fr if k >= 0.999 else Image.new("RGBA", (Wd, Ht), (0, 0, 0, 0))
        sp.alpha_composite(bloom(t))
        sc = 1 + 0.03 * L[f]; pw, ph = PFP[spk].size; pim = PFP2[spk].resize((round(pw * sc), round(ph * sc)), Image.LANCZOS)
        ox = round(cx - (cx - PB[0]) * sc); oy = round(cy - (cy - PB[1]) * sc); sp.alpha_composite(pim, (ox, oy))
        sp.alpha_composite(ring(f), (cx - EXT, cy - EXT))
        R = (P / 2 + 10) * sc; m = Image.new("L", pim.size, 0); ImageDraw.Draw(m).ellipse([cx - ox - R, cy - oy - R, cx - ox + R, cy - oy + R], fill=255)
        top = pim.copy(); top.putalpha(ImageChops.multiply(pim.getchannel("A"), m)); sp.alpha_composite(top, (ox, oy))
        if k < 0.999: sp.putalpha(sp.getchannel("A").point(lambda v: int(v * k))); fr.alpha_composite(sp)
        e = state_at(t)
        if e and e["hero"]: e = last_state_of(e["phrase"] - 1)   # the last caption stays until it has faded out
        if e:
            i = e["phrase"]; a = 1.0 if i == 0 else ease((t - PH_START[i]) / 0.2)   # first phrase is on screen at frame 0
            if a < 1 and i > 0:
                pe = last_state_of(i - 1)
                if pe: paste(fr, *layer(pe["png"]), alpha=(1 - a) * k, dy=-18 * a)
            paste(fr, *layer(e["png"]), alpha=a * k, dy=28 * (1 - a))
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
        f = min(NF - 1, int(round(t * FPS))); spk = W.speaker_at(TURNS, t)
        fr = scene(t, f, spk)
        if t >= TS:   # payoff text: only once the speaker, chip and captions are fully gone
            e = state_at(t); a = ease((t - TS) / HERO_TEXT_FADE)
            if e and e["hero"]: paste(fr, *layer(e["png"]), alpha=a, dy=22 * (1 - a))
        fr = Image.fromarray(np.clip(np.asarray(fr.convert("RGB"), dtype=np.float32) * VIG + 0.5, 0, 255).astype(np.uint8), "RGB").convert("RGBA")   # scene only
        return chrome(fr, spk, t / DUR, spk_alpha(t))
    if dump:
        outs = []
        for T in dump:
            p = os.path.join(clip_dir, f"ref_{T:06.2f}.png"); Image.fromarray(finish(frame(T), int(round(T * FPS)))).save(p); outs.append(p)
        return outs
    if master_wav and os.path.exists(master_wav):
        ain = ["-ss", f"{t0:.3f}", "-t", f"{max(0.05, T1 - t0):.3f}", "-i", master_wav]; log("audio: mastered " + master_wav)
    else:
        ain = ["-ss", f"{ST + t0:.3f}", "-t", f"{max(0.05, T1 - t0):.3f}", "-i", source_audio]; log("audio: RAW (no master found)")
    global _PRODUCE
    venc = list(ENC[quality]["v"])
    if crf is not None: venc[venc.index("-crf") + 1] = str(crf)   # explicit --crf still wins
    _PRODUCE = lambda F: finish(frame(F / FPS), F).tobytes()
    nw = workers or max(1, min(8, (os.cpu_count() or 2) - 2))
    pool = None
    if nw > 1:
        import multiprocessing as mp
        try: pool = mp.get_context("fork").Pool(nw)   # forked before ffmpeg starts, so workers share all loaded layers
        except Exception as ex: log(f"parallel frames unavailable ({ex}), drawing in one process"); pool = None
    ff = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{Wd}x{Ht}", "-r", str(FPS), "-i", "-",
        *ain, "-map", "0:v", "-map", "1:a", "-af", "apad", "-vf", VF,
        "-c:v", "libx264", *venc, "-pix_fmt", "yuv420p", *(["-threads", str(threads)] if threads else []),
        "-x264-params", "deblock=-1,-1:aq-mode=1:aq-strength=0.8:colorprim=bt709:transfer=bt709:colormatrix=bt709:range=tv",
        "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv",
        "-c:a", "aac", "-b:a", ENC[quality]["a"], "-ar", "48000", "-ac", "2", "-movflags", "+faststart", "-shortest", outf], stdin=subprocess.PIPE)
    F0, F1 = int(round(t0 * FPS)), int(round(T1 * FPS))
    try:
        if pool:
            pend = deque()
            for F in range(F0, F1):
                pend.append(pool.apply_async(_work, (F,)))
                if len(pend) >= nw * 3: ff.stdin.write(pend.popleft().get())   # bounded queue keeps memory flat
                if (F - F0) % 150 == 0: log(f"frame {F}/{F1}")
            while pend: ff.stdin.write(pend.popleft().get())
        else:
            for F in range(F0, F1):
                ff.stdin.write(_PRODUCE(F))
                if (F - F0) % 150 == 0: log(f"frame {F}/{F1}")
    finally:
        if pool: pool.close(); pool.join()
    ff.stdin.close(); ff.wait()
    if ff.returncode: raise SystemExit(f"ffmpeg failed ({ff.returncode})")
    return outf
