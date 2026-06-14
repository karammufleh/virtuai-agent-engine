#!/usr/bin/env python3
"""Synthesize basic SFX (whoosh, boom, riser) locally with FFmpeg."""
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "virtuai" / "data" / "sfx"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
FFMPEG = "/opt/homebrew/opt/ffmpeg@7/bin/ffmpeg"

def make_whoosh():
    """Filtered noise sweep — 'whoosh' effect."""
    out = OUTPUT_DIR / "whoosh.mp3"
    # White noise → narrow band-pass that sweeps from high to low → amplitude envelope
    subprocess.run([
        FFMPEG, "-y",
        "-f", "lavfi", "-t", "0.5",
        "-i", "anoisesrc=color=white:amplitude=0.5",
        "-af",
        "bandpass=f=4000:width_type=h:w=2500,"
        "volume='if(lt(t,0.05),t/0.05,if(lt(t,0.4),1.0-(t-0.05)/0.7,0.0))':eval=frame,"
        "aecho=0.6:0.6:30:0.4",
        "-ar", "44100", "-ac", "2",
        str(out),
    ], check=True, capture_output=True)
    print(f"  ✓ {out.name}")

def make_boom():
    """Low-freq sine with sharp attack and decay — sub bass impact."""
    out = OUTPUT_DIR / "boom.mp3"
    subprocess.run([
        FFMPEG, "-y",
        "-f", "lavfi", "-t", "0.8",
        "-i", "aevalsrc='0.9*sin(2*PI*55*t)*exp(-3.0*t)+0.3*sin(2*PI*110*t)*exp(-5*t)':s=44100:c=stereo",
        "-af", "volume=0.9,aecho=0.5:0.5:40:0.3",
        "-ar", "44100",
        str(out),
    ], check=True, capture_output=True)
    print(f"  ✓ {out.name}")

def make_riser():
    """Ascending tonal sweep — tension build."""
    out = OUTPUT_DIR / "riser.mp3"
    subprocess.run([
        FFMPEG, "-y",
        "-f", "lavfi", "-t", "1.5",
        "-i", "aevalsrc='0.4*sin(2*PI*(200+300*t)*t)*min(t/0.3,1)*(1-max(0,(t-1.2)/0.3))':s=44100:c=stereo",
        "-af", "volume=0.7,aecho=0.4:0.4:50:0.3",
        "-ar", "44100",
        str(out),
    ], check=True, capture_output=True)
    print(f"  ✓ {out.name}")

if __name__ == "__main__":
    print("Synthesizing SFX with FFmpeg...")
    make_whoosh()
    make_boom()
    make_riser()
    print(f"\nSFX library: {OUTPUT_DIR}")
