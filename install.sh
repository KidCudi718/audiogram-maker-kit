#!/usr/bin/env bash
# Audiogram Maker kit installer (Debian / Ubuntu style box). Safe to run more than once.
set -e
cd "$(dirname "$0")"
need() { command -v "$1" >/dev/null 2>&1; }
SUDO=""; if [ "$(id -u)" != "0" ] && need sudo; then SUDO="sudo"; fi
if ! need ffmpeg; then $SUDO apt-get update -y && $SUDO apt-get install -y ffmpeg; fi
if ! need google-chrome && ! need chromium && ! need chromium-browser; then
  $SUDO apt-get install -y chromium || $SUDO apt-get install -y chromium-browser || echo "Install Google Chrome or Chromium manually"
fi
need unzip || $SUDO apt-get install -y unzip
need pdftotext || $SUDO apt-get install -y poppler-utils || true
python3 -m pip install --user -q -r requirements.txt || python3 -m pip install --user -q --break-system-packages -r requirements.txt

# Font: Inter 4.1 (SIL Open Font License, see fonts/OFL.txt), fetched from the official release and checked.
if [ ! -s fonts/Inter-Variable.ttf ]; then
  T=$(mktemp -d); curl -sfL https://github.com/rsms/inter/releases/download/v4.1/Inter-4.1.zip -o "$T/inter.zip"
  echo "9883fdd4a49d4fb66bd8177ba6625ef9a64aa45899767dde3d36aa425756b11e  $T/inter.zip" | sha256sum -c --quiet
  unzip -q -o "$T/inter.zip" InterVariable.ttf -d "$T" && mkdir -p fonts && cp "$T/InterVariable.ttf" fonts/Inter-Variable.ttf; rm -rf "$T"
  echo "font ready"
fi

# Demo voice for the first run sample (synthetic, CC0). Built once with Piper in its own venv.
if [ ! -s demo/demo_voice.m4a ] || [ ! -s demo/demo_transcript.json ]; then bash demo/build/build_demo.sh; fi

# Speech model for real recordings (about 500 MB, once)
python3 - <<'PY'
from faster_whisper import WhisperModel
WhisperModel("small.en", device="cpu", compute_type="int8")
print("whisper small.en ready")
PY
python3 audiogram.py doctor
