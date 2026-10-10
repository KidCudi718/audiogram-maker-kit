"""Build a brand profile automatically from a website, a logo file, or a brand kit PDF.
Everything is a best guess with sensible defaults. The user can tweak any of it later.
  from_url(url, out_dir)    logo (site logos rendered with the site's own CSS, wordmark first), colors, name
  from_file(path, out_dir)  logo image (SVG/PNG/JPG/WebP) or brand kit PDF: dominant colors, hex codes, name
Writes out_dir/brand_profile.json and out_dir/assets/*. Returns (profile, notes).
Look rules (v1.2, the approved default look for every brand):
  * Backgrounds are always neutral (charcoal #0E0E10 dark, gray #DCDCDE light). The brand colour is used only as the
    accent (current word underline, accent words, progress bar). Glow and frame line are neutral.
  * No logo in the top bar. The brand mark (a square-ish logo, or the host's photo) is the first photo of the footer's
    Hosts row. Logos are NEVER recolored: if the original does not read, the notes say so and the user is asked.
  * Captions 84 px, weight 650. No background watermark."""
import colorsys, html as htmlmod, json, os, re, shutil, socket, subprocess, tempfile, urllib.parse, urllib.request
from collections import Counter
import numpy as np
from PIL import Image
from .theme import rgb, hexc, mix, contrast, lum

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEBUG = bool(os.environ.get("AG_DEBUG"))
# third party widgets (sign in buttons, cookie banners, chat, analytics) whose colors are not the brand's
VENDOR = (r"google|gsi|g_id|recaptcha|onetrust|optanon|cookie|consent|intercom|hubspot|drift|zendesk|facebook|twitter|"
          r"linkedin|youtube|apple-?pay|gpay|paypal|klarna|osano|cookiebot|gtm|doubleclick|hotjar|crisp|tawk|livechat|olark|"
          r"qualified|clearbit|truste|didomi|usercentrics")
THIRD_PARTY_COLORS = {"#4285F4", "#1A73E8", "#34A853", "#FBBC04", "#FBBC05", "#EA4335", "#D93025", "#188038", "#1967D2",
                      "#1877F2", "#1DA1F2", "#0A66C2", "#5865F2", "#0070BA", "#003087", "#FFC439", "#25D366", "#68C1B8"}
# logo walls and other companies' logos
LOGO_SKIP = (r"customer|partner|client|social|trusted|testimonial|integration|press|award|badge|store|google-?play|"
             r"avatar|marquee|carousel|sponsor|investor|case-?stud|compan|logos|proof|cookie|onetrust|powered")
LOOK = dict(background_dark="#0E0E10", background_light="#DCDCDE", glow_dark="#8A8A90", glow_light="#FFFFFF",
            edge_dark="#FFFFFF", edge_light="#000000")   # the approved neutral look: identical for every brand
NEUTRAL = dict(accent="#9AA6B8", primary="#1A1A1A", **LOOK)
MARK_MAX_ASPECT = 1.3   # wider logos (wordmarks) cannot sit in a round Hosts photo

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

BLOCKED = r"access denied|just a moment|attention required|are you a robot|verify you are human|captcha|request blocked|403 forbidden|enable javascript and cookies"
MIN_LOGO_PX = 40   # a raster logo shorter than this (and narrower than 3x it) is too small to look sharp at 1080p

def prep_raster(src, notes):
    """Bitmap logo clean up: ICO to PNG, reject tiny images, cut a plain opaque background out to transparency.
    Returns a PNG path to use, or None when the image is unusable."""
    try: im = Image.open(src)
    except Exception: return src   # not a bitmap PIL knows (SVG etc.): leave it to Chrome
    if getattr(im, "format", "") == "ICO":
        try: im = im.ico.getimage(max(im.ico.sizes()))   # largest image inside the favicon
        except Exception: pass
    im = im.convert("RGBA"); w, h = im.size
    if h < MIN_LOGO_PX or max(w, h) < 64:
        notes.append(f"logo image {os.path.basename(src)} is only {w}x{h} px, too small to look sharp; ask the user for a larger logo or an SVG")
        return None
    a = np.asarray(im, np.float32)
    if a[..., 3].min() > 250:   # fully opaque: knock out the background if the corners agree on one colour
        corners = np.array([a[0, 0, :3], a[0, -1, :3], a[-1, 0, :3], a[-1, -1, :3]])
        if np.abs(corners - corners.mean(0)).max() < 18:
            d = np.abs(a[..., :3] - corners.mean(0)).max(-1)
            a[..., 3] = np.clip((d - 14) / 36, 0, 1) * 255
            if (a[..., 3] > 128).mean() > 0.02:
                im = Image.fromarray(a.astype(np.uint8), "RGBA"); notes.append(f"logo {os.path.basename(src)} had a solid background; made it transparent")
            else: return None
        else:
            notes.append(f"logo {os.path.basename(src)} is a photo style image with no clean background; using it only as an icon")
    out = os.path.splitext(src)[0] + "_clean.png"; im.save(out); return out

# ---------------- colour helpers ----------------
def hsl(h):
    r, g, b = [v / 255 for v in rgb(h)]; hh, l, s = colorsys.rgb_to_hls(r, g, b); return hh, s, l

def from_hsl(hh, s, l):
    r, g, b = colorsys.hls_to_rgb(hh % 1, max(0, min(1, l)), max(0, min(1, s))); return hexc((r * 255, g * 255, b * 255))

def vivid(h, relaxed=False):
    _, s, l = hsl(h)
    return (s >= 0.30 and 0.08 <= l <= 0.88) if relaxed else (s >= 0.35 and 0.18 <= l <= 0.85)

def dominant(im, k=8):
    """[(hex, share, saturation, lightness)] of the opaque pixels of an image."""
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

def derive(brand):
    """accent, primary and both backgrounds from one brand colour."""
    hh, s, l = hsl(brand)
    acc = brand; bgd = LOOK["background_dark"]   # backgrounds never take the brand hue
    while contrast(acc, bgd) < 5.5 and hsl(acc)[2] < 0.8: acc = from_hsl(hh, max(s, 0.6), hsl(acc)[2] + 0.04)
    prim = brand; bgl = LOOK["background_light"]
    while contrast(prim, bgl) < 4.8 and hsl(prim)[2] > 0.12: prim = from_hsl(hh, s, hsl(prim)[2] - 0.04)
    return dict(accent=acc, primary=prim, **LOOK)

def score_colors(cands, relaxed=False):
    score = Counter()
    for c, w in cands:
        if not vivid(c, relaxed) or c.upper() in THIRD_PARTY_COLORS: continue
        key = next((k for k in score if same_hue(k, c) and abs(hsl(k)[2] - hsl(c)[2]) < 0.2), c)
        score[key] += w * (0.5 + hsl(c)[1])
    return score

def pick_palette(cands, page_dark=None, relaxed=False):
    """cands: [(hex, weight)]. Returns (colors, brand colour, second colour) or None."""
    score = score_colors(cands, relaxed)
    if DEBUG: print("scores:", [(c, round(v, 2)) for c, v in score.most_common(8)])
    if not score: return pick_palette(cands, page_dark, True) if not relaxed else None
    ranked = [c for c, _ in score.most_common()]; brand = ranked[0]
    second = next((c for c in ranked[1:] if not same_hue(c, brand, 0.1)), None)
    return derive(brand), brand, second

def neutral_palette(hint=None):
    """Black and white brands: neutral backgrounds and one quiet accent (a muted version of their own colour if any)."""
    c = dict(NEUTRAL)
    if hint:
        hh, s, _ = hsl(hint); acc = from_hsl(hh, min(s, 0.32), 0.66)
        while contrast(acc, c["background_dark"]) < 5.5 and hsl(acc)[2] < 0.85: acc = from_hsl(hh, min(s, 0.32), hsl(acc)[2] + 0.03)
        c["accent"] = acc
    return c

# ---------------- logo analysis ----------------
def analyze(png):
    """Shape and colour facts about a rendered logo: aspect, single colour or not, main colour, colourfulness."""
    im = Image.open(png).convert("RGBA"); w, h = im.size
    a = np.asarray(im.resize((max(1, w * 160 // max(w, h)), max(1, h * 160 // max(w, h)))), dtype=np.float32) / 255
    px = a[a[..., 3] > 0.5][:, :3]
    if len(px) < 30: return None
    mx, mn = px.max(1), px.min(1); L = (mx + mn) / 2; d = mx - mn
    S = np.where(d < 1e-6, 0, d / (1 - np.abs(2 * L - 1) + 1e-6))
    Y = 0.2126 * px[:, 0] + 0.7152 * px[:, 1] + 0.0722 * px[:, 2]
    spread = float(np.percentile(Y, 95) - np.percentile(Y, 5))
    sat = S > 0.25
    hue_ok = True
    if sat.sum() > 20:
        hs = np.array([colorsys.rgb_to_hls(*p)[0] for p in px[sat][::max(1, int(sat.sum()) // 400)]])
        ang = np.angle(np.mean(np.exp(2j * np.pi * hs))); hue_ok = float(np.mean(np.abs(np.angle(np.exp(2j * np.pi * hs - 1j * ang))) < 0.35)) > 0.9
    mono = spread < 0.22 and hue_ok
    main = hexc(np.median(px, 0) * 255)
    return dict(w=w, h=h, aspect=w / h, mono=mono, main=main, colourful=float(sat.mean()), spread=spread)

def apply_mode(png, mode):
    im = Image.open(png).convert("RGBA")
    if mode == "original": return im
    r, g, b, a = im.split(); v = 255 if mode == "white" else 0
    if mode == "dark": a = a.point(lambda x: int(x * 0.88))
    return Image.merge("RGBA", (*(Image.new("L", im.size, v),) * 3, a))

def legibility(png, mode, bg):
    """Share of the logo's visible pixels that stand out from the background (contrast 2:1 or more)."""
    im = apply_mode(png, mode); im.thumbnail((240, 240))
    a = np.asarray(im, dtype=np.float32) / 255; m = a[..., 3] > 0.5
    if m.sum() < 30: return 0.0
    B = np.array(rgb(bg), np.float32) / 255; col = a[..., :3] * a[..., 3:4] + B * (1 - a[..., 3:4])
    def rl(x):
        x = np.where(x <= 0.03928, x / 12.92, ((x + 0.055) / 1.055) ** 2.4); return 0.2126 * x[..., 0] + 0.7152 * x[..., 1] + 0.0722 * x[..., 2]
    L1 = rl(col[m]); L0 = float(rl(B[None])[0])
    c = (np.maximum(L1, L0) + 0.05) / (np.minimum(L1, L0) + 0.05)
    return float((c >= 2.0).mean())

def choose_logos(cands, colors, notes):
    """cands: [(file, check_png, info)] best first. Picks a readable logo and mode for each style, or text."""
    res = {}
    for theme, bg in (("dark", colors["background_dark"]), ("light", colors["background_light"])):
        res[theme] = None
        for f, png, info in cands:
            mode = "original"   # logos are never recolored (v1.2)
            lg = legibility(png, mode, bg); need = 0.55 if info["mono"] else (0.6 if info["aspect"] >= 1.8 else 0.5)   # icons carry their own tile
            if DEBUG: print(f"logo {os.path.basename(f)} {theme}: mono={info['mono']} aspect={info['aspect']:.2f} mode={mode} legible={lg:.2f}")
            if lg >= need:
                res[theme] = dict(file=f, mode=mode, style="wordmark" if info["aspect"] >= 1.8 else "icon", info=info, legible=lg); break
        if res[theme] is None and cands:
            notes.append(f"the logo in its own colors does not read well on the {theme} style ({bg}): never recolor it; ask the user for another file")
    return res

# ---------------- profile ----------------
def defaults(name, link, tagline, colors, theme, logo, notes, brand_mark=""):
    """The approved default look. tagline is accepted for compatibility but never shown (the footer is the Hosts row)."""
    return {
        "_readme": "Made automatically by Audiogram Maker. Every field can be changed. See brand_profile.template.json for all options.",
        "_notes": notes,
        "brand_name": name, "brand_mark": brand_mark, "logo": logo, "colors": dict(colors), "default_theme": "dark",
        "font": {"family": "Inter", "file": ""}, "link": link, "promo_code": "",
        "footer": {"cta_line": "", "code_label": "Code:", "button_text": "", "hosts_label": "Hosts", "footer_text": "",
                   "host_names": True, "photo_size": 56, "align": "center"},
        "hosts": [], "spelling": [name] if name and " " not in name else [],
        "captions": {"size": 84, "weight": 650, "highlight": "underline", "phrase_words": [4, 8]},
        "format": {"aspect": "16:9"}, "layout": {"chipy": 352, "capy": 438, "capmaxh": 420}}

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

def finish(out_dir, name, link, tagline, colors, theme, picks, notes, neutral=False):
    small = [n for n in notes if "too small to look sharp" in n]   # only worth mentioning when no usable logo was found
    notes[:] = [n for n in notes if n not in small] + ([] if (picks.get("dark") or picks.get("light")) else small[:1])
    logo = {"on_dark": "", "on_light": "", "mark": "", "style": "none", "style_dark": "none", "style_light": "none",
            "dark_mode": "original", "light_mode": "original", "title_size": 22}
    for th in ("dark", "light"):
        p = picks.get(th)
        if p: logo[f"on_{th}"] = os.path.relpath(p["file"], out_dir); notes.append(f"logo readable on {th}: {os.path.basename(p['file'])} ({p['style']}, original colors)")
    # brand mark for the Hosts row: the first readable logo that is square-ish (a round photo cannot hold a wide wordmark)
    # it must read on the dark main style (it sits on the dark footer), in its own colors
    mark = ""; d = picks.get("dark")
    if d and d["info"]["aspect"] <= MARK_MAX_ASPECT:
        mark = os.path.relpath(d["file"], out_dir); notes.append(f"brand mark (first Hosts photo): {mark}")
        if not picks.get("light"): notes.append("the brand mark does not read on the light style: check the light version by eye")
    elif d:
        notes.append("the logo is a wide wordmark and cannot sit in a round Hosts photo: ask for a square logo (their X profile picture works)")
    elif picks.get("light"):
        notes.append("the logo only reads on light backgrounds, so it is not used on the dark main style: ask for a version that reads on dark, or use their X profile picture")
    if neutral: colors = dict(colors, neutral=True)
    prof = defaults(name, link, tagline, colors, theme, logo, notes, mark)
    json.dump(prof, open(os.path.join(out_dir, "brand_profile.json"), "w"), indent=2)
    return prof, notes

# ---------------- website ----------------
SVG_FIX_JS = """<script>
const s=document.querySelector('#w svg');
const P=['fill','stroke','stroke-width','stroke-linecap','stroke-linejoin','opacity','fill-opacity','stroke-opacity','fill-rule','clip-rule','stop-color','stop-opacity','display','visibility'];
if(s){const r=s.getBoundingClientRect();const els=[s,...s.querySelectorAll('*')];
const vals=els.map(el=>{const cs=getComputedStyle(el);return P.map(p=>cs.getPropertyValue(p));});
els.forEach((el,i)=>{P.forEach((p,j)=>{if(vals[i][j])el.setAttribute(p,vals[i][j]);});el.removeAttribute('class');el.removeAttribute('style');});s.setAttribute('width',Math.round(r.width));s.setAttribute('height',Math.round(r.height));
document.body.innerHTML='<textarea id="out">'+s.outerHTML.replace(/&/g,'&amp;').replace(/</g,'&lt;')+'</textarea>';}
</script>"""

def _attrs(t):
    out = []
    for k in ("class", "id", "style", "data-theme", "data-color-mode", "data-mode"):
        v = t.get(k)
        if v: out.append(f'{k}="{htmlmod.escape(" ".join(v) if isinstance(v, list) else str(v))}"')
    return " ".join(out)

def bake_svg(svg_html, css_links, inline_css, page_url, out_svg, el=None):
    """Render an inline SVG from the page with the site's own CSS and write its computed colours as plain fills
    (resolves CSS variables, currentColor and class based colours). The logo's real ancestors (tags, classes, ids)
    are rebuilt around it so inherited colours and selectors like 'header a.logo svg' apply exactly as on the site."""
    links = "".join(f'<link rel="stylesheet" href="{htmlmod.escape(u)}">' for u in css_links)
    chain = [p for p in (el.parents if el is not None else []) if getattr(p, "name", None) and p.name != "[document]"]
    html_t = next((p for p in chain if p.name == "html"), None); body_t = next((p for p in chain if p.name == "body"), None)
    mids = [p for p in chain if p.name not in ("html", "body")][::-1][-12:]
    opn = "".join(f'<{p.name} {_attrs(p)}>' for p in mids); cls = "".join(f'</{p.name}>' for p in mids[::-1])
    doc = (f'<!doctype html><html {_attrs(html_t) if html_t else ""}><head><meta charset="utf-8"><base href="{htmlmod.escape(page_url)}">{links}<style>{inline_css}</style>'
           '<style>html,body{margin:0!important;background:transparent!important}#w{display:inline-block}'
           '#w svg{height:200px!important;width:auto!important;max-width:none!important;max-height:none!important;display:block!important;'
           f'visibility:visible!important;opacity:1!important}}</style></head><body {_attrs(body_t) if body_t else ""}><div id="w">' + opn + svg_html + cls + '</div>' + SVG_FIX_JS + '</body></html>')
    hp = tempfile.mktemp(suffix=".html"); open(hp, "w").write(doc)
    try: dom = chrome(["--virtual-time-budget=6000", "--window-size=1440,900", "--allow-file-access-from-files", "--dump-dom", "file://" + hp], timeout=60).stdout
    finally: os.remove(hp)
    m = re.search(r'<textarea id="out">([\s\S]*?)</textarea>', dom)
    if not m: return None
    s = htmlmod.unescape(m.group(1))
    s = re.sub(r'url\((["\']?)[^)#"\']*#', r'url(\1#', s)            # keep gradient / mask references local
    if "xmlns=" not in s[:300]: s = s.replace("<svg", '<svg xmlns="http://www.w3.org/2000/svg"', 1)
    if "xlink:" in s and "xmlns:xlink" not in s: s = s.replace("<svg", '<svg xmlns:xlink="http://www.w3.org/1999/xlink"', 1)
    open(out_svg, "w").write(s); return out_svg

def site_of(host):
    parts = host.split("."); return ".".join(parts[-2:]) if len(parts) >= 2 else host

def first_party(u, host, base):
    h = urllib.parse.urlparse(u).netloc.lower()
    return not h or h.endswith(site_of(host)) or base in h

def strip_vendor_css(css, base):
    vend = re.compile(VENDOR.replace(base + "|", "").replace("|" + base, ""), re.I)
    return re.sub(r"([^{}]{1,400})\{([^{}]{0,4000})\}", lambda m: "" if vend.search(m.group(1)) else m.group(0), css)

def logo_candidates(soup, name, final):
    out = []; seen = set(); skip = re.compile(LOGO_SKIP, re.I)
    home = {"/", "", final.rstrip("/"), urllib.parse.urljoin(final, "/").rstrip("/")}
    for t in soup.find_all(["svg", "img", "a", "div", "span"])[:5000]:
        alt = str(t.get("alt", "")); alt = alt if len(alt.split()) <= 6 else ""   # a long alt is a photo description, not a logo label
        own = " ".join(str(t.get(k, "")) for k in ("class", "id", "aria-label", "title")).lower() + " " + alt.lower()
        src = str(t.get("src", "")).lower()
        if re.search(r"\.jpe?g(\?|$)", src): continue   # photos are never logos
        label = (str(t.get("aria-label", "")) + " " + str(t.get("title", ""))).lower()
        homelink = t.name == "a" and str(t.get("href", "")).rstrip("/") in home and (name.lower() in label or "home" in label)
        if not ("logo" in own or "wordmark" in own or "logo" in src.split("?")[0].rsplit("/", 1)[-1] or homelink): continue
        ctx = own + " " + " ".join(" ".join(str(p.get(k, "")) for k in ("class", "id", "aria-label")) for p in list(t.parents)[:6] if hasattr(p, "get")).lower()
        if skip.search(ctx) or skip.search(src): continue
        el = t if t.name in ("svg", "img") else (t.find("svg") or t.find("img"))
        if el is None or id(el) in seen: continue
        seen.add(id(el))
        anc = [p.name for p in el.parents] + [str(p.get("class", "")).lower() for p in el.parents if hasattr(p, "get")]
        region = 0 if any(a in ("header", "nav") or "header" in a or "nav" in a for a in anc) else (1 if any(a == "footer" or "footer" in a for a in anc) else 2)
        named_here = name.lower() in (own + " " + label + " " + str(el.get("alt", "")).lower()) or (el.name == "svg" and el.find("title") is not None and name.lower() in el.find("title").get_text().lower())
        if region == 2 and not (homelink or named_here): continue     # a logo in the page body must name the brand (else it is likely someone else's)
        out.append((region, len(out), el))
    out.sort(key=lambda x: (x[0], x[1]))
    return [e for _, _, e in out][:8]

def size_of(link):
    m = re.match(r"(\d+)", (link.get("sizes") or "").strip()); return 4096 if (link.get("sizes") or "").strip() == "any" else int(m.group(1)) if m else 0

def from_url(url, out_dir):
    """Never crashes on a strange site: anything unexpected falls back to a text logo and a neutral design."""
    try: return _from_url(url, out_dir)
    except SystemExit: raise
    except Exception as e:
        host = urllib.parse.urlparse(url if re.match(r"^https?://", url) else "https://" + url).netloc.lower().removeprefix("www.")
        os.makedirs(os.path.join(out_dir, "assets"), exist_ok=True)
        return blocked_site(out_dir, host, [f"could not read the website fully ({type(e).__name__}: {str(e)[:120]})"])

def _from_url(url, out_dir):
    if not re.match(r"^https?://", url): url = "https://" + url
    A = os.path.join(out_dir, "assets"); os.makedirs(A, exist_ok=True); notes = []
    from bs4 import BeautifulSoup
    # 1. rendered DOM (handles JS sites), falling back to the raw HTML
    host0 = urllib.parse.urlparse(url).netloc.lower().removeprefix("www.")
    if not re.match(r"^[a-z0-9-]+(\.[a-z0-9-]+)+(:\d+)?$", host0):
        raise SystemExit(f"That does not look like a website address: {url}. Ask the user for the address again or a logo file.")
    try: socket.getaddrinfo(host0.split(":")[0], 443)
    except Exception: raise SystemExit(f"Could not find the website {host0} (the address does not exist). Ask the user to check it, or send a logo file.")
    try: dom = chrome(["--virtual-time-budget=8000", "--dump-dom", url], timeout=90).stdout
    except Exception as e: dom = ""; notes.append(f"browser load failed: {e}")
    try: raw, _, final = fetch(url); raw = raw.decode("utf-8", "ignore")
    except Exception as e: raw, final = "", url; notes.append(f"plain fetch failed: {e}")
    page = dom if len(dom) > len(raw) * 0.5 and len(dom) > 2000 else raw
    soup = BeautifulSoup(page or "<html></html>", "html.parser"); host = urllib.parse.urlparse(final).netloc.lower().removeprefix("www.") or host0
    text = soup.get_text(" ", strip=True)
    if not page or (len(text) < 400 and re.search(BLOCKED, text + " " + (soup.title.string if soup.title and soup.title.string else ""), re.I)) or len(text) < 40:
        return blocked_site(out_dir, host, notes)
    base = site_of(host).split(".")[0]
    meta = lambda **kw: (soup.find("meta", attrs=kw) or {}).get("content")
    title = meta(property="og:site_name") or (soup.title.string if soup.title and soup.title.string else "")
    name = clean_name(title.strip(), host)
    tagline = short(meta(name="description") or meta(property="og:description") or "")
    # 2. first party CSS only (third party widgets and embeds are ignored)
    all_links = [urllib.parse.urljoin(final, l["href"]) for l in soup.find_all("link") if "stylesheet" in (l.get("rel") or []) and l.get("href")]
    css_links = [u for u in all_links if first_party(u, host, base)][:12]
    bake_links = [u for u in all_links if first_party(u, host, base) or not re.search(VENDOR, u, re.I)][:16]   # CDN hosted site CSS too
    inline_css = " ".join(s.get_text() for s in soup.find_all("style"))
    css = inline_css
    for u in css_links[:6]:
        try: css += fetch(u, 2_000_000)[0].decode("utf-8", "ignore")
        except Exception: pass
    css = strip_vendor_css(css, base)
    # 3. logo candidates, rendered with the site's CSS, wordmarks first
    cands = []
    for i, el in enumerate(logo_candidates(soup, name, final)):
        try:
            if el.name == "svg":
                if len(str(el)) < 120: continue
                f = bake_svg(str(el), bake_links, inline_css.replace("</style", ""), final, os.path.join(A, f"logo_{i}.svg"), el)
            else:
                srcs = el.get("src") or (el.get("srcset") or "").split(" ")[0]
                if not srcs: continue
                u = urllib.parse.urljoin(final, srcs); data, ct, _ = fetch(u)
                ext = ".svg" if ("svg" in ct or u.split("?")[0].endswith(".svg")) else ".img"
                f = os.path.join(A, f"logo_{i}{ext}"); open(f, "wb").write(data)
                if ext != ".svg": f = prep_raster(f, notes)
            if not f: continue
            png = os.path.join(A, f"logo_{i}_check.png"); im = raster(f, png)
            info = analyze(png)
            if info is None or im.height < 12 or im.width < 12: continue
            cands.append((f, png, info))
        except Exception as e: notes.append(f"logo candidate skipped: {e}")
    if DEBUG: print("logo candidates:", [(os.path.basename(f), round(i["aspect"], 2), i["mono"], i["main"]) for f, _, i in cands])
    cands.sort(key=lambda c: 0 if c[2]["aspect"] >= 1.8 else 1)       # a full wordmark beats an icon
    icons = sorted([l for l in soup.find_all("link") if any(r in (l.get("rel") or []) for r in ("apple-touch-icon", "icon"))],
                   key=lambda l: (0 if "svg" in (l.get("type") or l.get("href", "")) else 1, -size_of(l)))
    for l in icons[:2]:   # site icon, last resort before plain text
        try:
            u = urllib.parse.urljoin(final, l["href"]); data, ct, _ = fetch(u)
            ext = ".svg" if "svg" in ct or u.split("?")[0].endswith(".svg") else ".img"
            f = os.path.join(A, "icon_source" + ext); open(f, "wb").write(data)
            if ext != ".svg":
                f = prep_raster(f, notes)
                if not f: continue
            png = os.path.join(A, "icon_check.png"); raster(f, png, (256, 256)); info = analyze(png)
            if info: info["aspect"] = min(info["aspect"], 1.0); cands.append((f, png, info)); break
        except Exception: continue
    # 4. colours: logo, theme-color and brand CSS variables count most; general CSS and the screenshot much less
    cc = []; strong = 0.0
    tc = meta(name="theme-color")
    if tc and re.match(r"^#[0-9a-fA-F]{3,6}$", tc.strip()):
        cc.append((tc.strip(), 4.0)); notes.append(f"theme-color {tc}"); strong += 4.0 if vivid(tc.strip()) else 0
    logo_vivid = 0.0
    for rank, wgt in zip(range(min(3, len(cands))), (6.0, 3.0, 1.5)):   # the main logo counts most, other site logos a little
        for c, sh, s, l in dominant(Image.open(cands[rank][1])):
            cc.append((c, wgt * sh)); logo_vivid += (wgt / 6.0) * sh if vivid(c) else 0
    def hex6(h): return (h if len(h) == 7 else "#" + "".join(c * 2 for c in h[1:])).upper()
    HEX = r"#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b"
    hx = Counter(hex6(h) for h in re.findall(HEX, css)); tot = sum(hx.values()) or 1
    for h, n in hx.most_common(40): cc.append((h, 1.0 * n / tot))
    named = Counter()
    for m in re.finditer(r"--[\w-]*(?:brand|primary|accent|cta|" + re.escape(base) + r")[\w-]*\s*:\s*(" + HEX + ")", css, re.I): named[hex6(m.group(1))] += 1
    ntot = sum(named.values()) or 1
    for h, n in named.most_common(20): cc.append((h, 4.0 * n / ntot))
    strong += sum(4.0 * n / ntot for h, n in named.items() if vivid(h) and h not in THIRD_PARTY_COLORS)
    btn = Counter()
    for sel, body in re.findall(r"([^{}]{1,300})\{([^{}]{1,2000})\}", css):
        if re.search(r"btn|button|cta", sel, re.I):
            for h in re.findall(HEX, body): btn[hex6(h)] += 1
    btot = sum(btn.values()) or 1
    for h, n in btn.most_common(20): cc.append((h, 1.5 * n / btot))
    if named: notes.append("brand CSS colors: " + ", ".join(h for h, _ in named.most_common(3)))
    shot = os.path.join(A, "site_screenshot.png"); page_dark = None
    try:
        chrome(["--window-size=1440,900", "--virtual-time-budget=8000", f"--screenshot={shot}", url], timeout=90)
        sim = Image.open(shot).convert("RGB")
        g = sim.convert("L").resize((144, 90)); page_dark = sorted(g.getdata())[len(g.getdata()) // 2] < 90
        for c, sh, s, l in dominant(sim, 10): cc.append((c, 0.5 * sh))
    except Exception as e: notes.append(f"screenshot failed: {e}")
    if DEBUG: print(f"logo_vivid={logo_vivid:.2f} strong={strong:.2f} named={named.most_common(5)}")
    res = pick_palette(cc, page_dark); neutral = False
    if res is None or (cands and logo_vivid < 0.08 and strong < 1.2):
        sc = score_colors(cc); hint = res[1] if res and sc and sc.most_common(1)[0][1] >= 0.5 else None
        colors = neutral_palette(hint); neutral = True
        notes.append("brand looks black and white: used a neutral design with one subtle accent " + colors["accent"]
                     + ". Ask the user (tweak pair 1) whether they want a specific accent color")
    else:
        colors, brandc, second = res; notes.append(f"brand color {brandc}" + (f", second color {second}" if second else ""))
    theme = "dark" if page_dark is None or page_dark else "light"
    notes.append(f"site looks {'dark' if page_dark else 'light'}, default style {theme}")
    picks = choose_logos(cands, colors, notes)
    link = host0 if site_of(host0.split(":")[0]) == site_of(host) else host   # keep the address the user gave when the site redirects within itself
    return finish(out_dir, name, link, tagline, colors, theme, picks, notes, neutral)

def blocked_site(out_dir, host, notes):
    """The site blocks automated visits or shows nothing. Never guess: brand name as a text logo, neutral design, and ask."""
    base = site_of(host).split(".")[0]; name = base.replace("-", " ").title()
    notes.append("website could not be loaded, blocked the visit, or was empty, so no logo or colors could be read. Used the name as a text logo and a "
                 "neutral design. Ask the user for their logo file (SVG or PNG) and their main brand color")
    colors = neutral_palette(); picks = choose_logos([], colors, notes)
    return finish(out_dir, name, host, "", colors, "dark", picks, notes, True)

# ---------------- logo or brand kit file ----------------
def from_file(path, out_dir, name=None):
    A = os.path.join(out_dir, "assets"); os.makedirs(A, exist_ok=True); notes = []
    ext = os.path.splitext(path)[1].lower(); cc = []; cands = []
    if ext == ".pdf":
        txt = subprocess.run(["pdftotext", "-l", "6", path, "-"], capture_output=True, text=True).stdout
        for h in re.findall(r"#?\b([0-9A-Fa-f]{6})\b", txt): cc.append(("#" + h.upper(), 3.0))
        pre = os.path.join(A, "kit_page")
        subprocess.run(["pdftoppm", "-png", "-r", "40", "-l", "4", path, pre], capture_output=True)
        for f in sorted(os.listdir(A)):
            if f.startswith("kit_page"):
                for c, sh, s, l in dominant(Image.open(os.path.join(A, f)), 8): cc.append((c, 1.5 * sh))
        notes.append("brand kit PDF: colors read from the hex codes and pages; ask the user to upload the logo file itself")
        if not name:
            first = next((l.strip() for l in txt.splitlines() if l.strip()), "")
            name = first[:24] if first else None
    else:
        f = os.path.join(A, "logo_source" + ext); shutil.copy(path, f)
        if ext != ".svg": f = prep_raster(f, notes)
        info = None
        if not f:   # too small to show, but its colours still tell us the brand colour
            try:
                for c, sh, s_, l in dominant(Image.open(path).convert("RGBA").resize((96, 96), Image.LANCZOS), 6): cc.append((c, 4.0 * sh))
            except Exception: pass
        if f:
            png = os.path.join(A, "logo_check.png"); im = raster(f, png); info = analyze(png)
            for c, sh, s, l in dominant(im, 8): cc.append((c, 4.0 * sh))
        if ext == ".svg":
            for h in re.findall(r"#[0-9a-fA-F]{6}\b", open(path, errors="ignore").read()): cc.append((h.upper(), 1.0))
        if info: cands.append((f, png, info))
    name = name or re.sub(r"[-_ ]*(logo|favicon|icon|on|dark|light|white|black|full|mark|color|colour)\b.*$", "", os.path.splitext(os.path.basename(path))[0], flags=re.I).replace("-", " ").replace("_", " ").strip().title() or "Your Brand"
    res = pick_palette(cc); neutral = False
    if not res:
        colors = neutral_palette(); neutral = True
        notes.append("logo has no clear color (single color logo): used a neutral design with one subtle accent. Ask the user for their brand color")
    else:
        colors, brandc, second = res; notes.append(f"brand color {brandc}" + (f", second color {second}" if second else ""))
    picks = choose_logos(cands, colors, notes)
    return finish(out_dir, name, "", "", colors, "dark", picks, notes, neutral)

# ---------------- host profile picture (no brand given) ----------------
SKIN = (0.0, 0.12)   # hue range of skin and hair tones: only used when a photo has no other real colour
def photo_accent(path):
    """One vivid brand colour from a profile photo. Prefers real colour in the photo (clothes, background, props) over
    skin tones, and always returns a colour that is vivid and readable on the dark and light styles (never dull grey)."""
    im = Image.open(path).convert("RGB"); im.thumbnail((160, 160))
    cols = dominant(im, 16); best = None
    for prefer_non_skin in (True, False):
        sc = []
        for c, share, s, l in cols:
            hh = hsl(c)[0]
            if s < 0.12 or not (0.10 <= l <= 0.90) or share < 0.015: continue
            if prefer_non_skin and SKIN[0] <= hh <= SKIN[1] and s < 0.75: continue   # skin is never this saturated: a vivid orange is a real colour
            sc.append((share * (0.3 + s), c))
        if sc: best = max(sc)[1]; break
    if not best:   # black and white photo: fall back to a clean electric blue
        return "#3D8BFF", "photo has no clear colour: used a clean electric blue"
    hh, s, l = hsl(best)
    acc = from_hsl(hh, max(0.86, min(0.95, s * 1.8)), min(0.62, max(0.56, l)))
    return acc, f"colour from the host's photo {best}, made vivid: {acc}"

def from_photo(path, out_dir, name, handle=""):
    """Brand profile for a user with no brand: colours from the host's profile photo, the host's name as a clean text
    wordmark, no logo, no link, no promo code (never invent those)."""
    os.makedirs(out_dir, exist_ok=True); acc, note = photo_accent(path)
    colors = derive(acc); colors["accent"] = acc if contrast(acc, colors["background_dark"]) >= 5.5 else colors["accent"]
    logo = {"on_dark": "", "on_light": "", "mark": "", "style": "none", "style_dark": "none", "style_light": "none",
            "dark_mode": "original", "light_mode": "original", "title_size": 22}
    prof = defaults(name, "", "", colors, "dark", logo, [note, "no brand given: the host's photo is the brand mark; no logo, link or code"],
                    os.path.abspath(path))
    prof["brand_handle"] = handle; prof["source"] = {"kind": "host_photo", "photo": os.path.abspath(path), "handle": handle}
    json.dump(prof, open(os.path.join(out_dir, "brand_profile.json"), "w"), indent=2)
    return prof, prof["_notes"]
