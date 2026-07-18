import json

import pytest

from k8s_upgrade_advisor.cli import EXIT_GATE_FAILED, main

pytestmark = pytest.mark.integration

FIXTURE = "tests/fixtures/eks_1_26.json"


class TestAssessCommand:
    def test_gate_fails_on_not_ready(self, tmp_path):
        code = main(
            [
                "assess",
                "-s",
                "1.26",
                "-t",
                "1.29",
                "--snapshot",
                FIXTURE,
                "--dry-run",
                "--md",
                str(tmp_path / "r.md"),
                "--html",
                str(tmp_path / "r.html"),
            ]
        )
        assert code == EXIT_GATE_FAILED
        assert (tmp_path / "r.md").is_file() and (tmp_path / "r.html").is_file()

    def test_gate_never_passes(self):
        code = main(
            [
                "assess",
                "-s",
                "1.26",
                "-t",
                "1.29",
                "--snapshot",
                FIXTURE,
                "--dry-run",
                "--no-save",
                "--fail-on",
                "never",
            ]
        )
        assert code == 0

    def test_gate_blocked_threshold_passes_not_ready(self):
        code = main(
            [
                "assess",
                "-s",
                "1.26",
                "-t",
                "1.29",
                "--snapshot",
                FIXTURE,
                "--dry-run",
                "--no-save",
                "--fail-on",
                "blocked",
            ]
        )
        assert code == 0

    def test_json_output(self, capsys):
        code = main(
            [
                "assess",
                "-s",
                "1.26",
                "-t",
                "1.29",
                "--snapshot",
                FIXTURE,
                "--dry-run",
                "--no-save",
                "--json",
                "--fail-on",
                "never",
            ]
        )
        assert code == 0
        report = json.loads(capsys.readouterr().out)
        assert report["source_version"] == "1.26"
        assert report["readiness"]["verdict"] == "not-ready"

    def test_invalid_versions_exit_config(self):
        assert (
            main(
                [
                    "assess",
                    "-s",
                    "1.29",
                    "-t",
                    "1.26",
                    "--snapshot",
                    FIXTURE,
                    "--dry-run",
                    "--no-save",
                ]
            )
            == 78
        )

    def test_missing_snapshot_exit_unavailable(self):
        assert (
            main(
                [
                    "assess",
                    "-s",
                    "1.26",
                    "-t",
                    "1.29",
                    "--snapshot",
                    "does-not-exist.json",
                    "--dry-run",
                    "--no-save",
                ]
            )
            == 69
        )
