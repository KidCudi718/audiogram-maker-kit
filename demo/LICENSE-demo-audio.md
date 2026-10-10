# Demo audio license

Files: `demo_voice.m4a`, `demo_transcript.json`, `demo_script.txt`.

- **Script:** written for this kit. Released under CC0 1.0 (public domain dedication). Free to use for anything.
- **Voice:** synthetic, generated on Oct 10 2026 with Piper TTS (https://github.com/OHF-Voice/piper1-gpl, version 1.8.0)
  using the `en_US-ljspeech-high` voice from https://huggingface.co/rhasspy/piper-voices (MIT license).
  That voice was trained on the LJ Speech dataset (https://keithito.com/LJ-Speech-Dataset/), which is in the
  public domain. See `build/MODEL_CARD_en_US-ljspeech-high.txt`.
- **Piper itself** (GPL-3.0) was only used as a tool to create the file. It is not included in or required by this
  kit, and its license does not apply to the generated audio.
- **The audio file** is released under CC0 1.0 as well. It is not a recording of any real person.
- **How it gets onto a box:** the git repo carries only the script and the build files. `install.sh` runs
  `build/build_demo.sh`, which makes the audio and the timestamps locally, once.
- **Word timestamps** (`demo_transcript.json`) come directly from Piper's phoneme alignments (`build/make_demo_voice.py`), so the sample
  render needs no transcription.
