"""Build a brand profile automatically from a website, a logo file, or a brand kit PDF.
Everything is a best guess with sensible defaults. The user can tweak any of it later.
  from_url(url, out_dir)    logo (inline SVG, logo <img>, touch icon), colors (theme-color, logo, CSS, screenshot), name
  from_file(path, out_dir)  logo image (SVG/PNG/JPG/WebP) or brand kit PDF: dominant colors, hex codes, name
Writes out_dir/brand_profile.json and out_dir/assets/*. Returns (profile, notes)."""
import colorsys, json, os, re, shutil, subprocess, tempfile, urllib.parse, urllib.request
from collections import Counter
from PIL import Image
from .theme import rgb, hexc, mix, contrast, lum

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def chrome_bin():
    for b in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        if shutil.which(b): return shutil.which(b)
    raise SystemExit("Chrome or Chromium not found. Run kit/install.sh")

def chrome(args, timeout=60):
    ud = tempfile.mkdtemp(prefix="agbr_")
    try:
        return subprocess.run([chrome_bin(), "--headless=new", "--no-sandbox", "--disable-gpu", "--hide-scrollbars", f"--user-data-dir={ud}",
                               f"--user-agent={UA}", *args], capture_output=True, text=True, timeout=timeout)
    finally: shutil.rmtree(ud, ignore_errors=True)

def fetch(url, limit=4_000_000):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read(limit), r.headers.get("Content-Type", ""), r.geturl()

def raster(path_or_url, out_png, size=(800, 400)):
    """Render any image (SVG included) on a transparent background with Chrome, trimmed to its content."""
    src = path_or_url if re.match(r"^(https?|file|data):", path_or_url) else "file://" + os.path.abspath(path_or_url)
    hp = tempfile.mktemp(suffix=".html")
    open(hp, "w").write(f'<html><body style="margin:0;background:transparent"><img src="{src}" style="max-width:{size[0]}px;max-height:{size[1]}px;'
                        f'min-height:{size[1]//2}px;object-fit:contain;display:block"></body></html>')
    chrome(["--default-background-color=00000000", "--allow-file-access-from-files", f"--window-size={size[0]},{size[1]}",
            "--virtual-time-budget=3000", f"--screenshot={out_png}", "file://" + hp]); os.remove(hp)
    im = Image.open(out_png).convert("RGBA"); bb = im.getchannel("A").point(lambda v: 255 if v > 8 else 0).getbbox()
    if bb: im = im.crop(bb)
    im.save(out_png); return im

# ---------------- colour helpers ----------------
def hsl(h):
    r, g, b = [v / 255 for v in rgb(h)]; hh, l, s = colorsys.rgb_to_hls(r, g, b); return hh, s, l

def from_hsl(hh, s, l):
    r, g, b = colorsys.hls_to_rgb(hh % 1, max(0, min(1, l)), max(0, min(1, s))); return hexc((r * 255, g * 255, b * 255))

def vivid(h, relaxed=False):
    _, s, l = hsl(h)
    return (s >= 0.30 and 0.08 <= l <= 0.88) if relaxed else (s >= 0.35 and 0.18 <= l <= 0.85)

def dominant(im, k=8):
    """[(hex, share, saturation)] of the opaque, non grey pixels of an image."""
    im = im.convert("RGBA"); im.thumbnail((200, 200))
    px = [p[:3] for p in im.getdata() if p[3] > 200]
    if not px: return []
    base = Image.new("RGB", (len(px), 1)); base.putdata(px)
    q = base.quantize(colors=k, method=Image.Quantize.MEDIANCUT); pal = q.getpalette()[:k * 3]; cnt = Counter(q.getdata())
    out = []
    for idx, n in cnt.most_common():
        c = hexc(pal[idx * 3: idx * 3 + 3]); _, s, l = hsl(c); out.append((c, n / len(px), s, l))
    return out

def same_hue(a, b, tol=0.06):
    ha, sa, la = hsl(a); hb, sb, lb = hsl(b); d = abs(ha - hb); return min(d, 1 - d) < tol

def pick_palette(cands, page_dark=None, relaxed=False):
    """cands: [(hex, weight)]. Returns accent, primary, background_dark, background_light, notes."""
    score = Counter()
    if os.environ.get("AG_DEBUG"): print("candidates:", sorted(((round(w, 3), c) for c, w in cands if vivid(c)), reverse=True)[:25])
    for c, w in cands:
        if not vivid(c, relaxed): continue
        key = next((k for k in score if same_hue(k, c) and abs(hsl(k)[2] - hsl(c)[2]) < 0.2), c)
        score[key] += w * (0.5 + hsl(c)[1])
    if not score:
        return pick_palette(cands, page_dark, True) if not relaxed else None
    ranked = [c for c, _ in score.most_common()]
    brand = ranked[0]; hh, s, l = hsl(brand)
    # accent: the brand hue, bright enough to pop on a very dark background
    acc = brand; bgd = from_hsl(hh, min(0.75, max(0.35, s * 0.8)), 0.085)
    while contrast(acc, bgd) < 5.5 and hsl(acc)[2] < 0.8: acc = from_hsl(hh, max(s, 0.6), hsl(acc)[2] + 0.04)
    # primary: a deeper version for text on light backgrounds
    prim = brand
    bgl = from_hsl(hh, 0.30, 0.975)
    while contrast(prim, bgl) < 4.8 and hsl(prim)[2] > 0.12: prim = from_hsl(hh, s, hsl(prim)[2] - 0.04)
    second = next((c for c in ranked[1:] if not same_hue(c, brand, 0.1)), None)
    return dict(accent=acc, primary=prim, background_dark=bgd, background_light=bgl), brand, second

def logo_modes(logo_png, colors):
    """Keep a colourful logo in colour where it reads well, otherwise use a clean white or dark version."""
    if not logo_png or not os.path.exists(logo_png): return "white", "dark"
    dom = dominant(Image.open(logo_png))
    colourful = sum(sh for c, sh, s, l in dom if s > 0.3 and 0.15 < l < 0.9) > 0.35
    if not colourful: return "white", "dark"
    main = max(dom, key=lambda d: d[1] * (d[2] + 0.2))[0]
    d = "original" if contrast(main, colors["background_dark"]) >= 3 else "white"
    li = "original" if contrast(main, colors["background_light"]) >= 3 else "dark"
    return d, li

def defaults(name, link, tagline, colors, theme, logo, notes):
    return {
        "_readme": "Made automatically by Audiogram Maker. Every field can be changed. See brand_profile.template.json for all options.",
        "_notes": notes,
        "brand_name": name, "logo": logo, "colors": colors, "default_theme": theme,
        "font": {"family": "Inter", "file": ""}, "link": link, "promo_code": "",
        "footer": {"cta_line": "Visit", "code_label": "Code:", "button_text": "", "hosts_label": "Hosts",
                   "footer_text": tagline or f"Clips from {name}"},
        "hosts": [], "spelling": [name] if name and " " not in name else [],
        "captions": {"size": "medium", "weight": 600, "highlight": "underline", "phrase_words": [4, 8]},
        "format": {"aspect": "16:9"}, "delivery": {"destination": "chat", "drive_folder_id": ""}}

def short(t, n=46):
    """A short footer line from the site description, only if a whole sentence or clause fits. Never cut mid thought."""
    t = re.sub(r"\s+", " ", (t or "")).strip().replace("\u2014", ",").replace("\u2013", "-")
    for part in re.split(r"(?<=[.!?])\s+|\s*[|:;]\s*", t):
        part = part.strip().rstrip(".")
        if 8 <= len(part) <= n: return part
    return ""

def clean_name(title, host):
    base = host.split(".")[-2] if host.count(".") else host
    if not title: return base.capitalize()
    parts = [p.strip() for p in re.split(r"\s[|\-\u2013\u2014:\u00b7]\s", title) if p.strip()]
    for p in parts:   # prefer the segment that contains the domain word
        if base.lower() in p.lower().replace(" ", ""): return p if len(p) <= 24 else base.capitalize()
    return parts[-1] if parts and len(parts[-1]) <= 24 else base.capitalize()

# ---------------- website ----------------
def from_url(url, out_dir):
    if not re.match(r"^https?://", url): url = "https://" + url
    os.makedirs(os.path.join(out_dir, "assets"), exist_ok=True); notes = []
    from bs4 import BeautifulSoup
    # 1. rendered DOM (handles JS sites), falling back to the raw HTML
    dom = chrome(["--virtual-time-budget=8000", "--dump-dom", url], timeout=90).stdout
    try: raw, _, final = fetch(url); raw = raw.decode("utf-8", "ignore")
    except Exception as e: raw, final = "", url; notes.append(f"plain fetch failed: {e}")
    html = dom if len(dom) > len(raw) * 0.5 and len(dom) > 2000 else raw
    if not html: raise SystemExit("Could not load the website. Ask the user for a logo file instead.")
    soup = BeautifulSoup(html, "html.parser"); host = urllib.parse.urlparse(final).netloc.lower().removeprefix("www.")
    meta = lambda **kw: (soup.find("meta", attrs=kw) or {}).get("content")
    title = meta(property="og:site_name") or (soup.title.string if soup.title and soup.title.string else "")
    name = clean_name(title.strip(), host)
    tagline = short(meta(name="description") or meta(property="og:description") or "")
    cands = []
    tc = meta(name="theme-color")
    if tc and re.match(r"^#[0-9a-fA-F]{3,6}$", tc.strip()): cands.append((tc.strip(), 3.0)); notes.append(f"theme-color {tc}")
    # 2. logo: inline SVG or <img> marked as a logo, else touch icon / favicon
    logo_file = None; style = "wordmark"
    def is_logo(t):
        blob = " ".join([str(t.get("class", "")), str(t.get("id", "")), str(t.get("aria-label", "")), str(t.get("alt", "")),
                         str(t.get("src", "")), str(t.get("title", ""))]).lower()
        return "logo" in blob or (name.lower() in blob and len(name) > 2)
    for t in soup.find_all(["svg", "img", "a"])[:400]:
        if not is_logo(t): continue
        svg = t if t.name == "svg" else t.find("svg")
        img = t if t.name == "img" else t.find("img")
        try:
            if svg is not None and len(str(svg)) > 200:
                s = str(svg)
                if "xmlns=" not in s: s = s.replace("<svg", '<svg xmlns="http://www.w3.org/2000/svg"', 1)
                s = s.replace("currentColor", "#111111")
                for k in ("viewBox", "preserveAspectRatio", "gradientUnits", "gradientTransform", "clipPathUnits", "maskUnits",
                          "patternUnits", "stdDeviation", "textLength", "lengthAdjust", "markerWidth", "markerHeight", "refX", "refY"):
                    s = re.sub(r"\b" + k.lower() + "=", k + "=", s)   # the HTML parser lowercases SVG attributes
                p = os.path.join(out_dir, "assets", "logo_source.svg"); open(p, "w").write(s); logo_file = p
            elif img is not None and img.get("src"):
                u = urllib.parse.urljoin(final, img["src"]); data, ct, _ = fetch(u)
                ext = ".svg" if ("svg" in ct or u.split("?")[0].endswith(".svg")) else ".png"
                p = os.path.join(out_dir, "assets", "logo_source" + ext); open(p, "wb").write(data); logo_file = p
        except Exception as e: notes.append(f"logo candidate skipped: {e}"); continue
        if logo_file:
            png = os.path.join(out_dir, "assets", "logo_check.png")
            try:
                im = raster(logo_file, png)
                if im.width < 40 or im.height < 12 or im.getchannel("A").getbbox() is None: logo_file = None; continue
            except Exception: logo_file = None; continue
            notes.append("logo from page: " + os.path.basename(logo_file)); break
    if not logo_file:
        icons = sorted([l for l in soup.find_all("link") if any(r in (l.get("rel") or []) for r in ("apple-touch-icon", "icon"))],
                       key=lambda l: (0 if "svg" in (l.get("type") or l.get("href", "")) else 1, -int((l.get("sizes") or "0x0").split("x")[0] or 0)))
        for l in icons + [None]:
            try:
                u = urllib.parse.urljoin(final, l["href"] if l else "/favicon.ico"); data, ct, _ = fetch(u)
                ext = ".svg" if "svg" in ct or u.split("?")[0].endswith(".svg") else ".png"
                p = os.path.join(out_dir, "assets", "icon_source" + ext); open(p, "wb").write(data)
                raster(p, os.path.join(out_dir, "assets", "logo_check.png"), (256, 256)); logo_file = p; style = "icon"
                notes.append("no wordmark found, using the site icon next to the brand name"); break
            except Exception: continue
    if logo_file:
        for c, sh, s, l in dominant(Image.open(os.path.join(out_dir, "assets", "logo_check.png"))):
            cands.append((c, 4.0 * sh))
    # 3. CSS colours (inline styles + first stylesheets)
    css = " ".join(s.get_text() for s in soup.find_all("style"))
    for l in [l for l in soup.find_all("link") if "stylesheet" in (l.get("rel") or [])][:5]:
        try: css += fetch(urllib.parse.urljoin(final, l["href"]), 1_500_000)[0].decode("utf-8", "ignore")
        except Exception: pass
    hx = Counter(h.upper() if len(h) == 7 else ("#" + "".join(c * 2 for c in h[1:])).upper() for h in re.findall(r"#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b", css))
    tot = sum(hx.values()) or 1
    for h, n in hx.most_common(40): cands.append((h, 2.0 * n / tot))
    def hex6(h): return (h if len(h) == 7 else "#" + "".join(c * 2 for c in h[1:])).upper()
    named = Counter()
    for m in re.finditer(r"--[\w-]*(?:brand|primary|accent|cta)[\w-]*\s*:\s*(#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b)", css): named[hex6(m.group(1))] += 1
    for sel, body in re.findall(r"([^{}]{1,300})\{([^{}]{1,2000})\}", css):
        if re.search(r"btn|button|cta|primary|brand|accent", sel, re.I):
            for h in re.findall(r"#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b", body): named[hex6(h)] += 1
    ntot = sum(named.values()) or 1
    for h, n in named.most_common(20): cands.append((h, 3.0 * n / ntot))
    if named: notes.append("button / brand CSS colors: " + ", ".join(h for h, _ in named.most_common(3)))
    # 4. what the page actually looks like
    shot = os.path.join(out_dir, "assets", "site_screenshot.png"); page_dark = None
    try:
        chrome(["--window-size=1440,900", "--virtual-time-budget=8000", f"--screenshot={shot}", url], timeout=90)
        sim = Image.open(shot).convert("RGB")
        g = sim.convert("L").resize((144, 90)); page_dark = sorted(g.getdata())[len(g.getdata()) // 2] < 90
        for c, sh, s, l in dominant(sim, 10): cands.append((c, 1.0 * sh))
    except Exception as e: notes.append(f"screenshot failed: {e}")
    res = pick_palette(cands, page_dark)
    if not res:
        colors = dict(accent="#4DA3FF", primary="#1F5FBF", background_dark="#0B1626", background_light="#F7F9FC")
        notes.append("no clear brand color found, used a neutral blue: ask the user")
    else:
        colors, brand, second = res; notes.append(f"brand color {brand}" + (f", second color {second}" if second else ""))
    theme = "dark" if page_dark is None or page_dark else "light"
    notes.append(f"site looks {'dark' if page_dark else 'light'}, default style {theme}")
    return finish(out_dir, name, host, tagline, colors, theme, logo_file, style, notes)

def finish(out_dir, name, link, tagline, colors, theme, logo_file, style, notes):
    logo = {"on_dark": "", "on_light": "", "mark": "", "style": style, "dark_mode": "white", "light_mode": "dark"}
    if logo_file:
        rel = os.path.relpath(logo_file, out_dir); logo.update(on_dark=rel, on_light=rel)
        dm, lm = logo_modes(os.path.join(out_dir, "assets", "logo_check.png"), colors)
        logo.update(dark_mode=dm, light_mode=lm); notes.append(f"logo on dark: {dm}, on light: {lm}")
        if style == "icon": logo.update(dark_mode="original", light_mode="original")   # app icons keep their own tile
        elif dm == "white" or lm == "dark": logo["mark"] = rel                      # mono logos make a clean faint watermark
    else: notes.append("no logo found: the brand name is set as text")
    prof = defaults(name, link, tagline, colors, theme, logo, notes)
    json.dump(prof, open(os.path.join(out_dir, "brand_profile.json"), "w"), indent=2)
    return prof, notes

# ---------------- logo or brand kit file ----------------
def from_file(path, out_dir, name=None):
    os.makedirs(os.path.join(out_dir, "assets"), exist_ok=True); notes = []
    ext = os.path.splitext(path)[1].lower(); cands = []; logo_file = None
    if ext == ".pdf":
        txt = subprocess.run(["pdftotext", "-l", "6", path, "-"], capture_output=True, text=True).stdout
        for h in re.findall(r"#?\b([0-9A-Fa-f]{6})\b", txt): cands.append(("#" + h.upper(), 3.0))
        pre = os.path.join(out_dir, "assets", "kit_page")
        subprocess.run(["pdftoppm", "-png", "-r", "40", "-l", "4", path, pre], capture_output=True)
        for f in sorted(os.listdir(os.path.join(out_dir, "assets"))):
            if f.startswith("kit_page"):
                for c, sh, s, l in dominant(Image.open(os.path.join(out_dir, "assets", f)), 8): cands.append((c, 1.5 * sh))
        notes.append("brand kit PDF: colors read from the hex codes and pages; ask the user to upload the logo file itself")
        if not name:
            first = next((l.strip() for l in txt.splitlines() if l.strip()), "")
            name = first[:24] if first else None
    else:
        logo_file = os.path.join(out_dir, "assets", "logo_source" + ext); shutil.copy(path, logo_file)
        im = raster(logo_file, os.path.join(out_dir, "assets", "logo_check.png"))
        for c, sh, s, l in dominant(im, 8): cands.append((c, 4.0 * sh))
        if ext == ".svg":
            for h in re.findall(r"#[0-9a-fA-F]{6}\b", open(path, errors="ignore").read()): cands.append((h.upper(), 1.0))
        style = "wordmark" if im.width >= im.height * 1.8 else "icon"
    name = name or re.sub(r"[-_ ]*(logo|on|dark|light|white|black|full|mark|color|colour)\b.*$", "", os.path.splitext(os.path.basename(path))[0], flags=re.I).replace("-", " ").replace("_", " ").strip().title() or "Your Brand"
    res = pick_palette(cands)
    if not res:
        colors = dict(accent="#4DA3FF", primary="#1F5FBF", background_dark="#0B1626", background_light="#F7F9FC")
        notes.append("logo has no clear color (mono logo): used a neutral blue accent, ask the user for their brand color")
    else:
        colors, brand, second = res; notes.append(f"brand color {brand}" + (f", second color {second}" if second else ""))
    return finish(out_dir, name, "", "", colors, "dark", logo_file, style if logo_file else "wordmark", notes)
