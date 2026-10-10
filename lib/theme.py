"""Turn a brand profile into the two render palettes (dark and light).
Every colour in the video comes from here. Nothing is hard coded to one brand.
The dark recipe is the proven "sharp glow" look: white ring bars, a deep tinted
background built from the brand's dark colour, pure accent highlights, no haze."""

def rgb(h):
    h = h.strip().lstrip("#")
    if len(h) == 3: h = "".join(c * 2 for c in h)
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))

def hexc(c): return "#%02X%02X%02X" % tuple(max(0, min(255, round(v))) for v in c)

def mix(a, b, t):
    """Blend colour a toward colour b by t (0..1). Accepts hex strings."""
    A, B = rgb(a), rgb(b)
    return hexc([A[i] + (B[i] - A[i]) * t for i in range(3)])

def rgba(h, a):
    r, g, b = rgb(h); return f"rgba({r},{g},{b},{a})"

def lum(h):
    def ch(v):
        v /= 255; return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = rgb(h); return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)

def contrast(a, b):
    la, lb = sorted([lum(a), lum(b)], reverse=True); return (la + 0.05) / (lb + 0.05)

def on(color, dark="#0B0B0F", light="#FFFFFF"):
    """Readable text colour on top of `color`."""
    return dark if contrast(color, dark) >= contrast(color, light) else light

BLACK, WHITE = "#000000", "#FFFFFF"

def palette(brand, theme):
    c = brand["colors"]
    acc = c["accent"]
    prim = c.get("primary", acc)
    cap = brand.get("captions", {})
    if theme == "dark":
        bg = c.get("background_dark", mix(prim, BLACK, 0.8))
        page = mix(bg, BLACK, 0.75)
        deep = mix(acc, BLACK, 0.75)
        ring = c.get("ring_dark", WHITE)
        und = cap.get("underline_color_dark", acc)
        btn = c.get("button", acc)
        T = dict(
            page=page, scene_bg=bg,
            base=f"radial-gradient(1400px 900px at 30% 45%,{bg} 0%,{mix(bg, BLACK, 0.4)} 42%,{page} 100%)",
            floor=rgba(mix(acc, BLACK, 0.4), .10), dots="rgba(255,255,255,.045)", mark=WHITE, markop=".03",
            b1=f"{rgba(mix(acc, BLACK, 0.33), .50)},{rgba(mix(acc, BLACK, 0.57), .20)}",
            b2=rgba(mix(acc, BLACK, 0.12), .34), b3=rgba(mix(acc, BLACK, 0.5), .12), bfade=rgba(acc, 0),
            halo=mix(bg, BLACK, 0.6), halo2=mix(bg, acc, 0.12),
            rim=f"linear-gradient(160deg,#FFFFFF,{acc} 45%,{deep})", pshadow="0 30px 60px rgba(0,0,0,.60)",
            edge=f"{rgba(acc, .85)},{rgba(mix(acc, BLACK, .12), .35)} 30%,rgba(255,255,255,.06) 55%,{rgba(mix(acc, BLACK, .12), .35)} 80%,{rgba(acc, .85)}",
            ink=WHITE, ink2=mix(WHITE, bg, 0.07), mute=mix(WHITE, bg, 0.35), lbl=mix(WHITE, bg, 0.42), rule=mix(bg, WHITE, 0.12),
            dot=acc, pillbg=f"linear-gradient(180deg,{mix(bg, WHITE, 0.05)},{mix(bg, WHITE, 0.02)})", pillbd=mix(bg, acc, 0.28),
            pillsh=f"inset 0 1px 0 {mix(bg, acc, 0.3)},0 14px 30px rgba(0,0,0,.55)",
            hosthalo=mix(bg, BLACK, 0.6), hostring=mix(acc, BLACK, 0.1), chipbg=mix(bg, WHITE, 0.03), chipbd=mix(bg, acc, 0.35),
            u=mix(WHITE, bg, 0.45), s=mix(WHITE, bg, 0.08), c=WHITE, und=und, key=acc, ku=mix(acc, bg, 0.45),
            nx=mix(WHITE, bg, 0.66), hu=mix(WHITE, bg, 0.45),
            btn_top=mix(btn, WHITE, 0.18), btn=mix(btn, BLACK, 0.1), btn_ink=on(btn), btn_line=mix(btn, WHITE, 0.55),
            codesh="0 6px 16px rgba(0,0,0,.55)",
            logo=brand["logo"].get("on_dark") or brand["logo"].get("on_light"),
            # per frame compositor values
            ring1=ring, ring2=c.get("ring_dark_outer", ring), progress=acc, progress_start=deep, track=[255, 255, 255, 30],
            knob=WHITE, scrim=mix(bg, BLACK, 0.75), scrim_a=0.78, vignette=0.25, grain=1.0)
    else:
        bg = c.get("background_light", "#F8F8FB")
        ink = c.get("text_light", mix(prim, BLACK, 0.7))
        key = c.get("key_light", prim if contrast(prim, bg) >= 4.5 else mix(prim, BLACK, 0.45))
        und = cap.get("underline_color_light", key)
        btn = c.get("button_light", c.get("button", acc))
        T = dict(
            page=bg, scene_bg=bg,
            base=f"linear-gradient(125deg,{bg} 0%,{mix(bg, acc, 0.03)} 38%,{mix(bg, acc, 0.08)} 72%,{mix(bg, acc, 0.14)} 100%)",
            floor=rgba(acc, .10), dots=rgba(ink, .085), mark=key, markop=".035",
            b1=f"{rgba(acc, .30)},{rgba(acc, .10)}", b2=rgba(mix(acc, WHITE, 0.2), .24), b3=rgba(acc, .12), bfade=rgba(acc, 0),
            halo=bg, halo2=rgba(ink, .08),
            rim=f"linear-gradient(160deg,{mix(acc, WHITE, 0.45)},{acc} 45%,{key})",
            pshadow=f"0 36px 64px {rgba(ink, .26)},0 10px 22px {rgba(ink, .12)}",
            edge=f"{rgba(key, .70)},{rgba(acc, .28)} 30%,{rgba(ink, .05)} 55%,{rgba(acc, .28)} 80%,{rgba(key, .65)}",
            ink=ink, ink2=rgba(ink, .85), mute=rgba(ink, .58), lbl=rgba(ink, .48), rule=rgba(ink, .10),
            dot=key, pillbg="linear-gradient(180deg,#FFFFFF,#FBFBFC)", pillbd=rgba(ink, .12),
            pillsh=f"0 18px 40px {rgba(ink, .12)}",
            hosthalo="#FFFFFF", hostring=key, chipbg="#FFFFFF", chipbd=rgba(ink, .14),
            u=mix(ink, bg, 0.68), s=ink, c=ink, und=und, key=key, ku=mix(key, bg, 0.55),
            nx=mix(ink, bg, 0.78), hu=mix(ink, bg, 0.58),
            btn_top=mix(btn, WHITE, 0.15), btn=btn, btn_ink=on(btn), btn_line=mix(btn, WHITE, 0.5),
            codesh=f"0 8px 22px {rgba(btn, .30)}",
            logo=brand["logo"].get("on_light") or brand["logo"].get("on_dark"),
            ring1=c.get("ring_light", key), ring2=c.get("ring_light_outer", acc), progress=key, progress_start=mix(acc, bg, 0.5),
            track=list(rgb(ink)) + [26], knob=key, scrim=bg, scrim_a=0.70, vignette=0.07, grain=2.2)
    T["accent"] = acc
    return T
