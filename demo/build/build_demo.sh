#!/usr/bin/env bash
# Builds demo/demo_voice.m4a + demo/demo_transcript.json from demo/demo_script.txt with Piper TTS.
# Piper (GPL-3.0) is installed into its own throwaway venv only to make this file; the kit never imports it.
# Voice: en_US-ljspeech-high (MIT, trained on the public domain LJ Speech dataset). See ../LICENSE-demo-audio.md.
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"; DEMO="$(dirname "$HERE")"
CACHE="${XDG_CACHE_HOME:-$HOME/.cache}/audiogram-maker"; mkdir -p "$CACHE"
VENV="$CACHE/piper-venv"; VOICE="$CACHE/en_US-ljspeech-high.onnx"
HF=https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/ljspeech/high
[ -x "$VENV/bin/python" ] || python3 -m venv "$VENV"
"$VENV/bin/pip" install -q "piper-tts==1.8.0" onnx
[ -s "$VOICE" ] || curl -sfL "$HF/en_US-ljspeech-high.onnx" -o "$VOICE"
[ -s "$VOICE.json" ] || curl -sfL "$HF/en_US-ljspeech-high.onnx.json" -o "$VOICE.json"
"$VENV/bin/python" "$HERE/make_demo_voice.py" "$VOICE" "$DEMO/demo_script.txt" "$DEMO"
ffmpeg -v error -y -i "$DEMO/demo_voice.wav" -ar 48000 -ac 1 -c:a aac -b:a 160k "$DEMO/demo_voice.m4a"
rm -f "$DEMO/demo_voice.wav"
echo "demo voice ready: $DEMO/demo_voice.m4a"
