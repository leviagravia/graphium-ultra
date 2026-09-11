import ast
import pathlib
import unittest

from graphium_plus.bibtex import (
    analyze_bibliography_import, export_bibliography, import_bibliography,
    plan_bibliography_import,
)
from graphium_plus.references import ReferenceRecord


class BibInteropTests(unittest.TestCase):
    def test_import_bibtex_article_maps_canonical_fields(self):
        result = import_bibliography(r'''@article{alpha2020,
          author = {Alpha, Anne and Beta, Bob},
          title = {A {DNA} Study},
          year = 2020,
          journal = {Journal of Tests},
          volume = {12}, number = {3}, pages = {1--9},
          doi = {10.1000/example}, note = {Imported note}
        }''')
        self.assertTrue(result.safe_to_apply, result.diagnostics)
        self.assertEqual(len(result.records), 1)
        record = result.records[0]
        self.assertEqual(record.key, "alpha2020")
        self.assertEqual(record.type, "article")
        self.assertEqual(record.authors, ("Alpha, Anne", "Beta, Bob"))
        self.assertEqual(record.title, "A DNA Study")
        self.assertEqual(record.year, "2020")
        self.assertEqual(record.container_title, "Journal of Tests")
        self.assertEqual(record.issue, "3")
        self.assertEqual(record.extra_fields, (("note", "Imported note"),))

    def test_import_preserves_unknown_latex_command_groups_without_corruption(self):
        result = import_bibliography(r"""@book{k, title={A \textit{DNA} Study and {RNA}}}""")
        self.assertTrue(result.safe_to_apply, result.diagnostics)
        self.assertEqual(result.records[0].title, r"A \textit{DNA} Study and RNA")

    def test_import_biblatex_synonyms_and_date(self):
        result = import_bibliography(r'''@book{book1,
          title={Book Title}, editor={One, Ed and Two, Ed}, date={2024-05-01},
          location={Rome}, langid={italian}, isbn={123}
        }''')
        self.assertTrue(result.safe_to_apply, result.diagnostics)
        record = result.records[0]
        self.assertEqual(record.editors, ("One, Ed", "Two, Ed"))
        self.assertEqual(record.year, "2024")
        self.assertEqual(record.location, "Rome")
        self.assertEqual(record.language, "italian")
        self.assertIn(("date", "2024-05-01"), record.extra_fields)

    def test_string_macros_and_concatenation_are_local_and_pure(self):
        result = import_bibliography(r'''@string{jn = "Journal " # "Name"}
        @article{k, title={T}, author={A}, journal=jn, month=jan}''')
        self.assertTrue(result.safe_to_apply, result.diagnostics)
        record = result.records[0]
        self.assertEqual(record.container_title, "Journal Name")
        self.assertIn(("month", "January"), record.extra_fields)

    def test_unresolved_macro_and_inheritance_fail_closed(self):
        result = import_bibliography(r'''@incollection{k,
          title={Child}, booktitle=unknownmacro, crossref={parent}
        }''')
        self.assertFalse(result.safe_to_apply)
        self.assertEqual(result.records[0].container_title, "unknownmacro")
        kinds = {item.kind for item in result.diagnostics}
        self.assertIn("unresolved-macro", kinds)
        self.assertIn("unresolved-inheritance", kinds)

    def test_duplicate_key_is_diagnostic_not_implicit_merge(self):
        result = import_bibliography("@book{k,title={One}}\n@book{k,title={Two}}")
        self.assertEqual([r.title for r in result.records], ["One"])
        self.assertFalse(result.safe_to_apply)
        self.assertIn("duplicate-key", {item.kind for item in result.diagnostics})

    def test_comments_preamble_and_stray_text_do_not_become_records(self):
        result = import_bibliography("noise\n@comment{ignored}\n@preamble{\"x\"}\n@misc{k,title={T}}")
        self.assertEqual([r.key for r in result.records], ["k"])
        self.assertIn("stray-text", {item.kind for item in result.diagnostics})
        self.assertIn("ignored-preamble", {item.kind for item in result.diagnostics})
        self.assertTrue(all(not d.blocking for d in result.diagnostics))

    def test_export_bibtex_is_deterministic_and_uses_bibtex_names(self):
        record = ReferenceRecord(
            key="a", type="article", title="Title", authors=("Alpha, A.",), year="2020",
            container_title="Journal", location="Rome", issue="2", language="english",
            extra_fields=(("note", "N"),),
        )
        result = export_bibliography((record,), flavor="bibtex")
        self.assertTrue(result.complete, result.diagnostics)
        self.assertEqual(result.text, '''@article{a,
  author = {Alpha, A.},
  title = {Title},
  year = {2020},
  journal = {Journal},
  address = {Rome},
  number = {2},
  language = {english},
  note = {N},
}\n''')

    def test_export_biblatex_uses_biblatex_names(self):
        record = ReferenceRecord(
            key="a", type="article", title="Title", container_title="Journal",
            location="Rome", language="italian",
        )
        result = export_bibliography((record,), flavor="biblatex")
        self.assertIn("journaltitle = {Journal}", result.text)
        self.assertIn("location = {Rome}", result.text)
        self.assertIn("langid = {italian}", result.text)
        self.assertNotIn("address =", result.text)

    def test_simple_roundtrip_preserves_canonical_record(self):
        original = ReferenceRecord(
            key="round", type="book", title="Round Trip", authors=("Doe, Jane",),
            year="2025", publisher="Press", location="Paris", isbn="123",
        )
        exported = export_bibliography((original,), flavor="biblatex")
        imported = import_bibliography(exported.text)
        self.assertTrue(exported.complete)
        self.assertTrue(imported.safe_to_apply, imported.diagnostics)
        self.assertEqual(imported.records, (original,))

    def test_import_analysis_separates_new_identical_and_conflicting_keys(self):
        existing = (
            ReferenceRecord(key="same", title="Same"),
            ReferenceRecord(key="conflict", title="Old"),
        )
        incoming = (
            ReferenceRecord(key="same", title="Same"),
            ReferenceRecord(key="conflict", title="New"),
            ReferenceRecord(key="fresh", title="Fresh"),
        )
        analysis = analyze_bibliography_import(existing, incoming)
        self.assertEqual([r.key for r in analysis.new_records], ["fresh"])
        self.assertEqual([r.key for r in analysis.identical_records], ["same"])
        self.assertEqual([c.key for c in analysis.conflicts], ["conflict"])

    def test_import_plan_requires_explicit_whole_record_conflict_policy(self):
        existing = (
            ReferenceRecord(key="first", title="First"),
            ReferenceRecord(key="conflict", title="Old", year="2020"),
        )
        incoming = (
            ReferenceRecord(key="conflict", title="New", year="2025"),
            ReferenceRecord(key="fresh", title="Fresh"),
        )
        keep = plan_bibliography_import(existing, incoming, conflict_policy="keep-existing")
        self.assertEqual([(r.key, r.title) for r in keep.records], [
            ("first", "First"), ("conflict", "Old"), ("fresh", "Fresh")
        ])
        self.assertEqual(keep.added_keys, ("fresh",))
        self.assertEqual(keep.replaced_keys, ())
        self.assertIn("conflict", keep.unchanged_keys)

        replace = plan_bibliography_import(existing, incoming, conflict_policy="replace-existing")
        self.assertEqual([(r.key, r.title) for r in replace.records], [
            ("first", "First"), ("conflict", "New"), ("fresh", "Fresh")
        ])
        self.assertEqual(replace.replaced_keys, ("conflict",))
        with self.assertRaises(ValueError):
            plan_bibliography_import(existing, incoming, conflict_policy="merge")

    def test_interop_module_has_no_io_gui_process_or_network_authority(self):
        path = pathlib.Path(__file__).parents[2] / "graphium_plus" / "bibtex.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports = set()
        calls = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module)
            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    calls.add(node.func.id)
                elif isinstance(node.func, ast.Attribute):
                    calls.add(node.func.attr)
        forbidden_import_roots = {"gi", "sqlite3", "subprocess", "socket", "urllib", "requests", "httpx"}
        self.assertFalse({name.split(".")[0] for name in imports} & forbidden_import_roots)
        self.assertFalse({"open", "write_text", "write_bytes", "Popen", "run"} & calls)


if __name__ == "__main__":
    unittest.main()
