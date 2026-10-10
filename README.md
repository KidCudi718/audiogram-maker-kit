# X Spaces Audiogram Maker kit

Turns an X Space into branded 16:9 audiogram clips: each speaker's photo inside a ring of audio reactive bars,
word by word karaoke captions, the brand's logo, colors, link and promo code. Paste a Space link, get clips.
Any other recording (podcast file, YouTube link) works too. Every brand detail comes from a brand profile JSON.
Nothing is hard coded.

## Install
```bash
git clone --depth 1 --branch v1.1 https://github.com/KidCudi718/audiogram-maker-kit /home/box/audiogram-maker/kit
bash /home/box/audiogram-maker/kit/install.sh
```
Needs: python 3.10+, ffmpeg 6+ (with loudnorm, afftdn, acompressor, alimiter, ebur128), Google Chrome or Chromium
(headless, used to draw the text and design layers), and the python packages in requirements.txt
(numpy, pillow, faster-whisper, yt-dlp, beautifulsoup4). Speech to text uses the faster-whisper `small.en` model on CPU (int8,
word timestamps, VAD on). Use `--model medium.en` for hard audio, or `small` for non English.

install.sh installs what is missing, fetches the Inter font (SIL Open Font License, `fonts/OFL.txt`) from its
official release and checks it, builds the demo voice once (see `demo/LICENSE-demo-audio.md`), downloads
`small.en`, then runs `python3 audiogram.py doctor`.

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
checked for legibility on both styles; single color logos may be recolored, multi color logos and icons never are,
and if nothing reads well the brand name is set as text. Black and white brands get a neutral design with one
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
python3 $K init --work $WK --brand /path/brand_profile.json      # project.json, prefilled from space.json (title, date, host)
python3 $K pfp --work $WK --key sam --url <profile_image_url> --name "Sam" --handle sam   # each speaker's X photo
python3 $K transcribe --work $WK                                  # transcript.json / .txt / .srt, then speakers
python3 $K speakers --work $WK                                    # who said what, from X's live captions (speakers.json)
#   edit $WK/project.json: episode title, people (name, handle, photo), clips (start, end, turns, accent words, caption edits)
python3 $K quote --work $WK --clip <id>                           # the exact quote to show the user first
python3 $K make  --work $WK --clip <id> --theme dark               # master audio + layers + render + QA
python3 $K make  --work $WK --clip <id> --theme light              # only after the dark one is approved
```
Fast design preview without a full render: `make ... --dump 0,8,20` writes exact stills.
Output: `$WK/out/<clip>_<theme>_16x9.mp4` (never overwritten: re-renders get `_v2`, `_v3`), a `.qa.json`,
and stills plus a contact sheet in `$WK/out/stills/`.

## QA gate (blocks delivery)
Every `make` checks the result and exits with an error when anything fails; the file is then renamed
`<clip>_<theme>_16x9_QA_FAILED.mp4` so it cannot be sent by mistake. `--dump` previews run the layout part first
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
Dark: deep background built from `colors.background_dark`, white ring bars, pure accent highlights, current word white
with an accent underline, solid footer pill, no haze or blur, grain only as dither, vignette on the scene only.
Upcoming words stay readable (3.4:1 or more on the real background; about #7F8984 on a near white background).
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
lib/check.py            QA gate: specs, loudness, clipping, timing, layout, contrast, stills, contact sheet
lib/brand.py            brand profile from a website, logo, brand kit PDF, or the host's profile photo
demo/                   demo script, its license, and the scripts that build the demo voice
brand_profile.template.json
examples/lumora/        a fictional example brand (not real)
LICENSE                 MIT
```
