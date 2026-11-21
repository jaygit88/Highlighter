"""Highlighter: a local web app that finds the best moments in football videos.

Run:  python app.py   then open http://localhost:5000
Phones on the same Wi-Fi can use it too: click "Use on phone" for the address and PIN.
"""

from __future__ import annotations

import datetime
import hashlib
import io
import json
import os
import queue
import secrets
import shutil
import socket
import sys
import threading
import time
import traceback
import uuid
import webbrowser

import qrcode
import qrcode.image.svg
from flask import Flask, abort, jsonify, request, send_file, send_from_directory, session
from werkzeug.utils import secure_filename

from highlighter import analysis
from highlighter.export import ReelClip, build_reel

ROOT = os.path.dirname(os.path.abspath(__file__))
VIDEOS_DIR = os.environ.get("HIGHLIGHTER_VIDEOS", os.path.join(ROOT, "videos"))
DATA_DIR = os.path.join(ROOT, "data")
EXPORTS_DIR = os.path.join(ROOT, "exports")
VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".mkv", ".avi", ".mts", ".lrv", ".360", ".insv"}
KINDS = {"highlight", "goal", "save", "miss", "celebration"}

UPLOADS_DIR = os.path.join(DATA_DIR, "_uploads")
PORT = int(os.environ.get("PORT", 5000))
LOCAL_ONLY = os.environ.get("HIGHLIGHTER_LOCAL_ONLY") == "1"

for d in (VIDEOS_DIR, DATA_DIR, EXPORTS_DIR, UPLOADS_DIR):
    os.makedirs(d, exist_ok=True)


def _persistent_secret(name: str, make) -> str:
    path = os.path.join(DATA_DIR, name)
    try:
        with open(path) as f:
            value = f.read().strip()
            if value:
                return value
    except FileNotFoundError:
        pass
    value = make()
    with open(path, "w") as f:
        f.write(value)
    return value


app = Flask(__name__, static_folder="static", static_url_path="/static")
app.config.update(
    MAX_CONTENT_LENGTH=None,
    SECRET_KEY=_persistent_secret("secret.txt", lambda: secrets.token_hex(32)),
    PERMANENT_SESSION_LIFETIME=datetime.timedelta(days=365),
    SESSION_COOKIE_SAMESITE="Lax",
)
PIN = _persistent_secret("pin.txt", lambda: f"{secrets.randbelow(10**6):06d}")

lock = threading.RLock()
work_queue: "queue.Queue[str]" = queue.Queue()
exports: dict[str, dict] = {}


# ---------------------------------------------------------------- storage

def video_id(filename: str) -> str:
    return hashlib.sha1(filename.encode()).hexdigest()[:12]


def vdir(vid: str) -> str:
    return os.path.join(DATA_DIR, vid)


def read_json(path: str, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def write_json(path: str, data) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.replace(tmp, path)


def get_meta(vid: str) -> dict | None:
    return read_json(os.path.join(vdir(vid), "meta.json"), None)


def set_meta(vid: str, **changes) -> dict:
    with lock:
        meta = get_meta(vid) or {}
        meta.update(changes)
        write_json(os.path.join(vdir(vid), "meta.json"), meta)
        return meta


def get_clips(vid: str) -> list[dict]:
    return read_json(os.path.join(vdir(vid), "clips.json"), [])


def save_clips(vid: str, clips: list[dict]) -> None:
    clips.sort(key=lambda c: c["start"])
    write_json(os.path.join(vdir(vid), "clips.json"), clips)


def scan_videos() -> list[dict]:
    """Register any new files in the videos folder and return all videos."""
    found = []
    for name in sorted(os.listdir(VIDEOS_DIR)):
        if os.path.splitext(name)[1].lower() not in VIDEO_EXTS or name.startswith("."):
            continue
        vid = video_id(name)
        meta = get_meta(vid)
        if meta is None:
            os.makedirs(vdir(vid), exist_ok=True)
            meta = set_meta(vid, id=vid, filename=name, status="new", progress=0, error=None,
                            added=time.time())
        found.append(meta)
    return found


def public(meta: dict) -> dict:
    clips = get_clips(meta["id"])
    return {**meta, "clip_count": len(clips), "kept_count": sum(1 for c in clips if c.get("keep"))}


def new_clip(start: float, end: float, peak: float | None = None, score: float = 0.0,
             source: str = "auto") -> dict:
    return {"id": uuid.uuid4().hex[:8], "start": start, "end": end,
            "peak": peak if peak is not None else (start + end) / 2,
            "score": score, "kind": "highlight", "keep": True, "source": source}


# ---------------------------------------------------------------- background work

def process_video(vid: str) -> None:
    meta = get_meta(vid)
    if not meta:
        return
    src = os.path.join(VIDEOS_DIR, meta["filename"])
    try:
        set_meta(vid, status="processing", stage="Reading video", progress=0, error=None)
        info = analysis.probe(src)
        set_meta(vid, duration=info.duration, width=info.width, height=info.height,
                 has_audio=info.has_audio)

        proxy = os.path.join(vdir(vid), "proxy.mp4")
        set_meta(vid, stage="Preparing preview")
        analysis.make_proxy(src, proxy, info.duration,
                            lambda p: set_meta(vid, progress=round(p * 0.6, 3)))

        set_meta(vid, stage="Looking for action")
        signal = analysis.analyse(proxy, info.duration, info.has_audio,
                                  lambda p: set_meta(vid, progress=round(0.6 + p * 0.4, 3)))
        write_json(os.path.join(vdir(vid), "signal.json"), signal)

        sensitivity = meta.get("sensitivity", 0.5)
        moments = analysis.find_moments(signal["score"], signal["window"], info.duration, sensitivity)
        manual = [c for c in get_clips(vid) if c.get("source") == "manual"]
        save_clips(vid, manual + [new_clip(m["start"], m["end"], m["peak"], m["score"]) for m in moments])
        set_meta(vid, status="ready", stage=None, progress=1, sensitivity=sensitivity)
    except Exception as exc:  # noqa: BLE001 - report anything to the UI
        traceback.print_exc()
        set_meta(vid, status="error", stage=None, error=str(exc))


def worker() -> None:
    while True:
        vid = work_queue.get()
        try:
            process_video(vid)
        finally:
            work_queue.task_done()


def run_export(job_id: str, items: list[ReelClip], out_path: str, opts: dict) -> None:
    def progress(p, msg):
        exports[job_id].update(progress=round(p, 3), message=msg)
    try:
        build_reel(items, out_path, height=opts["height"], fades=opts["fades"],
                   goal_replays=opts["replays"], on_progress=progress)
        exports[job_id].update(status="done", file=os.path.basename(out_path))
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        exports[job_id].update(status="error", message=str(exc))


# ---------------------------------------------------------------- phone access

def is_local() -> bool:
    return request.remote_addr in ("127.0.0.1", "::1")


def lan_ip() -> str | None:
    """This computer's address on the home network (no data is actually sent)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
        return None if ip.startswith("127.") else ip
    except OSError:
        return None
    finally:
        s.close()


failed_logins: dict[str, list[float]] = {}


@app.before_request
def require_pin():
    """The computer itself is always allowed; other devices need the PIN once."""
    if is_local() or session.get("ok") or request.path.startswith("/static/"):
        return None
    if request.path in ("/login", "/api/login"):
        return None
    if request.path.startswith("/api/"):
        return jsonify({"error": "Enter the PIN first", "login": True}), 401
    return send_from_directory(os.path.join(ROOT, "static"), "login.html")


@app.get("/login")
def login_page():
    return send_from_directory(os.path.join(ROOT, "static"), "login.html")


@app.post("/api/login")
def login():
    ip = request.remote_addr or "?"
    now = time.time()
    recent = [t for t in failed_logins.get(ip, []) if now - t < 300]
    failed_logins[ip] = recent
    if len(recent) >= 8:
        return jsonify({"error": "Too many wrong tries. Wait 5 minutes and try again."}), 429
    pin = str((request.get_json(silent=True) or {}).get("pin", "")).strip()
    if secrets.compare_digest(pin, PIN):
        session.permanent = True
        session["ok"] = True
        failed_logins.pop(ip, None)
        return jsonify({"ok": True})
    recent.append(now)
    return jsonify({"error": "That PIN isn't right. It's shown on the computer under “Use on phone”."}), 403


@app.get("/api/phone")
def phone_info():
    """Address, QR code and PIN for connecting a phone. Only shown on the computer itself."""
    if not is_local():
        abort(403)
    ip = None if LOCAL_ONLY else lan_ip()
    url = f"http://{ip}:{PORT}" if ip else None
    svg = None
    if url:
        buf = io.BytesIO()
        qrcode.make(url, image_factory=qrcode.image.svg.SvgPathImage, border=2).save(buf)
        svg = buf.getvalue().decode()
    return jsonify({"url": url, "pin": PIN, "qr": svg, "local_only": LOCAL_ONLY})


# ---------------------------------------------------------------- routes

@app.get("/")
def index():
    return send_from_directory(os.path.join(ROOT, "static"), "index.html")


@app.get("/api/videos")
def list_videos():
    videos = scan_videos()
    for m in videos:
        if m.get("status") == "new":
            queue_video(m["id"])
    return jsonify({"videos": [public(get_meta(m["id"])) for m in videos], "folder": VIDEOS_DIR})


# Uploads arrive in pieces, so a big file from a phone can carry on where it
# left off if the Wi-Fi drops or the screen locks.

def upload_part(uid: str) -> str:
    if not uid.isalnum():
        abort(400)
    return os.path.join(UPLOADS_DIR, uid + ".part")


@app.post("/api/upload/start")
def upload_start():
    body = request.get_json(force=True) or {}
    name = secure_filename(str(body.get("name", "")))
    size = int(body.get("size") or 0)
    if not name or os.path.splitext(name)[1].lower() not in VIDEO_EXTS:
        return jsonify({"error": f"“{body.get('name')}” doesn't look like a video file."}), 400
    existing = os.path.join(VIDEOS_DIR, name)
    if os.path.exists(existing) and os.path.getsize(existing) == size:
        return jsonify({"exists": True, "video": video_id(name)})
    uid = hashlib.sha1(f"{name}:{size}".encode()).hexdigest()[:20]
    part = upload_part(uid)
    offset = os.path.getsize(part) if os.path.exists(part) else 0
    write_json(os.path.join(UPLOADS_DIR, uid + ".json"), {"name": name, "size": size})
    return jsonify({"id": uid, "offset": offset})


@app.put("/api/upload/<uid>")
def upload_chunk(uid):
    part = upload_part(uid)
    info = read_json(os.path.join(UPLOADS_DIR, uid + ".json"), None)
    if info is None:
        abort(404)
    offset = int(request.args.get("offset", -1))
    current = os.path.getsize(part) if os.path.exists(part) else 0
    if offset != current:
        return jsonify({"offset": current}), 409
    with open(part, "ab") as f:
        while True:
            chunk = request.stream.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
    return jsonify({"offset": os.path.getsize(part)})


@app.post("/api/upload/<uid>/finish")
def upload_finish(uid):
    part = upload_part(uid)
    meta_path = os.path.join(UPLOADS_DIR, uid + ".json")
    info = read_json(meta_path, None)
    if info is None or not os.path.exists(part):
        abort(404)
    if os.path.getsize(part) != info["size"]:
        return jsonify({"error": "The upload isn't complete yet.", "offset": os.path.getsize(part)}), 409
    name = info["name"]
    base, ext = os.path.splitext(name)
    n = 1
    while os.path.exists(os.path.join(VIDEOS_DIR, name)):
        name = f"{base}-{n}{ext}"
        n += 1
    shutil.move(part, os.path.join(VIDEOS_DIR, name))
    os.remove(meta_path)
    scan_videos()
    queue_video(video_id(name))
    return jsonify({"video": video_id(name), "name": name})


def queue_video(vid: str) -> None:
    meta = get_meta(vid)
    if meta and meta.get("status") not in ("queued", "processing"):
        set_meta(vid, status="queued", progress=0, error=None, stage=None)
        work_queue.put(vid)


@app.post("/api/videos/<vid>/analyze")
def analyze(vid):
    if not get_meta(vid):
        abort(404)
    queue_video(vid)
    return jsonify(public(get_meta(vid)))


@app.post("/api/analyze-all")
def analyze_all():
    for m in scan_videos():
        if m.get("status") in ("new", "error"):
            queue_video(m["id"])
    return jsonify({"ok": True})


@app.get("/api/videos/<vid>")
def video_detail(vid):
    meta = get_meta(vid)
    if not meta:
        abort(404)
    return jsonify({"video": public(meta), "clips": get_clips(vid),
                    "signal": read_json(os.path.join(vdir(vid), "signal.json"), None)})


@app.get("/api/videos/<vid>/proxy")
def proxy(vid):
    path = os.path.join(vdir(vid), "proxy.mp4")
    if not os.path.exists(path):
        abort(404)
    return send_file(path, mimetype="video/mp4", conditional=True)


@app.put("/api/videos/<vid>/clips")
def put_clips(vid):
    meta = get_meta(vid)
    if not meta:
        abort(404)
    duration = meta.get("duration") or 1e9
    clean = []
    for c in request.get_json(force=True).get("clips", []):
        start = max(0.0, float(c["start"]))
        end = min(duration, float(c["end"]))
        if end - start < 0.5:
            continue
        clean.append({
            "id": str(c.get("id") or uuid.uuid4().hex[:8])[:16],
            "start": round(start, 2), "end": round(end, 2),
            "peak": float(c.get("peak") or (start + end) / 2),
            "score": float(c.get("score") or 0),
            "kind": c.get("kind") if c.get("kind") in KINDS else "highlight",
            "keep": bool(c.get("keep", True)),
            "source": "manual" if c.get("source") == "manual" else "auto",
        })
    save_clips(vid, clean)
    return jsonify({"clips": get_clips(vid)})


@app.post("/api/videos/<vid>/detect")
def redetect(vid):
    """Re-pick moments at a new sensitivity, keeping clips the user added or edited."""
    meta = get_meta(vid)
    signal = read_json(os.path.join(vdir(vid), "signal.json"), None)
    if not meta or not signal:
        abort(404)
    sensitivity = float(request.get_json(force=True).get("sensitivity", 0.5))
    moments = analysis.find_moments(signal["score"], signal["window"], meta["duration"], sensitivity)
    kept = [c for c in get_clips(vid) if c.get("source") == "manual"]
    fresh = [new_clip(m["start"], m["end"], m["peak"], m["score"]) for m in moments
             if not any(m["start"] < k["end"] and m["end"] > k["start"] for k in kept)]
    save_clips(vid, kept + fresh)
    set_meta(vid, sensitivity=sensitivity)
    return jsonify({"clips": get_clips(vid)})


@app.delete("/api/videos/<vid>")
def remove_video(vid):
    """Forget the analysis (the original video file is left untouched)."""
    if not get_meta(vid):
        abort(404)
    shutil.rmtree(vdir(vid), ignore_errors=True)
    return jsonify({"ok": True})


@app.post("/api/export")
def start_export():
    body = request.get_json(force=True) or {}
    ids = body.get("videos") or [m["id"] for m in scan_videos()]
    items: list[ReelClip] = []
    for vid in ids:
        meta = get_meta(vid)
        if not meta or meta.get("status") != "ready":
            continue
        src = os.path.join(VIDEOS_DIR, meta["filename"])
        for c in get_clips(vid):
            if c.get("keep"):
                items.append(ReelClip(src, c["start"], c["end"], meta.get("has_audio", True),
                                      c.get("kind", "highlight"), c.get("peak")))
    if not items:
        return jsonify({"error": "No clips are ticked to keep yet."}), 400

    title = secure_filename(body.get("title") or "") or "highlights"
    out = os.path.join(EXPORTS_DIR, f"{title}-{time.strftime('%Y%m%d-%H%M%S')}.mp4")
    opts = {"height": 720 if body.get("quality") == "720" else 1080,
            "fades": bool(body.get("fades", True)), "replays": bool(body.get("replays", True))}
    job = uuid.uuid4().hex[:10]
    exports[job] = {"id": job, "status": "running", "progress": 0, "message": "Starting",
                    "clips": len(items)}
    threading.Thread(target=run_export, args=(job, items, out, opts), daemon=True).start()
    return jsonify(exports[job])


@app.get("/api/export/<job>")
def export_status(job):
    if job not in exports:
        abort(404)
    return jsonify(exports[job])


@app.get("/exports/<path:name>")
def download(name):
    return send_from_directory(EXPORTS_DIR, name, as_attachment=request.args.get("dl") == "1")


# ---------------------------------------------------------------- startup

def resume_unfinished() -> None:
    for m in scan_videos():
        if m.get("status") in ("queued", "processing"):
            set_meta(m["id"], status="new")
            queue_video(m["id"])


def check_ffmpeg() -> None:
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        sys.exit("ffmpeg is not installed. See README.md for how to install it, then try again.")


if __name__ == "__main__":
    check_ffmpeg()
    threading.Thread(target=worker, daemon=True).start()
    resume_unfinished()
    url = f"http://localhost:{PORT}"
    print(f"\n  Highlighter is running at {url}")
    ip = None if LOCAL_ONLY else lan_ip()
    if ip:
        print(f"  On your phone (same Wi-Fi): http://{ip}:{PORT}   PIN: {PIN}")
    print(f"  Videos are kept in: {VIDEOS_DIR}\n  Keep this window open while you use it. Press Ctrl+C to stop.\n")
    if os.environ.get("HIGHLIGHTER_NO_BROWSER") != "1":
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    app.run(host="127.0.0.1" if LOCAL_ONLY else "0.0.0.0", port=PORT, threaded=True)
