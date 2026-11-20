import os
import unittest
from unittest.mock import patch
import runpod_backend


class RunPodTests(unittest.TestCase):
    def test_validation_does_not_create_pod(self):
        with self.assertRaises(ValueError):
            runpod_backend.validate_image({"task": "image", "prompt": "", "steps": 25})
        with self.assertRaises(ValueError):
            runpod_backend.validate_image({"task": "image", "prompt": "ok", "width": 1023})
        runpod_backend.validate_image({"task": "image", "prompt": "hello", "width": 1024})

    def test_tier_choices_and_cleanup(self):
        calls = []
        png = runpod_backend.PNG + b"data"
        def fake_api(method, url, data=None, token=None, **kwargs):
            calls.append((method, url, data))
            if method == "POST":
                return {"id": "pod123", "costPerHr": 0.4}
            if method == "DELETE":
                return {}
            if url.endswith("/status"):
                return {"job_id": "job123", "state": "completed", "duration_seconds": 2}
            if url.endswith("/image"):
                return png
            return {"desiredStatus": "RUNNING", "runtime": None}
        import tempfile
        for provider, tier in [("runpod-community", "COMMUNITY"), ("runpod-secure", "SECURE")]:
            calls.clear()
            with tempfile.TemporaryDirectory() as folder, \
                 patch.dict(os.environ, {"RUNPOD_API_KEY": "test", "RUNPOD_IMAGE": "example/image:1",
                                         "AI_LAB_DATA_DIR": folder}), \
                 patch.object(runpod_backend, "api_request", side_effect=fake_api), \
                 patch.object(runpod_backend, "record_pod"), \
                 patch.object(runpod_backend, "mark_terminated"):
                result = runpod_backend.run({"id": "job123", "provider": provider,
                    "payload": {"task": "image", "prompt": "a landscape"}})
                self.assertTrue((__import__("pathlib").Path(folder)/"artifacts"/"job123.png").exists())
            self.assertEqual(calls[0][2]["cloud"], tier)
            self.assertEqual(calls[-1][0], "DELETE")
            self.assertEqual(result["cloud"], tier)
            self.assertTrue(result["terminated"])

    def test_delete_failure_is_not_marked_terminated(self):
        calls = []
        def fake_api(method, url, data=None, token=None, **kwargs):
            calls.append(method)
            if method == "POST":
                return {"id": "pod123"}
            if method == "DELETE":
                raise RuntimeError("delete failed")
            if url.endswith("/status"):
                return {"job_id": "job123", "state": "failed", "error": "model issue"}
            return {"desiredStatus": "RUNNING"}
        with patch.dict(os.environ, {"RUNPOD_API_KEY": "test", "RUNPOD_IMAGE": "example/image:1"}), \
             patch.object(runpod_backend, "api_request", side_effect=fake_api), \
             patch.object(runpod_backend, "record_pod"), \
             patch.object(runpod_backend, "mark_terminated") as mark:
            with self.assertRaisesRegex(RuntimeError, "termination failed"):
                runpod_backend.run({"id":"job123","provider":"runpod-secure",
                                   "payload":{"task":"image","prompt":"test"}})
            mark.assert_not_called()
        self.assertIn("DELETE", calls)


if __name__ == "__main__":
    unittest.main()
