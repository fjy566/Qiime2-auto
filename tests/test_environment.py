import unittest
from unittest.mock import patch

from qiime2auto.environment import _parse_conda_env_list, discover_environments, figaro_install_command, install_command


class EnvironmentTests(unittest.TestCase):
    def test_parse_conda_env_list(self):
        output = "# conda environments:\nbase                  *  /opt/miniconda3\nqiime2-2024.10           /opt/miniconda3/envs/qiime2-2024.10\n"
        environments = _parse_conda_env_list(output)
        self.assertEqual([item.name for item in environments], ["base", "qiime2-2024.10"])
        self.assertTrue(environments[0].active)

    def test_install_command_is_argument_safe_and_linux_specific(self):
        command = install_command("2024.10", "amplicon")
        self.assertEqual(command[:5], ["conda", "env", "create", "-n", "qiime2-amplicon-2024.10"])
        self.assertIn("py310-linux-conda.yml", command[-1])
        with self.assertRaises(ValueError):
            install_command("2024.10", "amplicon", "bad name;rm")

    def test_latest_install_command_matches_official_2026_layout(self):
        command = install_command("2026.7", "amplicon")
        self.assertEqual(command[:5], ["conda", "env", "create", "-n", "rachis-qiime2-2026.7"])
        self.assertEqual(command[-1], "https://raw.githubusercontent.com/qiime2/distributions/refs/heads/dev/2026.7/qiime2/released/rachis-qiime2-linux-64-conda.yml")
        tiny = install_command("2026.7", "tiny")
        self.assertIn("/2026.7/tiny/released/rachis-tiny-linux-64-conda.yml", tiny[-1])

    def test_figaro_install_command_targets_selected_environment(self):
        self.assertEqual(figaro_install_command("qiime2-2024.10"), ["conda", "install", "-n", "qiime2-2024.10", "-c", "bioconda", "figaro", "-y"])
        with self.assertRaises(ValueError):
            figaro_install_command("bad name;rm")

    @patch("qiime2auto.environment.shutil.which", return_value=None)
    def test_discover_without_conda_is_actionable(self, _which):
        result = discover_environments()
        self.assertFalse(result["conda_available"])
        self.assertIn("conda", result["error"])


if __name__ == "__main__":
    unittest.main()
