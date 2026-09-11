from __future__ import annotations

import tempfile
from pathlib import Path
import unittest

from graphium_plus.pandoc import (
    FORMAT_DOCX,
    FORMAT_HTML,
    FORMAT_LATEX,
    FORMAT_ODT,
    PandocIdentity,
    PandocPlanError,
    pandoc_formats,
    pandoc_plan_matches_current_source,
    prepare_pandoc_export_plan,
    reject_remote_media,
)
from graphium_plus.references import ReferenceFileToken, ReferenceRecord


class PlusPandocPlanTests(unittest.TestCase):
    def setUp(self):
        self.identity = PandocIdentity("/usr/bin/pandoc", "3.1.11", (3, 1, 11))
        self.records = (
            ReferenceRecord(key="alpha2020", title="Alpha", type="article", authors=("Alpha, A.",), year="2020"),
            ReferenceRecord(key="beta2021", title="Beta", type="article", authors=("Beta, B.",), year="2021"),
        )
        self.token = ReferenceFileToken(True, 10, "a" * 64)

    def test_surface_is_small_and_pdf_is_deliberately_not_promised(self):
        values = pandoc_formats()
        self.assertEqual(tuple(item.id for item in values), (FORMAT_HTML, FORMAT_DOCX, FORMAT_ODT, FORMAT_LATEX))
        self.assertEqual(tuple(item.extension for item in values), (".html", ".docx", ".odt", ".tex"))
        self.assertNotIn("pdf", {item.id for item in values})

    def test_plan_uses_unsaved_source_and_only_cited_records_in_first_citation_order(self):
        with tempfile.TemporaryDirectory() as td:
            plan = prepare_pandoc_export_plan(
                identity=self.identity,
                format_id="docx",
                destination=Path(td) / "paper.docx",
                document_text="Draft [@beta2021] then [@alpha2020] and @beta2021.",
                source_state_id=17,
                document_directory=td,
                reference_records=self.records,
                reference_token=self.token,
            )
        self.assertEqual(plan.source_state_id, 17)
        self.assertEqual(plan.cited_keys, ("beta2021", "alpha2020"))
        self.assertEqual(tuple(record.key for record in plan.reference_records), plan.cited_keys)
        self.assertIn("@article{beta2021,", plan.bibliography_text)
        self.assertLess(plan.bibliography_text.index("beta2021"), plan.bibliography_text.index("alpha2020"))
        self.assertEqual(plan.format.writer, "docx")

    def test_missing_reference_and_wrong_extension_fail_before_process(self):
        with tempfile.TemporaryDirectory() as td:
            common = dict(
                identity=self.identity,
                document_text="Claim [@missing].",
                source_state_id=1,
                document_directory=td,
                reference_records=self.records,
                reference_token=self.token,
            )
            with self.assertRaisesRegex(PandocPlanError, "missing"):
                prepare_pandoc_export_plan(format_id="html", destination=Path(td) / "paper.html", **common)
            with self.assertRaisesRegex(PandocPlanError, r"\.docx"):
                prepare_pandoc_export_plan(
                    format_id="docx",
                    destination=Path(td) / "paper.txt",
                    **{**common, "document_text": "Claim."},
                )

    def test_remote_media_is_blocked_but_links_and_code_examples_are_allowed(self):
        with self.assertRaisesRegex(PandocPlanError, "Remote images"):
            reject_remote_media("![figure](https://example.test/figure.png)")
        with self.assertRaisesRegex(PandocPlanError, "Remote images"):
            reject_remote_media('<img src="https://example.test/figure.png">')
        reject_remote_media("[ordinary link](https://example.test/page)")
        reject_remote_media("`![example](https://example.test/code.png)`")
        reject_remote_media("```md\n![example](https://example.test/code.png)\n```\n")


    def test_pure_plan_owner_has_no_io_process_or_gtk_authority(self):
        source = (Path(__file__).parents[2] / "graphium_plus/pandoc.py").read_text(encoding="utf-8")
        for forbidden in (
            "import os", "import subprocess", "import threading", "import gi",
            ".read_text(", ".write_text(", ".open(", ".is_file(", ".is_dir(",
        ):
            self.assertNotIn(forbidden, source)

    def test_publication_revalidation_requires_exact_source_state_digest_and_reference_token(self):
        with tempfile.TemporaryDirectory() as td:
            plan = prepare_pandoc_export_plan(
                identity=self.identity,
                format_id="html",
                destination=Path(td) / "paper.html",
                document_text="Claim [@alpha2020].",
                source_state_id=9,
                document_directory=td,
                reference_records=self.records,
                reference_token=self.token,
            )
        self.assertEqual(
            pandoc_plan_matches_current_source(
                plan,
                current_text=plan.document_text,
                current_state_id=9,
                current_reference_token=self.token,
            ),
            (True, ""),
        )
        fresh, reason = pandoc_plan_matches_current_source(
            plan,
            current_text=plan.document_text + " changed",
            current_state_id=9,
            current_reference_token=self.token,
        )
        self.assertFalse(fresh)
        self.assertIn("document changed", reason)
        fresh, reason = pandoc_plan_matches_current_source(
            plan,
            current_text=plan.document_text,
            current_state_id=10,
            current_reference_token=self.token,
        )
        self.assertFalse(fresh)
        self.assertIn("document changed", reason)
        changed_token = ReferenceFileToken(True, 1, "0" * 64)
        fresh, reason = pandoc_plan_matches_current_source(
            plan,
            current_text=plan.document_text,
            current_state_id=9,
            current_reference_token=changed_token,
        )
        self.assertFalse(fresh)
        self.assertIn("Reference Library changed", reason)

    def test_no_citations_means_no_derived_bibliography(self):
        with tempfile.TemporaryDirectory() as td:
            plan = prepare_pandoc_export_plan(
                identity=self.identity,
                format_id="html",
                destination=Path(td) / "paper.html",
                document_text="# Paper\n\nNo citations.\n",
                source_state_id=2,
                document_directory=td,
                reference_records=self.records,
                reference_token=self.token,
            )
        self.assertEqual(plan.cited_keys, ())
        self.assertEqual(plan.reference_records, ())
        self.assertEqual(plan.bibliography_text, "")


if __name__ == "__main__":
    unittest.main()
