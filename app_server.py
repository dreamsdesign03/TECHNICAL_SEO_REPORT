"""
Technical SEO Crawler Web API & Local Server
===============================================
Serves the Frontend Dashboard UI and manages asynchronous crawling runs.

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

# Global state for current audit job
current_job = {
    "status": "idle",       # "idle", "running", "completed", "error"
    "job_id": None,         # unique id per audit run (per-visitor isolation)
    "url": "",
    "sheet_url": "",
    "total_pages": 0,
    "audited_count": 0,
    "logs": [],
    "result": None,
    "error_message": "",
    "start_time": 0,
    "end_time": 0
}
job_lock = threading.Lock()


def run_crawler_subprocess(target_url):
    global current_job

    with job_lock:
        current_job["status"] = "running"
        current_job["url"] = target_url
        current_job["sheet_url"] = ""
        current_job["total_pages"] = 0
        current_job["audited_count"] = 0
        current_job["logs"] = [f"[System] Starting Technical SEO audit for {target_url}..."]
        current_job["result"] = None
        current_job["error_message"] = ""
        current_job["start_time"] = time.time()
        current_job["end_time"] = 0

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
                current_job["logs"].append(line_str)
                # Keep max 1000 logs in memory
                if len(current_job["logs"]) > 1000:
                    current_job["logs"] = current_job["logs"][-1000:]

                if "[SheetURL]" in line_str:
                    try:
                        extracted = line_str.split("[SheetURL]")[1].strip()
                        if extracted.startswith("http"):
                            current_job["sheet_url"] = extracted
                    except Exception:
                        pass

                # Parse progress markers from log lines
                if "FOUND" in line_str and "USER-FACING PAGES TO AUDIT" in line_str:
                    try:
                        parts = line_str.split("FOUND")
                        num_part = parts[1].split("USER-FACING")[0].strip()
                        current_job["total_pages"] = int(num_part)
                    except Exception:
                        pass

                if "Audited:" in line_str or "Auditing page" in line_str:
                    current_job["audited_count"] += 1
                elif line_str.startswith("  [") and "/" in line_str and "]" in line_str:
                    try:
                        bracket_content = line_str.split("[")[1].split("]")[0]
                        current_num = int(bracket_content.split("/")[0].strip())
                        total_num = int(bracket_content.split("/")[1].strip())
                        current_job["audited_count"] = current_num
                        current_job["total_pages"] = total_num
                    except Exception:
                        pass

        process.stdout.close()
        return_code = process.wait()

        with job_lock:
            current_job["end_time"] = time.time()
            if return_code == 0:
                current_job["status"] = "completed"
                current_job["logs"].append("[System] ✅ SEO audit completed successfully!")
            else:
                current_job["status"] = "error"
                current_job["error_message"] = f"Process exited with code {return_code}"
                current_job["logs"].append(f"[System] ❌ Audit failed with exit code {return_code}")

    except Exception as exc:
        with job_lock:
            current_job["status"] = "error"
            current_job["error_message"] = str(exc)
            current_job["end_time"] = time.time()
            current_job["logs"].append(f"[System] ❌ Error launching crawler: {exc}")


class SEOCrawlerRequestHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIRECTORY, **kwargs)

    def end_headers(self):
        # Allow CORS and disable browser caching for local dev
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
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()

            with job_lock:
                data = dict(current_job)

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

            with job_lock:
                if current_job["status"] == "running":
                    self.send_response(409)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"error": "An audit is already in progress"}).encode("utf-8"))
                    return

            # Assign a unique Job ID for this audit run so each visitor
            # only ever sees the audit they started themselves.
            job_id = str(uuid.uuid4())
            with job_lock:
                current_job["job_id"] = job_id

            # Start background thread
            t = threading.Thread(target=run_crawler_subprocess, args=(target_url,), daemon=True)
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
    print("=" * 75)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Shutting down server...")
        server.server_close()


if __name__ == "__main__":
    start_server()
