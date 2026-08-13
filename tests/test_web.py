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

    def post_multipart(self, path, kind, files):
        return self.post_multipart_with_session(path, kind, files, None)

    def post_multipart_with_session(self, path, kind, files, session_id):
        boundary = "----qiime2auto-test-boundary"
        chunks = []
        chunks.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"kind\"\r\n\r\n{kind}\r\n".encode())
        if session_id:
            chunks.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"session_id\"\r\n\r\n{session_id}\r\n".encode())
        for filename, content in files:
            chunks.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"files\"; filename=\"{filename}\"\r\nContent-Type: application/octet-stream\r\n\r\n".encode())
            chunks.append(content)
            chunks.append(b"\r\n")
        chunks.append(f"--{boundary}--\r\n".encode())
        request = Request(self.base + path, data=b"".join(chunks), headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}, method="POST")
        with urlopen(request) as response:
            return json.loads(response.read().decode("utf-8"))

    def test_health_and_static_page(self):
        self.assertTrue(self.get_json("/api/health")["ok"])
        with urlopen(self.base + "/") as response:
            page = response.read().decode("utf-8")
            self.assertIn("QIIME2 Auto", page)
            self.assertIn("一键完成分析", page)
            self.assertNotIn("commandOutput", page)
            self.assertNotIn("copyButton", page)
        with urlopen(self.base + "/README.html") as response:
            self.assertEqual(response.status, 200)
            self.assertIn("使用说明", response.read().decode("utf-8"))
        with urlopen(self.base + "/guide.html") as response:
            self.assertEqual(response.status, 200)

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

    def test_environment_and_install_preview_endpoints(self):
        options = self.get_json("/api/install-options")
        self.assertIn("2024.10", options["versions"])
        result = self.post_json("/api/install-preview", {"version": "2024.10", "distribution": "amplicon"})
        self.assertIn("conda", result["command"])

    def test_multi_file_fastq_picker_upload(self):
        result = self.post_multipart("/api/upload", "fastq", [("S1_R1.fastq.gz", b"@r1\nACGT\n+\nIIII\n"), ("S1_R2.fastq.gz", b"@r2\nTGCA\n+\nIIII\n")])
        self.assertTrue(result["ok"])
        self.assertEqual(result["scan"]["data_type"], "Casava_paired")

    def test_manifest_and_fastq_picker_uploads_share_a_bundle(self):
        manifest = (
            b"sample-id\tforward-absolute-filepath\treverse-absolute-filepath\n"
            b"S1\t/raw/S1_R1.fastq.gz\t/raw/S1_R2.fastq.gz\n"
            b"S2\t/raw/S2_R1.fastq.gz\t/raw/S2_R2.fastq.gz\n"
        )
        first = self.post_multipart("/api/upload", "manifest", [("sample-sheet.weird", manifest)])
        self.assertEqual(first["scan"]["data_type"], "manifest_paired")
        self.assertEqual(first["scan"]["fastq_count"], 4)
        self.assertEqual(first["scan"]["missing_fastq_count"], 4)
        second = self.post_multipart_with_session(
            "/api/upload",
            "fastq",
            [(name, b"@read\nACGT\n+\nIIII\n") for name in ("S1_R1.fastq.gz", "S1_R2.fastq.gz", "S2_R1.fastq.gz", "S2_R2.fastq.gz")],
            first["session_id"],
        )
        self.assertEqual(second["scan"]["data_type"], "manifest_paired")
        self.assertEqual(second["scan"]["fastq_count"], 4)
        self.assertEqual(second["scan"]["existing_fastq_count"], 4)
        self.assertEqual(second["scan"]["missing_fastq_count"], 0)
        self.assertIn(".qiime2auto_manifest.tsv", second["path"])

    def test_metadata_picker_does_not_switch_back_to_manifest(self):
        result = self.post_multipart("/api/upload", "metadata", [("metadata.txt", b"sample-id\tgroup\nS1\tcontrol\n")])
        self.assertEqual(result["kind"], "metadata")
        self.assertTrue(result["path"].endswith("metadata.txt"))


if __name__ == "__main__":
    unittest.main()
