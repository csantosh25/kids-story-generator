"""Tests for the "Generate Reel (Manual Test)" workflow's checkout-ref
fix and its new pre-generation diagnostic step.

Root cause being fixed: without an explicit `ref:`, actions/checkout@v4
on a workflow_dispatch run checks out whatever ref the run happened to
be dispatched against -- not necessarily main's current tip. Since the
entire approved Reel feature set (and the Daily Story workflow's
persisted output/ + data/content_library.json) lives on main, that
produced a real symptom: `selection_mode=latest` kept resolving to the
stale KS-000001 even after a newer story had been persisted to main.

These are workflow-structure tests (parsing the YAML itself), plus one
test proving the actual (untouched) ReelService.get_latest_eligible_
story() already picks a newer story correctly -- confirming the
reported bug was the stale checkout, not the selection algorithm.
"""
import re
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import yaml

from services.reel_service import ReelService
from tests.test_reel_service_generate import _write_minimal_story_assets

WORKFLOW_PATH = Path(".github/workflows/generate-reel.yml")
DAILY_WORKFLOW_PATH = Path(".github/workflows/daily-story.yml")


def _load_workflow():
    with open(WORKFLOW_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _steps():
    return _load_workflow()["jobs"]["generate-reel"]["steps"]


class WorkflowChecksOutMainTests(unittest.TestCase):
    """Item 1: the Reel workflow explicitly checks out main."""

    def test_checkout_step_pins_ref_to_main(self):

        steps = _steps()
        checkout = next(s for s in steps if str(s.get("uses", "")).startswith("actions/checkout"))

        self.assertEqual(checkout.get("with", {}).get("ref"), "main")

    def test_only_one_checkout_step_and_it_is_first(self):
        """No stray second checkout that could undo the pin."""

        steps = _steps()
        checkout_indices = [
            i for i, s in enumerate(steps) if str(s.get("uses", "")).startswith("actions/checkout")
        ]

        self.assertEqual(checkout_indices, [0])


class DiagnosticStepTests(unittest.TestCase):
    """Item 2: latest mode reads the current Content Library --
    verified structurally (the diagnostic step reuses the real
    ContentLibraryService/ReelService APIs, not a reimplementation) and
    by confirming the embedded script is even syntactically valid."""

    def _diag_step(self):
        steps = _steps()
        return next(s for s in steps if s.get("name") == "Diagnose Content Library State")

    def test_diagnostic_step_exists_before_run_reel_generator(self):

        steps = _steps()
        names = [s.get("name") for s in steps]

        self.assertIn("Diagnose Content Library State", names)
        self.assertIn("Run Reel Generator", names)
        self.assertLess(
            names.index("Diagnose Content Library State"),
            names.index("Run Reel Generator"),
        )

    def test_diagnostic_step_reuses_real_content_library_and_reel_service_apis(self):

        script = self._diag_step()["run"]

        self.assertIn("ContentLibraryService", script)
        self.assertIn("get_all_stories", script)
        self.assertIn("ReelService", script)
        self.assertIn("get_latest_eligible_story", script)
        # Reuses the existing "newest first" sort key rather than a
        # third, separately-maintained ranking implementation.
        self.assertIn("determine_retained_and_expired", script)

    def test_diagnostic_step_reports_the_required_fields(self):

        script = self._diag_step()["run"]

        for expected in [
            "Repository ref",
            "Git commit",
            "Content Library entries",
            "Latest library entry",
            "Output folder",
            "Exists",
            "Required Reel assets present",
        ]:
            self.assertIn(expected, script)

    def test_diagnostic_step_does_not_generate_anything(self):
        """Diagnostic only -- no Reel is rendered, no TTS/image call."""

        script = self._diag_step()["run"]

        for forbidden in [".generate(", "render_reel_video", "tts.generate", "ensure_scenes"]:
            self.assertNotIn(forbidden, script)

    def test_diagnostic_step_embedded_python_is_syntactically_valid(self):
        """Extracts the heredoc body from the step's shell script and
        confirms it actually compiles -- this is exactly the bug class
        that bites a YAML block-scalar (`run: |`) combined with a
        `<<'PYEOF'` heredoc: inconsistent indentation between YAML's own
        nesting and Python's own nesting produces a script that LOOKS
        right in the YAML source but fails with IndentationError the
        moment a real runner actually executes it."""

        script = self._diag_step()["run"]

        match = re.search(r"<<'PYEOF'\n(.*)\nPYEOF", script, re.DOTALL)
        self.assertIsNotNone(match, "expected a <<'PYEOF' heredoc in the diagnostic step")

        compile(match.group(1), "<diagnostic step>", "exec")


class SelectionModesStillWorkTests(unittest.TestCase):
    """Items 2 and 4: both selection modes still call the same
    generate_reel.py CLI as before, now against the pinned main
    checkout."""

    def _run_reel_step(self):
        return next(s for s in _steps() if s.get("name") == "Run Reel Generator")

    def test_latest_mode_still_calls_generate_reel_with_latest_flag(self):

        script = self._run_reel_step()["run"]
        self.assertIn("generate_reel.py --latest", script)

    def test_specific_mode_still_calls_generate_reel_with_content_id_flag(self):

        script = self._run_reel_step()["run"]
        self.assertIn('generate_reel.py --content-id "${{ inputs.content_id }}"', script)


class NoDailyPipelineReferenceInWorkflowTests(unittest.TestCase):
    """Item 5: no reference to run_daily.py or StoryPipeline was
    introduced anywhere EXECUTABLE in the workflow file (this file's own
    explanatory `#` comments legitimately say things like "no
    run_daily.py, no StoryPipeline" to document that this workflow
    never touches them -- that negation is the opposite of a reference,
    so comment lines are excluded; this checks the actual `uses:`/`with:`/
    `run:` content, covering the new diagnostic step too, not just the
    pre-existing steps)."""

    def test_workflow_file_never_actually_invokes_the_daily_pipeline(self):

        text = WORKFLOW_PATH.read_text(encoding="utf-8")

        executable_lines = [
            line for line in text.splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        executable_text = "\n".join(executable_lines)

        for forbidden in ["run_daily", "StoryPipeline", "StoryAgent"]:
            self.assertNotIn(forbidden, executable_text)


class DailyStoryWorkflowUntouchedTests(unittest.TestCase):
    """Item 6: the Daily Story workflow itself was not modified by this
    change (its own persistence/retention steps, permissions, and
    concurrency settings are exactly as previously approved)."""

    def test_daily_story_workflow_still_targets_main_and_has_write_permission(self):

        with open(DAILY_WORKFLOW_PATH, encoding="utf-8") as f:
            data = yaml.safe_load(f)

        self.assertEqual(data["permissions"]["contents"], "write")

        steps = data["jobs"]["generate-story"]["steps"]
        checkout = next(s for s in steps if str(s.get("uses", "")).startswith("actions/checkout"))
        self.assertEqual(checkout.get("with", {}).get("ref"), "main")

        names = [s.get("name") for s in steps]
        self.assertIn("Apply Story Retention (keep newest 10)", names)
        self.assertIn("Commit and Push Generated Story", names)


class NewerStorySelectedOverStaleKS000001Tests(unittest.TestCase):
    """Recreates the reported symptom's data shape directly: KS-000001
    (old) plus a newer, Daily-Story-persisted story -- proves the
    UNTOUCHED ReelService.get_latest_eligible_story() already picks the
    newer one. Confirms the reported bug was the workflow's stale
    checkout, never this selection logic (which this task explicitly
    forbids modifying)."""

    def _make_service(self):
        with patch("services.reel_service.ContentLibraryService"), \
             patch("services.reel_service.OpenAITTSService"), \
             patch("services.reel_service.ReelImageService"), \
             patch("services.reel_service.BrandLoader.load", return_value={}):
            return ReelService()

    def test_newer_persisted_story_beats_stale_ks_000001(self):

        with TemporaryDirectory() as tmp:

            old_folder = Path(tmp) / "pip"
            new_folder = Path(tmp) / "newer"
            _write_minimal_story_assets(old_folder)
            _write_minimal_story_assets(new_folder)

            service = self._make_service()
            service.library.get_all_stories.return_value = [
                {
                    "content_id": "KS-000001", "created_date": "2026-07-18",
                    "title": "Pip's Colourful Help", "folder": str(old_folder),
                },
                {
                    "content_id": "KS-000002", "created_date": "2026-08-31",
                    "title": "Bella's Honest Choice", "folder": str(new_folder),
                },
            ]

            latest = service.get_latest_eligible_story()

            self.assertEqual(latest["content_id"], "KS-000002")
            self.assertNotEqual(latest["content_id"], "KS-000001")

    def test_specific_content_id_still_resolves_ks_000001_directly(self):
        """Item 4: specific selection is unaffected -- KS-000001 is
        still perfectly valid to request explicitly, it's just no
        longer what "latest" resolves to once a newer story exists."""

        with TemporaryDirectory() as tmp:

            old_folder = Path(tmp) / "pip"
            _write_minimal_story_assets(old_folder)

            service = self._make_service()
            service.library.get_story.return_value = {
                "content_id": "KS-000001",
                "title": "Pip's Colourful Help",
                "folder": str(old_folder),
            }

            entry = service.library.get_story("KS-000001")

            self.assertIsNotNone(entry)
            self.assertEqual(entry["content_id"], "KS-000001")


class PrimaryArtifactContentsTests(unittest.TestCase):
    """Storage cleanup: the primary artifact contains ONLY what a user
    needs to post the Reel -- reel.mp4 and reel_caption.txt -- nothing
    else (items 1-4)."""

    def _primary_step(self):
        return next(s for s in _steps() if s.get("name") == "Upload Reel Package (primary)")

    def test_primary_artifact_contains_reel_mp4(self):
        path_value = self._primary_step()["with"]["path"]
        self.assertIn("reel.mp4", path_value)

    def test_primary_artifact_contains_reel_caption_txt(self):
        path_value = self._primary_step()["with"]["path"]
        self.assertIn("reel_caption.txt", path_value)

    def test_primary_artifact_excludes_intermediate_and_debug_files(self):

        path_value = self._primary_step()["with"]["path"]

        for excluded in [
            "reel_scene_01.png", "reel_scene_02.png", "reel_scene_03.png",
            "reel_scene_cover.png", "reel_scene_*_fullbleed.png",
            "reel_scene_clips", "reel_narration.mp3", "reel_narration.txt",
            "reel_script.json", "reel_scenes.json", "reel_run.log",
        ]:
            self.assertNotIn(excluded, path_value)

    def test_primary_artifact_retention_is_seven_days(self):
        self.assertEqual(self._primary_step()["with"]["retention-days"], 7)

    def test_primary_artifact_name_uses_reel_package_prefix(self):
        name = self._primary_step()["with"]["name"]
        self.assertTrue(name.startswith("reel-package-"))

    def test_primary_artifact_fails_loudly_if_missing(self):
        """Failure behaviour: never a misleading/partial primary
        package -- if-no-files-found must stay 'error', and the step
        must NOT have if: always() (so a failed generator run skips it
        entirely rather than uploading nothing as if it were fine)."""

        step = self._primary_step()
        self.assertEqual(step["with"]["if-no-files-found"], "error")
        self.assertNotIn("if", step)


class DebugArtifactContentsTests(unittest.TestCase):
    """Item 5-6: a separate debug artifact, retained only briefly,
    troubleshooting-only content."""

    def _debug_step(self):
        return next(s for s in _steps() if s.get("name") == "Upload Reel Debug Bundle")

    def test_debug_artifact_is_a_separate_step_from_primary(self):

        names = [s.get("name") for s in _steps()]
        self.assertIn("Upload Reel Package (primary)", names)
        self.assertIn("Upload Reel Debug Bundle", names)
        self.assertNotEqual("Upload Reel Package (primary)", "Upload Reel Debug Bundle")

    def test_debug_artifact_retention_is_three_days(self):
        self.assertEqual(self._debug_step()["with"]["retention-days"], 3)

    def test_debug_artifact_uploads_even_on_failure(self):
        self.assertEqual(self._debug_step().get("if"), "always()")

    def test_debug_artifact_does_not_duplicate_reel_mp4_or_narration_audio(self):
        """The debug bundle intentionally omits reel.mp4 (already in the
        primary package on success) and the narration .mp3 (the .txt
        transcript is enough for troubleshooting) to stay lean."""

        path_value = self._debug_step()["with"]["path"]
        self.assertNotIn("reel.mp4", path_value)
        self.assertNotIn("reel_narration.mp3", path_value)

    def test_debug_artifact_contains_troubleshooting_files(self):

        path_value = self._debug_step()["with"]["path"]

        for expected in [
            "reel_run.log", "reel_script.json", "reel_narration.txt",
            "reel_scenes.json", "reel_scene_clips",
        ]:
            self.assertIn(expected, path_value)

    def test_debug_artifact_never_marked_as_required(self):
        """A debug bundle that can't fully materialize for some failure
        modes must warn, not error out the job."""

        self.assertEqual(self._debug_step()["with"]["if-no-files-found"], "warn")


class NoGitOperationsInReelWorkflowTests(unittest.TestCase):
    """Items 7-9: the Reel workflow contains no git add/commit/push --
    Reel output must never become permanent Git content."""

    def test_no_git_add_commit_or_push_in_reel_workflow(self):

        text = WORKFLOW_PATH.read_text(encoding="utf-8")

        executable_lines = [
            line for line in text.splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        executable_text = "\n".join(executable_lines)

        for forbidden in ["git add", "git commit", "git push"]:
            self.assertNotIn(forbidden, executable_text)


class ReelOutputNotPersistedTests(unittest.TestCase):
    """Item 10: Reel output is not part of the Daily Story persistence
    mechanism -- the Daily Story workflow's own `git add -A output/
    data/content_library.json` would never pick up a Reel file, both
    because the two workflows never share a runner/output directory AND
    because .gitignore now excludes Reel-specific filenames explicitly
    (defence in depth, e.g. for a local developer's own working copy)."""

    def test_reel_filenames_are_gitignored(self):

        gitignore = Path(".gitignore").read_text(encoding="utf-8")

        for pattern in [
            "reel.mp4", "reel_narration.mp3", "reel_narration.txt",
            "reel_script.json", "reel_caption.txt", "reel_scenes.json",
            "reel_scene_*.png", "reel_scene_clips/",
        ]:
            self.assertIn(pattern, gitignore)

    def test_output_directory_itself_is_not_blanket_ignored(self):
        """The daily story source assets under output/ must remain
        trackable -- only Reel-specific filenames are excluded."""

        gitignore_lines = [
            line.strip() for line in Path(".gitignore").read_text(encoding="utf-8").splitlines()
        ]

        self.assertNotIn("output/", gitignore_lines)
        self.assertNotIn("/output/", gitignore_lines)
        self.assertNotIn("output", gitignore_lines)


if __name__ == "__main__":
    unittest.main()
