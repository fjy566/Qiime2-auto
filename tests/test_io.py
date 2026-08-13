import tempfile
import unittest
from pathlib import Path

from qiime2auto.io import (
    detect_data_type,
    generate_manifest,
    generate_metadata_template,
    inspect_manifest,
    scan_input,
    validate_metadata_details,
)
from qiime2auto.table_editor import metadata_preview, preview_manifest, save_manifest_table, save_metadata_table


class InputWorkflowTests(unittest.TestCase):
    def test_manifest_pairs_casava_files_and_uses_qiime_header(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("SampleA_S1_L001_R1_001.fastq.gz", "SampleA_S1_L001_R2_001.fastq.gz", "SampleB_S2_L001_R1_001.fastq.gz"):
                (root / name).touch()
            self.assertEqual(detect_data_type(root), "Casava_paired")
            result = generate_manifest(root, root / "manifest.tsv", paired_end=True)
            self.assertIsNotNone(result)
            lines = (root / "manifest.tsv").read_text(encoding="utf-8").splitlines()
            self.assertEqual(lines[0], "sample-id\tforward-absolute-filepath\treverse-absolute-filepath")
            self.assertEqual(len(lines), 2)
            self.assertIn("SampleA", lines[1])

    def test_single_manifest_header_is_not_misspelled(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "sample.fastq.gz").touch()
            generate_manifest(root, root / "manifest.tsv", paired_end=False)
            self.assertEqual((root / "manifest.tsv").read_text(encoding="utf-8").splitlines()[0], "sample-id\tabsolute-filepath")

    def test_qiime_sampleid_header_is_validated(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "metadata.tsv"
            path.write_text("#SampleID\tgroup\nS1\tcontrol\nS2\ttreatment\n", encoding="utf-8")
            result = validate_metadata_details(path)
            self.assertTrue(result.valid)
            self.assertEqual(result.sample_count, 2)
            self.assertEqual(result.columns, ["sample-id", "group"])

    def test_duplicate_metadata_is_rejected_but_empty_metadata_warns(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "metadata.tsv"
            path.write_text("sample-id\tgroup\nS1\t\nS1\tcontrol\n", encoding="utf-8")
            result = validate_metadata_details(path)
            self.assertFalse(result.valid)
            self.assertTrue(any("重复" in error for error in result.errors))
            self.assertTrue(any("缺失值" in warning for warning in result.warnings))

    def test_metadata_editor_round_trip_preserves_q2_types(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "metadata.txt"
            source.write_text("#SampleID\tgroup\tage\n#q2:types\tcategorical\tnumeric\nS1\tcontrol\t3\nS2\t\t\n", encoding="utf-8")
            preview = metadata_preview(source)
            self.assertEqual(preview["headers"], ["sample-id", "group", "age"])
            self.assertEqual(preview["types"], ["", "categorical", "numeric"])
            self.assertTrue(preview["validation"]["valid"])
            saved = save_metadata_table(root / "saved.tsv", preview["headers"], preview["types"], preview["rows"])
            self.assertTrue(Path(saved["path"]).exists())
            self.assertIn("#q2:types", Path(saved["path"]).read_text(encoding="utf-8"))

    def test_manifest_editor_round_trip_keeps_paired_columns(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("S1_R1.fastq.gz", "S1_R2.fastq.gz"):
                (root / name).write_bytes(b"@read\nACGT\n+\nIIII\n")
            manifest = root / "sheet.any"
            manifest.write_text("sample-id\tforward-absolute-filepath\treverse-absolute-filepath\nS1\tS1_R1.fastq.gz\tS1_R2.fastq.gz\n", encoding="utf-8")
            preview = preview_manifest(manifest, root)
            saved = save_manifest_table(root / "edited.tsv", preview["data_type"], preview["rows"])
            self.assertEqual(list(saved["headers"]), ["sample-id", "forward-absolute-filepath", "reverse-absolute-filepath"])
            self.assertEqual(saved["missing_files"], [])

    def test_metadata_template_and_scan(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "manifest.tsv"
            source.write_text("sample-id\tabsolute-filepath\nS1\tC:/S1.fastq.gz\n", encoding="utf-8")
            metadata = generate_metadata_template(["S1"], root / "metadata.tsv", ["group", "time"])
            self.assertTrue(Path(metadata).exists())
            self.assertEqual(detect_data_type(source), "manifest_single")
            self.assertEqual(scan_input(source)["data_type"], "manifest_single")

    def test_manifest_detection_ignores_file_extension(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample-sheet.weird"
            path.write_text("sample-id\tabsolute-filepath\nS1\t/data/S1.fastq.gz\n", encoding="utf-8")
            self.assertEqual(detect_data_type(path), "manifest_single")

    def test_manifest_scan_counts_referenced_fastq_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / "samples.anything"
            manifest.write_text(
                "sample-id\tforward-absolute-filepath\treverse-absolute-filepath\n"
                "S1\tS1_R1.fastq.gz\tS1_R2.fastq.gz\n"
                "S2\tS2_R1.fastq.gz\tS2_R2.fastq.gz\n",
                encoding="utf-8",
            )
            for name in ("S1_R1.fastq.gz", "S1_R2.fastq.gz", "S2_R1.fastq.gz", "S2_R2.fastq.gz"):
                (root / name).write_bytes(b"@read\nACGT\n+\nIIII\n")
            details = inspect_manifest(manifest, root)
            self.assertEqual(details["fastq_count"], 4)
            self.assertEqual(details["missing_files"], [])
            scan = scan_input(manifest, root)
            self.assertEqual(scan["data_type"], "manifest_paired")
            self.assertEqual(scan["fastq_count"], 4)
            self.assertEqual(scan["existing_fastq_count"], 4)
            self.assertEqual(scan["sample_count"], 2)


if __name__ == "__main__":
    unittest.main()
