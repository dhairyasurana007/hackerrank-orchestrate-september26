from __future__ import annotations

import csv
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from buyorwait.contract import OUTPUT_COLUMNS


ROOT = Path(__file__).resolve().parents[2]


class StaticExplorerTest(unittest.TestCase):
    def test_bundle_covers_every_request_and_matches_output_csv(self):
        with tempfile.TemporaryDirectory() as temp:
            site = Path(temp) / "site"
            subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "code" / "scripts" / "build_static_explorer.py"),
                    "--output-dir",
                    str(site),
                    "--output-csv",
                    str(ROOT / "output.csv"),
                ],
                cwd=ROOT,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            bundle = json.loads((site / "data" / "results.json").read_text(encoding="utf-8"))

        with (ROOT / "output.csv").open(newline="", encoding="utf-8-sig") as handle:
            output_rows = {row["request_id"]: row for row in csv.DictReader(handle)}

        self.assertEqual(250, bundle["metadata"]["request_count"])
        self.assertEqual(set(output_rows), {item["request"]["request_id"] for item in bundle["requests"]})
        for item in bundle["requests"]:
            self.assertEqual(
                {column: output_rows[item["request"]["request_id"]][column] for column in OUTPUT_COLUMNS},
                item["finalDecision"],
            )
            self.assertTrue(item["decisionMatchesOutput"])

    def test_browser_assets_do_not_contain_model_call_paths_or_secrets(self):
        combined = "\n".join(
            (ROOT / "site" / name).read_text(encoding="utf-8")
            for name in ("index.html", "styles.css", "app.js")
        )
        forbidden = ("OPENROUTER_API_KEY", "api.openrouter.ai", "ModelClient", "fetch(\"https://")
        for needle in forbidden:
            self.assertNotIn(needle, combined)

    def test_site_has_chat_input_as_primary_surface(self):
        html = (ROOT / "site" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "site" / "app.js").read_text(encoding="utf-8")
        style = (ROOT / "site" / "styles.css").read_text(encoding="utf-8")
        self.assertIn('id="chatForm"', html)
        self.assertIn('id="chatInput"', html)
        self.assertIn('id="csvUpload"', html)
        self.assertIn("View chart data", script)
        self.assertIn("chat-chart", style)
        self.assertIn("typing-indicator", style)
        self.assertIn("CHAT_RESPONSE_DELAY_MS", script)
        self.assertIn("queueAssistantResponse", script)
        self.assertIn("decisionSummaryMessage", script)
        self.assertIn("chat-summary-row.recommendation", style)
        self.assertIn("chat-summary-row.safe", style)
        self.assertIn("chat-summary-row.date", style)
        self.assertIn("renderAssistantMarkdown", script)
        self.assertIn("forecastChartHtml", script)
        self.assertIn("uploadedChartHtml", script)
        self.assertIn("requestTitle", script)
        self.assertNotIn('id="detailsToggle"', html)
        self.assertIn("answerPrompt", script)
        self.assertIn("parseCsv", script)
        self.assertIn("chartAnswer", script)
        self.assertIn("chartAxisLabels", script)
        self.assertIn("chartHoverPoint", script)
        self.assertIn("chart-axis-label", style)
        self.assertIn("chart-hit-point", style)
        self.assertIn("answerUploadedPrompt", script)
        self.assertIn("appendMessage", script)


if __name__ == "__main__":
    unittest.main()
