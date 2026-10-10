"""Static and semi static layers for one clip, drawn as HTML in headless Chrome at 2x and downsampled.
   bg.png         background, optional faint brand mark watermark, dot texture   (opaque)
   bloom.png      soft accent glow behind the speaker (drifts per frame)          (RGBA)
   pfp_<spk>.png  speaker photo with rim and shadow (plus @2x for sharp scaling)  (RGBA)
   ui_<spk>.png   top bar (logo, title), name chip, footer pill (hosts, CTA, code) (RGBA)
   cap_*.png      caption column: current phrase in karaoke states + next phrase dim
   hero_*.png     the payoff line, centred and large, karaoke states
All colours come from theme.palette(brand). All text comes from the brand profile and project file."""
import os, re, html, json, shutil, subprocess, tempfile, concurrent.futures as cf
from PIL import Image
from . import words as W
from .theme import palette

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
G = dict(cx=390, cy=505, P=380, capx=820, capr=1810, chipy=286, capy=372, pill=(160, 946, 1760, 1034), progy=912, herocy=505)
SIZES = dict(small=60, medium=68, large=76)

def chrome_bin():
    for b in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        if shutil.which(b): return shutil.which(b)
    raise SystemExit("Chrome or Chromium not found. Run kit/install.sh")

def resolve(base_dir, p):
    if not p: return None
    if re.match(r"^(https?|file|data):", p): return p
    q = p if os.path.isabs(p) else os.path.join(base_dir, p)
    return "file://" + os.path.abspath(q) if os.path.exists(q) else None

def initials(name):
    parts = [x for x in re.split(r"\s+", re.sub(r"[^\w\s]", "", name or "")) if x]
    return ("".join(x[0] for x in parts[:2]) or "?").upper()

class Layers:
    def __init__(self, project_dir, project, brand, brand_dir, clip_id, theme, out_dir):
        self.pd, self.pj, self.br, self.bd, self.cid, self.theme = project_dir, project, brand, brand_dir, clip_id, theme
        self.clip = project["clips"][clip_id]; self.T = palette(brand, theme)
        out_dir = os.path.abspath(out_dir); self.CD = out_dir; self.OUT = os.path.join(out_dir, "layers"); self.HT = os.path.join(out_dir, "html")
        os.makedirs(self.OUT, exist_ok=True); os.makedirs(self.HT, exist_ok=True)
        cap = brand.get("captions", {})
        self.px = SIZES.get(cap.get("size", "medium"), 68) if not isinstance(cap.get("size"), int) else cap["size"]
        self.weight = int(cap.get("weight", 600)); self.hl = cap.get("highlight", "underline")
        self.acc = {W.norm_tok(k) for k in self.clip.get("accent_words", [])}
        font = brand.get("font", {}) or {}
        ff = font.get("file"); ffp = None
        if ff: ffp = resolve(brand_dir, ff)
        if not ffp: ffp = "file://" + os.path.join(KIT, "fonts", "Inter-Variable.ttf")
        fam = font.get("family", "Inter")
        self.BASE = (f"@font-face{{font-family:'AGFont';src:url('{ffp}');font-weight:100 900}}"
                     "*{margin:0;padding:0;box-sizing:border-box}html,body{width:1920px;height:1080px;overflow:hidden;background:transparent}"
                     f"body{{font-family:'AGFont','{fam}',Inter,'Helvetica Neue',Arial,sans-serif;position:relative;-webkit-font-smoothing:antialiased;"
                     "text-rendering:geometricPrecision}")
        self.people = project["people"]
        ep = project.get("episode", {})
        self.title = ep.get("title", ""); self.sub = ep.get("subtitle", "")
        ft = brand.get("footer", {})
        self.hosts = ep.get("hosts", brand.get("hosts", [])) or []
        self.ft = ft

    def img(self, person, cls, size):
        src = resolve(self.pd, person.get("img")) or resolve(self.bd, person.get("img"))
        if src: return f'<img class="{cls}" src="{src}">'
        T = self.T
        return (f'<div class="{cls} ini" style="display:flex;align-items:center;justify-content:center;'
                f'background:linear-gradient(160deg,{T["accent"]},{T["key"]});color:{T["btn_ink"]};font-weight:800;font-size:{int(size*0.36)}px">'
                f'{html.escape(initials(person.get("name") or person.get("handle")))}</div>')

    def doc(self, css, body, bg="transparent"):
        return f'<!doctype html><html><head><meta charset="utf-8"><style>{self.BASE}body{{background:{bg}}}{css}</style></head><body>{body}</body></html>'

    def shot(self, name, text, transparent=True):
        hp = os.path.join(self.HT, name + ".html"); open(hp, "w").write(text)
        big = os.path.join(self.OUT, f"_{name}@2x.png"); out = os.path.join(self.OUT, name + ".png")
        ud = tempfile.mkdtemp(prefix="agchr_")
        cmd = [chrome_bin(), "--headless=new", "--no-sandbox", "--disable-gpu", "--hide-scrollbars", "--force-device-scale-factor=2",
               f"--user-data-dir={ud}", "--allow-file-access-from-files", "--window-size=1920,1080", "--virtual-time-budget=3000",
               f"--screenshot={big}", "file://" + hp]
        if transparent: cmd.insert(3, "--default-background-color=00000000")
        try: subprocess.run(cmd, check=True, capture_output=True, timeout=120)
        finally: shutil.rmtree(ud, ignore_errors=True)
        im = Image.open(big).convert("RGBA" if transparent else "RGB")
        if name.startswith("pfp_"): im.save(os.path.join(self.OUT, name + "@2x.png"))
        im.resize((1920, 1080), Image.LANCZOS).save(out); os.remove(big); return out

    # ---------- layers ----------
    def bg(self):
        T = self.T; mark = resolve(self.bd, self.br["logo"].get("mark"))
        css = f""".base{{position:absolute;inset:0;background:{T['base']}}}
.floor{{position:absolute;inset:0;background:radial-gradient(1200px 340px at 60% 100%,{T['floor']},transparent 70%)}}
.dots{{position:absolute;inset:0;background-image:radial-gradient({T['dots']} 1.2px,transparent 1.3px);background-size:30px 30px;
 -webkit-mask-image:radial-gradient(1100px 760px at 72% 46%,#000,transparent 72%)}}"""
        body = '<div class="base"></div><div class="floor"></div><div class="dots"></div>'
        if mark:
            css += f""".mark{{position:absolute;left:{G['capx']+40}px;top:300px;width:1050px;height:647px;background:{T['mark']};opacity:{T['markop']};
 -webkit-mask:url('{mark}') no-repeat center/contain}}"""
            body += '<div class="mark"></div>'
        return self.doc(css, body, bg=T['page'])

    def bloom(self):
        T = self.T; cx, cy = G["cx"], G["cy"]
        css = f""".b1{{position:absolute;left:{cx-700}px;top:{cy-560}px;width:1400px;height:1120px;background:radial-gradient(closest-side,{T['b1']} 46%,{T['bfade']} 100%)}}
.b2{{position:absolute;left:{cx-330}px;top:{cy-330}px;width:660px;height:660px;background:radial-gradient(closest-side,{T['b2']},{T['bfade']})}}
.b3{{position:absolute;left:{cx+380}px;top:{cy-420}px;width:1200px;height:900px;background:radial-gradient(closest-side,{T['b3']},{T['bfade']})}}"""
        return self.doc(css, '<div class="b1"></div><div class="b3"></div><div class="b2"></div>')

    def pfp(self, spk):
        T = self.T; P = G["P"]; cx, cy = G["cx"], G["cy"]
        css = f""".pfp{{position:absolute;left:{cx-P/2}px;top:{cy-P/2}px;width:{P}px;height:{P}px;border-radius:50%;padding:4px;
 background:{T['rim']};box-shadow:0 0 0 9px {T['halo']},0 0 0 10px {T['halo2']},{T['pshadow']}}}
.pfp .im{{width:100%;height:100%;border-radius:50%;object-fit:cover;display:block}}"""
        return self.doc(css, f'<div class="pfp">{self.img(self.people[spk], "im", P)}</div>')

    def ui(self, spk):
        T = self.T; p = self.people[spk]; x0, y0, x1, y1 = G["pill"]; ft = self.ft
        logo = resolve(self.bd, T["logo"])
        hosts = "".join(f'<div class="h">{self.img(h, "hi", 48)}<span>{html.escape(h.get("handle") or h.get("name",""))}</span></div>' for h in self.hosts)
        left = (f'<span class="lbl">{html.escape(ft.get("hosts_label","Hosts"))}</span>{hosts}' if self.hosts
                else f'<span class="ftxt">{html.escape(ft.get("footer_text",""))}</span>')
        code = self.br.get("promo_code"); btn = (f'{html.escape(ft.get("code_label","Code:"))} {html.escape(code)}' if code
                                                else html.escape(ft.get("button_text", "")))
        cta = (f'<div class="lk"><small>{html.escape(ft.get("cta_line",""))}</small>{html.escape(self.br.get("link",""))}</div>'
               + (f'<div class="code">{btn}</div>' if btn else ""))
        brandname = html.escape(self.br.get("brand_name", ""))
        lg = self.br.get("logo", {}); mode = lg.get("dark_mode" if self.theme == "dark" else "light_mode", "original")
        filt = {"white": "filter:brightness(0) invert(1);", "dark": f"filter:brightness(0);opacity:.88;"}.get(mode, "")
        if logo and lg.get("style") == "icon":
            logo_html = f'<div class="lgrp"><img class="logoi" style="{filt}" src="{logo}"><div class="wm">{brandname}</div></div>'
        elif logo: logo_html = f'<img class="logo" style="{filt}" src="{logo}">'
        else: logo_html = f'<div class="wm">{brandname}</div>'
        if not self.br.get("link"): cta = (f'<div class="code">{btn}</div>' if btn else "")
        css = f""".edge{{position:absolute;inset:22px;border-radius:28px;padding:1.5px;background:linear-gradient(135deg,{T['edge']});
 -webkit-mask:linear-gradient(#000 0 0) content-box,linear-gradient(#000 0 0);-webkit-mask-composite:xor;mask-composite:exclude}}
.top{{position:absolute;left:86px;right:86px;top:56px;height:46px;display:flex;justify-content:space-between;align-items:center}}
.logo{{height:40px;max-width:420px;object-fit:contain}} .wm{{font-size:30px;font-weight:800;letter-spacing:-.5px;color:{T['ink']}}}
.lgrp{{display:flex;align-items:center;gap:14px}} .logoi{{height:44px;width:44px;object-fit:contain;border-radius:10px}}
.tt{{font-size:20px;font-weight:600;letter-spacing:.2px;color:{T['ink2']};display:flex;align-items:center;gap:12px}}
.tt b{{font-weight:700;color:{T['ink']}}} .tt em{{font-style:normal;color:{T['mute']}}} .tt i{{width:8px;height:8px;border-radius:50%;background:{T['dot']}}}
.rule{{position:absolute;left:86px;right:86px;top:124px;height:1px;background:linear-gradient(90deg,transparent,{T['rule']} 15%,{T['rule']} 85%,transparent)}}
.pill{{position:absolute;left:{x0}px;top:{y0}px;width:{x1-x0}px;height:{y1-y0}px;border-radius:{(y1-y0)/2}px;background:{T['pillbg']};border:1px solid {T['pillbd']};
 box-shadow:{T['pillsh']};display:flex;align-items:center;justify-content:space-between;padding:0 14px 0 26px}}
.hosts{{display:flex;align-items:center;gap:22px}} .lbl{{font-size:12px;font-weight:700;letter-spacing:4px;text-transform:uppercase;color:{T['lbl']}}}
.ftxt{{font-size:20px;font-weight:600;color:{T['ink2']}}}
.h{{display:flex;align-items:center;gap:10px;font-size:20px;font-weight:600;color:{T['ink']}}}
.hi{{width:48px;height:48px;border-radius:50%;object-fit:cover;box-shadow:0 0 0 2px {T['hosthalo']},0 0 0 3.5px {T['hostring']}}}
.cta{{display:flex;align-items:center;gap:20px}} .lk{{font-size:22px;font-weight:600;color:{T['ink']};letter-spacing:-.1px}}
.lk small{{font-size:12px;font-weight:700;letter-spacing:4px;text-transform:uppercase;color:{T['lbl']};margin-right:14px}}
.code{{font-size:22px;font-weight:800;color:{T['btn_ink']};background:linear-gradient(180deg,{T['btn_top']},{T['btn']});padding:14px 24px;border-radius:999px;
 box-shadow:0 0 0 1px {T['btn_line']} inset,{T['codesh']}}}
.chip{{position:absolute;left:{G['capx']}px;top:{G['chipy']}px;display:flex;align-items:center;gap:14px;padding:11px 14px 11px 18px;border-radius:999px;
 background:{T['chipbg']};border:1px solid {T['chipbd']};font-size:24px;font-weight:700;color:{T['ink']}}}
.chip i{{width:9px;height:9px;border-radius:50%;background:{T['dot']}}} .chip span{{font-weight:500;color:{T['mute']}}}"""
        sub = f'<em>·</em>{html.escape(self.sub)}' if self.sub else ""
        handle = f'<span>{html.escape(p.get("handle",""))}</span>' if p.get("handle") else ""
        body = f"""<div class="top">{logo_html}<div class="tt"><i></i><b>{html.escape(self.title)}</b>{sub}</div></div>
<div class="rule"></div>
<div class="chip"><i></i>{html.escape(p['name'])}{handle}</div>
<div class="pill"><div class="hosts">{left}</div><div class="cta">{cta}</div></div>
<div class="edge"></div>"""
        return self.doc(css, body)

    def kcss(self):
        T = self.T
        cur = f".c{{color:{T['c']}}}"
        if self.hl in ("underline", "both"):
            cur = (f".c{{color:{T['c']};text-decoration:underline solid {T['und']};text-decoration-thickness:{max(4,round(self.px*0.075))}px;"
                   f"text-underline-offset:{round(self.px*0.13)}px;text-decoration-skip-ink:none}}")
        if self.hl == "color": cur = f".c{{color:{T['und']}}}"
        return (f".u{{color:{T['u']}}} .s{{color:{T['s']}}} {cur}"
                f" .k.u{{color:{T['ku']}}} .k.s{{color:{T['key']}}} .k.c{{color:{T['key']}}}"
                f" .nx .w{{color:{T['nx']}!important;text-decoration:none!important}}")

    def words_html(self, ph, cur, spoken):
        k = W.key_index(ph, self.acc)
        return " ".join(f'<span class="w {"c" if i==cur else ("s" if i<spoken else "u")}{" k" if i==k else ""}">{html.escape(w["w"])}</span>'
                        for i, w in enumerate(ph))

    def cap(self, ph, cur, sp, nxt):
        px = self.px
        css = f""".cap{{position:absolute;left:{G['capx']}px;width:{G['capr']-G['capx']}px;top:{G['capy']}px;font-size:{px}px;font-weight:{self.weight};
 letter-spacing:{-0.0235*px:.2f}px;line-height:1.16;color:{self.T['s']};text-transform:none;text-wrap:pretty}} .nx{{margin-top:14px}}{self.kcss()}"""
        body = f'<div class="cap"><div>{self.words_html(ph,cur,sp)}</div>' + (f'<div class="nx">{self.words_html(nxt,None,0)}</div>' if nxt else '') + '</div>'
        return self.doc(css, body)

    def hero(self, ph, cur, sp):
        hp = round(self.px * 1.41)
        css = f""".hero{{position:absolute;left:210px;right:210px;top:{G['herocy']}px;transform:translateY(-50%);text-align:center;font-size:{hp}px;
 font-weight:{self.weight};letter-spacing:{-0.027*hp:.2f}px;line-height:1.1;color:{self.T['s']};text-wrap:balance;text-transform:none}}{self.kcss()}
.hero .u{{color:{self.T['hu']}}}"""
        return self.doc(css, f'<div class="hero">{self.words_html(ph,cur,sp)}</div>')

    def build(self, jobs_n=6):
        report = {}
        ws = W.load_words(self.pd, self.pj, self.br, self.cid, report)
        ST, EN, DUR = W.clip_window(self.clip)
        mn, mx = self.br.get("captions", {}).get("phrase_words", [4, 8])
        phs = W.build_phrases(ws, self.clip, mn, mx); tt = W.times(phs, DUR)
        payoff = self.clip.get("payoff", True)
        H = W.payoff_index(phs) if payoff else -1
        spks = list(dict.fromkeys(w["spk"] for w in ws))
        jobs = [("bg", self.bg(), False), ("bloom", self.bloom(), True)] + [(f"pfp_{s}", self.pfp(s), True) for s in spks] + [(f"ui_{s}", self.ui(s), True) for s in spks]
        tl = []; seen = set()
        for i, (ph, (s, e)) in enumerate(zip(phs, tt)):
            hero = (i == H); nxt = phs[i + 1] if (i + 1 < len(phs) and i + 1 != H) else None
            if hero: e = DUR
            for a, b_, cur, sp in W.karaoke_states(ph, s, e):
                n = f"{'hero' if hero else 'cap'}_{i:03d}_{'c%02d'%cur if cur is not None else 'n%02d'%sp}"
                if n not in seen:
                    jobs.append((n, self.hero(ph, cur, sp) if hero else self.cap(ph, cur, sp, nxt), True)); seen.add(n)
                tl.append(dict(s=a, e=b_, png=n + ".png", phrase=i, hero=hero, spk=ph[0]["spk"], cur=ph[cur]["w"] if cur is not None else None))
        hs = tt[H][0] if H >= 0 else DUR + 1
        meta = dict(clip=self.cid, theme=self.theme, start=ST, end=EN, dur=DUR, hero_start=hs,
                    hero_phrase=" ".join(w["w"] for w in phs[H]) if H >= 0 else None, phrase_starts=[t[0] for t in tt], geometry=G,
                    speakers=spks, turns=W.turns(self.clip), palette=self.T, timeline=tl,
                    caption_text=" ".join(w["w"] for w in ws), drops_report=report.get("drops", []))
        json.dump(meta, open(os.path.join(self.CD, "timeline.json"), "w"), indent=1)
        with cf.ThreadPoolExecutor(jobs_n) as ex: list(ex.map(lambda j: self.shot(*j), jobs))
        return meta, len(jobs)
