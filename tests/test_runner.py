import tempfile
import unittest
from pathlib import Path

from qiime2auto.config import AnalysisConfig, ConfigError
from qiime2auto.pipeline import PipelineOptions, run_analysis
from qiime2auto.runner import CommandRunner, command_text


class RunnerTests(unittest.TestCase):
    def test_command_text_quotes_spaces(self):
        rendered = command_text(["python", "folder with spaces/file.py"])
        self.assertTrue("folder with spaces/file.py" in rendered)
        self.assertTrue(('"' in rendered) or ("'" in rendered))

    def test_dry_run_does_not_require_executable(self):
        with tempfile.TemporaryDirectory() as directory:
            runner = CommandRunner(Path(directory), dry_run=True)
            runner.require(["definitely-not-installed-q2-tool"])
            result = runner.run(["definitely-not-installed-q2-tool", "--version"])
            self.assertEqual(result.returncode, 0)

    def test_selected_conda_environment_prefixes_qiime(self):
        runner = CommandRunner(dry_run=True, command_prefix=["conda", "run", "-n", "qiime2-test"])
        result = runner.run(["qiime", "--version"])
        self.assertEqual(result.args[:4], ["conda", "run", "-n", "qiime2-test"])

    def test_invalid_sampling_depth_is_rejected(self):
        with self.assertRaises(ConfigError):
            AnalysisConfig(sampling_depth="not-a-number").validate()

    def test_pipeline_dry_run_works_without_qiime2(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / "manifest.tsv"
            metadata = root / "metadata.tsv"
            manifest.write_text("sample-id\tabsolute-filepath\nS1\tC:/S1.fastq.gz\n", encoding="utf-8")
            metadata.write_text("sample-id\tgroup\nS1\tcontrol\n", encoding="utf-8")
            config = AnalysisConfig(metadata=str(metadata), sampling_depth=1000)
            options = PipelineOptions(
                input_path=str(manifest), output_dir=str(root / "results"), data_type="manifest_single",
                no_trim=True, no_filter=True, no_figaro=True, skip_taxonomy=True,
                skip_diversity=True, skip_ancom=True, dry_run=True,
            )
            result = run_analysis(config, options)
            self.assertTrue(result.success)
            self.assertTrue(Path(result.report).exists())


if __name__ == "__main__":
    unittest.main()
