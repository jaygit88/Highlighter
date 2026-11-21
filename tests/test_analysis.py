"""Run with:  python -m pytest tests   (or: python tests/test_analysis.py)"""

import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from highlighter import analysis  # noqa: E402

EVENTS = [(20, 24), (50, 54)]  # loud + busy moments in a 75s fake match


def make_fake_match(path: str) -> None:
    ev = "+".join(f"between(t,{a},{b})" for a, b in EVENTS)
    subprocess.run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "color=c=green:s=320x180:r=25:d=75",
        "-f", "lavfi", "-i", "anoisesrc=a=0.02:d=75:r=16000",
        "-filter_complex",
        f"[0]drawbox=x='mod(t*300,320)':y=40:w=110:h=110:color=white:t=fill:enable='{ev}'[v];"
        f"[1]volume='if({ev},25,1)':eval=frame[a]",
        "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "ultrafast",
        "-c:a", "aac", "-shortest", path,
    ], check=True)


def test_finds_the_planted_moments():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "match.mp4")
        make_fake_match(path)
        info = analysis.probe(path)
        assert info.has_audio and 74 < info.duration < 76
        sig = analysis.analyse(path, info.duration, info.has_audio)
        clips = analysis.find_moments(sig["score"], sig["window"], info.duration, 0.5)
        assert len(clips) == len(EVENTS)
        for clip, (a, b) in zip(clips, EVENTS):
            assert clip["start"] <= a and clip["end"] >= b


def test_sensitivity_and_merging():
    score = [0.0] * 200
    score[50] = 6.0   # big moment
    score[60] = 5.5   # 5s later: same moment, should merge into one clip
    score[150] = 2.5  # small moment
    few = analysis.find_moments(score, 0.5, 100, sensitivity=0.0, min_gap=2)
    many = analysis.find_moments(score, 0.5, 100, sensitivity=1.0, min_gap=2)
    assert len(few) == 1
    assert len(many) == 2


if __name__ == "__main__":
    test_sensitivity_and_merging()
    test_finds_the_planted_moments()
    print("All tests passed")
