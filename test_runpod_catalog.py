import unittest
from unittest.mock import patch
import runpod_backend


class GPUCatalogTests(unittest.TestCase):
    def test_cloud_filter_and_availability(self):
        catalog={"gpus":[
            {"id":"NVIDIA GeForce RTX 4090","name":"RTX 4090","memory":24,
             "community":True,"secure":True,"availability":"NONE","price":{"community":0.3,"secure":0.7}},
            {"id":"NVIDIA RTX A6000","name":"A6000","memory":48,
             "community":True,"secure":True,"availability":"HIGH","price":{"community":0.5,"secure":0.9}}
        ]}
        with patch.dict(runpod_backend.os.environ, {"RUNPOD_API_KEY":"test"}), \
             patch.object(runpod_backend,"api_request",return_value=catalog) as req:
            found=runpod_backend.available_gpus("COMMUNITY")
        self.assertIn("cloud=COMMUNITY",req.call_args.args[1])
        self.assertEqual(found[0]["id"],"NVIDIA RTX A6000")
        self.assertTrue(found[0]["available"])
        self.assertFalse(found[1]["available"])
        with self.assertRaises(ValueError):
            runpod_backend.available_gpus("ALL")

    def test_capacity_error_has_advice(self):
        job={"id":"job123","provider":"runpod-secure",
             "payload":{"task":"image","prompt":"test","gpu_id":"NVIDIA RTX A6000"}}
        with patch.dict(runpod_backend.os.environ,
                        {"RUNPOD_API_KEY":"test","RUNPOD_IMAGE":"example/image:1"}), \
             patch.object(runpod_backend,"api_request",
                          side_effect=RuntimeError("RunPod/worker HTTP 400: There are no longer any instances available")):
            with self.assertRaisesRegex(RuntimeError,"refresh RunPod GPUs"):
                runpod_backend.run(job)


if __name__=="__main__":
    unittest.main()
