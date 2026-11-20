import unittest
from unittest.mock import patch
import app


class CloudResultTests(unittest.TestCase):
    def test_exact_job_matching(self):
        entries = [
            {"textPayload": 'AI_LAB_RESULT {"job_id":"wrong","output":{"echo":"Wrong job"}}'},
            {"textPayload": 'AI_LAB_RESULT {"job_id":"abc123","task":"echo","output":{"echo":"Hello GCP"}}'}
        ]
        job = {"id": "abc123", "started_at": "2026-11-20T20:00:00+00:00"}
        with patch.dict(app.os.environ, {"GCP_PROJECT":"test-project","GCP_JOB":"ai-lab-worker"}), \
             patch.object(app, "gcp_token", return_value="fake-token"), \
             patch.object(app, "request_json", return_value={"entries": entries}) as request:
            output = app.fetch_gcp_worker_result(job)
            self.assertEqual(output["output"]["echo"], "Hello GCP")
            self.assertIn('resource.labels.job_name="ai-lab-worker"', request.call_args.kwargs["body"]["filter"])

    def test_no_result_is_not_fabricated(self):
        job = {"id": "abc123", "started_at": "2026-11-20T20:00:00+00:00"}
        with patch.dict(app.os.environ, {"GCP_PROJECT":"test-project","GCP_JOB":"ai-lab-worker"}), \
             patch.object(app, "gcp_token", return_value="fake-token"), \
             patch.object(app, "request_json", return_value={"entries": []}):
            self.assertIsNone(app.fetch_gcp_worker_result(job))


if __name__ == "__main__":
    unittest.main()
