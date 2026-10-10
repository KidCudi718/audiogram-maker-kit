"""Voice mastering for one clip. The video muxes the result.
Chain: high pass 80 Hz, mild afftdn noise reduction, -1.5 dB at 300 Hz, +2.5 dB at 3.2 kHz presence, gentle 2:1 compressor
(20/120 ms), light transient limiter, then two pass LINEAR loudnorm to -14 LUFS integrated / -1 dBTP. 0.5 s pre and post roll
so the filters settle, trimmed back to the exact clip. Stereo 48 kHz 24 bit WAV. No fades, no music.
CLI: python3 -m lib.master_audio SOURCE START DUR OUT.wav"""
import sys, json, subprocess, re
PRE = 0.5
CHAIN = ("highpass=f=80:poles=2,afftdn=nr=6:nf=-58,equalizer=f=300:t=q:w=1:g=-1.5,equalizer=f=3200:t=q:w=1.2:g=2.5,"
         "volume=8dB,acompressor=threshold=-21dB:ratio=2:attack=20:release=120:knee=4:makeup=1,"
         "alimiter=limit=0.33:attack=5:release=60:level=disabled:asc=1:latency=1")

def _run(af, inp, outa):
    return subprocess.run(["ffmpeg", "-hide_banner", "-y", *inp, "-af", af, *outa], capture_output=True, text=True).stderr

def measure(inp, af="anull"):
    e = _run(af + ",ebur128=peak=true", inp, ["-f", "null", "-"]); tail = e[e.rfind("Summary:"):]
    g = lambda k: float(re.search(k + r":\s+(-?[\d.]+)", tail).group(1))
    return dict(I=g("I"), LRA=g("LRA"), TP=g("Peak"))

def master(src, st, dur, out, target=-14.0, tp=-1.0):
    pre = max(0.0, st - PRE); lead = st - pre
    trim = f"atrim=start={lead:.3f}:duration={dur:.3f},asetpts=PTS-STARTPTS"
    src_in = ["-ss", f"{pre:.3f}", "-t", f"{dur + lead + PRE:.3f}", "-i", src]
    before = measure(["-ss", f"{st:.3f}", "-t", f"{dur:.3f}", "-i", src])
    e = _run(f"{CHAIN},{trim},loudnorm=I={target}:TP={tp}:LRA=11:print_format=json", src_in, ["-f", "null", "-"])
    m = json.loads(e[e.rfind("{"):e.rfind("}") + 1])
    ln = (f"loudnorm=I={target}:TP={tp}:LRA=11:measured_I={m['input_i']}:measured_TP={m['input_tp']}:measured_LRA={m['input_lra']}:"
          f"measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true:print_format=json")
    full = f"{CHAIN},{trim},{ln}"; outa = ["-ar", "48000", "-ac", "2", "-c:a", "pcm_s24le", out]
    e = _run(full, src_in, outa); p2 = json.loads(e[e.rfind("{"):e.rfind("}") + 1])
    after = measure(["-i", out])
    if abs(after["I"] - target) > 0.15:   # linear mode can land a few tenths off: one static trim, never dynamic
        full += f",volume={target - after['I']:.2f}dB"; _run(full, src_in, outa); after = measure(["-i", out])
    open(out + ".chain.txt", "w").write(full + "\n")
    return dict(before=before, after=after, loudnorm_mode=p2.get("normalization_type"), chain=full)

if __name__ == "__main__":
    print(json.dumps(master(sys.argv[1], float(sys.argv[2]), float(sys.argv[3]), sys.argv[4]), indent=1))
