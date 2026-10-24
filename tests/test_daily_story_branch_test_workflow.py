"""Workflow-structure tests for .github/workflows/daily-story-branch-test.yml
(parsing the YAML itself, like tests/test_publish_instagram_workflow.py).

The branch-test workflow must run run_daily.py on the selected branch only:
manual trigger, no commits/pushes, no retention, no Instagram publishing,
and it must not change the production Daily Story workflow."""
import unittest
from pathlib import Path

import yaml

WORKFLOW_PATH = Path(".github/workflows/daily-story-branch-test.yml")
PRODUCTION_PATH = Path(".github/workflows/daily-story.yml")
PUBLISH_PATH = Path(".github/workflows/publish-story-instagram.yml")

STORY_SECRETS = (
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "EMAIL_USERNAME",
    "EMAIL_PASSWORD",
    "EMAIL_TO",
)


def _load(path=WORKFLOW_PATH):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _triggers(workflow):
    # PyYAML parses the bare key `on` as boolean True.
    return workflow.get("on", workflow.get(True))


def _steps(path=WORKFLOW_PATH, job="generate-story"):
    return {step["name"]: step for step in _load(path)["jobs"][job]["steps"]}


class DailyStoryBranchTestWorkflowTests(unittest.TestCase):

    def test_manual_trigger_only(self):
        self.assertEqual(set(_triggers(_load())), {"workflow_dispatch"})

    def test_checks_out_selected_branch_without_push_credentials(self):
        checkout = _steps()["Checkout Selected Branch"]

        self.assertTrue(checkout["uses"].startswith("actions/checkout@"))
        self.assertNotIn("ref", checkout["with"])
        self.assertIs(checkout["with"]["persist-credentials"], False)
        self.assertNotIn("ref: main", WORKFLOW_PATH.read_text(encoding="utf-8"))

    def test_read_only_token_and_own_concurrency_group(self):
        workflow = _load()

        self.assertEqual(workflow["permissions"], {"contents": "read"})
        self.assertNotEqual(workflow["concurrency"]["group"], "daily-story-main")

    def test_runs_daily_generator_with_production_secrets(self):
        run_step = _steps()["Run Story Generator (sends a real email)"]
        production = _steps(PRODUCTION_PATH)["Run Story Generator"]

        self.assertIn("python run_daily.py", run_step["run"])

        for name in STORY_SECRETS:
            self.assertEqual(run_step["env"][name], production["env"][name])

    def test_never_commits_pushes_or_applies_retention(self):
        commands = "\n".join(step.get("run", "") for step in _steps().values())

        for forbidden in ("git push", "git commit", "git add", "apply_retention"):
            self.assertNotIn(forbidden, commands)

    def test_cannot_trigger_or_perform_instagram_publishing(self):
        workflow = _load()
        steps = _steps()

        # The publisher listens for this exact workflow name only.
        publish_triggers = _triggers(_load(PUBLISH_PATH))
        self.assertNotIn(workflow["name"], publish_triggers["workflow_run"]["workflows"])

        for step in steps.values():
            self.assertNotIn("publish_instagram", step.get("run", ""))

        # Parsed YAML excludes comments, so this covers every env/secret/run.
        parsed = str(workflow).upper()
        for name in ("INSTAGRAM", "CLOUDINARY"):
            self.assertNotIn(name, parsed)

    def test_uploads_generated_files_even_on_failure(self):
        steps = _steps()
        collect = steps["Collect Generated Files"]
        upload = steps["Upload Generated Files"]

        self.assertEqual(collect["if"], "always()")
        self.assertEqual(upload["if"], "always()")
        self.assertTrue(upload["uses"].startswith("actions/upload-artifact@"))
        self.assertEqual(upload["with"]["path"], "branch-test-artifacts/")
        self.assertIn("-- output data", collect["run"])

    def test_raw_logs_are_not_written_or_uploaded(self):
        # Artifact files are not secret-masked like the Actions log, so the
        # run output must stay in the Actions log only.
        steps = _steps()
        commands = "\n".join(step.get("run", "") for step in steps.values())

        self.assertEqual(
            steps["Run Story Generator (sends a real email)"]["run"].strip(),
            "python run_daily.py",
        )

        for forbidden in ("tee", ".log", "branch-test-logs", "> ", ">>"):
            self.assertNotIn(forbidden, commands)

        for step in steps.values():
            path = str(step.get("with", {}).get("path", ""))
            self.assertNotIn("log", path.lower())

    def test_no_secrets_outside_generator_step(self):
        for name, step in _steps().items():
            if name != "Run Story Generator (sends a real email)":
                self.assertNotIn("secrets.", str(step))


class ProductionDailyStoryWorkflowUnchangedTests(unittest.TestCase):

    def test_production_still_pinned_to_main_and_pushes_main(self):
        workflow = _load(PRODUCTION_PATH)
        steps = _steps(PRODUCTION_PATH)

        self.assertEqual(workflow["name"], "Daily Story Generator")
        self.assertEqual(steps["Checkout Repository"]["with"]["ref"], "main")
        self.assertIn("git push origin HEAD:main", steps["Commit and Push Generated Story"]["run"])
        self.assertIn("schedule", _triggers(workflow))


if __name__ == "__main__":
    unittest.main()
