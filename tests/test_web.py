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
            self.assertIn("metadataEditorCard", page)
            self.assertIn("manifestEditorCard", page)
            self.assertIn("manifestCreateButton", page)
            self.assertIn("groupSamplePicker", page)
            self.assertIn("metadataColumnTemplates", page)
            self.assertIn("manifestSavePath", page)
            self.assertIn("inputPathPickerButton", page)
            self.assertIn("metadataSourceSamples", page)
            self.assertIn("metadataSavePathPickerButton", page)
            self.assertIn("classifierCatalog", page)
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

    def test_preflight_explains_optional_metadata_steps(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / "manifest.tsv"
            classifier = root / "classifier.qza"
            manifest.write_text("sample-id\tabsolute-filepath\nS1\tS1.fastq.gz\n", encoding="utf-8")
            classifier.write_bytes(b"placeholder")
            result = self.post_json("/api/preflight", {
                "input_path": str(manifest),
                "output_dir": str(root / "results"),
                "data_type": "manifest_single",
                "qiime_env": "qiime2-test",
                "classifier": str(classifier),
                "platform": "posix",
            })
            self.assertTrue(result["preflight"]["can_run"])
            self.assertTrue(result["preflight"]["effective"]["skip_diversity"])
            self.assertTrue(result["preflight"]["effective"]["skip_ancom"])

    def test_preview_command_omits_optional_metadata(self):
        result = self.post_json("/api/preview", {"input_path": "/tmp/manifest.tsv", "output_dir": "/tmp/results"})
        self.assertNotIn("--metadata", result["command"])

    def test_environment_and_install_preview_endpoints(self):
        options = self.get_json("/api/install-options")
        self.assertIn("2024.10", options["versions"])
        self.assertEqual(options["latest"], "2026.7")
        self.assertEqual(options["versions"][0], "2026.7")
        result = self.post_json("/api/install-preview", {"version": "2024.10", "distribution": "amplicon"})
        self.assertIn("conda", result["command"])
        latest = self.post_json("/api/install-preview", {"version": "2026.7", "distribution": "amplicon"})
        self.assertIn("rachis-qiime2-linux-64-conda.yml", latest["command_text"])

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

    def test_table_editors_and_directory_picker_endpoints(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            metadata = root / "metadata.tsv"
            metadata.write_text("sample-id\tgroup\tage\nS1\tcontrol\t3\nS2\t\t\n", encoding="utf-8")
            preview = self.post_json("/api/metadata-preview", {"path": str(metadata)})
            self.assertEqual(preview["preview"]["types"], ["", "", ""])
            saved = self.post_json("/api/metadata-save", {"path": str(metadata), "headers": ["sample-id", "group", "age"], "types": ["", "categorical", "numeric"], "rows": [{"sample-id": "S1", "group": "control", "age": "3"}, {"sample-id": "S2", "group": "", "age": ""}]})
            self.assertTrue(saved["ok"])
            self.assertIn("#q2:types", metadata.read_text(encoding="utf-8"))
            manifest = root / "manifest.weird"
            (root / "S1_R1.fastq.gz").touch()
            (root / "S1_R2.fastq.gz").touch()
            manifest.write_text("sample-id\tforward-absolute-filepath\treverse-absolute-filepath\nS1\tS1_R1.fastq.gz\tS1_R2.fastq.gz\n", encoding="utf-8")
            manifest_preview = self.post_json("/api/manifest-preview", {"path": str(manifest), "bundle_dir": str(root)})
            self.assertEqual(manifest_preview["preview"]["fastq_count"], 2)
            manifest_saved = self.post_json("/api/manifest-save", {"path": str(manifest), "data_type": "manifest_paired", "rows": manifest_preview["preview"]["rows"]})
            self.assertEqual(manifest_saved["scan"]["existing_fastq_count"], 2)
            directories = self.get_json(f"/api/directories?path={root}")
            self.assertEqual(directories["current"], str(root.resolve()))
            self.assertEqual(directories["files"], [])
            filesystem = self.get_json(f"/api/directories?path={root}&include_files=1")
            self.assertTrue(any(item["name"] == "manifest.weird" for item in filesystem["files"]))
            samples = self.get_json(f"/api/samples?path={manifest}")
            self.assertEqual(samples["sample_ids"], ["S1"])
            self.assertTrue(samples["suggested_metadata_path"].endswith("metadata.tsv"))

    def test_classifier_catalog_is_allowlisted_and_project_scoped(self):
        catalog = self.get_json("/api/classifiers")
        self.assertTrue(catalog["catalog"])
        self.assertTrue(any(item["id"] == "silva-138-99-full-length" for item in catalog["catalog"]))
        self.assertTrue(catalog["root"].endswith("classifiers"))


if __name__ == "__main__":
    unittest.main()
