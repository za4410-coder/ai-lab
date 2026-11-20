import unittest
from unittest.mock import patch
import app


class OpenImageRoutingTests(unittest.TestCase):
    def test_worker_routing_uses_independent_gpu_job(self):
        calls = []
        def fake_request(url, method="GET", body=None, token=None, **kwargs):
            calls.append((url, body))
            if url.endswith(":run"):
                return {"name": "projects/demo/locations/us-central1/operations/123"}
            return {"done": True, "response": {}}
        job = {"id": "abc123", "provider": "gcp-cloud-run",
               "payload": {"task": "image", "prompt": "a landscape"}, "started_at": "2026-11-20T20:00:00+00:00"}
        with patch.dict(app.os.environ, {"GCP_PROJECT": "demo", "GCP_REGION": "us-central1",
                                         "GCP_JOB": "cpu-job", "GCP_IMAGE_JOB": "image-job",
                                         "AI_LAB_RESULTS_BUCKET": ""}), \
             patch.object(app, "request_json", side_effect=fake_request), \
             patch.object(app, "gcp_token", return_value="token"), \
             patch.object(app, "connect") as fake_connect, \
             patch.object(app.time, "sleep", return_value=None), \
             patch.object(app, "fetch_gcp_worker_result", return_value={"job_id": "abc123", "task":"image", "output": {}}):
            fake_connect.return_value.__enter__.return_value = fake_connect.return_value
            result = app.run_gcp(job)
        self.assertIn("/jobs/image-job:run", calls[0][0])
        self.assertEqual(result["artifact"]["job_id"], "abc123")

    def test_image_path_is_exact(self):
        job = {"id": "abc123", "result": {"artifact": {"output": {
            "image_uri": "gs://private-bucket/ai-lab/jobs/abc123/image.png"
        }}}}
        with patch.dict(app.os.environ, {"AI_LAB_RESULTS_BUCKET": "private-bucket"}):
            self.assertEqual(app.image_object_path(job), job["result"]["artifact"]["output"]["image_uri"])
            job["result"]["artifact"]["output"]["image_uri"] = "gs://private-bucket/ai-lab/jobs/other/image.png"
            with self.assertRaises(ValueError):
                app.image_object_path(job)


if __name__ == "__main__":
    unittest.main()
