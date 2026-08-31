"""Reel storage cleanup: Reel outputs must never become permanent Git
repository content -- only short-lived GitHub Actions artifacts. This
file verifies the .gitignore patterns actually behave correctly against
real git (not just string-matching the file), that the Daily Story
retention rule (keep newest 10, no exceptions) is untouched, and that
Reel generation still actually produces reel.mp4 + reel_caption.txt.

See tests/test_generate_reel_workflow.py for the workflow-YAML-
structure tests (primary/debug artifact contents, retention-days, no
git add/commit/push).
"""
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from services.story_retention_service import DEFAULT_RETENTION_COUNT
from services.reel_service import ReelService

from tests.test_reel_service_generate import (
    _fake_ensure_scenes_writing_real_images,
    _fake_ffmpeg_writes_output,
    _write_minimal_story_assets,
    _VALID_METADATA,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
GITIGNORE_PATH = REPO_ROOT / ".gitignore"
DAILY_WORKFLOW_PATH = REPO_ROOT / ".github/workflows/daily-story.yml"

REEL_OUTPUT_RELATIVE_PATHS = [
    "output/some_story/reel.mp4",
    "output/some_story/reel_narration.mp3",
    "output/some_story/reel_narration.txt",
    "output/some_story/reel_script.json",
    "output/some_story/reel_caption.txt",
    "output/some_story/reel_metadata.json",
    "output/some_story/reel_scenes.json",
    "output/some_story/reel_scene_01.png",
    "output/some_story/reel_scene_02.png",
    "output/some_story/reel_scene_03.png",
    "output/some_story/reel_scene_cover.png",
    "output/some_story/reel_scene_01_fullbleed.png",
    "output/some_story/reel_scene_clips/scene_00.mp4",
]

STORY_ASSET_RELATIVE_PATHS = [
    "output/some_story/story.json",
    "output/some_story/cover.png",
    "output/some_story/cover_final.png",
    "output/some_story/slide_1.png",
    "output/some_story/slide_6.png",
    "output/some_story/story_book.pdf",
    "output/some_story/publish.md",
    "output/some_story/report.md",
]


def _git_check_ignore(relative_path):
    """True if `git check-ignore` -- run against the REAL repo's
    .gitignore, from the repo root -- says this path would be ignored.
    Exercises the actual .gitignore parsing/matching rules git itself
    uses, not a hand-rolled glob reimplementation."""

    result = subprocess.run(
        ["git", "check-ignore", "-q", relative_path],
        cwd=REPO_ROOT, capture_output=True,
    )
    return result.returncode == 0


class GitignoreActuallyIgnoresReelOutputsTests(unittest.TestCase):
    """Item: if .gitignore is changed, prove story source assets remain
    trackable while every real Reel output filename is ignored -- using
    real `git check-ignore`, not string matching."""

    def test_every_known_reel_output_filename_is_ignored(self):

        for relative_path in REEL_OUTPUT_RELATIVE_PATHS:
            with self.subTest(path=relative_path):
                self.assertTrue(
                    _git_check_ignore(relative_path),
                    f"{relative_path} should be gitignored but isn't",
                )

    def test_every_known_story_asset_filename_remains_trackable(self):

        for relative_path in STORY_ASSET_RELATIVE_PATHS:
            with self.subTest(path=relative_path):
                self.assertFalse(
                    _git_check_ignore(relative_path),
                    f"{relative_path} must remain trackable but is gitignored",
                )

    def test_an_arbitrary_non_reel_file_under_output_is_not_ignored(self):
        """The whole output/ directory must not be blanket-ignored --
        only Reel-specific filenames."""

        self.assertFalse(_git_check_ignore("output/some_story/some_future_file.txt"))


class DailyStoryRetentionUnchangedTests(unittest.TestCase):
    """Item 11: latest-10 retention remains exactly as before -- this
    task must not touch story_retention_service.py at all."""

    def test_retention_count_is_still_exactly_ten(self):
        self.assertEqual(DEFAULT_RETENTION_COUNT, 10)

    def test_story_retention_service_module_is_untouched(self):
        """Confirms via git itself (not an assumption) that this file
        has zero diff against HEAD -- i.e. this task didn't modify it."""

        result = subprocess.run(
            ["git", "diff", "--quiet", "HEAD", "--", "services/story_retention_service.py"],
            cwd=REPO_ROOT,
        )
        self.assertEqual(result.returncode, 0, "story_retention_service.py has unexpected changes")


class DailyStoryWorkflowUnaffectedTests(unittest.TestCase):
    """Item 12: the Daily Story workflow's own generate -> update
    library -> retain -> commit/push sequence is untouched by this
    storage-cleanup change (Reel output never reaches its runner, so no
    workflow-level protection was actually needed there -- verified by
    the zero-diff check, not assumed)."""

    def test_daily_story_workflow_has_zero_diff(self):

        result = subprocess.run(
            ["git", "diff", "--quiet", "HEAD", "--", ".github/workflows/daily-story.yml"],
            cwd=REPO_ROOT,
        )
        self.assertEqual(result.returncode, 0, "daily-story.yml has unexpected changes")

    def test_daily_story_git_add_pathspec_unchanged(self):
        """The workflow's own `git add -A output/ data/content_library.
        json` pathspec is exactly as before -- combined with the new
        .gitignore rules (proven per-file against real git in
        GitignoreActuallyIgnoresReelOutputsTests above), a hypothetical
        stray Reel file under output/ would never actually get staged,
        even though this workflow never produces one in practice (Reel
        generation runs in a completely separate job/runner)."""

        text = DAILY_WORKFLOW_PATH.read_text(encoding="utf-8")
        self.assertIn("git add -A output/ data/content_library.json", text)


class ReelGenerationStillProducesRequiredFilesTests(unittest.TestCase):
    """Item 13: Reel generation still produces reel.mp4 and
    reel_caption.txt after this storage-only change -- no regression to
    ReelService.generate() itself."""

    def test_reel_mp4_and_reel_caption_txt_still_produced(self):

        with TemporaryDirectory() as tmp:

            folder = Path(tmp) / "story"
            _write_minimal_story_assets(folder)

            with patch("services.reel_service.ContentLibraryService"), \
                 patch("services.reel_service.OpenAITTSService"), \
                 patch("services.reel_service.ReelImageService"), \
                 patch("services.reel_service.BrandLoader.load", return_value={}):
                service = ReelService()

            service.library.get_story.return_value = {
                "content_id": "KS-000001", "title": "Test Story", "folder": str(folder),
            }

            def fake_tts_generate(text, output_file, **kwargs):
                Path(output_file).write_bytes(b"fake-mp3-bytes")
                return output_file

            service.tts.generate.side_effect = fake_tts_generate
            service.images.ensure_scenes.side_effect = _fake_ensure_scenes_writing_real_images(folder)

            with patch("services.reel_service.check_ffmpeg_available", return_value=True), \
                 patch("services.reel_service.run_ffmpeg_command", side_effect=_fake_ffmpeg_writes_output), \
                 patch("services.reel_service.probe_video_metadata", return_value=_VALID_METADATA):
                result = service.generate(content_id="KS-000001")

            self.assertTrue(result.exists())
            self.assertTrue((folder / "reel.mp4").exists())
            self.assertTrue((folder / "reel_caption.txt").exists())


if __name__ == "__main__":
    unittest.main()
