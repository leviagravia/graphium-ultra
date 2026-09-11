from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from graphium.infrastructure.guarded_file_writer import GuardedFileWriter
from graphium_plus.pandoc import PandocIdentity, prepare_pandoc_export_plan
from graphium_plus.pandoc_process import PandocArtifact
from graphium_plus.pandoc_publication import PandocPublicationError, publish_pandoc_artifact
from graphium_plus.references import ReferenceFileToken, ReferenceRecord


class PlusPandocPublicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.writer = GuardedFileWriter()
        self.record = ReferenceRecord(
            key="alpha2020", type="article", title="Alpha", authors=("Alpha, A.",), year="2020"
        )
        self.token = ReferenceFileToken(True, 8, "a" * 64)
        self.identity = PandocIdentity("/usr/bin/pandoc", "3.1.11", (3, 1, 11))

    def tearDown(self):
        self.temp.cleanup()

    def plan(self, target: Path, text: str = "Claim [@alpha2020]."):
        return prepare_pandoc_export_plan(
            identity=self.identity,
            format_id="html",
            destination=target,
            document_text=text,
            source_state_id=7,
            document_directory=self.root,
            reference_records=(self.record,),
            reference_token=self.token,
        )

    def test_success_uses_exact_observation_and_one_core_writer_commit(self):
        target = self.root / "paper.html"
        plan = self.plan(target)
        observed = self.writer.observe_target(str(target))
        result = publish_pandoc_artifact(
            plan=plan,
            artifact=PandocArtifact("built", data=b"<html>ok</html>"),
            current_text=plan.document_text,
            current_state_id=plan.source_state_id,
            current_reference_token=plan.reference_token,
            writer=self.writer,
            target_observation=observed,
        )
        self.assertEqual(target.read_bytes(), b"<html>ok</html>")
        self.assertEqual(result.write_result.logical_target_path, str(target))

    def test_changed_source_or_reference_refuses_before_target_commit(self):
        for current_text, token in (
            ("changed", self.token),
            ("Claim [@alpha2020].", ReferenceFileToken(True, 9, "b" * 64)),
        ):
            target = self.root / ("source.html" if current_text == "changed" else "refs.html")
            plan = self.plan(target)
            observed = self.writer.observe_target(str(target))
            with self.assertRaises(PandocPublicationError):
                publish_pandoc_artifact(
                    plan=plan,
                    artifact=PandocArtifact("built", data=b"no"),
                    current_text=current_text,
                    current_state_id=plan.source_state_id,
                    current_reference_token=token,
                    writer=self.writer,
                    target_observation=observed,
                )
            self.assertFalse(target.exists())

    def test_target_appearing_or_existing_target_changing_is_refused(self):
        absent = self.root / "appeared.html"
        plan = self.plan(absent)
        observed = self.writer.observe_target(str(absent))
        absent.write_bytes(b"external")
        with self.assertRaisesRegex(PandocPublicationError, "destination changed"):
            publish_pandoc_artifact(
                plan=plan,
                artifact=PandocArtifact("built", data=b"new"),
                current_text=plan.document_text,
                current_state_id=7,
                current_reference_token=self.token,
                writer=self.writer,
                target_observation=observed,
            )
        self.assertEqual(absent.read_bytes(), b"external")

        existing = self.root / "existing.html"
        existing.write_bytes(b"old")
        plan2 = self.plan(existing)
        observed2 = self.writer.observe_target(str(existing))
        existing.write_bytes(b"external change")
        with self.assertRaisesRegex(PandocPublicationError, "destination changed"):
            publish_pandoc_artifact(
                plan=plan2,
                artifact=PandocArtifact("built", data=b"new"),
                current_text=plan2.document_text,
                current_state_id=7,
                current_reference_token=self.token,
                writer=self.writer,
                target_observation=observed2,
            )
        self.assertEqual(existing.read_bytes(), b"external change")

    def test_non_built_artifact_is_refused(self):
        target = self.root / "failed.html"
        plan = self.plan(target)
        observed = self.writer.observe_target(str(target))
        with self.assertRaisesRegex(PandocPublicationError, "publishable"):
            publish_pandoc_artifact(
                plan=plan,
                artifact=PandocArtifact("error", message="failed"),
                current_text=plan.document_text,
                current_state_id=7,
                current_reference_token=self.token,
                writer=self.writer,
                target_observation=observed,
            )
        self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()
