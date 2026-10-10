"""Viewer style QA for a finished clip: ffprobe specs, loudness, colour tags, and stills at
0, 0.1, 0.25, 0.5, 1.0 s, the midpoint, the last 2 s and the very last frame, plus a contact sheet.
Also checks the caption text for em dashes and ALL CAPS words."""
import json, os, re, subprocess
from PIL import Image, ImageDraw, ImageFont, ImageStat
from .master_audio import measure

def probe(f):
    j = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", f], capture_output=True, text=True).stdout)
    v = next(s for s in j["streams"] if s["codec_type"] == "video"); a = next((s for s in j["streams"] if s["codec_type"] == "audio"), None)
    return v, a, float(j["format"]["duration"])

def grab(f, t, out, sseof=False):
    cmd = ["ffmpeg", "-v", "error", "-y"] + (["-sseof", "-0.25"] if sseof else ["-ss", f"{t:.3f}"]) + ["-i", f]
    cmd += (["-update", "1"] if sseof else ["-frames:v", "1"]) + [out]
    subprocess.run(cmd, check=True); return out

def check(f, still_dir, tag, caption_text=""):
    os.makedirs(still_dir, exist_ok=True)
    v, a, dur = probe(f); fails = []; notes = []
    spec = dict(codec=v["codec_name"], size=f'{v["width"]}x{v["height"]}', fps=v.get("r_frame_rate"), pix=v.get("pix_fmt"),
                matrix=v.get("color_space"), primaries=v.get("color_primaries"), trc=v.get("color_transfer"), range=v.get("color_range"),
                audio=(a or {}).get("codec_name"), ar=(a or {}).get("sample_rate"), dur=round(dur, 3))
    if spec["codec"] != "h264": fails.append("video is not H.264")
    if spec["size"] != "1920x1080": fails.append("not 1920x1080")
    if spec["fps"] != "30/1": fails.append("not 30 fps")
    if not (spec["matrix"] == spec["primaries"] == spec["trc"] == "bt709"): fails.append("missing BT.709 colour tags")
    if spec["range"] != "tv": fails.append("colour range not tv")
    if spec["audio"] != "aac": fails.append("audio is not AAC")
    loud = measure(["-i", f])
    if abs(loud["I"] + 14) > 1.0: fails.append(f"loudness {loud['I']} LUFS, want -14")
    if loud["TP"] > -0.5: fails.append(f"true peak {loud['TP']} dBTP, want -1")
    pts = [("f0", 0.0), ("t0.10", 0.10), ("t0.25", 0.25), ("t0.50", 0.50), ("t1.00", 1.0), ("mid", dur / 2),
           ("end-2.0", max(0, dur - 2.0)), ("end-1.0", max(0, dur - 1.0))]
    paths = []
    for name, t in pts: paths.append((name, grab(f, t, os.path.join(still_dir, f"{tag}_{name}.png"))))
    paths.append(("last", grab(f, 0, os.path.join(still_dir, f"{tag}_last.png"), sseof=True)))
    # frame 0 must already be the full design: not blank, and close in overall look to the 1 s frame
    s0 = ImageStat.Stat(Image.open(paths[0][1]).convert("L")); s1 = ImageStat.Stat(Image.open(paths[4][1]).convert("L"))
    if s0.stddev[0] < 12: fails.append("frame 0 looks blank (possible intro or fade in)")
    if abs(s0.mean[0] - s1.mean[0]) > 18: fails.append("frame 0 differs a lot from the 1 s frame (possible fade in)")
    sl = ImageStat.Stat(Image.open(paths[-1][1]).convert("L"))
    if sl.stddev[0] < 12: fails.append("last frame looks blank (possible fade out)")
    if "\u2014" in caption_text: fails.append("em dash in captions")
    caps = [w for w in re.findall(r"\b[A-Z]{4,}\b", caption_text)]
    if caps: notes.append("all caps words in captions (fine if acronyms): " + ", ".join(sorted(set(caps))))
    # contact sheet
    th = [Image.open(p).resize((480, 270)) for _, p in paths]; sheet = Image.new("RGB", (480 * 3 + 40, (270 + 30) * 3 + 10), (20, 20, 20))
    d = ImageDraw.Draw(sheet)
    for i, ((name, _), im) in enumerate(zip(paths, th)):
        x = 10 + (i % 3) * 490; y = 10 + (i // 3) * 300; sheet.paste(im, (x, y)); d.text((x, y + 274), name, fill=(230, 230, 230))
    sheet_p = os.path.join(still_dir, f"{tag}_sheet.png"); sheet.save(sheet_p)
    return dict(file=f, ok=not fails, fails=fails, notes=notes, spec=spec, loudness=loud, stills=dict(paths), sheet=sheet_p)
