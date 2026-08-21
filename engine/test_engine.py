import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).with_name("philon_engine.py")
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
            features = engine.native_pdf_features(source, 1)
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

    def test_marker_style_json_has_page_tree_source_polygons_and_embedded_native_images(self):
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
            pages = engine.render_marker_style_json(ir, output)
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
            self.assertFalse("marker_json" in result["outputs"])
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
        """The control: strong evidence is still required."""
        pages = [{"number": n, "text": f"Ordinary opening line {n}\nBody sentence number {n}.\n{n}"}
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
        self.assertEqual(engine.clean_reading_text("Adobe Photoshop \uf6d9"), "Adobe Photoshop \uf6d9")
        pages = [{"id": "page-1", "number": 1, "method": "pdfium-native",
                  "route": {"native_text_health": health}}]
        codes = [warning.code for warning in engine.verified_checks(pages, [])]
        self.assertIn("PRIVATE_USE_CHARACTERS", codes)

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


if __name__ == "__main__":
    unittest.main()
