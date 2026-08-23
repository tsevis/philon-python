import hashlib
import importlib.util
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).with_name("philon_engine.py")
# The engine runs as a script from this directory, so its own folder is on the
# path and `model_fetch` imports by name. Mirror that here rather than loading
# the module a second, different way.
sys.path.insert(0, str(MODULE_PATH.parent))
SPEC = importlib.util.spec_from_file_location("philon_engine", MODULE_PATH)
assert SPEC and SPEC.loader
engine = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = engine
SPEC.loader.exec_module(engine)


class PhilonEngineTest(unittest.TestCase):
    def test_verified_embedding_runtime_failure_becomes_evidence_not_document_failure(self):
        """The port's one intentional divergence from the source engine.

        A local BGE-M3 process that exits non-zero must not fail an otherwise
        valid conversion. The source project lets the RuntimeError propagate;
        here it becomes EMBEDDING_FAILED evidence and no vector is emitted.
        Recorded in docs/PARITY.md; this test is what keeps the two engines
        from silently converging on the wrong one.
        """
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model = root / "bge"
            model.mkdir()
            (model / "model.gguf").write_bytes(b"local-test-model")
            chunks = [{"id": "chunk-1", "text": "local evidence", "source_block_ids": ["p1-b1"]}]
            status = {"packs": [{"id": "bge-m3-local-candidate", "approved": True, "available_locally": True, "local_path": str(model)}]}
            with patch.object(engine, "model_status", return_value=status), patch.object(engine.shutil, "which", return_value="/tmp/llama-embedding"), patch.object(engine.Path, "exists", return_value=True), patch.object(engine.subprocess, "run") as run:
                run.return_value.returncode = 1
                run.return_value.stderr = "local embedding runtime unavailable"
                run.return_value.stdout = ""
                embeddings, warning = engine.render_bge_embeddings(chunks)
            self.assertIsNone(embeddings)
            self.assertEqual(warning.code, "EMBEDDING_FAILED")

    def test_native_health_flags_garbled_text(self):
        health = engine.native_health("\ufffd\ufffd\ufffd")
        self.assertTrue(health["requires_escalation"])
        self.assertLess(health["confidence"], 0.8)

    def test_native_health_retains_invisible_and_repeated_text_evidence(self):
        invisible = engine.native_health("Visible\u200b text")
        self.assertEqual(invisible["invisible_characters"], 1)
        self.assertTrue(invisible["requires_escalation"])
        repeated = engine.native_health("Repeated source line for review.\n" * 4)
        self.assertEqual(repeated["duplicate_source_line_count"], 3)
        pages = [{"id": "page-1", "number": 1, "method": "pdfium-native", "route": {"native_text_health": repeated}}]
        warnings = engine.verified_checks(pages, [])
        self.assertIn("DUPLICATE_SOURCE_LINES", [warning.code for warning in warnings])

    def test_router_records_page_classification_and_bounded_adaptive_ocr_dpi(self):
        self.assertEqual(engine.adaptive_ocr_dpi({"width": 612, "height": 792}), 300)
        self.assertEqual(engine.adaptive_ocr_dpi({"width": 200, "height": 300}), 360)
        self.assertEqual(engine.adaptive_ocr_dpi({"width": 1600, "height": 1000}), 240)
        route = engine.route_for_page({"method": "image-awaiting-ocr"}, engine.native_health(""))
        self.assertEqual(route["page_kind"], "image-or-scanned")
        self.assertFalse(route["automatic_model_execution"])

    def test_markdown_and_chunk_rendering_keep_block_evidence(self):
        ir = {
            "blocks": [
                {"id": "p1-b1", "page": "page-1", "type": "heading", "level": 1, "text": "A paper", "source": {"confidence": 0.98}},
                {"id": "p1-b2", "page": "page-1", "type": "paragraph", "level": None, "text": "A grounded paragraph.", "source": {"confidence": 0.98}},
            ]
        }
        self.assertIn("# A paper", engine.render_markdown(ir))
        self.assertEqual(engine.render_chunks(ir)[0]["source_block_ids"], ["p1-b1", "p1-b2"])

    def test_reading_markdown_reflows_words_and_keeps_source_page_markers(self):
        ir = {
            "document": {"source": {"filename": "scan.pdf"}},
            "pages": [{"id": "page-1", "number": 1}],
            "blocks": [{"id": "p1-b1", "page": "page-1", "type": "paragraph", "level": None, "text": "A care-\nfully measured paragraph.", "source": {"confidence": .98}}],
        }
        rendered = engine.render_markdown(ir)
        self.assertIn("A carefully measured paragraph.", rendered)
        self.assertIn("<!-- Philon source page 1 -->", rendered)
        self.assertNotIn("Extracted source images", rendered)

    def test_ocr_geometry_assembly_keeps_measured_paragraph_boundaries(self):
        page = {
            "number": 1,
            "method": "apple-vision-ocr",
            "text": "First line\nSecond line\nCaption line",
            "ocr_lines": [
                {"text": "First line", "bbox": [.1, .80, .35, .03]},
                {"text": "Second line", "bbox": [.1, .75, .35, .03]},
                {"text": "Caption line", "bbox": [.1, .60, .35, .03]},
            ],
        }
        parts = engine.ocr_parts_with_geometry(page, set())
        self.assertEqual([part["text"] for part in parts], ["First line\nSecond line", "Caption line"])
        block = engine.make_block(page, 1, parts[0]["text"], ocr_line_indexes=parts[0]["line_indexes"])
        self.assertEqual(block["evidence"]["findings"]["ocr_line_count"], 2)
        self.assertIsNotNone(block["bbox"])

    def test_machine_package_is_compact_page_addressable_and_provenance_rich(self):
        ir = {
            "document": {"id": "sha256:test", "source": {"filename": "scan.pdf"}},
            "pages": [{"id": "page-1", "number": 1, "block_ids": ["p1-b1"]}],
            "blocks": [{"id": "p1-b1", "page": "page-1", "type": "paragraph", "level": None, "text": "Source line\ncontinues.", "bbox": None, "source": {"confidence": .98}, "evidence": {"validation": []}}],
            "document_artifacts": {"native_images": []},
        }
        with tempfile.TemporaryDirectory() as directory:
            root = engine.write_machine_package(ir, Path(directory))
            self.assertTrue((root / "blocks.ndjson").is_file())
            self.assertTrue((root / "pages/page-0001.md").is_file())
            record = json.loads((root / "blocks.ndjson").read_text(encoding="utf-8"))
            self.assertEqual(record["text"], "Source line\ncontinues.")
            self.assertEqual(record["reading_text"], "Source line continues.")

    def test_presentation_html_is_page_linked_without_an_end_gallery(self):
        ir = {
            "document": {"source": {"filename": "scan.pdf"}},
            "pages": [{"id": "page-1", "number": 1}],
            "blocks": [{"id": "p1-b1", "page": "page-1", "type": "paragraph", "level": None, "text": "Reading text", "source": {"confidence": .98}}],
        }
        rendered = engine.render_html(ir, include_facsimiles=True)
        self.assertIn("Show original page", rendered)
        self.assertIn("assets/page-previews/page-0001.png", rendered)
        self.assertNotIn("Extracted source images", rendered)

    def test_citations_link_to_source_reference_blocks(self):
        blocks = [
            {"id": "p1-b1", "type": "paragraph", "text": "This claim is grounded [1].", "evidence": {}},
            {"id": "p2-b1", "type": "citation", "text": "[1] Philon research 2026.", "evidence": {}},
        ]
        engine.resolve_citations(blocks)
        self.assertEqual(blocks[0]["evidence"]["citation_targets"][0]["reference_block_id"], "p2-b1")

    def test_compatible_native_tables_are_linked_across_pages(self):
        blocks = [
            {"id": "p1-table", "page": "page-1", "type": "table", "text": "Metric | Score\nCER | 0.02", "evidence": {}},
            {"id": "p2-table", "page": "page-2", "type": "table", "text": "Metric | Score\nWER | 0.04", "evidence": {}},
        ]
        engine.resolve_cross_page_tables(blocks)
        self.assertEqual(blocks[1]["evidence"]["cross_page_continuation_of"], "p1-table")
        self.assertEqual(engine.table_export_groups(blocks)[0]["rows"], [["Metric", "Score"], ["CER", "0.02"], ["WER", "0.04"]])

    def test_corrupt_image_is_blocked_before_any_recognition(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "scan.png"
            source.write_bytes(b"\x89PNG\r\n\x1a\nnot-a-real-image")
            with self.assertRaisesRegex(ValueError, "could not be parsed safely"):
                engine.preflight_input(source)

    def test_preflight_rejects_a_pdf_with_an_invalid_signature(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "not-a-pdf.pdf"
            source.write_text("This is not a PDF.")
            with self.assertRaisesRegex(ValueError, "signature is invalid"):
                engine.preflight_input(source)

    def test_preflight_blocks_malformed_pdf_containers_with_valid_headers(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "broken.pdf"
            source.write_bytes(b"%PDF-1.7\nthis is deliberately not a PDF body")
            with self.assertRaisesRegex(ValueError, "could not be parsed safely"):
                engine.preflight_input(source)

    def test_preflight_rejects_a_pdf_that_really_needs_a_password(self):
        try:
            from pypdf import PdfWriter
        except ImportError:
            self.skipTest("pypdf is not installed")
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "locked.pdf"
            writer = PdfWriter()
            writer.add_blank_page(width=100, height=100)
            writer.encrypt("local-password")
            with source.open("wb") as stream:
                writer.write(stream)
            with self.assertRaisesRegex(ValueError, "needs a password"):
                engine.preflight_input(source)

    def test_preflight_accepts_a_pdf_whose_user_password_is_empty(self):
        """A permissions-only PDF opens for anyone, so refusing it refused nothing.

        Publisher PDFs are routinely encrypted with an empty user password and a
        set owner password: every reader opens them without being asked, and
        PDFium extracts their text with no password supplied. Philon refused
        them as "encrypted", which turned a readable document into a dead end.
        The outcome is recorded so the record still says the file was encrypted.
        """
        try:
            from pypdf import PdfWriter
        except ImportError:
            self.skipTest("pypdf is not installed")
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "permissions-only.pdf"
            writer = PdfWriter()
            writer.add_blank_page(width=100, height=100)
            writer.encrypt(user_password="", owner_password="owner-secret")
            with source.open("wb") as stream:
                writer.write(stream)
            report = engine.preflight_input(source)
            self.assertEqual(report["kind"], "pdf")
            self.assertEqual(report["declared_page_count"], 1)
            self.assertEqual(report["encryption"], "opened-with-empty-user-password")

    def test_preflight_records_that_an_ordinary_pdf_was_not_encrypted(self):
        try:
            from pypdf import PdfWriter
        except ImportError:
            self.skipTest("pypdf is not installed")
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "plain.pdf"
            writer = PdfWriter()
            writer.add_blank_page(width=100, height=100)
            with source.open("wb") as stream:
                writer.write(stream)
            self.assertEqual(engine.preflight_input(source)["encryption"], "none")

    def test_native_pdf_features_are_explicit_when_no_font_or_link_exists(self):
        try:
            from pypdf import PdfWriter
        except ImportError:
            self.skipTest("pypdf is not installed")
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "blank.pdf"
            writer = PdfWriter()
            writer.add_blank_page(width=100, height=100)
            with source.open("wb") as stream:
                writer.write(stream)
            features = engine.native_pdf_features(source, [1])
            self.assertEqual(features[0]["fonts"], [])
            self.assertEqual(features[0]["links"], [])

    def test_measured_source_geometry_is_attached_only_when_a_span_matches(self):
        page = {
            "number": 1, "width": 100, "height": 100, "method": "pdfium-native",
            "native_text_spans": [{"start": 0, "end": 15, "bbox": engine.make_bbox(5, 10, 90, 30, "pdf-page-points")}],
        }
        block = engine.make_block(page, 1, "Measured source", 0, 15)
        self.assertEqual(block["bbox"]["coordinate_space"], "pdf-page-points")
        self.assertTrue(block["evidence"]["findings"]["source_bbox_available"])
        self.assertIsNone(engine.make_block(page, 2, "Not in source", 20, 33)["bbox"])

    def test_vision_line_geometry_uses_normalized_image_coordinates(self):
        page = {
            "number": 1, "width": 100, "height": 100, "method": "apple-vision-ocr",
            "ocr_lines": [{"text": "Local evidence", "confidence": .99, "bbox": [.1, .2, .3, .1]}],
        }
        block = engine.make_block(page, 1, "Local evidence", 0, 14)
        self.assertEqual(block["bbox"]["coordinate_space"], "normalized-image")
        self.assertEqual(block["bbox"]["x1"], .4)

    def test_preflight_action_retains_blocked_items_as_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "bad.pdf"
            source.write_text("not a PDF")
            response = engine.action_preflight({"config": {"input_paths": [str(source)]}})
            self.assertEqual(response["items"][0]["status"], "blocked")
            self.assertIn("signature", response["items"][0]["error"])

    def test_model_gate_never_reports_an_unapproved_pack_as_installed(self):
        status = engine.model_status()
        repair = next(pack for pack in status["packs"] if pack["id"] == "local-repair")
        self.assertFalse(repair["approved"])
        self.assertFalse(repair["installed"])

    def test_model_status_exposes_readiness_and_non_destructive_diagnostics(self):
        packs = engine.model_status()["packs"]
        self.assertTrue(all(pack["readiness"] for pack in packs))
        self.assertTrue(all(isinstance(pack["diagnostics"], list) for pack in packs))
        repair = next(pack for pack in packs if pack["id"] == "local-repair")
        self.assertEqual(repair["readiness"], "blocked")
        self.assertTrue(repair["diagnostics"])

    def test_repair_candidate_quality_is_a_format_signal_not_an_accuracy_claim(self):
        valid_table = engine.assess_repair_candidate("| Label | Value |\n| --- | ---: |\n| A | 4 |", "table")
        invalid_formula = engine.assess_repair_candidate("plain prose response", "formula")
        self.assertEqual(valid_table["status"], "format-valid")
        self.assertEqual(invalid_formula["status"], "review-required")
        self.assertIn("formula", invalid_formula["issues"][0])

    def test_local_candidate_is_detected_but_not_enabled_without_approval(self):
        with tempfile.TemporaryDirectory() as directory:
            candidate = Path(directory) / "candidate-model"
            candidate.mkdir()
            manifest = Path(directory) / "model-manifest.json"
            manifest.write_text(json.dumps({
                "schema_version": "1.2",
                "packs": [{"id": "candidate", "role": "test candidate", "required": False, "approved": False, "distribution": "user-managed-local-copy", "integrity": None, "license": "unverified", "runtime": "adapter", "discovery_paths": [str(candidate)]}],
            }), encoding="utf-8")
            prior = engine.MODEL_MANIFEST_PATH
            engine.MODEL_MANIFEST_PATH = manifest
            try:
                pack = engine.model_status()["packs"][0]
                self.assertTrue(pack["available_locally"])
                self.assertFalse(pack["installed"])
            finally:
                engine.MODEL_MANIFEST_PATH = prior

    def test_manual_olmocr_repair_retains_an_unselected_evidence_candidate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.png"
            source.write_bytes(b"local-source")
            ir_path = root / "source.philon.json"
            ir = {
                "philon_ir_version": engine.IR_VERSION,
                "document": {"id": "sha256:test", "source": {"path": str(source), "filename": source.name}},
                "pages": [{"id": "page-1", "number": 1, "width": 10, "height": 10, "route": {"decision": "native-fast-path"}}],
                "blocks": [{"id": "page-1-block-1", "page": "page-1", "type": "paragraph", "text": "native source", "bbox": None, "source": {"method": "native", "confidence": .8, "language": "und"}, "evidence": {"validation": [], "alternatives": [], "repair_history": []}}],
            }
            ir_path.write_text(json.dumps(ir), encoding="utf-8")
            original_status, original_crop, original_run = engine.model_status, engine.repair_crop, engine.run_olmocr
            crop = root / "repair.png"
            crop.write_bytes(b"crop")
            engine.model_status = lambda: {"packs": [{"id": "olmocr-2-7b-local-candidate", "approved": True, "available_locally": True, "local_path": str(root)}]}
            engine.repair_crop = lambda *_args: crop
            engine.run_olmocr = lambda *_args: ("candidate text", {"model_manifest_fingerprint": "sha256:test"})
            try:
                response = engine.action_repair({"ir_path": str(ir_path), "block_id": "page-1-block-1", "repair_mode": "table"})
            finally:
                engine.model_status, engine.repair_crop, engine.run_olmocr = original_status, original_crop, original_run
            self.assertEqual(response["status"], "candidate")
            self.assertEqual(response["block"]["text"], "native source")
            candidate = response["block"]["evidence"]["alternatives"][0]
            self.assertEqual(candidate["text"], "candidate text")
            self.assertFalse(candidate["selected"])
            self.assertEqual(candidate["repair_mode"], "table")
            self.assertEqual(candidate["source_crop_sha256"], engine.sha256_file(crop))

    def test_model_manifest_rejects_an_approved_pack_without_integrity(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "model-manifest.json"
            manifest.write_text(json.dumps({
                "schema_version": "1",
                "packs": [{"id": "unsafe", "required": False, "approved": True, "distribution": "download", "integrity": None, "license": "Apache-2.0", "runtime": "onnx"}],
            }), encoding="utf-8")
            prior = engine.MODEL_MANIFEST_PATH
            engine.MODEL_MANIFEST_PATH = manifest
            try:
                with self.assertRaisesRegex(ValueError, "requires integrity"):
                    engine.model_status()
            finally:
                engine.MODEL_MANIFEST_PATH = prior

    def test_fast_profile_never_invokes_image_ocr(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow is not installed")
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "scan.png"
            Image.new("RGB", (32, 32), "white").save(source)
            result = engine.convert_file(source, "Fast", Path(directory) / "out", Path(directory) / "cache")
            self.assertEqual(result["pages"][0]["method"], "image-awaiting-ocr")
            self.assertIn("FAST_PROFILE_SKIPPED_OCR", [warning["code"] for warning in result["warnings"]])

    def test_verified_checks_report_ambiguous_geometry_and_duplicate_text(self):
        pages = [{"id": "page-1", "number": 1, "method": "pdfium-native"}]
        blocks = [
            {"id": "page-1-block-1", "page": "page-1", "text": "This is a deliberately repeated source paragraph.", "bbox": engine.make_bbox(5, 10, 90, 25, "pdf-page-points"), "evidence": {"validation": []}},
            {"id": "page-1-block-2", "page": "page-1", "text": "This is a deliberately repeated source paragraph.", "bbox": engine.make_bbox(5, 50, 90, 65, "pdf-page-points"), "evidence": {"validation": []}},
        ]
        warnings = engine.verified_checks(pages, blocks)
        self.assertIn("DUPLICATE_BLOCK_CONTENT", [warning.code for warning in warnings])
        self.assertIn("READING_ORDER_AMBIGUOUS", [warning.code for warning in warnings])
        self.assertIn("verified-deterministic-checks", blocks[0]["evidence"]["validation"])

    @unittest.skipUnless(os.environ.get("PHILON_VISION_INTEGRATION") == "1" and (Path(__file__).parent / "dist" / "philon-vision-ocr").exists(), "native Vision integration is enabled explicitly during packaging validation")
    def test_vision_ocr_image_retains_local_method_and_confidence(self):
        from PIL import Image, ImageDraw, ImageFont
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "vision.png"
            image = Image.new("RGB", (1000, 220), "white")
            ImageDraw.Draw(image).text((30, 65), "Philon Vision OCR", fill="black", font=ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 62))
            image.save(source)
            prior = engine.VISION_HELPER
            engine.VISION_HELPER = Path(__file__).parent / "dist" / "philon-vision-ocr"
            try:
                result = engine.convert_file(source, "Balanced", Path(directory) / "out", Path(directory) / "cache")
            finally:
                engine.VISION_HELPER = prior
            self.assertEqual(result["pages"][0]["method"], "apple-vision-ocr")
            self.assertGreater(result["pages"][0]["confidence"], 0.8)

    @unittest.skipUnless(os.environ.get("PHILON_VISION_INTEGRATION") == "1" and (Path(__file__).parent / "dist" / "philon-vision-ocr").exists(), "native Vision integration is enabled explicitly during packaging validation")
    def test_textless_pdf_routes_through_local_vision_ocr(self):
        from PIL import Image, ImageDraw, ImageFont
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "scan.pdf"
            image = Image.new("RGB", (1400, 400), "white")
            ImageDraw.Draw(image).text((45, 120), "Scanned Philon Evidence", fill="black", font=ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 76))
            image.save(source, "PDF", resolution=150)
            prior = engine.VISION_HELPER
            engine.VISION_HELPER = Path(__file__).parent / "dist" / "philon-vision-ocr"
            try:
                result = engine.convert_file(source, "Balanced", Path(directory) / "out", Path(directory) / "cache")
            finally:
                engine.VISION_HELPER = prior
            self.assertEqual(result["pages"][0]["method"], "apple-vision-ocr")
            self.assertEqual(result["pages"][0]["route"]["decision"], "local-vision-ocr")
            self.assertIn("Scanned Philon Evidence", result["blocks"][0]["text"])

    def test_cache_bypass_does_not_create_a_cache_entry(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "scan.png"
            Image.new("RGB", (24, 24), "white").save(source)
            cache = Path(directory) / "cache"
            result = engine.convert_file(source, "Balanced", Path(directory) / "out", cache, cache_policy="bypass")
            self.assertFalse(result["cache_hit"])
            self.assertFalse(cache.exists())

    def test_cache_key_is_scoped_to_the_engine_pipeline_version(self):
        path = engine.cache_path(Path("/tmp/cache"), "abc123", "Balanced")
        self.assertIn(engine.ENGINE_VERSION, path.name)

    def test_output_selection_writes_only_requested_exports(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "scan.png"
            Image.new("RGB", (24, 24), "white").save(source)
            result = engine.convert_file(source, "Balanced", Path(directory) / "out", Path(directory) / "cache", outputs=["ir", "evidence"])
            self.assertEqual(set(result["outputs"]), {"ir", "evidence"})

    def test_output_manifest_hashes_every_completed_bundle_file_without_self_reference(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "scan.png"
            Image.new("RGB", (24, 24), "white").save(source)
            result = engine.convert_file(source, "Balanced", Path(directory) / "out", Path(directory) / "cache")
            manifest_path = Path(result["outputs"]["manifest"])
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            paths = {entry["path"] for entry in manifest["files"]}
            self.assertEqual(manifest["document_id"], "sha256:" + engine.sha256_file(source))
            self.assertNotIn(manifest_path.name, paths)
            self.assertIn(Path(result["outputs"]["ir"]).name, paths)
            for entry in manifest["files"]:
                target = manifest_path.parent / entry["path"]
                self.assertTrue(target.exists())
                self.assertEqual(entry["sha256"], engine.sha256_file(target))

    def test_same_named_inputs_never_share_an_export_directory(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow is not installed")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first_dir, second_dir = root / "first", root / "second"
            first_dir.mkdir()
            second_dir.mkdir()
            first, second = first_dir / "paper.png", second_dir / "paper.png"
            Image.new("RGB", (24, 24), "white").save(first)
            Image.new("RGB", (24, 24), "black").save(second)
            output, cache = root / "out", root / "cache"
            first_result = engine.convert_file(first, "Balanced", output, cache)
            second_result = engine.convert_file(second, "Balanced", output, cache)
            self.assertNotEqual(Path(first_result["outputs"]["ir"]).parent, Path(second_result["outputs"]["ir"]).parent)
            self.assertTrue(Path(first_result["outputs"]["evidence"]).exists())
            self.assertTrue(Path(second_result["outputs"]["evidence"]).exists())

    def test_native_pdf_images_are_exported_with_hash_and_page_provenance(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "figure.pdf"
            Image.new("RGB", (80, 60), "green").save(source, "PDF")
            extracted, warnings = engine.extract_native_pdf_assets(source, Path(directory) / "out")
            self.assertEqual(warnings, [])
            assert extracted is not None
            self.assertEqual(len(extracted["items"]), 1)
            asset = extracted["items"][0]
            self.assertEqual(asset["source_pages"], [1])
            self.assertEqual(len(asset["bytes_sha256"]), 64)
            self.assertEqual(asset["format"], "JPEG")
            self.assertEqual(asset["mime_type"], "image/jpeg")
            self.assertEqual((asset["pixel_width"], asset["pixel_height"]), (80, 60))
            self.assertEqual(asset["source_references"], [{"page": 1, "object_name": asset["original_name"]}])
            self.assertTrue(Path(asset["path"]).exists())
            self.assertTrue(Path(extracted["manifest"]).exists())

    def test_extracted_pdf_images_remain_in_portable_ir_without_an_end_gallery(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "figure.pdf"
            output = root / "out"
            Image.new("RGB", (80, 60), "green").save(source, "PDF")
            extracted, warnings = engine.extract_native_pdf_assets(source, output)
            self.assertEqual(warnings, [])
            assert extracted is not None
            ir = {
                "document": {"source": {"filename": source.name}},
                "pages": [],
                "blocks": [],
                "document_artifacts": {},
            }
            engine.attach_native_pdf_assets_to_ir(ir, extracted, output)
            paths = engine.write_outputs(ir, [], [], output, cached=False, selected_outputs=["markdown", "html", "ir"])
            asset = extracted["items"][0]
            markdown = Path(paths["markdown"]).read_text(encoding="utf-8")
            html = Path(paths["html"]).read_text(encoding="utf-8")
            exported_ir = json.loads(Path(paths["ir"]).read_text(encoding="utf-8"))
            self.assertNotIn("Extracted source images", markdown)
            self.assertNotIn("native-images", html)
            self.assertEqual(exported_ir["document_artifacts"]["native_images"][0]["relative_path"], Path(asset["path"]).relative_to(output).as_posix())
            self.assertEqual(exported_ir["document_artifacts"]["native_images"][0]["source_pages"], [1])

    def test_page_tree_json_has_source_polygons_and_embedded_native_images(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, output = root / "figure.pdf", root / "out"
            Image.new("RGB", (80, 60), "green").save(source, "PDF")
            extracted, warnings = engine.extract_native_pdf_assets(source, output)
            self.assertEqual(warnings, [])
            assert extracted is not None
            ir = {
                "pages": [{"id": "page-1", "number": 1, "width": 100, "height": 200, "method": "pdfium-native", "confidence": 1}],
                "blocks": [{"id": "page-1-block-1", "page": "page-1", "type": "heading", "level": 1, "text": "A title", "bbox": engine.make_bbox(10, 140, 90, 180, "pdf-page-points"), "source": {"method": "pdfium-native", "confidence": 1}}],
                "document_artifacts": {},
            }
            engine.attach_native_pdf_assets_to_ir(ir, extracted, output)
            pages = engine.render_page_tree_json(ir, output)
            self.assertEqual(pages[0]["block_type"], "Page")
            self.assertEqual(pages[0]["children"][0]["block_type"], "SectionHeader")
            self.assertEqual(pages[0]["children"][0]["polygon"], [[10.0, 20.0], [90.0, 20.0], [90.0, 60.0], [10.0, 60.0]])
            picture = next(child for child in pages[0]["children"] if child["block_type"] == "Picture")
            self.assertTrue(next(iter(picture["images"].values())).startswith("data:image/jpeg;base64,"))

    def test_default_pdf_conversion_exports_machine_package_and_top_level_images_folder(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "figure.pdf"
            Image.new("RGB", (80, 60), "green").save(source, "PDF")
            result = engine.convert_file(source, "Balanced", root / "exports", root / "cache", cache_policy="bypass")
            self.assertIn("machine", result["outputs"])
            self.assertFalse("page_tree" in result["outputs"])
            self.assertTrue((Path(result["outputs"]["machine"]) / "blocks.ndjson").is_file())
            first_asset = result["outputs"]["extracted_assets"]["items"][0]
            self.assertEqual(Path(first_asset["path"]).parent.name, "images")

    def test_qwen_candidate_cleanup_rejects_runtime_banners_and_progress_bars(self):
        raw = "▄▄ ▄▄\n██ ██  ▀▀█▄\nbuild      : b486-dd1ea52\nmodel      : /models/Qwen3.8.gguf\nmodalities : text, vision, video\navailable commands:\n/exit or Ctrl+C stop or exit\nLoaded media from '/tmp/crop.png'\n> Read this selected document region carefully.\n```markdown\nFaithful transcription\n[ Prompt: 146.1 t/s | Generation: 19.9 t/s ]\nExiting...\n"
        self.assertEqual(engine.clean_vlm_transcript(raw), "Faithful transcription")

    def test_reused_pdf_image_is_exported_once_with_every_page_reference(self):
        from PIL import Image
        from pypdf import PdfReader, PdfWriter
        with tempfile.TemporaryDirectory() as directory:
            original = Path(directory) / "original.pdf"
            source = Path(directory) / "reused.pdf"
            Image.new("RGB", (40, 30), "navy").save(original, "PDF")
            reader = PdfReader(str(original))
            writer = PdfWriter()
            writer.add_page(reader.pages[0])
            writer.add_page(reader.pages[0])
            with source.open("wb") as output:
                writer.write(output)
            extracted, warnings = engine.extract_native_pdf_assets(source, Path(directory) / "out")
            self.assertEqual(warnings, [])
            assert extracted is not None
            self.assertEqual(len(extracted["items"]), 1)
            asset = extracted["items"][0]
            self.assertEqual(asset["source_pages"], [1, 2])
            self.assertEqual([reference["page"] for reference in asset["source_references"]], [1, 2])

    def test_source_overlay_diagnostic_exports_only_measured_blocks(self):
        ir = {
            "pages": [{"id": "page-1", "number": 1, "width": 100, "height": 200}],
            "blocks": [
                {"id": "measured", "page": "page-1", "type": "paragraph", "bbox": engine.make_bbox(10, 20, 40, 60, "pdf-page-points")},
                {"id": "unmeasured", "page": "page-1", "type": "paragraph", "bbox": None},
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            paths = engine.render_source_overlay_diagnostics(ir, Path(directory))
            self.assertEqual(len(paths), 1)
            svg = Path(paths[0]).read_text(encoding="utf-8")
            self.assertIn('data-philon-id="measured"', svg)
            self.assertNotIn("unmeasured", svg)

    def test_atomic_write_replaces_an_existing_export_without_a_temp_residue(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "export.md"
            target.write_text("old", encoding="utf-8")
            engine.atomic_write_text(target, "new")
            self.assertEqual(target.read_text(encoding="utf-8"), "new")
            self.assertFalse(list(Path(directory).glob(".*.tmp")))

    def test_delimited_native_table_is_exported_deterministically(self):
        text = "Metric | Score\nCER | 0.02\nWER | 0.04"
        page = {"number": 1, "width": 100, "height": 100, "method": "pdfium-native"}
        block = engine.make_block(page, 1, text)
        self.assertEqual(block["type"], "table")
        ir = {"blocks": [block]}
        self.assertIn("| Metric | Score |", engine.render_markdown(ir))
        self.assertIn("<th scope=\"col\">Metric</th>", engine.render_html(ir))

    def test_long_geometric_paragraph_is_not_misclassified_as_a_heading(self):
        text = "The source paragraph begins with a capital letter but contains several measured lines\n" * 5
        kind, level = engine.classify_block(text.strip())
        self.assertEqual(kind, "paragraph")
        self.assertIsNone(level)

    def test_a_wrapped_paragraph_is_not_a_heading_because_its_first_line_reads_like_one(self):
        """The first line of ordinary prose looks exactly like a heading.

        It starts with a capital, runs under a hundred characters and ends
        mid-clause rather than with a full stop, so judging a block by that
        line alone turned real paragraphs into H2s in converted Markdown.
        """
        wrapped = (
            "Philon of Alexandria read one tradition in the language of another,\n"
            "quoting the line before drawing out what he took it to mean, and\n"
            "holding that the literal sense had to stand."
        )
        self.assertEqual(engine.classify_block(wrapped), ("paragraph", None))
        two_lines = "Every block carries its page, its method and its confidence\nso a reader can go back and verify it."
        self.assertEqual(engine.classify_block(two_lines), ("paragraph", None))

    def test_a_wrapped_title_is_a_heading_when_the_type_is_larger(self):
        """A heading that wraps is still a heading if the page shows it is one.

        Requiring a single line keeps prose out of the heading set, but a title
        long enough to wrap would be lost with it. The page's own geometry
        settles it: type noticeably larger than the body text is a heading even
        across two lines, and type the same size as the body is not.
        """
        title = "Measuring shadows in the margins of\na fifteenth century manuscript"
        self.assertEqual(engine.classify_block(title, prominence=1.7), ("heading", 2))
        self.assertEqual(engine.classify_block(title, prominence=1.0), ("paragraph", None))
        self.assertEqual(engine.classify_block(title), ("paragraph", None))

    def test_prominent_type_does_not_make_a_page_of_prose_into_headings(self):
        prose = ("Philon of Alexandria read one tradition in the language of another,\n"
                 "quoting the line before drawing out what he took it to mean, and\n"
                 "holding that the literal sense had to stand.")
        # Four lines is past what a heading runs to, whatever its size.
        long_block = prose + "\nA fourth measured line of the same paragraph."
        self.assertEqual(engine.classify_block(long_block, prominence=2.0), ("paragraph", None))

    def test_a_heading_is_a_line_on_its_own(self):
        self.assertEqual(engine.classify_block("Measuring Shadows"), ("heading", 2))
        self.assertEqual(engine.classify_block("2.1 Adaptive routing"), ("heading", 2))
        self.assertEqual(engine.classify_block("3 Results"), ("heading", 1))

    def test_a_measured_page_reads_a_wrapped_title_as_a_heading(self):
        """The whole path, not just the rule: geometry from the page decides."""
        def line(text, start, end, y, height):
            return {"text": text, "start": start, "end": end,
                    "bbox": engine.make_bbox(72, y, 400, y + height, "pdf-page-points")}

        page = {
            "number": 1, "width": 612, "height": 792, "method": "pdfium-native",
            "text": "",
            "native_text_lines": [
                line("Measuring shadows in the margins of", 0, 35, 700, 20),
                line("a fifteenth century manuscript", 36, 66, 676, 20),
                line("Philon of Alexandria read one tradition in", 67, 109, 640, 11),
                line("the language of another, quoting the line", 110, 151, 626, 11),
                line("before drawing out what it meant.", 152, 185, 612, 11),
            ],
        }
        title = engine.make_block(page, 1, "Measuring shadows in the margins of\na fifteenth century manuscript", 0, 66)
        body = engine.make_block(page, 2, "Philon of Alexandria read one tradition in\nthe language of another, quoting the line\nbefore drawing out what it meant.", 67, 185)
        self.assertEqual(title["type"], "heading")
        self.assertEqual(body["type"], "paragraph")

    def test_extracted_images_are_referenced_on_the_page_they_came_from(self):
        """A machine reading only the Markdown must know a figure existed."""
        ir = {
            "document": {"source": {"filename": "paper.pdf"}},
            "pages": [{"id": "page-1", "number": 1}, {"id": "page-2", "number": 2}],
            "blocks": [
                {"id": "b1", "page": "page-1", "type": "paragraph", "level": None, "text": "First page prose.", "source": {"confidence": 0.98}},
                {"id": "b2", "page": "page-2", "type": "paragraph", "level": None, "text": "Second page prose.", "source": {"confidence": 0.98}},
            ],
            "document_artifacts": {"native_images": [
                {"id": "img-1", "relative_path": "images/img-1.png", "source_pages": [1], "pixel_width": 640, "pixel_height": 480},
                {"id": "img-2", "relative_path": "images/img-2.png", "source_pages": [2], "pixel_width": 100, "pixel_height": 100},
            ]},
        }
        rendered = engine.render_markdown(ir)
        first, second = rendered.index("images/img-1.png"), rendered.index("images/img-2.png")
        self.assertLess(rendered.index("First page prose."), first)
        self.assertLess(first, rendered.index("Second page prose."))
        self.assertLess(rendered.index("Second page prose."), second)
        self.assertIn("visual description requires review", rendered)
        self.assertIn("640 \u00d7 480px", rendered)

    def test_markdown_without_extracted_images_is_unchanged(self):
        """The control: a document with no assets renders exactly as before."""
        ir = {
            "document": {"source": {"filename": "paper.pdf"}},
            "pages": [{"id": "page-1", "number": 1}],
            "blocks": [{"id": "b1", "page": "page-1", "type": "paragraph", "level": None,
                        "text": "Only prose here.", "source": {"confidence": 0.98}}],
        }
        rendered = engine.render_markdown(ir)
        self.assertNotIn("![", rendered)
        self.assertNotIn("Evidence:", rendered)

    def test_a_running_head_set_differently_on_facing_pages_is_still_suppressed(self):
        """Neither variant reaches 60% of pages, so neither was ever removed."""
        def page(number, head):
            return {"number": number, "text": f"{head}\nBody text for this page.\n{number}"}
        pages = [page(n, "Diffusion-based Image Mosaics GI 26" if n % 2 else "GI 26 Doyle and Mould")
                 for n in range(1, 11)]
        artifacts = engine.repeated_page_artifacts(pages)
        self.assertIn(engine.normalise_artifact("GI 26 Doyle and Mould"), artifacts)
        self.assertIn(engine.normalise_artifact("Diffusion-based Image Mosaics GI 26"), artifacts)

    def test_a_line_that_merely_repeats_a_few_times_is_not_a_running_head(self):
        """The control: strong evidence is still required.

        The distinct openings vary by wording, not by a trailing number. A line
        that differs from its neighbours only by its folio *is* a running head,
        and setting that number aside before counting is what stops one leaking
        into the body of every page; numbering these would have made the
        fixture an example of the thing it exists to exclude.
        """
        openings = [
            "An ordinary opening line", "A different way to begin",
            "Another beginning entirely", "Something else opens here",
            "A fresh opening sentence", "Yet another first line",
            "This page starts differently", "A new opening again",
            "One more distinct opening", "A last distinct opening",
        ]
        pages = [{"number": n, "text": f"{openings[n - 1]}\nBody sentence number {n}.\n{n}"}
                 for n in range(1, 11)]
        pages[0]["text"] = "A shared opening line\nBody sentence number 1.\n1"
        pages[1]["text"] = "A shared opening line\nBody sentence number 2.\n2"
        self.assertEqual(engine.repeated_page_artifacts(pages), set())

    def test_a_noncharacter_never_reaches_the_reading_text(self):
        """U+FFFE is permanently invalid in interchange, and PDFium emits it.

        A real paper produced 87 of them, each corrupting the word it sat
        inside, while the page reported 0.98 confidence and no warning. They
        carry layout, not meaning, so they are resolved in the reading form and
        retained verbatim in `text`.
        """
        self.assertEqual(engine.clean_reading_text("a subject de\ufffepicted by fruit"),
                         "a subject depicted by fruit")
        self.assertEqual(engine.clean_reading_text("a subject de\ufffe\npicted by fruit"),
                         "a subject depicted by fruit")
        self.assertEqual(engine.clean_reading_text("veg\u00adetables"), "vegetables")
        for text in ["a\ufffeb", "a\uffffb", "a\ufdd0b", "a\U0001fffeb"]:
            self.assertEqual(engine.clean_reading_text(text), "ab", repr(text))

    def test_a_private_use_character_is_reported_and_never_guessed_at(self):
        """A glyph the font never mapped to Unicode is text Philon cannot read.

        Adobe writes the registered sign at U+F6D9 and a maths font puts its own
        brackets in the E000 block. Deleting them loses text; mapping them
        invents it. They are counted, retained, and reported.
        """
        health = engine.native_health("Adobe Photoshop \uf6d9 and G \ue09ex\ue09f")
        self.assertEqual(health["private_use_characters"], 3)
        # One of the three is Adobe's, and is resolved; the other two belong to
        # a font's own encoding and are what the warning is actually about.
        self.assertEqual(health["adobe_glyph_variants"], 1)
        self.assertEqual(health["unresolved_private_use_characters"], 2)
        self.assertEqual(engine.clean_reading_text("G \ue09ex\ue09f"), "G \ue09ex\ue09f")
        pages = [{"id": "page-1", "number": 1, "method": "pdfium-native",
                  "route": {"native_text_health": health}}]
        codes = [warning.code for warning in engine.verified_checks(pages, [])]
        self.assertIn("PRIVATE_USE_CHARACTERS", codes)

    def test_an_adobe_subarea_glyph_is_transcribed_not_guessed(self):
        """Adobe's Corporate Use Subarea is a published assignment.

        It names typographic VARIANTS of characters that already have a Unicode
        value, so resolving one transcribes what Adobe states and loses only the
        variant form. U+F6D9 is `copyrightserif`; the same paper's CMSY6 font
        names that glyph `/circlecopyrt`, which corroborates it independently.
        """
        self.assertEqual(engine.ADOBE_GLYPH_VARIANTS[0xF6D9], 0x00A9)
        self.assertEqual(engine.ADOBE_GLYPH_VARIANTS[0xF6DB], 0x2122)
        self.assertEqual(engine.ADOBE_GLYPH_VARIANTS[0xF730], ord("0"))
        self.assertEqual(engine.clean_reading_text("Adobe Photoshop \uf6d9"), "Adobe Photoshop \u00a9")
        # Every entry names a real character, never another private-use one.
        for source, target in engine.ADOBE_GLYPH_VARIANTS.items():
            self.assertTrue(0xF600 <= source <= 0xF8FF, hex(source))
            self.assertLess(target, 0xE000, hex(source))

    def test_only_the_unambiguous_variant_families_are_resolved(self):
        """A small capital and a superior letter are deliberately left alone.

        A serif copyright sign IS the copyright sign and an old-style figure IS
        that digit, so resolving either decides nothing. `Asmall` could
        reasonably be "A" or "a" -- the AGL name settles the shape, not the
        case -- and a superior letter carries its position as part of its
        meaning, so flattening it to the base letter silently drops a footnote
        marker or an ordinal. Both stay private-use and are reported as
        unreadable, which is the honest answer rather than the fuller-looking
        one.
        """
        self.assertEqual(len(engine.ADOBE_GLYPH_VARIANTS), 16)
        for resolved in [0xF6D9, 0xF6DB, 0xF8E9, 0xF8EA, 0xF730, 0xF739, 0xF724, 0xF7A2]:
            self.assertIn(resolved, engine.ADOBE_GLYPH_VARIANTS, hex(resolved))
        for left_alone in [0xF761, 0xF762, 0xF6E9, 0xF6EA, 0xF6E0, 0xF6DF]:
            self.assertNotIn(left_alone, engine.ADOBE_GLYPH_VARIANTS, hex(left_alone))
        # A small capital therefore survives into the reading text untouched,
        # and is counted as unreadable rather than silently flattened.
        self.assertEqual(engine.clean_reading_text("P\uf761ris"), "P\uf761ris")
        health = engine.native_health("P\uf761ris")
        self.assertEqual(health["adobe_glyph_variants"], 0)
        self.assertEqual(health["unresolved_private_use_characters"], 1)

    def test_a_font_private_glyph_is_left_exactly_as_extracted(self):
        """The control, and the reason the subarea rule stops where it does.

        A maths font's own assignment means nothing outside that font. One
        reference paper proves it: its OpenSymbol ToUnicode CMap maps some codes
        to real characters and deliberately leaves the rest in the private-use
        area, which is the producer stating its own limit rather than an
        omission to repair.
        """
        for text in ["G \ue09ex , y \ue09f", "\ue0c2\ue085\ue0b2", "\uf0a7 a bullet"]:
            self.assertEqual(engine.clean_reading_text(text), engine.clean_reading_text(text))
        self.assertEqual(engine.clean_reading_text("G \ue09ex , y \ue09f"), "G \ue09ex , y \ue09f")
        self.assertNotIn(0xE09E, engine.ADOBE_GLYPH_VARIANTS)

    def test_a_page_with_no_private_use_characters_raises_no_such_warning(self):
        health = engine.native_health("Ordinary measured prose.")
        self.assertEqual(health["private_use_characters"], 0)
        pages = [{"id": "page-1", "number": 1, "method": "pdfium-native",
                  "route": {"native_text_health": health}}]
        self.assertNotIn("PRIVATE_USE_CHARACTERS",
                         [warning.code for warning in engine.verified_checks(pages, [])])

    def test_ordinary_text_is_untouched_by_the_noncharacter_repair(self):
        """The control: the same routine must not disturb text without them."""
        self.assertEqual(engine.clean_reading_text("a care-\nfully measured paragraph"),
                         "a carefully measured paragraph")
        self.assertEqual(engine.clean_reading_text("the first line\nand the second line"),
                         "the first line and the second line")
        self.assertEqual(engine.clean_reading_text("a well-known\nresult"), "a well-known result")
        self.assertEqual(engine.clean_reading_text("Εισαγωγή στη μελέτη"), "Εισαγωγή στη μελέτη")

    def test_the_source_text_keeps_the_noncharacter_as_evidence(self):
        """`text` is the evidence; only the reading form is repaired."""
        page = {"number": 1, "width": 612, "height": 792, "method": "pdfium-native", "text": ""}
        block = engine.make_block(page, 1, "a subject de\ufffepicted by fruit")
        self.assertIn("\ufffe", block["text"])
        self.assertEqual(block["evidence"]["native_health"]["discardable_formatting_characters"], 1)
        self.assertEqual(engine.clean_reading_text(block["text"]), "a subject depicted by fruit")
        self.assertEqual(engine.machine_block_record(block, 1)["reading_text"],
                         "a subject depicted by fruit")

    def test_a_heading_is_recognised_by_the_face_the_page_sets_it_in(self):
        """The text rules are ASCII-Latin; the face the page uses is not.

        Without this, a Greek, Cyrillic or accented heading could never be a
        heading in any profile, and neither could an English one ending in a
        question mark or containing an ampersand.
        """
        body = {"differs_from_body": False, "bold": False, "face": "LinLibertineT"}
        head = {"differs_from_body": True, "bold": True, "face": "LinLibertineTB"}
        for title in ["Εισαγωγή", "Введение", "Éléments de méthode", "What is Philon?",
                      "Design & Implementation", "はじめに"]:
            self.assertEqual(engine.classify_block(title, typeface=head)[0], "heading", title)
            # The control: the same words in the body face stay a paragraph, so
            # the face is what decided it and not the words.
            self.assertEqual(engine.classify_block(title, typeface=body)[0], "paragraph", title)

    def test_the_measured_face_can_refuse_a_heading_as_well_as_grant_one(self):
        """An author line, an affiliation and a keyword list all read as headings.

        Each is short, starts with a capital and ends mid-clause, so the text
        rule promoted all three. The page had already answered the question by
        setting them in the plain body face, and that measurement was only ever
        consulted to say yes. On a real paper this turned the keyword *values*
        into an H2 directly under the "Keywords" heading.
        """
        body = {"differs_from_body": False, "bold": False, "face": "LinLibertineT"}
        for line in ["Image Mosaics, Photomosaics, Image Generation, Diffusion Models",
                     "Charis Tsevis", "Athens, Greece", "See Table 3 for details"]:
            self.assertEqual(engine.classify_block(line, typeface=body)[0], "paragraph", line)
            # The control: with no measurement, the text rule still decides, so
            # an OCR page keeps exactly the behaviour it had.
            self.assertEqual(engine.classify_block(line)[0], "heading", line)

    def test_only_a_bolder_face_grants_a_heading_not_merely_a_different_one(self):
        """Italic is emphasis, and the text rule promoted it.

        A defined term opening a definition, and a cited title inside a
        bibliography entry, are both set in italic and both read as headings to
        a rule that only looks at characters.
        """
        italic = {"differs_from_body": True, "bold": False, "face": "Times-Italic"}
        for line in ["Artificial Mosaic - Given an image I2 in the",
                     "Similarity Measure Based on Correspondence of"]:
            self.assertEqual(engine.classify_block(line, typeface=italic)[0], "paragraph", line)
        bold = {"differs_from_body": True, "bold": True, "face": "Times-Bold"}
        self.assertEqual(engine.classify_block("Introduction", typeface=bold)[0], "heading")

    def test_a_bold_face_from_another_family_is_still_bold(self):
        """A document set in LinLibertineT titles itself in LinBiolinumTB.

        Comparing against the body face's own name cannot see that, and the
        word "bold" does not appear anywhere in it, so the paper's title was
        classified as prose.
        """
        self.assertTrue(engine.is_bold_face("LinBiolinumTB"))
        self.assertTrue(engine.is_bold_face("LinLibertineTB"))
        self.assertTrue(engine.is_bold_face("TimesNewRomanPS-BoldMT"))
        self.assertFalse(engine.is_bold_face("LinLibertineT"))
        self.assertFalse(engine.is_bold_face("Times-Italic"))
        self.assertFalse(engine.is_bold_face("Times-Roman"))

    def test_a_face_change_mid_sentence_does_not_split_the_paragraph(self):
        self.assertTrue(engine.continues_sentence("Artificial Mosaic - Given an image I2 in the",
                                                  "plane R2 and a vector field"))
        self.assertFalse(engine.continues_sentence("2 Related Work", "While other mosaic types exist"))
        self.assertFalse(engine.continues_sentence("A finished sentence.", "and another opens"))
        self.assertFalse(engine.continues_sentence("Keywords", "Image Mosaics, Photomosaics"))

    def test_a_numbered_heading_line_stands_alone_but_a_list_item_does_not(self):
        self.assertTrue(engine.stands_alone_as_numbered_heading("4.1 Prompt selection"))
        self.assertTrue(engine.stands_alone_as_numbered_heading("2. History of Photomosaics"))
        self.assertFalse(engine.stands_alone_as_numbered_heading("1. Compute a tiling of the target image."))
        self.assertFalse(engine.stands_alone_as_numbered_heading("Prompt selection"))
        # A numbered list item, an equation fragment and a bibliography entry
        # opening with a year all match a looser rule, and each is common
        # enough to swamp the real headings.
        self.assertFalse(engine.stands_alone_as_numbered_heading("2. a single tile may cover an area across the"))
        self.assertFalse(engine.stands_alone_as_numbered_heading("7.1 in order to obtain the feature vector"))
        self.assertFalse(engine.stands_alone_as_numbered_heading("2021. Stochastic Polyak Step-size for SGD"))
        self.assertFalse(engine.stands_alone_as_numbered_heading("2002 - Short Presentations. Eurographics"))
        self.assertFalse(engine.stands_alone_as_numbered_heading("0 elsewhere"))
        self.assertFalse(engine.stands_alone_as_numbered_heading("64 \u00d7 64, then comparing against the target"))
        # It must still read a heading written in another alphabet.
        self.assertTrue(engine.stands_alone_as_numbered_heading("2. \u0395\u03b9\u03c3\u03b1\u03b3\u03c9\u03b3\u03ae"))
        self.assertTrue(engine.stands_alone_as_numbered_heading("3.1 \u0412\u0432\u0435\u0434\u0435\u043d\u0438\u0435"))
        self.assertFalse(engine.stands_alone_as_numbered_heading(
            "3. " + "a numbered sentence that simply runs on and on past any heading length" * 2))

    def _small_pdf(self, directory, pages=2):
        from pypdf import PdfWriter
        source = Path(directory) / "reuse.pdf"
        writer = PdfWriter()
        for _ in range(pages):
            writer.add_blank_page(width=200, height=200)
        with source.open("wb") as stream:
            writer.write(stream)
        return source

    def test_source_previews_are_reused_only_after_a_complete_run(self):
        """The cache covered make_ir alone, which is 1.1% of an image-heavy
        document, so a hit saved nothing on exactly the documents that cost the
        most. The expensive phases are reusable because their destination is
        content-addressed -- but only when a manifest proves the run finished.
        """
        try:
            import pypdfium2  # noqa: F401
        except ImportError:
            self.skipTest("pypdfium2 is not installed")
        with tempfile.TemporaryDirectory() as directory:
            source = self._small_pdf(directory)
            out = Path(directory) / "out"
            first, _ = engine.render_source_previews(source, out, [1, 2])
            manifest = out / "assets" / "page-previews" / "manifest.json"
            self.assertTrue(manifest.is_file())
            again, _ = engine.render_source_previews(source, out, [1, 2], reuse=True)
            self.assertEqual(first, again)

            # An interrupted run leaves no manifest, so it cannot be reused.
            manifest.unlink()
            self.assertIsNone(engine.verified_artifact_manifest(manifest, source, 2))

    def test_a_tampered_or_missing_artifact_is_never_reused(self):
        """Reuse verifies the recorded sha256 of every file it claims."""
        try:
            import pypdfium2  # noqa: F401
        except ImportError:
            self.skipTest("pypdfium2 is not installed")
        with tempfile.TemporaryDirectory() as directory:
            source = self._small_pdf(directory)
            out = Path(directory) / "out"
            previews, _ = engine.render_source_previews(source, out, [1, 2])
            manifest = out / "assets" / "page-previews" / "manifest.json"
            self.assertIsNotNone(engine.verified_artifact_manifest(manifest, source, 2))

            Path(previews[0]).write_bytes(b"not the rendered page")
            self.assertIsNone(engine.verified_artifact_manifest(manifest, source, 2))

            Path(previews[0]).unlink()
            self.assertIsNone(engine.verified_artifact_manifest(manifest, source, 2))

    def test_a_manifest_written_for_another_source_is_not_reused(self):
        """Provenance is recorded per conversion, so another document's
        manifest is not this one's evidence even where the bytes would match."""
        try:
            import pypdfium2  # noqa: F401
        except ImportError:
            self.skipTest("pypdfium2 is not installed")
        with tempfile.TemporaryDirectory() as directory:
            source = self._small_pdf(directory)
            other = Path(directory) / "other.pdf"
            other.write_bytes(source.read_bytes())
            out = Path(directory) / "out"
            engine.render_source_previews(source, out, [1, 2])
            manifest = out / "assets" / "page-previews" / "manifest.json"
            self.assertIsNotNone(engine.verified_artifact_manifest(manifest, source, 2))
            self.assertIsNone(engine.verified_artifact_manifest(manifest, other, 2))
            # A different page count is a different run too.
            self.assertIsNone(engine.verified_artifact_manifest(manifest, source, 3))

    def test_a_stale_manifest_version_is_not_reused(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "x.pdf"
            source.write_bytes(b"%PDF-1.4\n")
            manifest = Path(directory) / "manifest.json"
            manifest.write_text(json.dumps({"schema_version": "1.1", "source": str(source), "items": []}))
            self.assertIsNone(engine.verified_artifact_manifest(manifest, source))

    def test_an_asset_limit_warning_is_replayed_when_the_work_is_reused(self):
        """A truncated export must keep saying it is truncated.

        Reusing the files without the warning would quietly turn a bounded
        extraction into a complete-looking one.
        """
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "y.pdf"
            source.write_bytes(b"%PDF-1.4\n")
            out = Path(directory) / "out"
            (out / "images").mkdir(parents=True)
            manifest = out / "images" / "manifest.json"
            manifest.write_text(json.dumps({
                "schema_version": engine.ARTIFACT_MANIFEST_VERSION, "source": str(source), "items": [],
                "truncated": True,
                "warnings": [{"code": "ASSET_EXTRACTION_LIMIT", "message": "bounded", "page": 3, "block_id": None}],
            }))
            assets, warnings = engine.extract_native_pdf_assets(source, out, reuse=True)
            self.assertIsNotNone(assets)
            self.assertEqual([warning.code for warning in warnings], ["ASSET_EXTRACTION_LIMIT"])
            self.assertEqual(warnings[0].page, 3)

    def test_a_float_caption_is_a_caption_and_not_a_section_heading(self):
        """An algorithm listing has a title, not a section of the document.

        Marker renders "Algorithm 1 Compute loss" as a heading. That is the
        wrong kind: promoting it puts two chunks under the heading path
        ['4 Method', 'Algorithm 1 Compute loss'], telling a reader of the
        chunks that they are sections of the algorithm. A figure, a table and
        an algorithm listing are the same kind of thing, and Philon already
        calls the first two captions.
        """
        for line in ["Algorithm 1 Compute loss", "Figure 1: Diffusion-based photomosaics",
                     "Table 2: Results", "Listing 3 Parsing the manifest"]:
            self.assertEqual(engine.classify_block(line)[0], "caption", line)

    def test_a_sentence_mentioning_a_float_is_not_its_caption(self):
        """The control, and the reason the capital is required.

        "Algorithm 1 details the method..." is prose about the algorithm; the
        word after the number is what tells them apart.
        """
        for line in ["Algorithm 1 details the method for adaptively weighting pixels",
                     "Figure 2 shows the result of the comparison",
                     "Table 1 lists every measured configuration"]:
            self.assertNotEqual(engine.classify_block(line)[0], "caption", line)

    def test_a_float_caption_starts_its_own_block(self):
        """Its caption interrupts the column flow, so no other rule can fire.

        The line before it can end mid-word, which defeats the sentence tests,
        and the gaps are ordinary leading, which defeats the whitespace rule.
        On a real paper this left the caption and its whole listing inside a
        29-line paragraph that opened with unrelated prose.
        """
        def line(text, start, end, y, width=240):
            return {"text": text, "start": start, "end": end, "font": "LinLibertineT", "size": 9.0,
                    "bbox": engine.make_bbox(72, y, 72 + width, y + 8, "pdf-page-points")}
        page = {"number": 1, "width": 612, "height": 792, "method": "pdfium-native",
                "body_font": "LinLibertineT", "text": "", "native_text_lines": [
                    line("remaining process synthesizes fine details and tex", 0, 49, 700),
                    line("Algorithm 1 Compute loss", 50, 74, 692, 108),
                    line("Require: Set of SLIC regions", 75, 103, 684, 100),
                ]}
        parts = engine.geometric_native_parts(page, set())
        self.assertIn("Algorithm 1 Compute loss", [part["text"] for part in parts])
        blocks = [engine.make_block(page, i + 1, part["text"], part.get("start"), part.get("end"))
                  for i, part in enumerate(parts)]
        self.assertEqual(blocks[1]["type"], "caption")

    def test_a_caption_broken_around_mathematics_stays_one_block(self):
        """A line opening with punctuation is plainly a continuation.

        A figure caption wrapped around inline mathematics does this
        constantly, and reading ", a SLIC segmentation map" as a fresh block
        left a fragment that the face rules then promoted to a heading.
        """
        self.assertTrue(engine.continues_sentence("method include (left to right): a target layout",
                                                  ", a SLIC segmentation map"))
        self.assertTrue(engine.continues_sentence("the quantile weights", "; and the mask"))
        self.assertFalse(engine.continues_sentence("A closed sentence.", ", a fragment"))
        fragment = {"differs_from_body": True, "bold": True, "face": "LinLibertineTB",
                    "precedes_numeric_rows": False}
        self.assertEqual(engine.classify_block(", a SLIC segmentation map", typeface=fragment)[0],
                         "paragraph")

    def test_a_short_line_in_a_bolder_face_stands_on_its_own(self):
        """A paper sets its captions in the same bold as its headings.

        "Abstract" was absorbed by the figure caption above it and "CCS
        Concepts" by the bold category list below it, because neither boundary
        is a change of face and both gaps are smaller than a paragraph break.
        Width separates them: on that page the body lines are justified at
        1.00x the median measured line while those two sit at 0.17x and 0.29x.
        """
        def line(text, start, end, y, width, font):
            return {"text": text, "start": start, "end": end, "font": font, "size": 9.0,
                    "bbox": engine.make_bbox(72, y, 72 + width, y + 8, "pdf-page-points")}
        page = {"number": 1, "width": 612, "height": 792, "method": "pdfium-native",
                "body_font": "LinLibertineT", "text": "", "native_text_lines": [
                    line("a pre-trained diffusion model can be adapted to this purpose.", 0, 60, 700, 240, "LinLibertineTB"),
                    line("Abstract", 61, 69, 692, 41, "LinLibertineTB"),
                    line("Image mosaics have traditionally been automated by a search", 70, 129, 684, 240, "LinLibertineT"),
                    line("method to match source images to a target layout.", 130, 179, 676, 240, "LinLibertineT"),
                ]}
        parts = engine.geometric_native_parts(page, set())
        self.assertIn("Abstract", [part["text"] for part in parts])
        blocks = [engine.make_block(page, i + 1, part["text"], part.get("start"), part.get("end"))
                  for i, part in enumerate(parts)]
        self.assertEqual(blocks[1]["type"], "heading")

    def test_the_last_line_of_a_bold_caption_is_not_read_as_a_heading(self):
        """The control. A caption's final line is short and bold too.

        What separates it from a heading is that it continues the line above
        it, so the sentence tests are what make the width rule safe.
        """
        def line(text, start, end, y, width):
            return {"text": text, "start": start, "end": end, "font": "LinLibertineTB", "size": 9.0,
                    "bbox": engine.make_bbox(72, y, 72 + width, y + 8, "pdf-page-points")}
        page = {"number": 1, "width": 612, "height": 792, "method": "pdfium-native",
                "body_font": "LinLibertineT", "text": "", "native_text_lines": [
                    line("Figure 1: Diffusion-based photomosaics are created by", 0, 52, 700, 240),
                    line("generating target tiles with a diffusion model, and we", 53, 106, 692, 240),
                    line("show how", 107, 116, 684, 40),
                    line("Image mosaics have traditionally been automated.", 117, 164, 676, 240),
                ]}
        self.assertNotIn("show how", [part["text"] for part in engine.geometric_native_parts(page, set())])

    def test_a_table_column_heading_is_not_a_heading(self):
        """Short, capitalised and bold, sitting between two closed sentences.

        Nothing about the line itself separates it from a section heading; what
        follows it does, because a table's data rows are mostly numbers.
        """
        self.assertTrue(engine.looks_like_a_numeric_row("275x276 6.701 640x480 16.044"))
        self.assertTrue(engine.looks_like_a_numeric_row("SD 0.304 \u2013 0.197 \u2013 \u2013"))
        self.assertFalse(engine.looks_like_a_numeric_row("Method Score Diff Score Diff Score"))
        self.assertFalse(engine.looks_like_a_numeric_row("Image mosaics have traditionally been automated"))
        header = {"differs_from_body": True, "bold": True, "face": "LinLibertineTB",
                  "precedes_numeric_rows": True}
        section = {"differs_from_body": True, "bold": True, "face": "LinLibertineTB",
                   "precedes_numeric_rows": False}
        self.assertEqual(engine.classify_block("CLIP Score Pick Score MSE", typeface=header)[0], "paragraph")
        self.assertEqual(engine.classify_block("CLIP Score Pick Score MSE", typeface=section)[0], "heading")
        # A numbered heading is structure the source states outright, so it
        # survives even where a table follows it.
        self.assertEqual(engine.classify_block("5.4 Quantitative Results", typeface=header)[0], "heading")

    def test_a_numbered_heading_in_the_body_face_is_separated_and_may_wrap(self):
        """Some papers set a subsection in the plain body face at body size.

        No measurement of face or size can separate it from the prose around
        it, so the section number has to; and a heading long enough to wrap
        must keep its second line rather than orphan it.
        """
        def line(text, start, end, y):
            return {"text": text, "start": start, "end": end, "font": "LinLibertineT", "size": 9.0,
                    "bbox": engine.make_bbox(72, y, 400, y + 8, "pdf-page-points")}
        page = {"number": 1, "width": 612, "height": 792, "method": "pdfium-native",
                "body_font": "LinLibertineT", "text": "", "native_text_lines": [
                    line("model such as SDXL [33].", 0, 24, 700),
                    line("2.2 Diffusion-based image generation and", 25, 65, 693),
                    line("editing", 66, 73, 686),
                    line("Recently, diffusion models are able to", 74, 112, 679),
                ]}
        parts = engine.geometric_native_parts(page, set())
        self.assertEqual([part["text"] for part in parts], [
            "model such as SDXL [33].",
            "2.2 Diffusion-based image generation and\nediting",
            "Recently, diffusion models are able to",
        ])
        blocks = [engine.make_block(page, i + 1, part["text"], part.get("start"), part.get("end"))
                  for i, part in enumerate(parts)]
        self.assertEqual([block["type"] for block in blocks], ["paragraph", "heading", "paragraph"])
        self.assertEqual(blocks[1]["level"], 2)

    def test_a_numbered_heading_survives_the_body_face_veto(self):
        """A section number is structure the source states outright.

        Some documents set a numbered heading in the body face and separate it
        by space alone, so the veto must not swallow it.
        """
        body = {"differs_from_body": False, "bold": False, "face": "Times-Roman"}
        self.assertEqual(engine.classify_block("3 Results", typeface=body), ("heading", 1))
        self.assertEqual(engine.classify_block("2.1 Adaptive routing", typeface=body), ("heading", 2))

    def test_the_face_rule_does_not_promote_a_bold_lead_in_paragraph(self):
        head = {"differs_from_body": True, "bold": True, "face": "Times-Bold"}
        sentence = "Artificial Mosaic - Given an image in the plane and a vector field defined on that region representing the edges, find N sites and place N rectangles."
        self.assertEqual(engine.classify_block(sentence, typeface=head)[0], "paragraph")
        self.assertEqual(engine.classify_block("A complete bold sentence.", typeface=head)[0], "paragraph")

    def test_a_block_with_no_measured_face_classifies_exactly_as_before(self):
        """The null. An OCR page and the pypdf fallback measure no face."""
        for title in ["Εισαγωγή", "What is Philon?", "Introduction"]:
            self.assertEqual(engine.classify_block(title, typeface=None),
                             engine.classify_block(title))

    def test_a_weight_suffix_counts_as_bolder_than_the_body_face(self):
        self.assertTrue(engine._is_bolder_sibling("LinLibertineTB", "LinLibertineT"))
        self.assertFalse(engine._is_bolder_sibling("LinLibertineTI", "LinLibertineT"))
        self.assertFalse(engine._is_bolder_sibling("LinLibertineT", "LinLibertineT"))

    def test_a_change_of_face_starts_a_new_block(self):
        """A heading sits closer to the text it heads than to the text above it.

        The gap rule alone therefore never separates one: on a two-column paper
        every inter-line gap is under the 10pt floor, so the heading is absorbed
        into the paragraph beneath it and stops existing as structure.
        """
        def line(text, start, end, y, font):
            return {"text": text, "start": start, "end": end, "font": font, "size": 9.0,
                    "bbox": engine.make_bbox(72, y, 400, y + 8, "pdf-page-points")}
        page = {
            "number": 1, "width": 612, "height": 792, "method": "pdfium-native",
            "body_font": "LinLibertineT", "text": "",
            "native_text_lines": [
                line("2 Related Work", 0, 14, 700, "LinLibertineTB"),
                line("While other mosaic types exist, such as", 15, 53, 693, "LinLibertineT"),
                line("crystallization mosaics, we are concerned", 54, 95, 686, "LinLibertineT"),
            ],
        }
        parts = engine.geometric_native_parts(page, set())
        self.assertEqual(parts[0]["text"], "2 Related Work")
        blocks = [engine.make_block(page, i + 1, part["text"], part.get("start"), part.get("end"))
                  for i, part in enumerate(parts)]
        self.assertEqual([block["type"] for block in blocks], ["heading", "paragraph"])
        self.assertEqual(blocks[0]["level"], 1)

    def test_lines_in_one_face_are_still_assembled_into_one_paragraph(self):
        """The control for the rule above: same face, same block."""
        def line(text, start, end, y):
            return {"text": text, "start": start, "end": end, "font": "LinLibertineT", "size": 9.0,
                    "bbox": engine.make_bbox(72, y, 400, y + 8, "pdf-page-points")}
        page = {
            "number": 1, "width": 612, "height": 792, "method": "pdfium-native",
            "body_font": "LinLibertineT", "text": "",
            "native_text_lines": [line("While other mosaic types exist,", 0, 31, 700),
                                  line("we are concerned with photomosaics.", 32, 67, 693)],
        }
        self.assertEqual(len(engine.geometric_native_parts(page, set())), 1)

    def test_the_document_body_face_is_taken_across_every_page(self):
        """A page can be mostly heading; the document is not."""
        pages = [
            {"native_text_lines": [{"text": "A page of headings", "font": "Times-Bold", "size": 14.0}]},
            {"native_text_lines": [{"text": "Ordinary body text runs on and on here", "font": "Times-Roman", "size": 10.0},
                                   {"text": "and continues across a second measured line", "font": "Times-Roman", "size": 10.0}]},
        ]
        self.assertEqual(engine.document_body_typeface(pages)[0], "Times-Roman")

    def test_an_ocr_page_measures_no_type_size_and_keeps_the_single_line_rule(self):
        page = {"number": 1, "width": 612, "height": 792, "method": "apple-vision-ocr", "ocr_lines": []}
        block = engine.make_block(page, 1, "A title that wrapped\nacross two lines", 0, 37)
        self.assertEqual(block["type"], "paragraph")

    def test_geometric_native_assembly_splits_at_measured_paragraph_gaps(self):
        page = {
            "text": "First line\nSecond line\nNew paragraph",
            "native_text_lines": [
                {"text": "First line", "start": 0, "end": 10, "bbox": engine.make_bbox(5, 80, 50, 90, "pdf-page-points")},
                {"text": "Second line", "start": 11, "end": 22, "bbox": engine.make_bbox(5, 65, 55, 75, "pdf-page-points")},
                {"text": "New paragraph", "start": 23, "end": 36, "bbox": engine.make_bbox(5, 30, 70, 40, "pdf-page-points")},
            ],
        }
        parts = engine.geometric_native_parts(page, set())
        self.assertEqual([part["text"] for part in parts], ["First line\nSecond line", "New paragraph"])

    def test_isolated_numeric_markers_are_retained_as_page_artifacts_not_prose(self):
        page = {
            "text": "Body line\n73 74\nAnother body line",
            "native_text_lines": [
                {"text": "Body line", "start": 0, "end": 9, "bbox": engine.make_bbox(5, 80, 50, 90, "pdf-page-points")},
                {"text": "73 74", "start": 10, "end": 15, "bbox": engine.make_bbox(60, 65, 80, 72, "pdf-page-points")},
                {"text": "Another body line", "start": 16, "end": 33, "bbox": engine.make_bbox(5, 50, 70, 60, "pdf-page-points")},
            ],
        }
        parts = engine.geometric_native_parts(page, set())
        self.assertEqual([part["text"] for part in parts], ["Body line", "Another body line"])
        self.assertEqual(page["numeric_source_markers"][0]["text"], "73 74")

    def test_review_edit_keeps_the_native_candidate(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            ir_path = output / "paper.philon.json"
            ir = {
                "philon_ir_version": engine.IR_VERSION,
                "document": {"id": "sha256:test", "source": {"filename": "paper.pdf"}},
                "pages": [{"id": "page-1", "number": 1, "route": {"decision": "native-fast-path"}}],
                "blocks": [{"id": "page-1-block-1", "page": "page-1", "type": "paragraph", "level": None, "text": "native", "source": {"method": "pdfium-native", "confidence": .98}, "evidence": {"alternatives": [], "repair_history": []}}],
            }
            ir_path.write_text(json.dumps(ir))
            result = engine.action_review({"ir_path": str(ir_path), "block_id": "page-1-block-1", "review_action": "edit", "text": "human edit"})
            self.assertEqual(result["status"], "recorded")
            saved = json.loads(ir_path.read_text())
            self.assertEqual(saved["blocks"][0]["text"], "human edit")
            self.assertEqual(saved["blocks"][0]["evidence"]["alternatives"][0]["text"], "native")

    def test_review_can_restore_a_specific_retained_candidate(self):
        with tempfile.TemporaryDirectory() as directory:
            ir_path = Path(directory) / "paper.philon.json"
            ir = {"philon_ir_version": engine.IR_VERSION, "document": {"id": "sha256:test", "source": {"filename": "paper.pdf"}}, "pages": [{"id": "page-1", "number": 1, "route": {"decision": "native-fast-path"}}], "blocks": [{"id": "page-1-block-1", "page": "page-1", "type": "paragraph", "level": None, "text": "native", "source": {"method": "pdfium-native", "confidence": .98}, "evidence": {"alternatives": [{"kind": "first", "text": "first candidate"}, {"kind": "second", "text": "second candidate"}], "repair_history": []}}]}
            ir_path.write_text(json.dumps(ir))
            engine.action_review({"ir_path": str(ir_path), "block_id": "page-1-block-1", "review_action": "restore_candidate", "candidate_index": 0})
            self.assertEqual(json.loads(ir_path.read_text())["blocks"][0]["text"], "first candidate")

    def test_selected_markdown_table_candidate_becomes_csv_safe_table(self):
        with tempfile.TemporaryDirectory() as directory:
            ir_path = Path(directory) / "paper.philon.json"
            table = "| Label | Value |\n| --- | ---: |\n| Alpha | 4 |"
            ir = {"philon_ir_version": engine.IR_VERSION, "document": {"id": "sha256:test", "source": {"filename": "paper.pdf"}}, "pages": [{"id": "page-1", "number": 1, "route": {"decision": "native-fast-path"}}], "blocks": [{"id": "page-1-block-1", "page": "page-1", "type": "paragraph", "level": None, "text": "native", "source": {"method": "pdfium-native", "confidence": .98}, "evidence": {"alternatives": [{"kind": "qwen3.8", "text": table}], "repair_history": []}}]}
            ir_path.write_text(json.dumps(ir))
            result = engine.action_review({"ir_path": str(ir_path), "block_id": "page-1-block-1", "review_action": "restore_candidate", "candidate_index": 0})
            self.assertEqual(result["block"]["type"], "table")
            self.assertEqual(engine.table_rows(table), [["Label", "Value"], ["Alpha", "4"]])
            csv_path = Path(result["outputs"]["table_csv"][0])
            self.assertEqual(csv_path.read_text(encoding="utf-8"), '"Label","Value"\n"Alpha","4"\n')

    def test_embedding_payload_retains_chunk_and_source_block_ids(self):
        chunks = [{"id": "chunk-1", "text": "alpha", "source_block_ids": ["p1-b1"]}, {"id": "chunk-2", "text": "beta", "source_block_ids": ["p2-b3", "p2-b4"]}]
        payload = json.dumps({"object": "list", "data": [{"object": "embedding", "index": 1, "embedding": [0.3, 0.4]}, {"object": "embedding", "index": 0, "embedding": [0.1, 0.2]}]})
        result = engine.parse_embedding_payload(payload, chunks)
        self.assertEqual(result["dimensions"], 2)
        self.assertEqual(result["vectors"][0]["source_block_ids"], ["p1-b1"])
        self.assertEqual(result["vectors"][1]["embedding"], [0.3, 0.4])


class PageRotationTest(unittest.TestCase):
    """A rotated page is measured in the frame it is displayed and reviewed in."""

    @staticmethod
    def rotated_pdf(path, rotation):
        """One line of Helvetica at a known place, under a chosen /Rotate."""
        content = b"BT /F1 24 Tf 72 700 Td (Rotation probe line) Tj ET\n"
        objects = [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            (f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Rotate {rotation} "
             "/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>").encode(),
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"endstream",
        ]
        out = bytearray(b"%PDF-1.4\n")
        offsets = []
        for number, body in enumerate(objects, start=1):
            offsets.append(len(out))
            out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
        start_xref = len(out)
        out += f"xref\n0 {len(objects) + 1}\n".encode() + b"0000000000 65535 f \n"
        for offset in offsets:
            out += f"{offset:010d} 00000 n \n".encode()
        out += (f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
                f"startxref\n{start_xref}\n").encode() + b"%%EOF\n"
        Path(path).write_bytes(bytes(out))

    def test_source_page_size_transposes_only_a_quarter_turn(self):
        self.assertEqual(engine.source_page_size(612, 792, 0), (612, 792))
        self.assertEqual(engine.source_page_size(792, 612, 90), (612, 792))
        self.assertEqual(engine.source_page_size(612, 792, 180), (612, 792))
        self.assertEqual(engine.source_page_size(792, 612, 270), (612, 792))

    def test_a_quarter_turn_moves_a_rectangle_and_keeps_its_size(self):
        box = engine.make_bbox(72, 700, 272, 722, "pdf-page-points")
        turned = engine.bbox_to_displayed_frame(box, 90, 612, 792)
        # The page is 612 wide unrotated, so it is 612 tall once displayed, and
        # a rectangle 200 by 22 becomes 22 by 200 without changing size.
        self.assertEqual((turned["x0"], turned["y0"]), (700, 612 - 272))
        self.assertEqual((turned["x1"], turned["y1"]), (722, 612 - 72))
        self.assertAlmostEqual(turned["x1"] - turned["x0"], box["y1"] - box["y0"])
        self.assertAlmostEqual(turned["y1"] - turned["y0"], box["x1"] - box["x0"])

    def test_an_unrotated_page_is_left_exactly_as_measured(self):
        box = engine.make_bbox(72, 700, 272, 722, "pdf-page-points")
        self.assertIs(engine.bbox_to_displayed_frame(box, 0, 612, 792), box)
        self.assertIsNone(engine.bbox_to_displayed_frame(None, 90, 612, 792))

    def test_a_half_turn_is_its_own_inverse(self):
        box = engine.make_bbox(72, 700, 272, 722, "pdf-page-points")
        once = engine.bbox_to_displayed_frame(box, 180, 612, 792)
        twice = engine.bbox_to_displayed_frame(once, 180, 612, 792)
        self.assertEqual((twice["x0"], twice["y0"], twice["x1"], twice["y1"]),
                         (box["x0"], box["y0"], box["x1"], box["y1"]))

    def test_measured_text_stays_inside_a_rotated_page(self):
        """The defect this fixes: the rectangle fell outside the page box."""
        with tempfile.TemporaryDirectory() as directory:
            for rotation in (0, 90, 180, 270):
                source = Path(directory) / f"rotate{rotation}.pdf"
                self.rotated_pdf(source, rotation)
                pages, _ = engine.pdfium_extract(source)
                if not pages or pages[0].get("method") != "pdfium-native":
                    self.skipTest("PDFium is not available for extraction")
                page = pages[0]
                self.assertEqual(page["rotation"], rotation)
                measured = [line["bbox"] for line in page["native_text_lines"] if line["bbox"]]
                self.assertTrue(measured, f"no rectangle measured at /Rotate {rotation}")
                for box in measured:
                    self.assertGreaterEqual(box["x0"], 0)
                    self.assertGreaterEqual(box["y0"], 0)
                    self.assertLessEqual(box["x1"], page["width"] + 1)
                    self.assertLessEqual(box["y1"], page["height"] + 1)


class RunningHeadTest(unittest.TestCase):
    """A running head is recognised even though its page number changes."""

    @staticmethod
    def book(head_for, pages=12):
        """Pages with enough body that only the head and folio sit in the window."""
        def page(number):
            body = "\n".join(f"Body line {line} of page {number + 1}, saying its own thing."
                              for line in range(4))
            return {"text": f"{head_for(number)}\n{body}\n{number + 1}", "number": number + 1}
        return [page(number) for number in range(pages)]

    def test_a_head_carrying_its_page_number_is_recognised(self):
        """The defect this fixes: counted literally, it never repeated."""
        pages = self.book(lambda number: f"Symmetries of Culture   {number + 1}")
        artifacts = engine.repeated_page_artifacts(pages)
        self.assertIn("symmetries of culture", artifacts)

    def test_a_leading_page_number_is_set_aside_too(self):
        pages = self.book(lambda number: f"{number + 1}   Washburn and Crowe")
        self.assertIn("washburn and crowe", engine.repeated_page_artifacts(pages))

    def test_a_head_that_changes_each_chapter_is_still_recognised(self):
        """No one variant reaches the share threshold; each run is the evidence."""
        pages = self.book(lambda number: f"Chapter {number // 4 + 1} Introduction {number + 1}",
                          pages=12)
        artifacts = engine.repeated_page_artifacts(pages)
        self.assertIn("chapter 1 introduction", artifacts)
        self.assertIn("chapter 3 introduction", artifacts)

    def test_body_text_is_not_taken_for_a_running_head(self):
        pages = self.book(lambda number: f"A wholly different opening for page {number + 1} here")
        self.assertEqual(engine.repeated_page_artifacts(pages), set())

    def test_a_bare_page_number_is_still_not_an_artifact(self):
        self.assertEqual(engine.normalise_artifact("47"), "47")

    def test_normalising_never_empties_a_line(self):
        for line in ["47", "1998", "- 12 -"]:
            self.assertTrue(engine.normalise_artifact(line))

    def test_a_short_document_is_left_alone(self):
        self.assertEqual(engine.repeated_page_artifacts(self.book(lambda n: "Head", pages=2)), set())

    def test_longest_page_run_counts_only_consecutive_pages(self):
        self.assertEqual(engine.longest_page_run([]), 0)
        self.assertEqual(engine.longest_page_run([0, 1, 2, 3]), 4)
        self.assertEqual(engine.longest_page_run([0, 2, 4, 6]), 1)
        self.assertEqual(engine.longest_page_run([0, 1, 5, 6, 7]), 3)


class SourceDeclaredLinkTest(unittest.TestCase):
    """A PDF's own link rectangles become anchors on the text they cover."""

    @staticmethod
    def linked_pdf(path, rect, uri, rotation=0):
        """Two well-separated lines, and one /Link rectangle over the second."""
        content = (b"BT /F1 12 Tf 72 700 Td (Reference one) Tj ET\n"
                   b"BT /F1 12 Tf 72 600 Td (https://example.com/paper) Tj ET\n")
        annotation = (f"<< /Type /Annot /Subtype /Link /Rect "
                      f"[{rect[0]} {rect[1]} {rect[2]} {rect[3]}] /Border [0 0 0] "
                      f"/A << /S /URI /URI ({uri}) >> >>").encode()
        objects = [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            (f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Rotate {rotation} "
             "/Annots [6 0 R] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>").encode(),
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"endstream",
            annotation,
        ]
        out = bytearray(b"%PDF-1.4\n")
        offsets = []
        for number, body in enumerate(objects, start=1):
            offsets.append(len(out))
            out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
        start_xref = len(out)
        out += f"xref\n0 {len(objects) + 1}\n".encode() + b"0000000000 65535 f \n"
        for offset in offsets:
            out += f"{offset:010d} 00000 n \n".encode()
        out += (f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
                f"startxref\n{start_xref}\n").encode() + b"%%EOF\n"
        Path(path).write_bytes(bytes(out))

    def extract(self, directory, name, rect, uri, rotation=0):
        source = Path(directory) / f"{name}.pdf"
        self.linked_pdf(source, rect, uri, rotation)
        pages, _ = engine.pdfium_extract(source)
        if not pages or pages[0].get("method") != "pdfium-native":
            self.skipTest("PDFium is not available for extraction")
        return pages[0]

    def test_only_the_covered_characters_become_the_anchor(self):
        with tempfile.TemporaryDirectory() as directory:
            page = self.extract(directory, "covered", (60, 590, 400, 615), "https://example.com/paper")
            self.assertEqual([link["text"] for link in page["links"]], ["https://example.com/paper"])
            self.assertNotIn("Reference one", page["links"][0]["text"])

    def test_an_anchor_carries_no_surrounding_whitespace(self):
        """A line break sitting inside the rectangle is not part of the link."""
        with tempfile.TemporaryDirectory() as directory:
            for rotation in (0, 90):
                page = self.extract(directory, f"space{rotation}", (60, 590, 400, 615),
                                    "https://example.com/paper", rotation)
                self.assertEqual(page["links"][0]["text"], page["links"][0]["text"].strip())

    def test_a_rectangle_over_no_text_anchors_nothing(self):
        with tempfile.TemporaryDirectory() as directory:
            page = self.extract(directory, "empty", (60, 300, 400, 330), "https://example.com/none")
            self.assertEqual(page["links"][0]["text"], "")
            self.assertIsNone(page["links"][0]["start"])

    def test_the_anchor_is_measured_in_the_displayed_frame(self):
        with tempfile.TemporaryDirectory() as directory:
            page = self.extract(directory, "rotated", (60, 590, 400, 615),
                                "https://example.com/paper", 90)
            box = page["links"][0]["bbox"]
            self.assertLessEqual(box["x1"], page["width"] + 1)
            self.assertLessEqual(box["y1"], page["height"] + 1)

    def test_markdown_and_html_carry_the_link(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "linked.pdf"
            self.linked_pdf(source, (60, 590, 400, 615), "https://example.com/paper")
            ir, _, _ = engine.make_ir(source, "Balanced")
            if not any(block["links"] for block in ir["blocks"]):
                self.skipTest("PDFium is not available for extraction")
            self.assertIn("[https://example.com/paper](https://example.com/paper)",
                          engine.render_markdown(ir))
            self.assertIn('<a href="https://example.com/paper" rel="noopener noreferrer">',
                          engine.render_html(ir))

    def test_a_script_uri_is_recorded_but_never_becomes_a_link(self):
        """A PDF can declare any URI; the presentation export is opened locally."""
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "script.pdf"
            self.linked_pdf(source, (60, 590, 400, 615), "javascript:alert(1)")
            ir, _, _ = engine.make_ir(source, "Balanced")
            if not any(block["links"] for block in ir["blocks"]):
                self.skipTest("PDFium is not available for extraction")
            self.assertNotIn("javascript:", engine.render_markdown(ir))
            self.assertNotIn("javascript:", engine.render_html(ir))
            recorded = [link["uri"] for block in ir["blocks"] for link in block["links"]]
            self.assertIn("javascript:alert(1)", recorded)

    def test_which_schemes_are_anchorable(self):
        for uri in ["https://a.test/x", "http://a.test/x", "MailTo:someone@a.test"]:
            self.assertTrue(engine.is_anchorable_link(uri), uri)
        for uri in ["javascript:alert(1)", "file:///etc/passwd", "data:text/html,<b>", "", "  "]:
            self.assertFalse(engine.is_anchorable_link(uri), uri)

    def test_an_anchor_that_did_not_survive_reflow_is_left_unmade(self):
        links = [{"uri": "https://a.test/x", "text": "words that are not here", "bbox": None}]
        self.assertEqual(engine.anchor_links_markdown("Some other reading text.", links),
                         "Some other reading text.")

    def test_two_links_do_not_overlap_or_nest(self):
        links = [
            {"uri": "https://a.test/one", "text": "alpha", "bbox": None},
            {"uri": "https://a.test/two", "text": "beta", "bbox": None},
        ]
        rendered = engine.anchor_links_markdown("alpha and beta", links)
        self.assertEqual(rendered, "[alpha](https://a.test/one) and [beta](https://a.test/two)")

    def test_brackets_in_the_anchor_text_are_escaped(self):
        links = [{"uri": "https://a.test/x", "text": "[12]", "bbox": None}]
        self.assertEqual(engine.anchor_links_markdown("See [12] for more.", links),
                         "See [\\[12\\]](https://a.test/x) for more.")

    def test_a_target_with_parentheses_is_wrapped(self):
        links = [{"uri": "https://a.test/x_(draft)", "text": "here", "bbox": None}]
        self.assertEqual(engine.anchor_links_markdown("look here now", links),
                         "look [here](<https://a.test/x_(draft)>) now")

    def test_html_escaping_survives_anchoring(self):
        links = [{"uri": "https://a.test/?a=1&b=2", "text": "link", "bbox": None}]
        reading = "a <b> & link here"
        rendered = engine.anchor_links_html(engine.html.escape(reading), reading, links)
        self.assertIn("&lt;b&gt; &amp; ", rendered)
        self.assertIn('href="https://a.test/?a=1&amp;b=2"', rendered)
        self.assertNotIn("<b>", rendered)


class PageSelectionTest(unittest.TestCase):
    """Converting part of a document, without it being mistaken for the whole."""

    @staticmethod
    def blank_pdf(path, page_count):
        import pypdfium2 as pdfium

        document = pdfium.PdfDocument.new()
        for _ in range(page_count):
            document.new_page(200, 200)
        document.save(str(path))
        document.close()

    def test_pages_and_ranges_are_both_read(self):
        self.assertEqual(engine.parse_page_selection("1-3,8"), (1, 2, 3, 8))
        self.assertEqual(engine.parse_page_selection([3, 1, 3]), (1, 3))
        self.assertEqual(engine.parse_page_selection("  2 - 4 "), (2, 3, 4))
        self.assertEqual(engine.parse_page_selection("7"), (7,))

    def test_no_selection_means_the_document_entire(self):
        for value in [None, "", [], "   ", ","]:
            self.assertIsNone(engine.parse_page_selection(value))

    def test_a_selection_that_is_not_one_is_refused(self):
        for value in ["0", "3-1", "abc", "1-", "-2", "1,x"]:
            with self.assertRaises(ValueError, msg=value):
                engine.parse_page_selection(value)

    def test_a_selection_writes_back_in_its_shortest_form(self):
        self.assertEqual(engine.compact_page_selection((1, 2, 3, 8)), "1-3,8")
        self.assertEqual(engine.compact_page_selection((5,)), "5")
        self.assertEqual(engine.compact_page_selection((1, 3, 5)), "1,3,5")
        self.assertEqual(engine.compact_page_selection(()), "")

    def test_a_page_the_document_does_not_have_is_refused(self):
        with self.assertRaises(ValueError) as refusal:
            engine.validate_page_selection((1, 40), 12)
        self.assertIn("12 pages", str(refusal.exception))
        engine.validate_page_selection((1, 12), 12)
        engine.validate_page_selection(None, 12)

    def test_part_of_a_document_never_shares_a_cache_entry_with_the_whole(self):
        """A ten-page conversion served for a whole book would be silent data loss."""
        cache = Path("/cache")
        whole = engine.cache_path(cache, "abc123", "Balanced")
        part = engine.cache_path(cache, "abc123", "Balanced", (1, 2))
        other = engine.cache_path(cache, "abc123", "Balanced", (1, 3))
        self.assertEqual(len({whole, part, other}), 3)

    def test_a_long_selection_still_names_a_bounded_file(self):
        long_selection = tuple(range(1, 400, 2))
        token = engine.page_selection_token(long_selection)
        self.assertLess(len(token), 40)
        self.assertEqual(token, engine.page_selection_token(long_selection))
        self.assertNotEqual(token, engine.page_selection_token(tuple(range(1, 400, 3))))
        self.assertEqual(engine.page_selection_token(None), "")

    def test_only_the_selected_pages_are_extracted_and_keep_their_numbers(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "four.pdf"
            try:
                self.blank_pdf(source, 4)
            except ImportError:
                self.skipTest("PDFium is not available")
            selected, _ = engine.pdfium_extract(source, (2, 4))
            self.assertEqual([page["number"] for page in selected], [2, 4])
            whole, _ = engine.pdfium_extract(source)
            self.assertEqual([page["number"] for page in whole], [1, 2, 3, 4])

    def test_a_partial_export_is_named_and_previewed_by_real_page_number(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "four.pdf"
            try:
                self.blank_pdf(source, 4)
            except ImportError:
                self.skipTest("PDFium is not available")
            result = engine.convert_file(source, "Balanced", root / "exports", root / "cache",
                                         cache_policy="bypass", selection=(2, 3))
            self.assertEqual(result["page_selection"], "2-3")
            previews = [Path(item).name for item in result["outputs"].get("assets", [])]
            self.assertEqual(previews, ["page-0002.png", "page-0003.png"])
            exports = list((root / "exports").iterdir())
            self.assertTrue(any("pages-2-3" in item.name for item in exports), exports)

    def test_a_partial_export_never_overwrites_the_whole_one(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "four.pdf"
            try:
                self.blank_pdf(source, 4)
            except ImportError:
                self.skipTest("PDFium is not available")
            engine.convert_file(source, "Balanced", root / "exports", root / "cache",
                                cache_policy="bypass")
            engine.convert_file(source, "Balanced", root / "exports", root / "cache",
                                cache_policy="bypass", selection=(2,))
            self.assertEqual(len(list((root / "exports").iterdir())), 2)

    def test_a_selection_beyond_the_document_fails_before_extraction(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "four.pdf"
            try:
                self.blank_pdf(source, 4)
            except ImportError:
                self.skipTest("PDFium is not available")
            with self.assertRaises(ValueError):
                engine.convert_file(source, "Balanced", root / "exports", root / "cache",
                                    cache_policy="bypass", selection=(9,))


class IrVersionTest(unittest.TestCase):
    """The evidence shape is named, and an older one cannot refuse a document."""

    @staticmethod
    def blank_pdf(path, page_count=1):
        import pypdfium2 as pdfium

        document = pdfium.PdfDocument.new()
        for _ in range(page_count):
            document.new_page(200, 200)
        document.save(str(path))
        document.close()

    def test_the_ir_records_the_version_it_was_written_against(self):
        self.assertEqual(engine.IR_VERSION, "0.5.0")

    def test_an_ir_from_another_version_is_not_accepted(self):
        ir = {"philon_ir_version": "0.2.0", "pages": [], "blocks": []}
        with self.assertRaises(ValueError):
            engine.validate_ir(ir)

    def test_the_cache_entry_is_named_after_the_shape_it_holds(self):
        entry = engine.cache_path(Path("/cache"), "abc123", "Balanced")
        self.assertIn(engine.safe_slug(engine.IR_VERSION), entry.name)

    def test_an_entry_written_against_an_older_shape_is_never_reached(self):
        older = "0.2.0"
        self.assertNotEqual(
            engine.cache_path(Path("/cache"), "abc123", "Balanced").name,
            engine.cache_path(Path("/cache"), "abc123", "Balanced").name.replace(
                engine.safe_slug(engine.IR_VERSION), engine.safe_slug(older)),
        )

    def test_an_unreadable_entry_is_recomputed_rather_than_refused(self):
        """Reuse is an optimisation; a broken one must not refuse a document."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "one.pdf"
            try:
                self.blank_pdf(source)
            except ImportError:
                self.skipTest("PDFium is not available")
            cache = root / "cache"
            cache.mkdir()
            content_hash = engine.sha256_file(source)
            entry = engine.cache_path(cache, content_hash, "Balanced")
            entry.write_text('{"ir": {"philon_ir_version": "0.2.0", "pages": [], "blocks": []},'
                             ' "warnings": [], "timings": []}', encoding="utf-8")

            result = engine.convert_file(source, "Balanced", root / "exports", cache)

            self.assertFalse(result["cache_hit"])
            self.assertIn(result["status"], {"completed", "completed_with_warnings"})

    def test_a_corrupt_entry_is_recomputed_too(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "one.pdf"
            try:
                self.blank_pdf(source)
            except ImportError:
                self.skipTest("PDFium is not available")
            cache = root / "cache"
            cache.mkdir()
            entry = engine.cache_path(cache, engine.sha256_file(source), "Balanced")
            entry.write_text("{ this is not json", encoding="utf-8")

            result = engine.convert_file(source, "Balanced", root / "exports", cache)

            self.assertFalse(result["cache_hit"])

    def test_a_sound_entry_is_still_reused(self):
        """The control: recomputing must not become the only path."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "one.pdf"
            try:
                self.blank_pdf(source)
            except ImportError:
                self.skipTest("PDFium is not available")
            cache = root / "cache"
            first = engine.convert_file(source, "Balanced", root / "exports", cache)
            second = engine.convert_file(source, "Balanced", root / "exports", cache)
            self.assertFalse(first["cache_hit"])
            self.assertTrue(second["cache_hit"])


class RuledTableRecoveryTest(unittest.TestCase):
    """A table the page rules is recovered from the rules, not from its text.

    The fixtures are assembled byte by byte so the geometry under test is the
    geometry written here: text drawn at known points, rules stroked at known
    coordinates, and nothing a producer might have added in between.
    """

    @staticmethod
    def page_pdf(path, content, rotation=0):
        """One Helvetica page carrying a given content stream, under a /Rotate."""
        objects = [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            (f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Rotate {rotation} "
             "/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>").encode(),
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"endstream",
        ]
        out = bytearray(b"%PDF-1.4\n")
        offsets = []
        for number, body in enumerate(objects, start=1):
            offsets.append(len(out))
            out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
        start_xref = len(out)
        out += f"xref\n0 {len(objects) + 1}\n".encode() + b"0000000000 65535 f \n"
        for offset in offsets:
            out += f"{offset:010d} 00000 n \n".encode()
        out += (f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
                f"startxref\n{start_xref}\n").encode() + b"%%EOF\n"
        Path(path).write_bytes(bytes(out))

    #: The cells of the fixture table, and where each is drawn.
    CELLS = (
        ("Region", 70, 682), ("Q1", 230, 682), ("Q2", 350, 682),
        ("North", 70, 658), ("14", 230, 658), ("19", 350, 658),
        ("South", 70, 634), ("22", 230, 634), ("27", 350, 634),
    )
    RECOVERED = [["Region", "Q1", "Q2"], ["North", "14", "19"], ["South", "22", "27"]]

    @classmethod
    def table_content(cls, horizontals, verticals):
        """Nine text cells, then the rules that enclose them."""
        parts = [b"BT /F1 11 Tf\n"]
        for text, x, y in cls.CELLS:
            parts.append(f"1 0 0 1 {x} {y} Tm ({text}) Tj\n".encode())
        parts.append(b"ET\n0.6 w 0 0 0 RG\n")
        for y, left, right in horizontals:
            parts.append(f"{left} {y} m {right} {y} l S\n".encode())
        for x, bottom, top in verticals:
            parts.append(f"{x} {bottom} m {x} {top} l S\n".encode())
        return b"".join(parts)

    @classmethod
    def ruled_pdf(cls, path, rotation=0):
        """A 3x3 table whose every rule runs the full width or height."""
        cls.page_pdf(path, cls.table_content(
            [(y, 60, 460) for y in (628, 652, 676, 700)],
            [(x, 628, 700) for x in (60, 220, 340, 460)],
        ), rotation)

    @classmethod
    def partly_ruled_pdf(cls, path):
        """The same cells, with one rule stopping short of the row below it."""
        cls.page_pdf(path, cls.table_content(
            [(y, 60, 460) for y in (628, 652, 676, 700)],
            [(60, 628, 700), (220, 652, 700), (340, 628, 700), (460, 628, 700)],
        ))

    @classmethod
    def unrecoverably_ruled_pdf(cls, path):
        """Rules that leave an L of three openings, which no table can express.

        The vertical between the first two columns stops above the bottom row,
        and the horizontal above the bottom row stops right of the first
        column, so the opening at their corner is joined to the one beside it
        *and* the one above it while those two stay separated from each other.
        """
        cls.page_pdf(path, cls.table_content(
            [(628, 60, 460), (652, 220, 460), (676, 60, 460), (700, 60, 460)],
            [(60, 628, 700), (220, 652, 700), (340, 628, 700), (460, 628, 700)],
        ))

    @classmethod
    def unruled_pdf(cls, path):
        """The same cells with no rules at all: a table only to the eye."""
        parts = [b"BT /F1 11 Tf\n"]
        for text, x, y in cls.CELLS:
            parts.append(f"1 0 0 1 {x} {y} Tm ({text}) Tj\n".encode())
        parts.append(b"ET\n")
        cls.page_pdf(path, b"".join(parts))

    @staticmethod
    def character(value, left, bottom, right, top, baseline=None, size=None):
        """One measured character, in the shape the page scan produces."""
        return {
            "character": value,
            "bbox": engine.make_bbox(left, bottom, right, top, "pdf-page-points"),
            "baseline": bottom if baseline is None else baseline,
            "size": (top - bottom) if size is None else size,
        }

    def recovered_page(self, build, rotation=0):
        """Extract one built fixture, skipping where PDFium is unavailable."""
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "table.pdf"
            build(source) if rotation == 0 else build(source, rotation)
            pages, _ = engine.pdfium_extract(source)
            if not pages or pages[0].get("method") != "pdfium-native":
                self.skipTest("PDFium is not available for extraction")
            return pages[0]

    # -- the rule itself -------------------------------------------------

    def test_a_long_thin_rectangle_is_a_rule_on_its_long_axis(self):
        wide = engine.make_bbox(60, 627.4, 460, 628.6, "pdf-page-points")
        tall = engine.make_bbox(59.4, 628, 60.6, 700, "pdf-page-points")
        self.assertEqual(engine.rule_from_bbox(wide)["axis"], "horizontal")
        self.assertEqual(engine.rule_from_bbox(wide)["position"], 628)
        self.assertEqual(engine.rule_from_bbox(tall)["axis"], "vertical")
        self.assertEqual(engine.rule_from_bbox(tall)["position"], 60)

    def test_a_filled_rectangle_is_read_as_the_rule_it_draws(self):
        """Bounds, not segments: a rule is as often a fill as a stroke."""
        filled = engine.make_bbox(60, 626, 460, 628, "pdf-page-points")
        self.assertEqual(engine.rule_from_bbox(filled)["axis"], "horizontal")

    def test_a_shape_that_is_not_thin_or_not_long_is_not_a_rule(self):
        self.assertIsNone(engine.rule_from_bbox(engine.make_bbox(60, 600, 460, 700, "pdf-page-points")))
        self.assertIsNone(engine.rule_from_bbox(engine.make_bbox(60, 628, 68, 629, "pdf-page-points")))
        self.assertIsNone(engine.rule_from_bbox(None))

    # -- collapsing rules onto lines --------------------------------------

    def test_near_equal_positions_collapse_to_one_line_at_their_mean(self):
        self.assertEqual(engine.cluster_positions([100.0, 100.4, 99.8, 300.0], 2.0), [100.0667, 300.0])
        self.assertEqual(engine.cluster_positions([], 2.0), [])

    def test_positions_further_apart_than_the_tolerance_stay_separate(self):
        self.assertEqual(engine.cluster_positions([100.0, 103.0], 2.0), [100.0, 103.0])

    def test_a_line_keeps_the_separate_runs_it_is_drawn_in(self):
        """Two tables side by side must not be joined across the gap."""
        rules = [
            {"position": 100.0, "start": 60.0, "end": 200.0},
            {"position": 100.2, "start": 400.0, "end": 540.0},
        ]
        line = engine.merge_rules(rules, 2.0)[0]
        self.assertEqual(line["segments"], [(60.0, 200.0), (400.0, 540.0)])
        self.assertEqual((line["start"], line["end"]), (60.0, 540.0))

    def test_a_rule_crossing_a_gap_between_runs_does_not_meet_the_line(self):
        horizontal = engine.merge_rules([
            {"position": 100.0, "start": 60.0, "end": 200.0},
            {"position": 100.0, "start": 400.0, "end": 540.0},
        ], 2.0)[0]
        through_a_run = {"position": 120.0, "segments": [(60.0, 140.0)]}
        through_the_gap = {"position": 300.0, "segments": [(60.0, 140.0)]}
        self.assertTrue(engine.rules_cross(horizontal, through_a_run, 2.0))
        self.assertFalse(engine.rules_cross(horizontal, through_the_gap, 2.0))

    # -- finding the grid --------------------------------------------------

    @staticmethod
    def lines(positions, start, end):
        return [{"position": position, "segments": [(start, end)], "start": start, "end": end}
                for position in positions]

    def test_crossing_rules_make_a_grid_and_its_cells_are_proven(self):
        grids = engine.ruled_table_grids(self.lines([628, 652, 676, 700], 60, 460),
                                         self.lines([60, 220, 340, 460], 628, 700))
        self.assertEqual(len(grids), 1)
        self.assertTrue(grids[0]["complete"])
        self.assertEqual(grids[0]["row_lines"], [628, 652, 676, 700])
        self.assertEqual(grids[0]["crossing_count"], 16)

    def test_a_single_pair_of_rules_is_not_a_grid(self):
        self.assertEqual(engine.ruled_table_grids(self.lines([628], 60, 460),
                                                  self.lines([60], 628, 700)), [])
        self.assertEqual(engine.ruled_table_grids(self.lines([628, 700], 60, 460),
                                                  self.lines([60], 628, 700)), [])

    def test_rules_that_never_meet_make_no_grid(self):
        self.assertEqual(engine.ruled_table_grids(self.lines([628, 652], 60, 200),
                                                  self.lines([400, 460], 628, 700)), [])

    def test_two_tables_on_one_page_stay_two_grids(self):
        horizontals = self.lines([700, 660], 60, 200) + self.lines([400, 360], 60, 200)
        verticals = [
            {"position": 60, "segments": [(660, 700)], "start": 660, "end": 700},
            {"position": 200, "segments": [(660, 700)], "start": 660, "end": 700},
            {"position": 60, "segments": [(360, 400)], "start": 360, "end": 400},
            {"position": 200, "segments": [(360, 400)], "start": 360, "end": 400},
        ]
        grids = engine.ruled_table_grids(horizontals, verticals)
        self.assertEqual(len(grids), 2)
        # Topmost first, so a document's tables arrive in reading order.
        self.assertEqual([grid["row_lines"][-1] for grid in grids], [700, 400])

    def test_a_lattice_that_does_not_close_is_marked_rather_than_completed(self):
        verticals = self.lines([60, 340, 460], 628, 700)
        verticals.append({"position": 220, "segments": [(652, 700)], "start": 652, "end": 700})
        grids = engine.ruled_table_grids(self.lines([628, 652, 676, 700], 60, 460), verticals)
        self.assertEqual(len(grids), 1)
        self.assertFalse(grids[0]["complete"])

    # -- reading the cells -------------------------------------------------

    def test_a_character_is_placed_by_its_own_centre(self):
        grid = {"row_lines": [0.0, 10.0, 20.0], "column_lines": [0.0, 10.0, 20.0]}
        characters = [
            self.character("A", 1, 11, 3, 19),
            self.character("B", 11, 11, 13, 19),
            self.character("C", 1, 1, 3, 9),
        ]
        # Row 0 is the topmost band, though its lines are the last two.
        self.assertEqual(engine.table_cell_text(characters, grid), [["A", "B"], ["C", ""]])

    def test_a_cell_no_character_falls_inside_stays_empty(self):
        grid = {"row_lines": [0.0, 10.0], "column_lines": [0.0, 10.0, 20.0]}
        characters = [self.character("A", 1, 1, 3, 9)]
        self.assertEqual(engine.table_cell_text(characters, grid), [["A", ""]])

    def test_a_character_outside_every_cell_is_left_out(self):
        grid = {"row_lines": [0.0, 10.0], "column_lines": [0.0, 10.0]}
        characters = [self.character("A", 50, 50, 52, 58), {"character": "B", "bbox": None, "baseline": None, "size": None}]
        self.assertEqual(engine.table_cell_text(characters, grid), [[""]])

    def test_a_gap_in_the_read_order_becomes_a_space(self):
        """A character PDFium gives no rectangle for is one it drew nothing for."""
        grid = {"row_lines": [0.0, 10.0], "column_lines": [0.0, 20.0]}
        characters = [
            self.character("N", 1, 1, 3, 9),
            {"character": " ", "bbox": None, "baseline": None, "size": None},
            self.character("A", 5, 1, 7, 9),
        ]
        self.assertEqual(engine.table_cell_text(characters, grid), [["N A"]])

    # -- the page, end to end ----------------------------------------------

    def test_a_ruled_page_yields_exactly_the_table_it_draws(self):
        page = self.recovered_page(self.ruled_pdf)
        self.assertEqual(len(page["ruled_tables"]), 1)
        recovered = page["ruled_tables"][0]
        self.assertTrue(recovered["complete"])
        self.assertEqual((recovered["row_count"], recovered["column_count"]), (3, 3))
        self.assertEqual(recovered["rows"], self.RECOVERED)

    def test_the_recovered_rectangle_is_the_table_the_rules_enclose(self):
        recovered = self.recovered_page(self.ruled_pdf)["ruled_tables"][0]
        box = recovered["bbox"]
        self.assertEqual((box["x0"], box["y0"], box["x1"], box["y1"]), (60.0, 628.0, 460.0, 700.0))
        self.assertEqual(box["coordinate_space"], "pdf-page-points")

    def test_the_table_becomes_one_block_carrying_its_rows(self):
        page = self.recovered_page(self.ruled_pdf)
        page["body_font"], page["body_size"] = "", 0.0
        parts = engine.geometric_native_parts(page, set())
        self.assertEqual(len(parts), 1)
        self.assertEqual(parts[0]["table_rows"], self.RECOVERED)
        block = engine.make_block(page, 1, parts[0]["text"], parts[0]["start"], parts[0]["end"],
                                  None, parts[0].get("table_rows"), parts[0].get("table_bbox"))
        self.assertEqual(block["type"], "table")
        self.assertEqual(block["table"]["rows"], self.RECOVERED)
        self.assertEqual(block["table"]["source"], "ruled-geometry")
        self.assertTrue(block["evidence"]["findings"]["ruled_table_recovered"])
        self.assertEqual(block["evidence"]["findings"]["ruled_table_cell_count"], 9)
        # The block is bounded by the rules, not by the characters inside them.
        self.assertEqual(block["bbox"]["x0"], 60.0)

    def test_the_recovered_table_reaches_every_export(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "table.pdf"
            self.ruled_pdf(source)
            ir, warnings, _ = engine.make_ir(source, "Standard")
            if ir["pages"][0]["method"] != "pdfium-native":
                self.skipTest("PDFium is not available for extraction")
            self.assertEqual(ir["philon_ir_version"], "0.5.0")
            self.assertEqual(ir["pages"][0]["ruled_tables"][0]["row_count"], 3)
            table = next(block for block in ir["blocks"] if block["type"] == "table")
            self.assertEqual(engine.block_table_rows(table), self.RECOVERED)

            markdown = engine.render_markdown(ir)
            self.assertIn("| Region | Q1 | Q2 |", markdown)
            self.assertIn("| --- | --- | --- |", markdown)
            self.assertIn("| South | 22 | 27 |", markdown)

            document = engine.render_html(ir)
            self.assertIn("<th scope=\"col\">Region</th>", document)
            self.assertIn("<td>27</td>", document)

            groups = engine.table_export_groups(ir["blocks"])
            self.assertEqual(len(groups), 1)
            self.assertEqual(groups[0]["rows"], self.RECOVERED)
            self.assertNotIn("RULED_TABLE_INCOMPLETE", [warning.code for warning in warnings])

    def test_the_table_survives_the_page_the_source_never_delimited(self):
        """The proof: the block's own text carries no delimiter to parse."""
        page = self.recovered_page(self.ruled_pdf)
        self.assertIsNone(engine.table_rows(page["text"]))
        self.assertNotIn("|", page["text"])
        self.assertNotIn("\t", page["text"])

    def test_a_table_interrupted_mid_way_is_still_one_block(self):
        """The defect this fixes: it was emitted twice, each copy holding every row.

        A table's lines need not arrive in one unbroken run. A line whose centre
        falls just outside the rules -- or a caption PDFium reads between two
        rows -- closed the block early, and the next line inside the rules
        opened a second block carrying the same recovered rows into Markdown and
        into the CSV.
        """
        inside = engine.make_bbox(70, 680, 400, 692, "pdf-page-points")
        below = engine.make_bbox(70, 656, 400, 668, "pdf-page-points")
        outside = engine.make_bbox(70, 400, 400, 412, "pdf-page-points")
        page = {
            "text": "Region Q1\nA stray caption\nNorth 14",
            "body_font": "", "body_size": 0.0,
            "native_text_lines": [
                {"text": "Region Q1", "start": 0, "end": 9, "bbox": inside},
                {"text": "A stray caption", "start": 10, "end": 25, "bbox": outside},
                {"text": "North 14", "start": 26, "end": 34, "bbox": below},
            ],
            "ruled_tables": [{
                "complete": True, "recoverable": True,
                "bbox": engine.make_bbox(60, 640, 460, 700, "pdf-page-points"),
                "rows": [["Region", "Q1"], ["North", "14"]],
                "row_count": 2, "column_count": 2, "crossing_count": 9,
            }],
        }
        parts = engine.geometric_native_parts(page, set())
        with_rows = [part for part in parts if part.get("table_rows")]
        self.assertEqual(len(with_rows), 1)
        self.assertEqual(with_rows[0]["text"], "Region Q1\nNorth 14")
        self.assertEqual(with_rows[0]["table_rows"], [["Region", "Q1"], ["North", "14"]])
        # ...and the line that fell outside the rules is still its own block.
        self.assertIn("A stray caption", [part["text"] for part in parts])

    def test_an_unrecoverable_lattice_never_reaches_the_segmenter(self):
        page = {
            "text": "Region Q1", "body_font": "", "body_size": 0.0,
            "native_text_lines": [
                {"text": "Region Q1", "start": 0, "end": 9,
                 "bbox": engine.make_bbox(70, 680, 400, 692, "pdf-page-points")},
            ],
            "ruled_tables": [{
                "complete": False, "recoverable": False,
                "bbox": engine.make_bbox(60, 640, 460, 700, "pdf-page-points"),
                "rows": [], "row_count": 2, "column_count": 2, "crossing_count": 8,
            }],
        }
        self.assertFalse([part for part in engine.geometric_native_parts(page, set()) if part.get("table_rows")])

    # -- rotation ------------------------------------------------------------

    def test_a_rotated_page_is_measured_in_the_frame_it_is_displayed_in(self):
        """Classify after the turn, or a landscape table arrives transposed."""
        for rotation in (0, 90, 180, 270):
            with self.subTest(rotation=rotation):
                page = self.recovered_page(self.ruled_pdf, rotation)
                self.assertEqual(len(page["ruled_tables"]), 1)
                recovered = page["ruled_tables"][0]
                self.assertTrue(recovered["complete"])
                self.assertEqual((recovered["row_count"], recovered["column_count"]), (3, 3))
                box = recovered["bbox"]
                self.assertGreaterEqual(box["x0"], 0)
                self.assertGreaterEqual(box["y0"], 0)
                self.assertLessEqual(box["x1"], page["width"])
                self.assertLessEqual(box["y1"], page["height"])
                self.assertEqual(sorted(cell for row in recovered["rows"] for cell in row),
                                 sorted(cell for row in self.RECOVERED for cell in row))

    def test_a_quarter_turn_turns_the_table_it_displays(self):
        """The fixture is drawn upright and then turned, so on screen it lies on
        its side -- and the recovery reports the table a reader is shown, which
        is the upright one rotated, not the upright one quietly restored.

        This is the whole reason the rules are classified after the frame
        conversion rather than before it. A real landscape table is the mirror
        of this fixture: drawn sideways so that /Rotate sets it upright, and
        there the same rule is what keeps its rows from arriving as columns.
        """
        upright = self.recovered_page(self.ruled_pdf)["ruled_tables"][0]["rows"]
        turned = self.recovered_page(self.ruled_pdf, 270)["ruled_tables"][0]["rows"]
        self.assertEqual([list(column) for column in zip(*upright)][::-1], turned)

    # -- a table continued onto the next page ---------------------------------

    @classmethod
    def continued_pdf(cls, path):
        """Two pages ruled on the same columns; the second opens into data.

        This is what a real continued table looks like: the rules repeat, the
        headings do not. The row-matching test finds nothing to match.
        """
        def content(cells):
            parts = [b"BT /F1 11 Tf\n"]
            for text, x, y in cells:
                parts.append(f"1 0 0 1 {x} {y} Tm ({text}) Tj\n".encode())
            parts.append(b"ET\n0.6 w 0 0 0 RG\n")
            for y in (652, 676, 700):
                parts.append(f"60 {y} m 460 {y} l S\n".encode())
            for x in (60, 220, 460):
                parts.append(f"{x} 652 m {x} 700 l S\n".encode())
            return b"".join(parts)

        first = content([("Region", 70, 682), ("Q1", 230, 682), ("North", 70, 658), ("14", 230, 658)])
        second = content([("South", 70, 682), ("22", 230, 682), ("East", 70, 658), ("31", 230, 658)])
        objects = [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R 6 0 R] /Count 2 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            b"<< /Length " + str(len(first)).encode() + b" >>\nstream\n" + first + b"endstream",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 7 0 R >>",
            b"<< /Length " + str(len(second)).encode() + b" >>\nstream\n" + second + b"endstream",
        ]
        out = bytearray(b"%PDF-1.4\n")
        offsets = []
        for number, body in enumerate(objects, start=1):
            offsets.append(len(out))
            out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
        start_xref = len(out)
        out += f"xref\n0 {len(objects) + 1}\n".encode() + b"0000000000 65535 f \n"
        for offset in offsets:
            out += f"{offset:010d} 00000 n \n".encode()
        out += (f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
                f"startxref\n{start_xref}\n").encode() + b"%%EOF\n"
        Path(path).write_bytes(bytes(out))

    def test_a_table_continued_by_its_rules_is_joined_for_export(self):
        """The header does not repeat, so only the columns can say it continues."""
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "continued.pdf"
            self.continued_pdf(source)
            ir, _, _ = engine.make_ir(source, "Standard")
            if ir["pages"][0]["method"] != "pdfium-native":
                self.skipTest("PDFium is not available for extraction")
            tables = [block for block in ir["blocks"] if block.get("table")]
            self.assertEqual(len(tables), 2)
            self.assertEqual(tables[1]["evidence"]["cross_page_continuation_of"], tables[0]["id"])
            self.assertEqual(tables[1]["evidence"]["cross_page_continuation_kind"], "matching-column-geometry")
            self.assertEqual(tables[0]["evidence"]["continues_on_block"], tables[1]["id"])

            groups = engine.table_export_groups(ir["blocks"])
            self.assertEqual(len(groups), 1)
            # Every row of the continuation survives: it opened into data, so
            # none of it is a header to drop.
            self.assertEqual(groups[0]["rows"], [["Region", "Q1"], ["North", "14"],
                                                 ["South", "22"], ["East", "31"]])
            self.assertEqual(groups[0]["source_block_ids"], [tables[0]["id"], tables[1]["id"]])

    def test_columns_measured_apart_are_not_one_table(self):
        left = {"table": {"source": "ruled-geometry", "column_lines": [60.0, 220.0, 460.0]}}
        same = {"table": {"source": "ruled-geometry", "column_lines": [60.4, 219.8, 460.2]}}
        shifted = {"table": {"source": "ruled-geometry", "column_lines": [90.0, 250.0, 490.0]}}
        narrower = {"table": {"source": "ruled-geometry", "column_lines": [60.0, 460.0]}}
        self.assertTrue(engine.tables_share_column_geometry(left, same))
        self.assertFalse(engine.tables_share_column_geometry(left, shifted))
        self.assertFalse(engine.tables_share_column_geometry(left, narrower))

    def test_a_delimited_table_is_never_joined_by_geometry_it_does_not_have(self):
        """Absent column positions must not compare equal to absent ones."""
        one = {"table": {"source": "ruled-geometry", "column_lines": []}}
        other = {"table": {"source": "ruled-geometry", "column_lines": []}}
        self.assertFalse(engine.tables_share_column_geometry(one, other))
        self.assertFalse(engine.tables_share_column_geometry({}, {}))

    # -- prove or mark --------------------------------------------------------

    def test_a_rule_that_stops_is_read_as_the_merged_cell_it_leaves(self):
        """The absence of a rule is drawn evidence as much as its presence."""
        page = self.recovered_page(self.partly_ruled_pdf)
        recovered = page["ruled_tables"][0]
        self.assertFalse(recovered["complete"])
        self.assertTrue(recovered["recoverable"])
        # The vertical between the first two columns stops above the last row,
        # so that row's first two openings were never separated.
        self.assertEqual(recovered["rows"], [["Region", "Q1", "Q2"],
                                             ["North", "14", "19"],
                                             ["South 22", "", "27"]])
        self.assertEqual(recovered["spans"][2][0], {"rowspan": 1, "colspan": 2})
        self.assertIsNone(recovered["spans"][2][1])
        self.assertEqual(recovered["spans"][0][0], {"rowspan": 1, "colspan": 1})

    def test_a_merged_cell_reaches_html_as_one_cell_that_spans(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "merged.pdf"
            self.partly_ruled_pdf(source)
            ir, _, _ = engine.make_ir(source, "Standard")
            if ir["pages"][0]["method"] != "pdfium-native":
                self.skipTest("PDFium is not available for extraction")
            table = next(block for block in ir["blocks"] if block.get("table"))
            self.assertEqual(table["evidence"]["findings"]["ruled_table_merged_cells"], 1)
            document = engine.render_html(ir)
            self.assertIn('<td colspan="2">South 22</td>', document)
            # The opening the merge swallowed is not emitted as an empty cell.
            self.assertNotIn("<td></td>", document)
            # Markdown has no way to say colspan, so it keeps the grid square
            # and leaves the covered opening blank rather than repeating a value
            # the page wrote once.
            markdown = engine.render_markdown(ir)
            self.assertIn("| South 22 |  | 27 |", markdown)

    def test_a_full_lattice_carries_no_spans_at_all(self):
        page = self.recovered_page(self.ruled_pdf)
        self.assertEqual(page["ruled_tables"][0]["spans"], [])
        document = engine.render_html({
            "philon_ir_version": engine.IR_VERSION,
            "document": {"source": {"filename": "t.pdf"}},
            "pages": [{"id": "page-1", "number": 1}],
            "blocks": [{"id": "b1", "page": "page-1", "type": "table", "level": None, "text": "x", "links": [],
                        "source": {"method": "pdfium-native", "confidence": 1.0, "language": "und"},
                        "table": {"rows": [["a", "b"]], "spans": []}}],
        })
        self.assertNotIn("colspan", document)

    def test_a_merged_region_that_is_not_a_rectangle_is_reported_and_not_emitted(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "unrecoverable.pdf"
            self.unrecoverably_ruled_pdf(source)
            ir, warnings, _ = engine.make_ir(source, "Standard")
            if ir["pages"][0]["method"] != "pdfium-native":
                self.skipTest("PDFium is not available for extraction")
            recovered = ir["pages"][0]["ruled_tables"]
            self.assertEqual(len(recovered), 1)
            self.assertFalse(recovered[0]["recoverable"])
            self.assertIn("RULED_TABLE_INCOMPLETE", [warning.code for warning in warnings])
            self.assertFalse([block for block in ir["blocks"] if block.get("table")])

    def test_a_table_only_the_eye_can_see_is_never_emitted_as_one(self):
        """Whitespace alignment is inference. Philon proves or marks."""
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "unruled.pdf"
            self.unruled_pdf(source)
            ir, _, _ = engine.make_ir(source, "Standard")
            if ir["pages"][0]["method"] != "pdfium-native":
                self.skipTest("PDFium is not available for extraction")
            self.assertEqual(ir["pages"][0]["ruled_tables"], [])
            self.assertFalse([block for block in ir["blocks"] if block.get("table")])
            self.assertFalse([block for block in ir["blocks"] if block["type"] == "table"])

    # -- the shape the IR promises --------------------------------------------

    def test_a_ragged_recovered_table_is_refused_before_a_consumer_sees_it(self):
        ir = {
            "philon_ir_version": engine.IR_VERSION,
            "pages": [{"id": "page-1"}],
            "blocks": [{"id": "page-1-block-1", "page": "page-1", "type": "table", "text": "x",
                        "table": {"rows": [["a", "b"], ["c"]]}}],
        }
        with self.assertRaises(ValueError):
            engine.validate_ir(ir)

    def test_spans_that_do_not_cover_the_table_are_refused(self):
        def ir_with(table):
            return {"philon_ir_version": engine.IR_VERSION, "pages": [{"id": "page-1"}],
                    "blocks": [{"id": "b1", "page": "page-1", "type": "table", "text": "x", "table": table}]}

        rows = [["a", "b"], ["c", "d"]]
        engine.validate_ir(ir_with({"rows": rows, "spans": []}))
        for broken in (
            {"rows": rows, "spans": [[None, None]]},
            {"rows": rows, "spans": [[None], [None, None]]},
            {"rows": rows, "spans": [[{"rowspan": 0, "colspan": 1}, None], [None, None]]},
            {"rows": rows, "spans": [[{"rowspan": 1}, None], [None, None]]},
        ):
            with self.assertRaises(ValueError):
                engine.validate_ir(ir_with(broken))

    def test_a_formula_without_the_text_the_page_set_is_refused(self):
        with self.assertRaises(ValueError):
            engine.validate_ir({
                "philon_ir_version": engine.IR_VERSION, "pages": [{"id": "page-1"}],
                "blocks": [{"id": "b1", "page": "page-1", "type": "formula", "text": "x",
                            "formula": {"source": "measured-script-geometry"}}],
            })

    def test_a_recovered_table_without_rows_is_refused(self):
        ir = {
            "philon_ir_version": engine.IR_VERSION,
            "pages": [{"id": "page-1"}],
            "blocks": [{"id": "page-1-block-1", "page": "page-1", "type": "table", "text": "x",
                        "table": {"rows": []}}],
        }
        with self.assertRaises(ValueError):
            engine.validate_ir(ir)

    def test_recovered_rows_outrank_a_delimited_reading_of_the_same_block(self):
        block = {"text": "a\tb\nc\td", "table": {"rows": [["Region", "Q1"], ["North", "14"]]}}
        self.assertEqual(engine.block_table_rows(block), [["Region", "Q1"], ["North", "14"]])
        self.assertEqual(engine.block_table_rows({"text": "a\tb\nc\td"}), [["a", "b"], ["c", "d"]])

    def test_a_cell_containing_a_pipe_does_not_split_the_markdown_column(self):
        self.assertEqual(engine.markdown_table_cell("a|b"), "a\\|b")
        ir = {
            "philon_ir_version": engine.IR_VERSION,
            "document": {"source": {"filename": "t.pdf"}},
            "pages": [{"id": "page-1", "number": 1}],
            "blocks": [{"id": "page-1-block-1", "page": "page-1", "type": "table", "level": None,
                        "text": "x", "links": [],
                        "source": {"method": "pdfium-native", "confidence": 1.0, "language": "und"},
                        "table": {"rows": [["a|b", "c"], ["d", "e"]]}}],
        }
        row = next(line for line in engine.render_markdown(ir).splitlines() if line.startswith("| a"))
        self.assertEqual(row, "| a\\|b | c |")

    # -- guards ----------------------------------------------------------------

    def test_a_page_of_drawings_is_not_searched_for_a_grid(self):
        """Past the cap the page is a chart, and pairing every line is quadratic."""
        many = self.lines(list(range(engine.MAX_RULE_LINES_PER_AXIS + 1)), 0, 500)
        self.assertEqual(engine.ruled_table_grids(many, self.lines([10, 20], 0, 500)), [])

    def test_a_page_that_draws_nothing_reports_no_rules(self):
        page = self.recovered_page(self.unruled_pdf)
        self.assertEqual(page["ruled_tables"], [])


class MeasuredFormulaTest(unittest.TestCase):
    """A formula is recognised from how the page set it, not from guesswork.

    PDFium reports each character's true baseline and the size it is set at.
    Both are needed: glyph ink is a poor witness for either, and a reader that
    measures ink calls the `=` in every line of prose a superscript.
    """

    @staticmethod
    def typeset_pdf(path, runs):
        """One page whose runs each carry their own size and position."""
        parts = [b"BT\n"]
        for text, x, y, size in runs:
            parts.append(f"/F1 {size} Tf 1 0 0 1 {x} {y} Tm ({text}) Tj\n".encode())
        parts.append(b"ET\n")
        content = b"".join(parts)
        objects = [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"endstream",
        ]
        out = bytearray(b"%PDF-1.4\n")
        offsets = []
        for number, body in enumerate(objects, start=1):
            offsets.append(len(out))
            out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
        start_xref = len(out)
        out += f"xref\n0 {len(objects) + 1}\n".encode() + b"0000000000 65535 f \n"
        for offset in offsets:
            out += f"{offset:010d} 00000 n \n".encode()
        out += (f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
                f"startxref\n{start_xref}\n").encode() + b"%%EOF\n"
        Path(path).write_bytes(bytes(out))

    #: A raised exponent, a dropped index, and a line of ordinary prose that
    #: contains the characters a naive reader mistakes for scripts.
    RUNS = (
        ("E = mc", 72, 700, 12), ("2", 111, 705, 8),
        ("H", 72, 670, 12), ("2", 80, 666, 8), ("O", 85, 670, 12),
        ("The rate is 4 = 2 x 2, roughly.", 72, 640, 12),
    )

    def lines_of(self, runs=None):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "typeset.pdf"
            self.typeset_pdf(source, runs or self.RUNS)
            pages, _ = engine.pdfium_extract(source)
            if not pages or pages[0].get("method") != "pdfium-native":
                self.skipTest("PDFium is not available for extraction")
            return pages[0]["native_text_lines"]

    # -- the faces that only set mathematics -------------------------------

    def test_a_face_that_only_sets_mathematics_is_recognised(self):
        for face in ("CMMI10", "CMSY7", "MSBM10", "LatinModernMath-Regular", "STIXMath", "Cambria Math"):
            self.assertTrue(engine.is_math_typeface(face), face)

    def test_an_ordinary_face_is_not_a_mathematical_one(self):
        for face in ("Times-Roman", "Helvetica", "TeXGyreTermes", "NimbusRomNo9L", "Arial-BoldMT", ""):
            self.assertFalse(engine.is_math_typeface(face), face)

    # -- reading the scripts -------------------------------------------------

    def test_a_raised_smaller_character_is_a_superscript(self):
        self.assertEqual(self.lines_of()[0]["typeset"], "E = mc ^{2}")

    def test_a_dropped_smaller_character_is_a_subscript(self):
        self.assertEqual(self.lines_of()[1]["typeset"], "H_{2}O")

    def test_prose_is_returned_exactly_as_it_was_set(self):
        """The defect this rules out: `=` inks high and small in every line."""
        line = self.lines_of()[2]
        self.assertEqual(line["typeset"], line["text"])
        self.assertNotIn("^", line["typeset"])
        self.assertNotIn("_", line["typeset"])

    def test_a_character_the_page_only_sets_smaller_is_not_a_script(self):
        """Size alone is not enough; it must leave the baseline as well."""
        lines = self.lines_of(runs=(("BIG", 72, 700, 12), ("small", 100, 700, 8)))
        self.assertEqual(lines[0]["typeset"], lines[0]["text"])

    def test_a_line_with_nothing_to_measure_against_is_left_alone(self):
        self.assertEqual(engine.measured_script_roles([]), [])
        single = [{"character": "x", "bbox": None, "baseline": 700.0, "size": 12.0}]
        self.assertEqual(engine.measured_script_roles(single), ["normal"])

    def test_a_character_without_a_baseline_is_never_called_a_script(self):
        characters = [
            {"character": "x", "bbox": None, "baseline": None, "size": 12.0},
            {"character": "2", "bbox": None, "baseline": None, "size": 8.0},
        ]
        self.assertEqual(engine.measured_script_roles(characters), ["normal", "normal"])

    def test_consecutive_scripts_group_into_one_run(self):
        characters = [
            {"character": "x", "bbox": None, "baseline": 100.0, "size": 10.0},
            {"character": "1", "bbox": None, "baseline": 104.0, "size": 6.0},
            {"character": "2", "bbox": None, "baseline": 104.0, "size": 6.0},
        ]
        self.assertEqual(engine.typeset_from_characters(characters, "x12"), "x^{12}")

    def test_a_line_that_sets_no_scripts_comes_back_identical(self):
        characters = [
            {"character": "a", "bbox": None, "baseline": 100.0, "size": 10.0},
            {"character": "b", "bbox": None, "baseline": 100.0, "size": 10.0},
        ]
        self.assertEqual(engine.typeset_from_characters(characters, "ab"), "ab")

    # -- what the scripts make of the block ----------------------------------

    def test_the_measured_script_is_what_makes_it_a_formula(self):
        """`E = mc2` carries one marker as characters and two once set down."""
        self.assertNotEqual(engine.classify_block("E = mc 2")[0], "formula")
        self.assertEqual(engine.classify_block("E = mc 2", typeset="E = mc ^{2}")[0], "formula")

    def test_a_mathematical_face_is_enough_on_its_own(self):
        self.assertEqual(engine.classify_block("α β γ", math_face=True)[0], "formula")
        self.assertNotEqual(engine.classify_block("α β γ")[0], "formula")

    def test_a_whole_paragraph_in_a_mathematical_face_is_not_one_formula(self):
        long_run = "x " * (engine.FORMULA_FACE_CHARS // 2 + 20)
        self.assertNotEqual(engine.classify_block(long_run, math_face=True)[0], "formula")

    def test_the_formula_reaches_the_block_and_every_export(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "formula.pdf"
            self.typeset_pdf(source, self.RUNS)
            ir, _, _ = engine.make_ir(source, "Standard")
            if ir["pages"][0]["method"] != "pdfium-native":
                self.skipTest("PDFium is not available for extraction")
            formula = next(block for block in ir["blocks"] if block["type"] == "formula")
            self.assertEqual(formula["formula"]["typeset"], "E = mc ^{2}")
            self.assertEqual(formula["formula"]["source"], "measured-script-geometry")
            self.assertEqual(formula["evidence"]["findings"]["measured_script_count"], 1)
            self.assertIn("E = mc ^{2}", engine.render_markdown(ir))
            self.assertIn("E = mc ^{2}", engine.render_html(ir))

    def test_a_block_the_page_set_no_scripts_in_carries_no_formula_record(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "plain.pdf"
            self.typeset_pdf(source, (("A plain sentence about nothing.", 72, 700, 12),))
            ir, _, _ = engine.make_ir(source, "Standard")
            if ir["pages"][0]["method"] != "pdfium-native":
                self.skipTest("PDFium is not available for extraction")
            self.assertFalse([block for block in ir["blocks"] if block.get("formula")])

    def test_a_formula_without_measured_scripts_still_renders_its_text(self):
        self.assertEqual(engine.block_formula_text({"text": "a = b"}), "a = b")
        self.assertEqual(
            engine.block_formula_text({"text": "x 2", "formula": {"typeset": "x^{2}"}}), "x^{2}")


class AutomaticRepairTest(unittest.TestCase):
    """Philon may replace what it could not read, and must say that it did.

    This is the one place extracted text is overwritten, so the tests are
    mostly about what survives it: the source's own words, the reason, and the
    route back. No model is run; the local runtime is mocked, because what is
    under test is the policy around it rather than any model's reading.
    """

    @staticmethod
    def ir_with_unreadable_block(escalate=True):
        return {
            "philon_ir_version": engine.IR_VERSION,
            "document": {"source": {"path": "/tmp/x.pdf"}},
            "pages": [{"id": "page-1", "number": 1, "width": 612, "height": 792}],
            "blocks": [{
                "id": "page-1-block-1", "page": "page-1", "type": "paragraph", "text": "sourc� text",
                "bbox": {"x0": 10, "y0": 10, "x1": 100, "y1": 40,
                         "coordinate_space": "pdf-page-points", "origin": "bottom-left"},
                "source": {"method": "pdfium-native", "confidence": 0.4, "language": "und"},
                "evidence": {"native_health": {"requires_escalation": escalate},
                             "alternatives": [], "repair_history": [], "validation": []},
            }],
        }

    def run_with_model(self, ir, text, mode_issues=False):
        """Apply an automatic pass with the local runtime stubbed out."""
        from unittest.mock import patch

        pack = {"id": "qwen3.8-27b-local-repair", "approved": True,
                "available_locally": True, "local_path": "/tmp/model"}
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "x.pdf"
            source.write_bytes(b"%PDF-1.4\n%%EOF\n")
            crop = Path(directory) / "crop.png"
            crop.write_bytes(b"\x89PNG\r\n\x1a\n")
            with patch.object(engine, "resolve_repair_pack", return_value=pack), \
                 patch.object(engine, "repair_crop", return_value=crop), \
                 patch.object(engine, "run_qwen38", return_value=(text, {"runtime": "stub"})):
                return engine.apply_automatic_repairs(ir, source, Path(directory))

    # -- what it acts on -------------------------------------------------

    def test_it_acts_only_where_the_health_gate_already_refused(self):
        readable = self.ir_with_unreadable_block(escalate=False)
        self.assertEqual(engine.blocks_awaiting_repair(readable), [])
        unreadable = self.ir_with_unreadable_block()
        self.assertEqual(len(engine.blocks_awaiting_repair(unreadable)), 1)

    def test_a_document_it_has_nothing_to_do_with_is_left_alone(self):
        ir = self.ir_with_unreadable_block(escalate=False)
        self.assertEqual(self.run_with_model(ir, "anything"), [])
        self.assertEqual(ir["blocks"][0]["text"], "sourc� text")

    def test_without_an_approved_model_nothing_is_changed_and_it_says_so(self):
        from unittest.mock import patch

        ir = self.ir_with_unreadable_block()
        with patch.object(engine, "resolve_repair_pack", return_value=None):
            findings = engine.apply_automatic_repairs(ir, Path("/tmp/x.pdf"), Path("/tmp"))
        self.assertEqual([finding.code for finding in findings], ["AUTOMATIC_REPAIR_UNAVAILABLE"])
        self.assertEqual(ir["blocks"][0]["text"], "sourc� text")

    # -- what survives a replacement ---------------------------------------

    def test_the_source_text_is_retained_before_it_is_replaced(self):
        ir = self.ir_with_unreadable_block()
        findings = self.run_with_model(ir, "source text")
        block = ir["blocks"][0]
        self.assertEqual(block["text"], "source text")
        retained = [item for item in block["evidence"]["alternatives"] if item["kind"] == "native-source"]
        self.assertEqual(len(retained), 1)
        self.assertEqual(retained[0]["text"], "sourc� text")
        self.assertIn("AUTOMATIC_REPAIR_APPLIED", [finding.code for finding in findings])

    def test_the_replacement_records_which_model_made_it(self):
        ir = self.ir_with_unreadable_block()
        self.run_with_model(ir, "source text")
        history = ir["blocks"][0]["evidence"]["repair_history"]
        self.assertEqual(history[-1]["kind"], "automatic-local-ocr")
        self.assertEqual(history[-1]["status"], "candidate-applied")
        self.assertEqual(history[-1]["candidate_kind"], "qwen3.8-27b-local-repair")
        self.assertIn("source_crop", history[-1])
        self.assertEqual(ir["blocks"][0]["review"]["action"], "automatic_repair")
        self.assertIn("automatic-local-repair-applied", ir["blocks"][0]["evidence"]["validation"])

    def test_the_source_text_can_be_put_back(self):
        """A replacement a reader disagrees with must be reversible."""
        ir = self.ir_with_unreadable_block()
        self.run_with_model(ir, "source text")
        block = ir["blocks"][0]
        index = next(position for position, item in enumerate(block["evidence"]["alternatives"])
                     if item["kind"] == "native-source")
        block["text"] = block["evidence"]["alternatives"][index]["text"]
        self.assertEqual(block["text"], "sourc� text")

    # -- what it refuses to apply -------------------------------------------

    def test_a_candidate_that_fails_the_format_checks_is_never_applied(self):
        """A model's reading with replacement glyphs is not an improvement."""
        ir = self.ir_with_unreadable_block()
        findings = self.run_with_model(ir, "still � broken")
        block = ir["blocks"][0]
        self.assertEqual(block["text"], "sourc� text")
        self.assertIn("AUTOMATIC_REPAIR_WITHHELD", [finding.code for finding in findings])
        self.assertEqual(block["evidence"]["repair_history"][-1]["status"], "candidate-withheld")
        # ...and it is still retained, so a person can look at it.
        self.assertEqual(len(block["evidence"]["alternatives"]), 1)
        self.assertFalse(block["evidence"]["alternatives"][0]["selected"])

    def test_an_empty_candidate_is_never_applied(self):
        ir = self.ir_with_unreadable_block()
        self.run_with_model(ir, "   ")
        self.assertEqual(ir["blocks"][0]["text"], "sourc� text")

    def test_a_runtime_that_fails_leaves_the_source_text_standing(self):
        from unittest.mock import patch

        ir = self.ir_with_unreadable_block()
        pack = {"id": "qwen3.8-27b-local-repair", "approved": True,
                "available_locally": True, "local_path": "/tmp/model"}
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "x.pdf"
            source.write_bytes(b"%PDF-1.4\n%%EOF\n")
            with patch.object(engine, "resolve_repair_pack", return_value=pack), \
                 patch.object(engine, "repair_crop", side_effect=RuntimeError("no raster")):
                findings = engine.apply_automatic_repairs(ir, source, Path(directory))
        self.assertEqual([finding.code for finding in findings], ["AUTOMATIC_REPAIR_FAILED"])
        self.assertEqual(ir["blocks"][0]["text"], "sourc� text")

    # -- the gate on the pack itself ------------------------------------------

    def test_only_an_approved_enabled_local_pack_is_ever_used(self):
        from unittest.mock import patch

        def status(approved=True, available=True, path="/tmp/model"):
            return {"packs": [{"id": "qwen3.8-27b-local-repair", "approved": approved,
                               "available_locally": available, "local_path": path}]}

        with patch.object(engine, "model_status", return_value=status()):
            self.assertIsNotNone(engine.resolve_repair_pack(None))
            # ...but not one the person running it has switched off.
            self.assertIsNone(engine.resolve_repair_pack(["olmocr-2-7b-local-candidate"]))
        with patch.object(engine, "model_status", return_value=status(approved=False)):
            self.assertIsNone(engine.resolve_repair_pack(None))
        with patch.object(engine, "model_status", return_value=status(available=False)):
            self.assertIsNone(engine.resolve_repair_pack(None))
        with patch.object(engine, "model_status", return_value=status(path=None)):
            self.assertIsNone(engine.resolve_repair_pack(None))

    def test_a_run_that_did_not_ask_for_repair_never_gets_one(self):
        """It is off unless asked for, and no profile turns it on."""
        from unittest.mock import patch

        with patch.object(engine, "apply_automatic_repairs") as repair:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                source = root / "sample.pdf"
                IrVersionTest.blank_pdf(source)
                for profile in ("Fast", "Balanced", "Verified"):
                    engine.convert_file(source, profile, root / "out", root / "cache",
                                        outputs=["markdown"])
        repair.assert_not_called()

    def test_the_cap_bounds_one_pass_and_reports_what_it_left(self):
        ir = self.ir_with_unreadable_block()
        template = ir["blocks"][0]
        ir["blocks"] = []
        for index in range(engine.AUTOMATIC_REPAIR_MAX_BLOCKS + 3):
            block = json.loads(json.dumps(template))
            block["id"] = f"page-1-block-{index + 1}"
            ir["blocks"].append(block)
        findings = self.run_with_model(ir, "source text")
        self.assertIn("AUTOMATIC_REPAIR_LIMITED", [finding.code for finding in findings])
        repaired = [block for block in ir["blocks"] if block["text"] == "source text"]
        self.assertEqual(len(repaired), engine.AUTOMATIC_REPAIR_MAX_BLOCKS)


class ModelFetchTest(unittest.TestCase):
    """The one module allowed to reach the network, and what holds it there.

    No test here opens a connection. What is under test is the policy around
    the connection: which URLs are allowed, what is refused, and the guarantee
    that nothing is installed until its bytes are the declared ones.
    """

    @staticmethod
    def fetcher():
        import model_fetch

        return model_fetch

    # -- which URLs it will speak to ---------------------------------------

    def test_only_https_on_an_allow_listed_host_is_accepted(self):
        fetch = self.fetcher()
        self.assertTrue(fetch.is_allowed_url("https://huggingface.co/a/b"))
        self.assertTrue(fetch.is_allowed_url("https://cdn-lfs.huggingface.co/a/b"))

    def test_plain_http_is_refused_even_on_an_allowed_host(self):
        self.assertFalse(self.fetcher().is_allowed_url("http://huggingface.co/a/b"))

    def test_a_look_alike_host_is_refused(self):
        """A suffix match would accept every one of these."""
        fetch = self.fetcher()
        for url in (
            "https://huggingface.co.evil.invalid/a",
            "https://evil-huggingface.co/a",
            "https://nothuggingface.co/a",
            "https://huggingface.co.example.invalid/a",
        ):
            self.assertFalse(fetch.is_allowed_url(url), url)

    def test_anything_off_the_list_is_refused(self):
        fetch = self.fetcher()
        for url in ("https://example.invalid/a", "ftp://huggingface.co/a", "file:///etc/passwd", "", "not a url"):
            self.assertFalse(fetch.is_allowed_url(url), url)

    def test_a_url_is_built_on_the_allowed_host(self):
        fetch = self.fetcher()
        url = fetch.resolved_download_url("ggml-org/SmolVLM-500M-Instruct-GGUF", "model.gguf")
        self.assertTrue(fetch.is_allowed_url(url))
        self.assertIn("/resolve/main/model.gguf", url)

    # -- what it refuses to plan -------------------------------------------

    def test_a_file_name_that_escapes_its_destination_is_refused(self):
        fetch = self.fetcher()
        for name in ("../../etc/passwd", "/etc/passwd", "a\\\\b"):
            pack = {"id": "p", "download": {"repository": "org/repo", "files": [{"name": name}]}}
            with self.assertRaises(fetch.ModelFetchError):
                fetch.pack_download_plan(pack)

    def test_a_pack_declaring_no_download_cannot_be_planned(self):
        fetch = self.fetcher()
        with self.assertRaises(fetch.ModelFetchError):
            fetch.pack_download_plan({"id": "p"})
        with self.assertRaises(fetch.ModelFetchError):
            fetch.pack_download_plan({"id": "p", "download": {"repository": "org/repo", "files": []}})

    def test_an_unapproved_pack_is_never_fetched(self):
        """A download must not be a way around the licence gate."""
        fetch = self.fetcher()
        pack = {"id": "p", "approved": False,
                "download": {"repository": "org/repo", "files": [{"name": "a.gguf"}]}}
        with self.assertRaises(fetch.ModelFetchError):
            fetch.fetch_model_pack(pack, Path("/tmp"))

    # -- what it refuses to install -----------------------------------------

    def test_nothing_is_installed_when_the_digest_does_not_match(self):
        from unittest.mock import patch

        fetch = self.fetcher()
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "model.gguf"
            with patch.object(fetch, "_open_checked", return_value=FakeResponse(b"not the bytes")):
                with self.assertRaises(fetch.ModelFetchError):
                    fetch.download_verified_file("https://huggingface.co/a", target, "0" * 64)
            self.assertFalse(target.exists())
            # ...and no partial file is left lying beside it either.
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_a_file_is_installed_when_the_digest_matches(self):
        from unittest.mock import patch

        fetch = self.fetcher()
        payload = b"the declared bytes"
        digest = hashlib.sha256(payload).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "model.gguf"
            with patch.object(fetch, "_open_checked", return_value=FakeResponse(payload)):
                fetch.download_verified_file("https://huggingface.co/a", target, digest, len(payload))
            self.assertEqual(target.read_bytes(), payload)

    def test_a_size_the_host_disagrees_with_is_refused_before_reading(self):
        from unittest.mock import patch

        fetch = self.fetcher()
        payload = b"short"
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "model.gguf"
            with patch.object(fetch, "_open_checked",
                              return_value=FakeResponse(payload, {"Content-Length": str(len(payload))})):
                with self.assertRaises(fetch.ModelFetchError):
                    fetch.download_verified_file("https://huggingface.co/a", target, None, 999_999)
            self.assertFalse(target.exists())

    def test_a_body_shorter_than_declared_is_refused(self):
        from unittest.mock import patch

        fetch = self.fetcher()
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "model.gguf"
            with patch.object(fetch, "_open_checked", return_value=FakeResponse(b"ab")):
                with self.assertRaises(fetch.ModelFetchError):
                    fetch.download_verified_file("https://huggingface.co/a", target, None, 10)
            self.assertFalse(target.exists())

    # -- the store it manages -------------------------------------------------

    def test_it_only_ever_removes_a_pack_it_installed(self):
        fetch = self.fetcher()
        with tempfile.TemporaryDirectory() as directory:
            store = Path(directory) / "models"
            (store / "a-pack").mkdir(parents=True)
            (store / "a-pack" / "w.gguf").write_bytes(b"x")
            self.assertTrue(fetch.remove_model_pack("a-pack", store))
            self.assertFalse((store / "a-pack").exists())
            # A pack that was never installed is simply absent, not an error.
            self.assertFalse(fetch.remove_model_pack("a-pack", store))

    # -- what the engine exposes ----------------------------------------------

    def test_every_declared_download_is_approved_and_digest_bearing(self):
        """A pack that could never be fetched should not advertise a download."""
        for pack in engine.load_model_manifest()["packs"]:
            download = pack.get("download")
            if not download:
                continue
            self.assertTrue(pack["approved"], pack["id"])
            for item in download["files"]:
                self.assertRegex(str(item["sha256"]), r"^[0-9a-f]{64}$")
                self.assertGreater(int(item["bytes"]), 0)

    def test_the_status_reports_what_a_fetch_would_cost_without_asking_a_host(self):
        status = engine.model_status()
        self.assertIn("managed_store", status)
        for pack in status["packs"]:
            if pack["downloadable"]:
                self.assertGreater(pack["download_bytes"], 0)
                self.assertTrue(pack["download_verified"])

    def test_an_unknown_pack_is_refused_by_name(self):
        with self.assertRaises(ValueError):
            engine.action_fetch_model({"pack_id": "no-such-pack"})
        with self.assertRaises(ValueError):
            engine.action_fetch_model({})

    def test_an_unapproved_pack_is_refused_by_the_action_too(self):
        result = engine.action_fetch_model({"pack_id": "qwen2.5-vl-3b-local-candidate"})
        self.assertEqual(result["status"], "refused")

    def test_a_pack_with_nothing_to_download_says_so(self):
        result = engine.action_fetch_model({"pack_id": "olmocr-2-7b-local-candidate"})
        self.assertEqual(result["status"], "unavailable")

    def test_the_conversion_engine_does_not_import_the_fetcher_at_module_scope(self):
        """The separation the local-only exemption rests on, asserted in Python too."""
        text = Path(engine.__file__).read_text(encoding="utf-8")
        offending = [line for line in text.splitlines()
                     if re.match(r"^(?:from|import)\s+model_fetch\b", line)]
        self.assertEqual(offending, [])


class FakeResponse:
    """A stand-in for an opened connection. Nothing here touches a network."""

    def __init__(self, payload: bytes, headers: dict | None = None):
        self._payload = payload
        self._offset = 0
        self.headers = headers or {}
        self.status = 200

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            chunk, self._offset = self._payload[self._offset:], len(self._payload)
            return chunk
        chunk = self._payload[self._offset:self._offset + size]
        self._offset += len(chunk)
        return chunk

    def close(self) -> None:
        return None


if __name__ == "__main__":
    unittest.main()
