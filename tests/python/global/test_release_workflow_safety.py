"""Release publication must always require a separate, explicit authorization."""

from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release-publish.yml"


class ReleaseWorkflowSafetyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = WORKFLOW.read_text(encoding="utf-8")
        cls.trigger = cls.workflow.split("permissions:", 1)[0]

    def test_publication_workflow_is_manual_only(self):
        self.assertIn("workflow_dispatch:", self.trigger)
        self.assertNotIn("\n  push:", self.trigger)

    def test_publication_defaults_to_disabled(self):
        publish_input = self.trigger.split("publish:", 1)[1]
        self.assertIn("default: false", publish_input)

    def test_every_irreversible_job_uses_explicit_publication_gate(self):
        gate = "external_publish == 'true'"
        self.assertIn("name: Publish cadgen to PyPI", self.workflow)
        self.assertIn("name: Deploy Docs", self.workflow)
        self.assertIn("name: Tag and GitHub Release", self.workflow)
        self.assertEqual(3, self.workflow.count(gate))


if __name__ == "__main__":
    unittest.main()
