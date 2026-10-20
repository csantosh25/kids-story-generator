"""Workflow-structure tests for .github/workflows/publish-story-instagram.yml
(parsing the YAML itself, like tests/test_generate_reel_workflow.py)."""
import unittest
from pathlib import Path

import yaml

WORKFLOW_PATH = Path(".github/workflows/publish-story-instagram.yml")

SECRET_NAMES = (
    "INSTAGRAM_ACCESS_TOKEN",
    "INSTAGRAM_USER_ID",
    "CLOUDINARY_CLOUD_NAME",
    "CLOUDINARY_API_KEY",
    "CLOUDINARY_API_SECRET",
)


def _load():
    with open(WORKFLOW_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _triggers(workflow):
    # PyYAML parses the bare key `on` as boolean True.
    return workflow.get("on", workflow.get(True))


def _steps():
    return {step["name"]: step for step in _load()["jobs"]["publish"]["steps"]}


class PublishWorkflowTests(unittest.TestCase):

    def test_triggered_by_successful_daily_story_and_manual_only(self):
        triggers = _triggers(_load())

        self.assertEqual(set(triggers), {"workflow_run", "workflow_dispatch"})
        self.assertEqual(triggers["workflow_run"]["workflows"], ["Daily Story Generator"])
        self.assertEqual(triggers["workflow_run"]["types"], ["completed"])
        self.assertNotIn("schedule", triggers)
        self.assertIn("conclusion == 'success'", _load()["jobs"]["publish"]["if"])

    def test_manual_runs_default_to_dry_run(self):
        dry_run = _triggers(_load())["workflow_dispatch"]["inputs"]["dry_run"]
        self.assertIs(dry_run["default"], True)

    def test_least_privilege_and_serialised(self):
        workflow = _load()

        self.assertEqual(workflow["permissions"], {"contents": "write"})
        self.assertEqual(workflow["concurrency"]["group"], "instagram-publish")
        self.assertIs(workflow["concurrency"]["cancel-in-progress"], False)
        self.assertNotEqual(workflow["concurrency"]["group"], "daily-story-main")

    def test_live_step_gated_on_enable_flag_and_secrets_only_there(self):
        steps = _steps()

        self.assertIn("vars.INSTAGRAM_AUTO_PUBLISH_ENABLED", steps["Decide Mode"]["env"]["ENABLED"])
        self.assertEqual(steps["Publish Carousel"]["if"], "steps.mode.outputs.live == 'true'")
        self.assertIn("--live", steps["Publish Carousel"]["run"])

        for name in SECRET_NAMES:
            self.assertEqual(steps["Publish Carousel"]["env"][name], f"${{{{ secrets.{name} }}}}")

        dry = steps["Dry Run"]
        self.assertNotIn("--live", dry["run"])
        self.assertNotIn("secrets.", str(dry))

    def test_inputs_never_interpolated_into_shell(self):
        for step in _steps().values():
            self.assertNotIn("${{ inputs.", step.get("run", ""))

    def test_never_pushes_main_or_commits_files(self):
        text = WORKFLOW_PATH.read_text(encoding="utf-8")
        self.assertNotIn("git push", text)
        self.assertNotIn("git commit", text)
        self.assertNotIn("reel", text.lower().replace("never a reel", ""))


if __name__ == "__main__":
    unittest.main()
