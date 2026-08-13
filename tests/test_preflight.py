import tempfile
import unittest
from pathlib import Path

from qiime2auto.preflight import build_preflight, inspect_metadata_capabilities


class PreflightTests(unittest.TestCase):
    def _base(self, root: Path) -> dict:
        input_path = root / "manifest.tsv"
        classifier = root / "classifier.qza"
        input_path.write_text("sample-id\tabsolute-filepath\nS1\tS1.fastq.gz\n", encoding="utf-8")
        classifier.write_bytes(b"placeholder")
        return {
            "input_path": str(input_path),
            "output_dir": str(root / "results"),
            "data_type": "manifest_single",
            "qiime_env": "qiime2-test",
            "classifier": str(classifier),
            "platform": "posix",
        }

    def test_missing_metadata_is_allowed_but_dependent_steps_are_skipped(self):
        with tempfile.TemporaryDirectory() as directory:
            plan = build_preflight(self._base(Path(directory)))
            self.assertTrue(plan["can_run"])
            self.assertTrue(plan["effective"]["skip_diversity"])
            self.assertTrue(plan["effective"]["skip_ancom"])
            self.assertTrue(any("metadata" in item["message"] for item in plan["warnings"]))

    def test_grouped_metadata_enables_diversity_and_ancom(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            metadata = root / "metadata.tsv"
            metadata.write_text("sample-id\tgroup\nS1\tcontrol\nS2\ttreatment\n", encoding="utf-8")
            payload = self._base(root)
            payload["metadata"] = str(metadata)
            plan = build_preflight(payload)
            self.assertTrue(plan["can_run"])
            self.assertFalse(plan["effective"]["skip_diversity"])
            self.assertFalse(plan["effective"]["skip_ancom"])
            self.assertEqual(plan["metadata"]["group_column"], "group")

    def test_one_group_metadata_skips_only_ancom(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            metadata = root / "metadata.tsv"
            metadata.write_text("sample-id\tgroup\nS1\tcontrol\nS2\tcontrol\n", encoding="utf-8")
            payload = self._base(root)
            payload["metadata"] = str(metadata)
            plan = build_preflight(payload)
            self.assertTrue(plan["metadata"]["usable"])
            self.assertFalse(plan["effective"]["skip_diversity"])
            self.assertTrue(plan["effective"]["skip_ancom"])
            self.assertTrue(any(item["id"] == "ancom" for item in plan["warnings"]))

    def test_skip_taxonomy_disables_classifier_requirement_and_ancom(self):
        with tempfile.TemporaryDirectory() as directory:
            payload = self._base(Path(directory))
            payload["classifier"] = ""
            payload["skip_taxonomy"] = True
            plan = build_preflight(payload)
            self.assertTrue(plan["can_run"])
            self.assertTrue(plan["effective"]["skip_ancom"])

    def test_metadata_capabilities_ignore_numeric_columns_for_groups(self):
        with tempfile.TemporaryDirectory() as directory:
            metadata = Path(directory) / "metadata.tsv"
            metadata.write_text(
                "sample-id\tage\n#q2:types\tnumeric\nS1\t3\nS2\t8\n",
                encoding="utf-8",
            )
            result = inspect_metadata_capabilities(metadata)
            self.assertTrue(result["usable"])
            self.assertFalse(result["group_ready"])
            self.assertEqual(result["categorical_columns"], [])

    def test_invalid_custom_sampling_depth_blocks_only_when_diversity_can_run(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            metadata = root / "metadata.tsv"
            metadata.write_text("sample-id\tgroup\nS1\tcontrol\nS2\ttreatment\n", encoding="utf-8")
            payload = self._base(root)
            payload.update({"metadata": str(metadata), "sampling_depth": "0"})
            plan = build_preflight(payload)
            self.assertFalse(plan["can_run"])
            self.assertTrue(any(item["id"] == "sampling_depth" for item in plan["blockers"]))

    def test_invalid_custom_sampling_depth_is_ignored_when_diversity_is_skipped(self):
        with tempfile.TemporaryDirectory() as directory:
            payload = self._base(Path(directory))
            payload.update({"sampling_depth": "not-a-number", "skip_diversity": True})
            plan = build_preflight(payload)
            self.assertTrue(plan["can_run"])
            self.assertFalse(any(item["id"] == "sampling_depth" for item in plan["blockers"]))

    def test_missing_biom_skips_auto_diversity_but_not_custom_depth(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            metadata = root / "metadata.tsv"
            metadata.write_text("sample-id\tgroup\nS1\tcontrol\nS2\ttreatment\n", encoding="utf-8")
            payload = self._base(root)
            payload.update({"metadata": str(metadata), "biom_available": False})
            plan = build_preflight(payload)
            self.assertTrue(plan["can_run"])
            self.assertTrue(plan["effective"]["skip_diversity"])
            self.assertTrue(any(item["id"] == "biom" for item in plan["warnings"]))

            payload["sampling_depth"] = "1000"
            custom_plan = build_preflight(payload)
            self.assertFalse(custom_plan["effective"]["skip_diversity"])


if __name__ == "__main__":
    unittest.main()
