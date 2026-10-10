"""Pluggable publish step: put a finished clip on storage the USER owns and print ONE link that plays and downloads.

Nothing here is tied to any account. Everything comes from the user's own config and secrets:
  ~/.config/audiogram-maker/publish.json   (override with AG_PUBLISH_CONFIG)
  ~/.config/audiogram-maker/.secrets       JSON {"access_key_id": "...", "secret_access_key": "..."}, mode 600
                                           (or env AG_PUBLISH_ACCESS_KEY_ID / AG_PUBLISH_SECRET_ACCESS_KEY)
Secrets are never printed or logged.

publish.json:
  {"provider": "s3", "endpoint": "https://<s3 endpoint>", "region": "auto", "bucket": "<bucket>", "prefix": "clips/",
   "public_base": "https://<public host for the bucket>/", "player_base": "https://<static player page>/",
   "ttl_hours": 2, "cache_control": "public, max-age=3600", "max_mb": 50}

Providers are small adapters with put / head / list / delete. "s3" (AWS Signature V4, path style) works with any
S3-compatible store, including free tiers such as Cloudflare R2 or Backblaze B2."""
import datetime, hashlib, hmac, json, os, re, secrets as rnd, time, urllib.error, urllib.parse, urllib.request
import xml.etree.ElementTree as ET

CFG_DIR = os.path.expanduser("~/.config/audiogram-maker")
TYPES = {".mp4": "video/mp4", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}
MAX_CACHE = 3600

class PublishError(Exception): pass

def cfg_path(): return os.environ.get("AG_PUBLISH_CONFIG") or os.path.join(CFG_DIR, "publish.json")

def load_config():
    p = cfg_path()
    if not os.path.exists(p): raise PublishError(f"NOT CONFIGURED: no {p}. Set up the user's own storage first (see the delivery skill).")
    c = json.load(open(p))
    miss = [k for k in ("provider", "bucket", "public_base") if not c.get(k)]
    if c.get("provider") == "s3" and not c.get("endpoint"): miss.append("endpoint")
    if miss: raise PublishError(f"publish.json is missing: {', '.join(miss)}")
    c.setdefault("prefix", "clips/"); c.setdefault("ttl_hours", 2); c.setdefault("max_mb", 50); c.setdefault("region", "auto")
    c.setdefault("cache_control", f"public, max-age={MAX_CACHE}")
    m = re.search(r"max-age=(\d+)", c["cache_control"])
    if not m or int(m.group(1)) > MAX_CACHE: c["cache_control"] = f"public, max-age={MAX_CACHE}"   # clips expire: never cache long
    if not c["prefix"].endswith("/"): c["prefix"] += "/"
    return c

def load_secrets():
    k, s = os.environ.get("AG_PUBLISH_ACCESS_KEY_ID"), os.environ.get("AG_PUBLISH_SECRET_ACCESS_KEY")
    if k and s: return k, s
    p = os.path.join(CFG_DIR, ".secrets")
    if not os.path.exists(p): raise PublishError(f"NO CREDENTIALS: {p} is missing. Ask the user for them through the secure secret request.")
    if os.stat(p).st_mode & 0o077: os.chmod(p, 0o600)
    d = json.load(open(p))
    if not d.get("access_key_id") or not d.get("secret_access_key"): raise PublishError(f"{p} needs access_key_id and secret_access_key")
    return d["access_key_id"], d["secret_access_key"]

def status():
    """One line for doctor. Never shows a secret."""
    try: c = load_config()
    except PublishError as e: return "not configured (optional: the user's own hosting)" if "NOT CONFIGURED" in str(e) else f"config problem: {e}"
    try: load_secrets(); sec = "credentials present"
    except PublishError: sec = "credentials MISSING"
    return f"{c['provider']} bucket {c['bucket']}, {sec}"

# ---------------- S3 (SigV4) ----------------
class S3:
    def __init__(self, c, key_id, secret):
        self.ep = c["endpoint"].rstrip("/"); self.bucket = c["bucket"]; self.region = c.get("region", "auto")
        self.kid, self.sec = key_id, secret; self.host = urllib.parse.urlparse(self.ep).netloc

    def _req(self, method, key="", query=None, body=b"", headers=None):
        path = "/" + urllib.parse.quote(self.bucket) + ("/" + urllib.parse.quote(key, safe="/~") if key else "")
        q = "&".join(f"{urllib.parse.quote(k, safe='-_.~')}={urllib.parse.quote(str(v), safe='-_.~')}" for k, v in sorted((query or {}).items()))
        now = datetime.datetime.now(datetime.timezone.utc); amz = now.strftime("%Y%m%dT%H%M%SZ"); day = amz[:8]
        h = {k.lower(): v for k, v in (headers or {}).items()}
        h.update({"host": self.host, "x-amz-date": amz, "x-amz-content-sha256": hashlib.sha256(body).hexdigest()})
        names = sorted(h); canon = "\n".join([method, path, q, "".join(f"{k}:{str(h[k]).strip()}\n" for k in names), ";".join(names), h["x-amz-content-sha256"]])
        scope = f"{day}/{self.region}/s3/aws4_request"
        sts = "\n".join(["AWS4-HMAC-SHA256", amz, scope, hashlib.sha256(canon.encode()).hexdigest()])
        k = ("AWS4" + self.sec).encode()
        for part in (day, self.region, "s3", "aws4_request"): k = hmac.new(k, part.encode(), hashlib.sha256).digest()
        sig = hmac.new(k, sts.encode(), hashlib.sha256).hexdigest()
        h["authorization"] = f"AWS4-HMAC-SHA256 Credential={self.kid}/{scope}, SignedHeaders={';'.join(names)}, Signature={sig}"
        req = urllib.request.Request(self.ep + path + ("?" + q if q else ""), data=body if method in ("PUT", "POST") else None, method=method,
                                     headers={k: v for k, v in h.items() if k != "host"})
        try:
            with urllib.request.urlopen(req, timeout=120) as r: return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()

    def put(self, key, data, ctype, cache, filename=None):
        h = {"content-type": ctype, "cache-control": cache}
        if filename: h["content-disposition"] = f'attachment; filename="{filename}"'   # Download button saves instead of opening
        st, _, b = self._req("PUT", key, body=data, headers=h)
        if st not in (200, 201): raise PublishError(f"upload failed: HTTP {st} {b[:200]!r}")

    def delete(self, key):
        st, _, b = self._req("DELETE", key)
        if st not in (200, 204): raise PublishError(f"delete failed: HTTP {st} {b[:200]!r}")

    def list(self, prefix):
        out, token = [], None
        while True:
            q = {"list-type": "2", "prefix": prefix}
            if token: q["continuation-token"] = token
            st, _, b = self._req("GET", query=q)
            if st != 200: raise PublishError(f"list failed: HTTP {st} {b[:200]!r}")
            root = ET.fromstring(b); ns = re.match(r"\{.*\}", root.tag); ns = ns.group(0) if ns else ""
            for c in root.findall(f"{ns}Contents"):
                lm = c.findtext(f"{ns}LastModified"); ts = datetime.datetime.fromisoformat(lm.replace("Z", "+00:00")).timestamp()
                out.append((c.findtext(f"{ns}Key"), ts, int(c.findtext(f"{ns}Size") or 0)))
            if root.findtext(f"{ns}IsTruncated") == "true": token = root.findtext(f"{ns}NextContinuationToken")
            else: return out

PROVIDERS = {"s3": S3}

def provider(c):
    if c["provider"] not in PROVIDERS: raise PublishError(f"unknown provider {c['provider']!r} (have: {', '.join(PROVIDERS)})")
    return PROVIDERS[c["provider"]](c, *load_secrets())

def public_url(c, key): return c["public_base"].rstrip("/") + "/" + urllib.parse.quote(key, safe="/~")

def head_public(url):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=60) as r: return r.status, int(r.headers.get("Content-Length") or -1)
    except urllib.error.HTTPError as e: return e.code, -1
    except Exception as e: raise PublishError(f"public URL not reachable: {e}")

def safe(name): return re.sub(r"[^A-Za-z0-9._-]", "_", name)[:120]

def publish(path, title="", log=print):
    c = load_config(); ext = os.path.splitext(path)[1].lower()
    if ext not in TYPES: raise PublishError(f"unsupported file type {ext} (mp4, png, jpg, webp)")
    size = os.path.getsize(path)
    if size > c["max_mb"] * 1e6: raise PublishError(f"file is {size / 1e6:.1f} MB, limit {c['max_mb']} MB: publish the shareable copy that make writes")
    now = int(time.time()); oid = f"{time.strftime('%Y%m%d', time.gmtime(now))}-{rnd.token_hex(4)}-{safe(os.path.splitext(os.path.basename(path))[0])}"
    key = c["prefix"] + oid + ext; p = provider(c)
    nice = (re.sub(r"[^A-Za-z0-9]+", "-", title).strip("-").lower()[:80] or safe(os.path.basename(os.path.splitext(path)[0]))) + ext
    p.put(key, open(path, "rb").read(), TYPES[ext], c["cache_control"], nice)
    url = public_url(c, key); st, ln = head_public(url)
    if st != 200 or ln != size:
        try: p.delete(key)   # never leave a half-published file behind
        except PublishError: pass
        raise PublishError(f"uploaded but the public URL does not serve it correctly (HTTP {st}, {ln} of {size} bytes): {url}")
    exp = now + int(float(c["ttl_hours"]) * 3600)
    link = url
    if c.get("player_base") and ext == ".mp4":
        q = {"c": oid, "e": exp}
        if title: q["t"] = title[:140]
        link = c["player_base"].rstrip("/") + "/?" + urllib.parse.urlencode(q, quote_via=urllib.parse.quote)
    return dict(link=link, url=url, path=key, bytes=size, uploaded_at=now, expires_at=exp,
                expires_local=time.strftime("%-I:%M %p %Z", time.localtime(exp)))

def sweep(log=print, dry=False):
    c = load_config(); p = provider(c); cutoff = time.time() - float(c["ttl_hours"]) * 3600; n = 0
    for key, ts, _ in p.list(c["prefix"]):
        if ts < cutoff:
            if not dry: p.delete(key)
            n += 1; log(("would delete " if dry else "deleted ") + key)
    log(f"sweep: {n} expired clip(s) {'found' if dry else 'deleted'} (older than {c['ttl_hours']} h)"); return n

def check(log=print):
    """Config, credentials, a tiny upload / public read / list / delete round trip, and the player page."""
    c = load_config(); log(f"config: {cfg_path()} ok ({c['provider']}, bucket {c['bucket']}, ttl {c['ttl_hours']} h, {c['cache_control']})")
    p = provider(c); log("credentials: present (not shown)")
    key = c["prefix"] + f"_check-{rnd.token_hex(4)}.png"
    data = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000"
                         "1f15c4890000000d49444154789c6360000002000100e221bc330000000049454e44ae426082")
    p.put(key, data, "image/png", c["cache_control"]); log("upload: ok")
    try:
        st, ln = head_public(public_url(c, key))
        if st != 200: raise PublishError(f"public read failed (HTTP {st}): turn on public read access for the bucket and check public_base")
        log("public read: ok")
        if not any(k == key for k, _, _ in p.list(c["prefix"])): raise PublishError("list did not show the test file")
        log("list: ok")
    finally:
        p.delete(key); log("delete: ok")
    if c.get("player_base"):
        try:
            with urllib.request.urlopen(c["player_base"], timeout=30) as r: log(f"player page: HTTP {r.status}")
        except Exception as e: raise PublishError(f"player page not reachable at {c['player_base']}: {e}")
    else: log("player page: not set (links will be the raw file URL)")
    log("PUBLISH READY"); return True

def make_player(out_dir, log=print):
    """Write the static player page (index.html + config.js) for the user's own free static hosting."""
    c = load_config(); os.makedirs(out_dir, exist_ok=True)
    kit = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = open(os.path.join(kit, "player", "index.html")).read()
    host = urllib.parse.urlparse(c["public_base"]); origin = f"{host.scheme}://{host.netloc}"
    open(os.path.join(out_dir, "index.html"), "w").write(src.replace("__MEDIA_ORIGIN__", origin))
    open(os.path.join(out_dir, "config.js"), "w").write(
        "window.AG = " + json.dumps({"media_base": public_url(c, c["prefix"]), "ttl_hours": c["ttl_hours"]}) + ";\n")
    log(f"player page written to {out_dir} (index.html, config.js): deploy it to the user's static hosting and set player_base")
