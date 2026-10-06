import json
import os
import re
import shutil
import threading
import time
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

PORT = 8000
BASE = os.path.dirname(os.path.abspath(__file__))
TASKS_FILE = os.path.join(BASE, "tasks.json")

_lock = threading.Lock()
_tasks = []
_logs = []


def safe_print(text):
    """print() that never crashes on Windows cp1252 consoles."""
    try:
        print(text, flush=True)
    except UnicodeEncodeError:
        print(text.encode("ascii", "replace").decode(), flush=True)


def log(msg):
    entry = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
    safe_print(entry)
    with _lock:
        _logs.append(entry)
        _logs[:] = _logs[-300:]


def safe_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def load_tasks():
    global _tasks
    if os.path.exists(TASKS_FILE):
        try:
            with open(TASKS_FILE, "r", encoding="utf-8") as f:
                _tasks = json.load(f)
        except Exception as e:
            log(f"Load error: {e}")
            _tasks = []
    else:
        _tasks = [
            {"id": 1, "name": "Organize Downloads", "type": "organize_files",
             "params": {"folder": os.path.join(os.path.expanduser("~"), "Downloads")},
             "schedule_interval_sec": 0, "last_run": None, "next_run": None, "enabled": True},
            {"id": 2, "name": "Daily cleanup demo", "type": "clean_temp",
             "params": {"folder": os.path.join(BASE, "demo_folder"), "older_than_days": 7},
             "schedule_interval_sec": 86400, "last_run": None, "next_run": None, "enabled": False},
        ]
        save_tasks()


def save_tasks():
    with open(TASKS_FILE, "w", encoding="utf-8") as f:
        json.dump(_tasks, f, indent=2)


def next_id():
    return max([t["id"] for t in _tasks], default=0) + 1


# ---------- task implementations ----------
def task_organize_files(params):
    folder = (params.get("folder") or "").strip()
    if not folder or not os.path.isdir(folder):
        return False, f"Folder not found: {folder}"
    moved = 0
    for name in os.listdir(folder):
        src = os.path.join(folder, name)
        if os.path.isfile(src):
            ext = os.path.splitext(name)[1].lower().lstrip(".") or "misc"
            dest_dir = os.path.join(folder, ext)
            os.makedirs(dest_dir, exist_ok=True)
            try:
                shutil.move(src, os.path.join(dest_dir, name))
                moved += 1
            except Exception as e:
                log(f"skip {name}: {e}")
    return True, f"Organized {moved} file(s) in {folder}"


def task_bulk_rename(params):
    folder = (params.get("folder") or "").strip()
    pattern = params.get("pattern") or "file_{n}"
    if not os.path.isdir(folder):
        return False, f"Folder not found: {folder}"
    files = sorted([f for f in os.listdir(folder) if os.path.isfile(os.path.join(folder, f))])
    n = 0
    for i, name in enumerate(files, 1):
        ext = os.path.splitext(name)[1]
        new = pattern.replace("{n}", str(i).zfill(3)) + ext
        try:
            os.rename(os.path.join(folder, name), os.path.join(folder, new))
            n += 1
        except Exception as e:
            log(f"rename skip {name}: {e}")
    return True, f"Renamed {n} file(s) with pattern '{pattern}'"


def task_clean_temp(params):
    folder = (params.get("folder") or "").strip()
    days = safe_int(params.get("older_than_days", 7), 7)
    cutoff = time.time() - days * 86400
    if not os.path.isdir(folder):
        return False, f"Folder not found: {folder}"
    removed = 0
    for root, _, files in os.walk(folder):
        for name in files:
            p = os.path.join(root, name)
            try:
                if os.path.getmtime(p) < cutoff:
                    os.remove(p)
                    removed += 1
            except Exception as e:
                log(f"clean skip {p}: {e}")
    return True, f"Deleted {removed} file(s) older than {days} days in {folder}"


def task_write_note(params):
    text = params.get("text", "hello")
    if text is None:
        text = "hello"
    filename = params.get("filename") or f"note_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    folder = (params.get("folder") or "").strip() or BASE
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, filename)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return True, f"Wrote note to {path}"


def task_fetch_url(params):
    url = params.get("url", "")
    if not url.startswith(("http://", "https://")):
        return False, "URL must start with http:// or https://"
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            html = r.read(200000).decode("utf-8", "ignore")
        m = re.search(r"<title>(.*?)</title>", html, re.I | re.S)
        title = (m.group(1).strip()[:200] if m else "no title found")
        return True, f"Fetched {url} (status {r.status}), title: {title}"
    except Exception as e:
        return False, f"Fetch failed: {e}"


RUNNERS = {
    "organize_files": task_organize_files,
    "bulk_rename": task_bulk_rename,
    "clean_temp": task_clean_temp,
    "write_note": task_write_note,
    "fetch_url": task_fetch_url,
}


def run_task(task):
    fn = RUNNERS.get(task["type"])
    if not fn:
        return False, f"Unknown task type: {task['type']}"
    log(f"▶ Running '{task['name']}' ({task['type']})...")
    try:
        ok, msg = fn(task.get("params", {}))
    except Exception as e:
        ok, msg = False, f"Error: {e}"
    task["last_run"] = datetime.now().isoformat(timespec="seconds")
    task["last_result"] = msg
    task["last_ok"] = ok
    log(("✅ " if ok else "❌ ") + f"{task['name']}: {msg}")
    save_tasks()
    return ok, msg


def scheduler_loop():
    while True:
        time.sleep(10)
        now = time.time()
        with _lock:
            snapshot = list(_tasks)
        changed = False
        for t in snapshot:
            if not t.get("enabled"):
                continue
            interval = safe_int(t.get("schedule_interval_sec") or 0)
            if interval <= 0:
                continue
            nxt = t.get("_next_ts") or 0
            if now >= nxt:
                run_task(t)
                t["_next_ts"] = now + interval
                changed = True
        if changed:
            save_tasks()


# ---------- HTTP ----------
class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        try:
            safe_print(f"[{datetime.now().strftime('%H:%M:%S')}] {fmt % args}")
        except Exception:
            pass

    def serve(self, path, ctype):
        try:
            with open(os.path.join(BASE, path), "rb") as f:
                body = f.read()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except FileNotFoundError:
            self.send_error(404, path)

    def body_json(self):
        n = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(n).decode("utf-8") if n else "{}"
        try:
            return json.loads(raw or "{}")
        except json.JSONDecodeError:
            return {}

    def json(self, obj, status=200):
        b = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        p = urlparse(self.path)
        if p.path in ("/", "/index.html"):
            return self.serve("index.html", "text/html; charset=utf-8")
        if p.path == "/style.css":
            return self.serve("style.css", "text/css; charset=utf-8")
        if p.path == "/script.js":
            return self.serve("script.js", "text/javascript; charset=utf-8")
        if p.path == "/api/health":
            return self.json({"status": "ok", "agent": "task-automation", "port": PORT})
        if p.path == "/api/tasks":
            with _lock:
                return self.json({"tasks": _tasks, "types": list(RUNNERS)})
        if p.path == "/api/logs":
            with _lock:
                return self.json({"logs": _logs[-150:]})
        return self.send_error(404)

    def do_POST(self):
        p = urlparse(self.path)
        q = parse_qs(p.query)
        if p.path == "/api/tasks":
            data = self.body_json()
            interval = safe_int(data.get("schedule_interval_sec", 0))
            t = {"id": next_id(), "name": data.get("name", "Untitled") or "Untitled",
                 "type": data.get("type", "write_note"),
                 "params": data.get("params", {}) or {},
                 "schedule_interval_sec": interval,
                 "enabled": bool(data.get("enabled", True)),
                 "last_run": None, "_next_ts": time.time() + (interval or 0)}
            with _lock:
                _tasks.append(t)
                save_tasks()
            log(f"➕ Created task '{t['name']}'")
            return self.json({"task": t})
        if p.path == "/api/run":
            raw_id = q.get("id", [None])[0] or self.body_json().get("id", 0)
            tid = safe_int(raw_id, -1)
            with _lock:
                t = next((x for x in _tasks if x["id"] == tid), None)
            if not t:
                return self.json({"error": "not found"}, 404)
            ok, msg = run_task(t)
            return self.json({"ok": ok, "message": msg, "task": t})
        if p.path == "/api/toggle":
            tid = safe_int(q.get("id", [None])[0], -1)
            with _lock:
                t = next((x for x in _tasks if x["id"] == tid), None)
                if t:
                    t["enabled"] = not t.get("enabled", True)
                    save_tasks()
            return self.json({"task": t})
        return self.json({"error": "unknown"}, 404)

    def do_DELETE(self):
        p = urlparse(self.path)
        q = parse_qs(p.query)
        if p.path == "/api/tasks":
            tid = safe_int(q.get("id", [None])[0], -1)
            with _lock:
                _tasks[:] = [x for x in _tasks if x["id"] != tid]
                save_tasks()
            log(f"🗑 Deleted task {tid}")
            return self.json({"deleted": tid})
        return self.json({"error": "unknown"}, 404)


if __name__ == "__main__":
    load_tasks()
    threading.Thread(target=scheduler_loop, daemon=True).start()
    print("=" * 50)
    print(" Task Automation Agent running!")
    print(f" Open: http://localhost:{PORT}")
    print(" Press Ctrl+C to stop.")
    print("=" * 50)
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
