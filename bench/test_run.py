import hashlib
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

            # A result has to say what it was measured on, or it cannot be the
            # "same version-pinned corpus" bench/README.md gates a claim behind.
            # The corpus is private, so the digest is what is recorded, not the
            # path: two runs carrying the same digests read the same bytes.
            identity = report["results"][0]["source"]
            self.assertEqual(identity["filename"], "source.png")
            self.assertEqual(identity["bytes"], source.stat().st_size)
            self.assertEqual(identity["sha256"], hashlib.sha256(source.read_bytes()).hexdigest())
            self.assertNotIn(str(root), json.dumps(report), "a private corpus path must not reach the result")

    def test_a_document_that_fails_is_still_identified(self):
        """A failure names the file too, or a corpus cannot be reconciled after one."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "not-a-document.pdf"
            source.write_bytes(b"%PDF-1.7\nthis is not a PDF\n")
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps({
                "corpus_name": "test-corpus", "version": "1", "comparators": {},
                "documents": [{"id": "broken", "path": str(source)}],
            }), encoding="utf-8")
            output = root / "result.json"
            subprocess.run([sys.executable, str(ROOT / "bench" / "run.py"), str(manifest),
                            "--output", str(output), "--profile", "Fast"], check=False, capture_output=True, text=True)
            result = json.loads(output.read_text(encoding="utf-8"))["results"][0]
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["source"]["filename"], "not-a-document.pdf")
            self.assertEqual(result["source"]["sha256"], hashlib.sha256(source.read_bytes()).hexdigest())


if __name__ == "__main__":
    unittest.main()
