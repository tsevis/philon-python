"""Deterministic malformed-input coverage for Philon's intake boundary."""

import importlib.util
import random
import sys
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("philon_engine.py")
SPEC = importlib.util.spec_from_file_location("philon_engine_fuzz", MODULE_PATH)
assert SPEC and SPEC.loader
engine = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = engine
SPEC.loader.exec_module(engine)


class PreflightFuzzTest(unittest.TestCase):
    def test_malformed_pdf_and_png_corpus_fails_closed(self):
        generator = random.Random(20260816)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for index in range(32):
                payload = bytes(generator.randrange(0, 256) for _ in range(16 + index * 7))
                pdf = root / f"bad-{index}.pdf"
                pdf.write_bytes(b"%PDF-1.7\n" + payload)
                with self.assertRaises(ValueError, msg=f"PDF case {index}"):
                    engine.preflight_input(pdf)
                png = root / f"bad-{index}.png"
                png.write_bytes(b"\x89PNG\r\n\x1a\n" + payload)
                with self.assertRaises(ValueError, msg=f"PNG case {index}"):
                    engine.preflight_input(png)


if __name__ == "__main__":
    unittest.main()
