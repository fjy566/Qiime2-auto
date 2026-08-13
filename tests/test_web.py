import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen

from qiime2auto.web import DashboardHandler


class WebSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), DashboardHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def get_json(self, path):
        with urlopen(self.base + path) as response:
            return json.loads(response.read().decode("utf-8"))

    def post_json(self, path, payload):
        request = Request(self.base + path, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"}, method="POST")
        with urlopen(request) as response:
            return json.loads(response.read().decode("utf-8"))

    def test_health_and_static_page(self):
        self.assertTrue(self.get_json("/api/health")["ok"])
        with urlopen(self.base + "/") as response:
            self.assertIn("QIIME2 Auto", response.read().decode("utf-8"))

    def test_scan_and_metadata_validation_endpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "S1_R1.fastq.gz").touch()
            scan = self.get_json(f"/api/scan?path={root}")
            self.assertEqual(scan["scan"]["data_type"], "Casava_single")
            metadata = root / "metadata.tsv"
            metadata.write_text("sample-id\tgroup\nS1\tcontrol\n", encoding="utf-8")
            result = self.post_json("/api/validate-metadata", {"path": str(metadata)})
            self.assertTrue(result["ok"])


if __name__ == "__main__":
    unittest.main()
