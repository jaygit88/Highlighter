"""Builds the highlight reel from the chosen clips using ffmpeg."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass

FADE = 0.3


@dataclass
class ReelClip:
    source: str
    start: float
    end: float
    has_audio: bool
    kind: str = "highlight"
    peak: float | None = None


def _encode(clip: ReelClip, start: float, end: float, out: str, height: int,
            fades: bool, speed: float = 1.0) -> None:
    dur = max(0.2, end - start)
    out_dur = dur / speed
    width = round(height * 16 / 9 / 2) * 2
    vf = [
        f"scale={width}:{height}:force_original_aspect_ratio=decrease",
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2",
        "setsar=1",
    ]
    if speed != 1.0:
        vf.append(f"setpts={1 / speed:.4f}*PTS")
    vf.append("fps=30")
    af = []
    if speed != 1.0:
        af.append(f"atempo={speed:.4f}")
    if fades and out_dur > 2 * FADE + 0.2:
        vf += [f"fade=t=in:st=0:d={FADE}", f"fade=t=out:st={out_dur - FADE:.3f}:d={FADE}"]
        af += [f"afade=t=in:st=0:d={FADE}", f"afade=t=out:st={out_dur - FADE:.3f}:d={FADE}"]

    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
           "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", clip.source]
    if clip.has_audio:
        audio_in = "0:a:0"
    else:
        cmd += ["-f", "lavfi", "-t", f"{dur:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
        audio_in = "1:a:0"
    cmd += ["-map", "0:v:0", "-map", audio_in, "-vf", ",".join(vf)]
    if af:
        cmd += ["-af", ",".join(af)]
    cmd += ["-t", f"{out_dur:.3f}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2", out]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"ffmpeg failed on {os.path.basename(clip.source)}: {res.stderr.strip()[-400:]}")


def build_reel(clips: list[ReelClip], out_path: str, height: int = 1080, fades: bool = True,
               goal_replays: bool = True, on_progress=lambda p, msg: None) -> None:
    if not clips:
        raise ValueError("No clips selected")
    tmp = tempfile.mkdtemp(prefix="highlighter-")
    try:
        parts: list[str] = []
        steps = len(clips) + sum(1 for c in clips if goal_replays and c.kind == "goal")
        done = 0
        for n, clip in enumerate(clips, 1):
            on_progress(done / (steps + 1), f"Cutting clip {n} of {len(clips)}")
            part = os.path.join(tmp, f"{len(parts):03d}.mp4")
            _encode(clip, clip.start, clip.end, part, height, fades)
            parts.append(part)
            done += 1
            if goal_replays and clip.kind == "goal":
                # Half-speed replay of the moment itself.
                peak = clip.peak if clip.peak is not None else (clip.start + clip.end) / 2
                r_start = max(clip.start, peak - 3.5)
                r_end = min(clip.end, peak + 1.5)
                on_progress(done / (steps + 1), f"Slow-motion replay for clip {n}")
                part = os.path.join(tmp, f"{len(parts):03d}.mp4")
                _encode(clip, r_start, r_end, part, height, fades, speed=0.5)
                parts.append(part)
                done += 1

        on_progress(done / (steps + 1), "Joining clips")
        listing = os.path.join(tmp, "list.txt")
        with open(listing, "w") as f:
            for p in parts:
                f.write(f"file '{p}'\n")
        res = subprocess.run(
            ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0",
             "-i", listing, "-c", "copy", "-movflags", "+faststart", out_path],
            capture_output=True, text=True,
        )
        if res.returncode != 0:
            raise RuntimeError(f"Joining clips failed: {res.stderr.strip()[-400:]}")
        on_progress(1.0, "Done")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
