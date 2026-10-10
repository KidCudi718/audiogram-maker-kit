# X Spaces Audiogram Maker kit

Turns an X Space into branded 16:9 audiogram clips: each speaker's photo inside a ring of audio reactive bars,
big word by word karaoke captions, the Space title, and a footer Hosts row led by the brand's mark. Paste a Space
link, get clips. Every brand detail comes from a brand profile JSON. Nothing is hard coded: no accounts, storage or
credentials live in the kit.

## What's new in v1.2
- The approved look is the built-in default for every brand: neutral charcoal (#0E0E10) dark and gray (#DCDCDE) light
  backgrounds, no top-bar logo (title left, "X Space · date" right), a centered Hosts row (56 px photos, name and
  handle) with the brand mark first, 84 px / 650 captions, accent only on highlights, neutral glow and frame line.
- Logos are never recolored, and the logo badge option is gone.
- `make` writes ONE small shareable MP4 in a single encode (CRF 23, maxrate 3M, AAC 160k, +faststart);
  `--master` gives the old archival encode.
- Automatic end padding: the clip end always sits 0.4 s after the last word (never into the next word).
- `transcribe` works in 10 minute chunks and logs `CHUNK READY mm:ss-mm:ss` after each one.
- `publish`: a pluggable upload to the user's OWN storage (S3-compatible, free tiers work) with one player link,
  `--check`, `--sweep` (2 hour expiry) and `--make-player`.
- The demo voice is built on first use instead of at install.

## Install
```bash
git clone --depth 1 --branch v1.2 https://github.com/KidCudi718/audiogram-maker-kit /home/box/audiogram-maker/kit
bash /home/box/audiogram-maker/kit/install.sh
```
Needs: python 3.10+, ffmpeg 6+ (with loudnorm, afftdn, acompressor, alimiter, ebur128), Google Chrome or Chromium
(headless, used to draw the text and design layers), and the python packages in requirements.txt
(numpy, pillow, faster-whisper, yt-dlp, beautifulsoup4). Speech to text uses the faster-whisper `small.en` model on CPU (int8,
word timestamps, VAD on), sequential decoding in 10 minute chunks. `--batch N` turns on faster-whisper's batched
pipeline; it is off by default because on CPU it was only slightly faster and dropped words on real Space audio. Use `--model medium.en` for hard audio, or `small` for non English.

install.sh installs what is missing, fetches the Inter font (SIL Open Font License, `fonts/OFL.txt`) from its
official release and checks it, downloads `small.en`, then runs `python3 audiogram.py doctor`. The demo voice used by
`sample` and `space-preview` (see `demo/LICENSE-demo-audio.md`) is built the first time one of them runs.

## Preview still of the user's own Space (before any transcript)
```bash
python3 kit/audiogram.py space-preview --work W --space https://x.com/i/spaces/<id> \
    --host @handle --host-name "Name" --host-pfp host.jpg [--cohost @handle --cohost-name "Name" --cohost-pfp cohost.jpg] \
    [--brand brands/<slug>/brand_profile.json] [--theme dark|light]
```
One still in the full design: the host's real photo in the ring, name chip, the Space title and date in the top bar
(read from the link and saved to `W/project.json`), host and co-host in the footer, and a placeholder caption with the
first words lit. With no `--brand`, the colours come from the host's photo and the host's name is a text wordmark;
the link and promo code only show when the brand has them. Writes `W/out/space_preview_<theme>.png`.

## First run for a new brand (no questions first)
```bash
python3 kit/audiogram.py brand-from-url https://example.com --out brands/example       # or:
python3 kit/audiogram.py brand-from-logo logo.svg --out brands/example --name "Example"  # SVG/PNG/JPG/WebP/ICO or a brand kit PDF
python3 kit/audiogram.py sample --brand brands/example/brand_profile.json               # still first, then an about 30 second video
```
brand-from-url reads the site's own logos (rendered with the site's CSS so variables and class colors become
plain fills, full wordmark preferred over an icon), colors (logo, theme-color and brand CSS variables count most;
third party widgets such as sign in buttons and cookie banners are ignored), name and description. Each logo is
checked for legibility on both styles in its OWN colors (logos are never recolored); a square-ish logo that reads on
dark becomes the `brand_mark` (first photo of the Hosts row), and the notes say when to ask the user for another file. Black and white brands get a neutral design with one
subtle accent. brand-from-logo uses the logo's own colors. Bitmap logos with a plain background are cut out to
transparency; logos under 40 px tall are not used (the name is set as text and the notes ask for a bigger file).
A site that blocks automated visits or shows nothing gets the name as text and a neutral design, with a note to
ask for the logo. Neither command ever guesses a third party's logo or colors.
Both write a full `brand_profile.json` with sensible defaults and print notes on what they guessed.
`sample` uses the bundled synthetic demo voice and an initials badge, so it needs no recording.

## One clip, start to finish
```bash
K=kit/audiogram.py; WK=/workspace/audiograms/<job>
python3 $K fetch --work $WK "https://x.com/i/spaces/<id>"        # also /peek links, twitter.com, a post with a Space, a file
python3 $K init --work $WK --brand /path/brand_profile.json      # title, "X Space · date", Hosts row (brand mark + Space hosts)
python3 $K pfp --work $WK --key sam --url <profile_image_url> --name "Sam" --handle sam   # each speaker's X photo
python3 $K transcribe --work $WK                                  # transcript.json / .txt / .srt, then speakers
python3 $K speakers --work $WK                                    # who said what, from X's live captions (speakers.json)
#   edit $WK/project.json: episode title, people (name, handle, photo), clips (start, end, turns, accent words, caption edits)
python3 $K quote --work $WK --clip <id>                           # the exact quote to show the user first
python3 $K make  --work $WK --clip <id> --theme dark               # master audio + layers + one encode to the shareable MP4 + QA
python3 $K make  --work $WK --clip <id> --theme light              # render while the user reviews dark; send after approval
python3 $K publish $WK/out/<clip>_dark.mp4 --title "Speaker: topic"   # optional: the user's own storage, prints ONE link
```
Fast design preview without a full render: `make ... --dump 0,8,20` writes exact stills.
Output: `$WK/out/<clip>_<theme>.mp4` (about 6 to 7 MB for 35 s; `--master` writes `<clip>_<theme>_master.mp4`) (never overwritten: re-renders get `_v2`, `_v3`), a `.qa.json`,
and stills plus a contact sheet in `$WK/out/stills/`.

## QA gate (blocks delivery)
Every `make` checks the result and exits with an error when anything fails; the file is then renamed
`<clip>_<theme>_QA_FAILED.mp4` so it cannot be sent by mistake. `--dump` previews run the layout part first
and refuse to draw a broken design (`LAYOUT FAIL`).
- Video: H.264 1920x1080 30 fps yuv420p, BT.709 tags, tv range, AAC 48 kHz.
- Audio: -14 LUFS plus or minus 1, true peak at or below -1 dBTP, no clipped samples.
- Timing: frame 0 is the full design with the first word lit (no fade in), no fade at the end, length matches the
  clip, captions end on a finished sentence, the payoff starts on its first word, and the speaker, ring, glow,
  name chip and last caption are fully gone (checked on the actual frame) before the payoff text fades in.
- Layout (real element boxes measured in Chrome): logo readable against its background and not a solid block,
  caption and payoff contrast at least 4.5:1 on the actual background pixels (colors that would fail are moved
  toward white or black just enough), every text box inside the title safe area, no text overflow (long titles,
  links and words shrink to fit), no overlaps between logo and title, footer items, chip and captions, captions
  and ring, or the payoff and anything else.

## project.json
```json
{
 "brand_profile": "/abs/path/brand_profile.json",
 "episode": {"title": "Show name", "subtitle": "X Space · Oct 9, 2026", "source": "source.m4a",
             "transcript": "transcript.json", "spelling": ["NamesToAlwaysSpellThisWay"],
             "hosts": "(optional) overrides the brand's hosts for this episode"},
 "people": {"sam": {"name": "Sam", "handle": "@sam", "img": "pfp/sam.jpg"}},
 "clips": {
  "sam_pricing": {
   "start": 1101.79, "end": 1123.84,
   "turns": [[1101.79, "sam"]],
   "accent_words": ["clarity", "$400"],
   "drops": ["just [like] having", "[um] so"],
   "fixes": [["wall,", "wallets,"]],
   "payoff_words": 0
  }
 }
}
```
- `turns`: who is speaking from when (source seconds). The photo, name and handle switch at that exact time.
- `accent_words`: the only words that get the accent color. Use 1 or 2 per sentence at most: numbers, the brand, the key idea.
- `drops`: caption only filler removal. Bracketed tokens are removed from the captions; the audio is never edited.
- `fixes`: caption only corrections of transcription errors (names, tickers, brand words).
- `payoff_words`: 0 means the last full sentence becomes the large centred payoff line. Set N to make the last N words the payoff.
- Speaker photos download from X automatically; `make` refuses to render an initials badge.
- Re-transcribe one clip more carefully with `transcribe --clip <id> --model medium.en` (writes tx/<id>.json, used automatically).

## What the look is (do not drift from this without the user asking)
Dark (main style): neutral charcoal `#0E0E10` background with a neutral glow, white ring bars, current word white with
an accent underline, upcoming words readable (3:1 or more), faint neutral frame line, solid footer pill with the
centered Hosts row (brand mark first, round 56 px photos with name and handle). No logo in the top bar: the Space title
on the left, "X Space · date" on the right. Captions 84 px, weight 650, sentence case, 4 to 8 words, at most 2 lines.
Light: the same layout on neutral gray `#DCDCDE`, key words and ring in the brand's primary color.
The brand colour is only ever a highlight. Logos are never recolored and never put on a colored background.
The last sentence becomes the large centered payoff line (`clip.payoff`, default on). No intro, fade in or fade out.
Audio: HPF 80 Hz, mild noise reduction, small low mid cut, presence lift, 2:1 compression, light limiter, two pass
linear loudnorm to -14 LUFS / -1 dBTP. Video: 1920x1080 30 fps, x264 preset slow CRF 23 maxrate 3M, BT.709 conversion
and tags, AAC 160k 48 kHz, +faststart (`--master`: CRF 12 tune film, AAC 320k).

## Publishing to the user's own storage (optional)
`publish` never uses anyone else's storage and has no built-in account. The user's own config lives in
`~/.config/audiogram-maker/publish.json`, their keys in `~/.config/audiogram-maker/.secrets` (mode 600, never printed):
```json
{"provider": "s3", "endpoint": "https://<s3 endpoint>", "region": "auto", "bucket": "<bucket>", "prefix": "clips/",
 "public_base": "https://<public host for the bucket>/", "player_base": "https://<static player page>/",
 "ttl_hours": 2, "cache_control": "public, max-age=3600", "max_mb": 50}
```
`.secrets`: `{"access_key_id": "...", "secret_access_key": "..."}` (or env `AG_PUBLISH_ACCESS_KEY_ID` / `AG_PUBLISH_SECRET_ACCESS_KEY`).
- `publish --check`: config, credentials, a tiny upload, public read, list and delete, and the player page.
- `publish FILE --title T [--json]`: uploads (Cache-Control at most 1 hour, Content-Disposition attachment so Download
  saves the file), verifies the public copy byte for byte, and prints ONE link: the player page
  `<player_base>?c=<id>&e=<expiry>&t=<title>` or, with no player page, the file URL.
- `publish --sweep [--dry-run]`: deletes clips older than `ttl_hours`; run it from a routine every 30 minutes.
  Add a 1 day lifecycle rule on the bucket as a backstop.
- `publish --make-player DIR`: writes `index.html` and `config.js` (the player page, locked to the bucket's host) to
  deploy on free static hosting such as GitHub Pages or Cloudflare Pages.

## Files
```
audiogram.py            the one entry command
lib/words.py            words, caption edits, speaker turns, caption phrases, karaoke timing
lib/theme.py            brand profile -> dark and light palettes
lib/layers.py           HTML design layers rendered in headless Chrome
lib/render.py           per frame compositor and encoder
lib/master_audio.py     voice mastering to -14 LUFS / -1 dBTP
lib/transcribe.py       faster-whisper word timestamps, in chunks with CHUNK READY progress
lib/publish.py          pluggable upload to the user's own storage, sweep, player page
lib/xspace.py           X Space live captions and speaker tagging
player/index.html       static player page (play + Download + expiry) for publish
lib/check.py            QA gate: specs, loudness, clipping, timing, layout, contrast, stills, contact sheet
lib/brand.py            brand profile from a website, logo, brand kit PDF, or the host's profile photo
demo/                   demo script, its license, and the scripts that build the demo voice
brand_profile.template.json
examples/lumora/        a fictional example brand (not real)
LICENSE                 MIT
```
