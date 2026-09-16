"""
Technical SEO Crawler Web API & Local Server
===============================================
Serves the Frontend Dashboard UI and manages asynchronous crawling runs.
Supports MULTIPLE CONCURRENT audits — every visitor gets their own
independent job keyed by a unique job_id, so many users can crawl
different websites at the same time.

Usage:
    python app_server.py
"""

import sys
import os
import io
import json
import time
import threading
import subprocess
import uuid
from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

# Ensure stdout uses UTF-8
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

PORT = int(os.environ.get("PORT", 8000))
DIRECTORY = os.path.dirname(os.path.abspath(__file__))

# In-memory store of audit jobs, keyed by a unique job_id.
# Each visitor starts their own job, so audits never collide.
jobs = {}
job_lock = threading.Lock()

JOB_MAX_AGE = 60 * 60   # drop finished jobs after 1 hour
JOB_MAX_LIVE = 200      # never hold more than this many jobs in memory
MAX_JOB_LOGS = 1000     # keep only the most recent log lines per job


def now():
    return time.time()


def new_job(target_url):
    """Create and register a fresh job. Returns the job dict and its id."""
    job_id = str(uuid.uuid4())
    job = {
        "status": "running",       # "running", "completed", "error"
        "job_id": job_id,
        "url": target_url,
        "sheet_url": "",
        "total_pages": 0,
        "audited_count": 0,
        "logs": [f"[System] Starting Technical SEO audit for {target_url}..."],
        "result": None,
        "error_message": "",
        "start_time": now(),
        "end_time": 0,
    }
    with job_lock:
        jobs[job_id] = job
        cleanup_jobs()
    return job_id, job


def cleanup_jobs():
    """Remove old finished jobs so memory stays bounded."""
    cutoff = now() - JOB_MAX_AGE
    finished = [
        jid for jid, j in jobs.items()
        if j["status"] in ("completed", "error") and j["end_time"] < cutoff
    ]
    for jid in finished:
        jobs.pop(jid, None)
    # Hard cap: if we somehow exceed the limit, drop oldest jobs.
    if len(jobs) > JOB_MAX_LIVE:
        for jid in sorted(jobs, key=lambda j: jobs[j]["start_time"])[: len(jobs) - JOB_MAX_LIVE]:
            jobs.pop(jid, None)


def run_crawler_subprocess(job_id, target_url):
    if job_id not in jobs:
        return

    cmd = [sys.executable, "-u", os.path.join(DIRECTORY, "seo_crawler.py"), target_url]

    try:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            cwd=DIRECTORY
        )

        for line in iter(process.stdout.readline, ''):
            line_str = line.rstrip()
            if not line_str:
                continue

            with job_lock:
                current = jobs.get(job_id)
                if current is None:
                    break

                current["logs"].append(line_str)
                if len(current["logs"]) > MAX_JOB_LOGS:
                    current["logs"] = current["logs"][-MAX_JOB_LOGS:]

                if "[SheetURL]" in line_str:
                    try:
                        extracted = line_str.split("[SheetURL]")[1].strip()
                        if extracted.startswith("http"):
                            current["sheet_url"] = extracted
                    except Exception:
                        pass

                # Parse progress markers from log lines
                if "FOUND" in line_str and "USER-FACING PAGES TO AUDIT" in line_str:
                    try:
                        parts = line_str.split("FOUND")
                        num_part = parts[1].split("USER-FACING")[0].strip()
                        current["total_pages"] = int(num_part)
                    except Exception:
                        pass

                if "Audited:" in line_str or "Auditing page" in line_str:
                    current["audited_count"] += 1
                elif line_str.startswith("  [") and "/" in line_str and "]" in line_str:
                    try:
                        bracket_content = line_str.split("[")[1].split("]")[0]
                        current_num = int(bracket_content.split("/")[0].strip())
                        total_num = int(bracket_content.split("/")[1].strip())
                        current["audited_count"] = current_num
                        current["total_pages"] = total_num
                    except Exception:
                        pass

        process.stdout.close()
        return_code = process.wait()

        with job_lock:
            current = jobs.get(job_id)
            if current is not None:
                current["end_time"] = now()
                if return_code == 0:
                    current["status"] = "completed"
                    current["logs"].append("[System] ✅ SEO audit completed successfully!")
                else:
                    current["status"] = "error"
                    current["error_message"] = f"Process exited with code {return_code}"
                    current["logs"].append(f"[System] ❌ Audit failed with exit code {return_code}")

    except Exception as exc:
        with job_lock:
            current = jobs.get(job_id)
            if current is not None:
                current["status"] = "error"
                current["error_message"] = str(exc)
                current["end_time"] = now()
                current["logs"].append(f"[System] ❌ Error launching crawler: {exc}")


class SEOCrawlerRequestHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIRECTORY, **kwargs)

    def end_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
        self.send_header('Pragma', 'no-cache')
        self.send_header('Expires', '0')
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/status":
            query = parse_qs(parsed.query)
            job_id = (query.get("job_id") or [""])[0]

            with job_lock:
                if job_id and job_id in jobs:
                    data = dict(jobs[job_id])
                    data["logs"] = list(jobs[job_id]["logs"])
                else:
                    data = {
                        "status": "idle",
                        "job_id": None,
                        "url": "",
                        "sheet_url": "",
                        "total_pages": 0,
                        "audited_count": 0,
                        "logs": [],
                        "result": None,
                        "error_message": "",
                        "start_time": 0,
                        "end_time": 0,
                    }

            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))
            return

        # Serve static files for everything else
        return super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/audit":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode("utf-8")

            target_url = ""
            try:
                data = json.loads(body)
                target_url = data.get("url", "").strip()
            except Exception:
                params = parse_qs(body)
                target_url = params.get("url", [""])[0].strip()

            if not target_url:
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": "Target URL is required"}).encode("utf-8"))
                return

            if not target_url.startswith(("http://", "https://")):
                target_url = "https://" + target_url

            # Every audit is fully independent — no global status lock,
            # so multiple users can crawl different sites simultaneously.
            job_id, _ = new_job(target_url)
            t = threading.Thread(
                target=run_crawler_subprocess,
                args=(job_id, target_url),
                daemon=True
            )
            t.start()

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({
                "message": "Audit started successfully",
                "target_url": target_url,
                "job_id": job_id
            }).encode("utf-8"))
            return

        self.send_response(404)
        self.end_headers()


def start_server():
    server = HTTPServer(("0.0.0.0", PORT), SEOCrawlerRequestHandler)
    print("=" * 75)
    print(f"  🚀 Technical SEO Crawler Web Server Running!")
    print(f"  🌐 Local Access:    http://localhost:{PORT}")
    print(f"  🌐 Network Access:  http://127.0.0.1:{PORT}")
    print(f"  ⚡ Concurrent audits: ON  (independent job per visitor)")
    print("=" * 75)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Shutting down server...")
        server.server_close()


if __name__ == "__main__":
    start_server()