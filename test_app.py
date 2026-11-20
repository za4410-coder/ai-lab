import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent


class AppSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tempdir = tempfile.TemporaryDirectory()
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            cls.port = sock.getsockname()[1]
        env = {**os.environ, "AI_LAB_DATA_DIR": cls.tempdir.name, "AI_LAB_PORT": str(cls.port)}
        cls.process = subprocess.Popen([sys.executable, "app.py"], cwd=ROOT, env=env,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
        cls.url = "http://127.0.0.1:" + str(cls.port)
        for _ in range(80):
            try:
                cls.request("GET", "/api/health")
                return
            except (urllib.error.URLError, ConnectionError):
                time.sleep(.1)
        raise RuntimeError("Server did not start.")

    @classmethod
    def tearDownClass(cls):
        cls.process.terminate()
        try:
            cls.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            cls.process.kill()
        cls.tempdir.cleanup()

    @classmethod
    def request(cls, method, route, data=None):
        body = json.dumps(data).encode() if data is not None else None
        req = urllib.request.Request(cls.url + route, data=body, method=method,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as res:
            return json.load(res)

    def test_api_and_lifecycle(self):
        job = self.request("POST", "/api/jobs", {
            "title": "Smoke demo", "provider": "local-demo",
            "payload": {"prompt": "hello"}, "enqueue": False
        })
        jid = job["id"]
        self.assertEqual(job["status"], "draft")
        job = self.request("PATCH", "/api/jobs/" + jid, {"title": "Updated smoke"})
        self.assertEqual(job["title"], "Updated smoke")
        job = self.request("POST", "/api/jobs/" + jid + "/enqueue")
        self.assertEqual(job["status"], "queued")
        for _ in range(80):
            job = self.request("GET", "/api/jobs/" + jid)
            if job["status"] == "completed":
                break
            time.sleep(.1)
        self.assertEqual(job["status"], "completed", job)
        self.assertTrue(job["result"]["demo"])
        self.assertGreaterEqual(len(job["events"]), 3)
        listing = self.request("GET", "/api/jobs")["jobs"]
        self.assertTrue(any(j["id"] == jid for j in listing))
        self.assertTrue(self.request("DELETE", "/api/jobs/" + jid)["deleted"])

    def test_cancel_retry(self):
        job = self.request("POST", "/api/jobs", {
            "title": "Cancelled draft", "provider": "local-demo", "payload": {}
        })
        jid = job["id"]
        self.assertEqual(self.request("POST", "/api/jobs/" + jid + "/cancel")["status"], "cancelled")
        self.assertEqual(self.request("POST", "/api/jobs/" + jid + "/retry")["status"], "queued")


if __name__ == "__main__":
    unittest.main()
