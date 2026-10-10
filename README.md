# Audiogram Maker kit

Turns an X Space, podcast, or any audio or video recording into branded 16:9 audiogram clips:
speaker photo inside a ring of audio reactive bars, word by word karaoke captions, the brand's logo,
colors, link and promo code. Every brand detail comes from a brand profile JSON. Nothing is hard coded.

## Install
```bash
git clone --depth 1 --branch v1.0 https://github.com/KidCudi718/audiogram-maker-kit /home/box/audiogram-maker/kit
bash /home/box/audiogram-maker/kit/install.sh
```
Needs: python 3.10+, ffmpeg 6+ (with loudnorm, afftdn, acompressor, alimiter, ebur128), Google Chrome or Chromium
(headless, used to draw the text and design layers), and the python packages in requirements.txt
(numpy, pillow, faster-whisper, yt-dlp, beautifulsoup4). Speech to text uses the faster-whisper `small.en` model on CPU (int8,
word timestamps, VAD on). Use `--model medium.en` for hard audio, or `small` for non English.

install.sh installs what is missing, fetches the Inter font (SIL Open Font License, `fonts/OFL.txt`) from its
official release and checks it, builds the demo voice once (see `demo/LICENSE-demo-audio.md`), downloads
`small.en`, then runs `python3 audiogram.py doctor`.

## First run for a new brand (no questions first)
```bash
python3 kit/audiogram.py brand-from-url https://example.com --out brands/example       # or:
python3 kit/audiogram.py brand-from-logo logo.svg --out brands/example --name "Example"  # SVG/PNG/JPG or a brand kit PDF
python3 kit/audiogram.py sample --brand brands/example/brand_profile.json               # still first, then a 30 s video
```
brand-from-url reads the site's logo (inline SVG, logo image, or app icon), colors (theme-color, logo,
button and brand CSS colors, a screenshot), name and description. brand-from-logo uses the logo's own colors.
Both write a full `brand_profile.json` with sensible defaults and print notes on what they guessed.
`sample` uses the bundled synthetic demo voice and an initials badge, so it needs no recording.

## One clip, start to finish
```bash
K=kit/audiogram.py; WK=/workspace/audiograms/<job>
python3 $K init --work $WK --brand /path/brand_profile.json      # writes $WK/project.json
python3 $K fetch --work $WK "https://x.com/i/spaces/<id>"        # or a local file path
python3 $K transcribe --work $WK                                  # transcript.json / .txt / .srt
#   edit $WK/project.json: episode title, people (name, handle, photo), clips (start, end, turns, accent words, caption edits)
python3 $K quote --work $WK --clip <id>                           # the exact quote to show the user first
python3 $K make  --work $WK --clip <id> --theme dark               # master audio + layers + render + QA
python3 $K make  --work $WK --clip <id> --theme light              # only after the dark one is approved
```
Fast design preview without a full render: `make ... --dump 0,8,20` writes exact stills.
Output: `$WK/out/<clip>_<theme>_16x9.mp4` (never overwritten: re-renders get `_v2`, `_v3`), a `.qa.json`,
and stills plus a contact sheet in `$WK/out/stills/`.

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
- A missing photo falls back to an initials badge in brand colors.
- Re-transcribe one clip more carefully with `transcribe --clip <id> --model medium.en` (writes tx/<id>.json, used automatically).

## What the look is (do not drift from this without the user asking)
Dark: deep background built from `colors.background_dark`, white ring bars, pure accent highlights, current word white
with an accent underline, solid footer pill, no haze or blur, grain only as dither, vignette on the scene only.
Light: the same layout on `colors.background_light`, ring and key words in the brand's primary color.
Audio: HPF 80 Hz, mild noise reduction, small low mid cut, presence lift, 2:1 compression, light limiter, two pass
linear loudnorm to -14 LUFS / -1 dBTP. Video: 1920x1080 30 fps, x264 CRF 12 preset slow tune film, BT.709 conversion
and tags, AAC 320k 48 kHz, +faststart. No intro, no fade in, no fade out.

## Files
```
audiogram.py            the one entry command
lib/words.py            words, caption edits, speaker turns, caption phrases, karaoke timing
lib/theme.py            brand profile -> dark and light palettes
lib/layers.py           HTML design layers rendered in headless Chrome
lib/render.py           per frame compositor and encoder
lib/master_audio.py     voice mastering to -14 LUFS / -1 dBTP
lib/transcribe.py       faster-whisper word timestamps
lib/check.py            QA: specs, loudness, colour tags, stills, contact sheet
lib/brand.py            brand profile from a website, logo or brand kit PDF
demo/                   demo script, its license, and the scripts that build the demo voice
brand_profile.template.json
examples/lumora/        a fictional example brand (not real)
LICENSE                 MIT
```
