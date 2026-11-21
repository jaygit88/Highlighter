"""Finds exciting moments in goal-mounted action camera footage.

With the camera sitting in the goal, almost everything worth keeping happens
close to the lens:

* goals, saves and close misses bring players and the ball right up to the
  camera, so the picture changes a lot from frame to frame (and a ball hitting
  the net shakes the camera);
* the sideline gets loud for goals, saves, misses and celebrations.

When play is at the other end of the pitch the players are small and the
picture barely changes. So we measure two signals across the whole video,
"motion" and "loudness", score each half-second, and pick the peaks.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass

import numpy as np

WINDOW = 0.5  # seconds per analysis step

# Small greyscale frames are plenty to measure how much the picture changes.
MOTION_FPS = 10
MOTION_W, MOTION_H = 96, 54
AUDIO_RATE = 8000


@dataclass
class Probe:
    duration: float
    width: int
    height: int
    has_audio: bool


def probe(path: str) -> Probe:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "format=duration:stream=codec_type,width,height", "-of", "json", path],
        capture_output=True, text=True, check=True,
    ).stdout
    info = json.loads(out)
    streams = info.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        raise ValueError("No video stream found in this file")
    return Probe(
        duration=float(info.get("format", {}).get("duration") or 0),
        width=int(video.get("width") or 0),
        height=int(video.get("height") or 0),
        has_audio=any(s.get("codec_type") == "audio" for s in streams),
    )


def _run_with_progress(cmd: list[str], duration: float, on_progress) -> None:
    """Run ffmpeg with `-progress pipe:1` and report 0..1 progress."""
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    assert proc.stdout is not None
    for line in proc.stdout:
        if line.startswith("out_time_us=") and duration > 0:
            try:
                secs = int(line.split("=", 1)[1]) / 1e6
            except ValueError:
                continue
            on_progress(min(1.0, max(0.0, secs / duration)))
    _, err = proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {err.strip()[-500:]}")


def make_proxy(src: str, dst: str, duration: float, on_progress=lambda p: None) -> None:
    """Small, browser-friendly copy for reviewing (action cams often record HEVC,
    which many browsers can't play). Keyframe every second so seeking is instant."""
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", src,
        "-map", "0:v:0", "-map", "0:a:0?",
        "-vf", "fps=30,scale=-2:480",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "28", "-g", "30",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "96k", "-ac", "2",
        "-movflags", "+faststart",
        "-progress", "pipe:1", "-nostats", dst,
    ]
    _run_with_progress(cmd, duration, on_progress)


def audio_loudness(path: str, n_windows: int) -> np.ndarray:
    """Loudness in dB for each window (zeros if the video has no sound)."""
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", path, "-vn",
         "-ac", "1", "-ar", str(AUDIO_RATE), "-f", "s16le", "-"],
        capture_output=True,
    )
    samples = np.frombuffer(proc.stdout, dtype=np.int16).astype(np.float32) / 32768.0
    per = int(AUDIO_RATE * WINDOW)
    out = np.zeros(n_windows, dtype=np.float32)
    if samples.size < per:
        return out
    usable = (samples.size // per) * per
    rms = np.sqrt(np.mean(samples[:usable].reshape(-1, per) ** 2, axis=1))
    db = 20 * np.log10(rms + 1e-5)
    n = min(n_windows, db.size)
    out[:n] = db[:n]
    if n < n_windows:
        out[n:] = db.min()
    return out


def motion_energy(path: str, n_windows: int, duration: float, on_progress=lambda p: None) -> np.ndarray:
    """How much the picture changes in each window.

    The lower two thirds of the frame (where players and the ball are when
    they're near the goal) counts double, so clouds and trees matter less.
    """
    frame_bytes = MOTION_W * MOTION_H
    proc = subprocess.Popen(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", path, "-an",
         "-vf", f"fps={MOTION_FPS},scale={MOTION_W}:{MOTION_H},format=gray",
         "-f", "rawvideo", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )
    assert proc.stdout is not None
    weights = np.ones((MOTION_H, MOTION_W), dtype=np.float32)
    weights[MOTION_H // 3:, :] = 2.0
    weights /= weights.sum()

    sums = np.zeros(n_windows, dtype=np.float32)
    counts = np.zeros(n_windows, dtype=np.float32)
    prev = None
    idx = 0
    total_frames = max(1, int(duration * MOTION_FPS))
    while True:
        buf = proc.stdout.read(frame_bytes)
        if len(buf) < frame_bytes:
            break
        frame = np.frombuffer(buf, dtype=np.uint8).reshape(MOTION_H, MOTION_W).astype(np.float32)
        if prev is not None:
            diff = float((np.abs(frame - prev) * weights).sum())
            w = int((idx / MOTION_FPS) / WINDOW)
            if 0 <= w < n_windows:
                sums[w] += diff
                counts[w] += 1
        prev = frame
        idx += 1
        if idx % 300 == 0:
            on_progress(min(1.0, idx / total_frames))
    proc.wait()
    out = np.divide(sums, counts, out=np.zeros_like(sums), where=counts > 0)
    return out


def robust_z(x: np.ndarray) -> np.ndarray:
    """Standardise against the 'normal' level of the match, ignoring outliers."""
    med = np.median(x)
    mad = np.median(np.abs(x - med)) * 1.4826
    spread = max(mad, 1e-6, 0.05 * abs(med))
    return np.clip((x - med) / spread, -3, 12)


def smooth(x: np.ndarray, n: int) -> np.ndarray:
    if n <= 1 or x.size == 0:
        return x
    kernel = np.ones(n) / n
    return np.convolve(x, kernel, mode="same")


def excitement(audio_db: np.ndarray, motion: np.ndarray, has_audio: bool) -> np.ndarray:
    mz = robust_z(smooth(motion, 3))
    if not has_audio:
        return smooth(mz, 3)
    az = robust_z(smooth(audio_db, 3))
    # Both together is the strongest sign; either alone still counts.
    score = 0.5 * az + 0.5 * mz + 0.15 * np.minimum(az, mz)
    return smooth(score, 3)


def analyse(path: str, duration: float, has_audio: bool, on_progress=lambda p: None) -> dict:
    n = max(1, int(np.ceil(duration / WINDOW)))
    audio = audio_loudness(path, n) if has_audio else np.zeros(n, dtype=np.float32)
    on_progress(0.1)
    motion = motion_energy(path, n, duration, lambda p: on_progress(0.1 + 0.9 * p))
    score = excitement(audio, motion, has_audio)
    return {
        "window": WINDOW,
        "audio": np.round(robust_z(audio), 2).tolist() if has_audio else [0.0] * n,
        "motion": np.round(robust_z(motion), 2).tolist(),
        "score": np.round(score, 3).tolist(),
    }


def find_moments(score: list[float], window: float, duration: float, sensitivity: float = 0.5,
                 pre: float = 7.0, post: float = 7.0, min_gap: float = 15.0,
                 max_clips: int = 40) -> list[dict]:
    """Pick the strongest peaks and turn them into clips.

    sensitivity 0 = only the biggest moments, 1 = anything a bit lively.
    Clips start `pre` seconds before the peak (to see the build-up) and run
    `post` seconds after it (to catch the celebration).
    """
    s = np.asarray(score, dtype=np.float32)
    if s.size == 0:
        return []
    sensitivity = float(min(1.0, max(0.0, sensitivity)))
    threshold = 5.0 - 3.5 * sensitivity
    gap = max(1, int(min_gap / window))

    picked: list[int] = []
    for i in np.argsort(-s):
        if s[i] < threshold or len(picked) >= max_clips:
            break
        if all(abs(int(i) - j) >= gap for j in picked):
            picked.append(int(i))

    clips = []
    for i in sorted(picked):
        peak = (i + 0.5) * window
        start = max(0.0, peak - pre)
        end = min(duration, peak + post)
        if clips and start <= clips[-1]["end"]:
            clips[-1]["end"] = max(clips[-1]["end"], end)
            clips[-1]["score"] = max(clips[-1]["score"], float(s[i]))
            continue
        clips.append({"start": round(start, 2), "end": round(end, 2),
                      "peak": round(peak, 2), "score": round(float(s[i]), 2)})
    return clips
