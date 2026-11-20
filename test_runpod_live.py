import os
import unittest
from unittest.mock import patch
import runpod_backend


class LiveRunPodTests(unittest.TestCase):
    def test_pod_telemetry_redacts_env_and_handles_null_runtime(self):
        job={"id":"abc123","provider":"runpod-secure","payload":{"gpu_id":"NVIDIA L4"},
             "started_at":"2026-11-20T22:00:00+00:00",
             "result":{"runpod_id":"pod123","terminated":False}}
        api_response={"id":"pod123","status":"RUNNING","runtime":None,"gpu":{"name":"NVIDIA L4"},
                      "adjustedCostPerHr":0.79,"dataCenterId":"EU-RO-1",
                      "env":{"AI_LAB_JOB_TOKEN":"secret"}}
        with patch.dict(os.environ,{"RUNPOD_API_KEY":"fake-secret"}), \
             patch.object(runpod_backend,"api_request",return_value=api_response):
            data=runpod_backend.live_details(job,with_logs=False)
        self.assertEqual(data["gpu"],"NVIDIA L4")
        self.assertEqual(data["status"],"RUNNING")
        self.assertEqual(data["hourly_rate"],0.79)
        self.assertNotIn("env",data)
        self.assertNotIn("secret",str(data))
        self.assertEqual(data["gpu_metrics"],[])

    def test_deleted_pod_does_not_make_api_call(self):
        job={"id":"abc123","provider":"runpod-community",
             "result":{"runpod_id":"pod123","terminated":True}}
        with patch.object(runpod_backend,"api_request") as req:
            data=runpod_backend.live_details(job)
        self.assertTrue(data["terminated"])
        req.assert_not_called()

    def test_provisioning_has_safe_message(self):
        job={"id":"abc123","provider":"runpod-secure","result":None}
        self.assertFalse(runpod_backend.live_details(job)["active"])


if __name__=="__main__":
    unittest.main()
