import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class BenchmarkHarnessTest(unittest.TestCase):
    def test_declared_external_comparator_isolated_and_recorded(self):
        from PIL import Image

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.png"
            Image.new("RGB", (24, 24), "white").save(source)
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps({
                "corpus_name": "test-corpus",
                "version": "1",
                "comparators": {
                    "fixture": {
                        "version_command": [sys.executable, "-c", "print('fixture-1')"],
                        "command": [sys.executable, "-c", "from pathlib import Path; import sys; Path(sys.argv[1], 'artifact.txt').write_text('ok')", "{output}"],
                        "timeout_seconds": 30,
                    }
                },
                "documents": [{"id": "fixture", "path": str(source), "expected": {"min_pages": 1}}],
            }), encoding="utf-8")
            output = root / "result.json"
            process = subprocess.run([sys.executable, str(ROOT / "bench" / "run.py"), str(manifest), "--output", str(output), "--profile", "Fast"], check=False, capture_output=True, text=True)
            self.assertEqual(process.returncode, 0, process.stderr)
            report = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(report["comparators"]["fixture"]["status"], "recorded")
            comparison = report["results"][0]["comparators"]["fixture"]
            self.assertEqual(comparison["status"], "completed")
            self.assertEqual(comparison["artifacts"][0]["path"], "artifact.txt")
            self.assertLess(report["results"][0]["milliseconds"], 1_000)
            self.assertTrue(report["results"][0]["latency"]["warm_cache_hit"])
            self.assertEqual(report["results"][0]["metrics"]["unsupported_output_rate"], 0)
            self.assertIn("engine_process_peak_rss", report["summary"])


if __name__ == "__main__":
    unittest.main()
