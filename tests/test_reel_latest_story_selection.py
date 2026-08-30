"""Tests for the "Generate Reel (Manual Test)" workflow usability
improvement: choosing either the latest eligible story or a specific
Content ID.

Covers:
- ReelService.get_latest_eligible_story() -- reuses list_reel_eligible_
  stories() for the actual eligibility check (see services/
  reel_service.py), then picks the most recent by created_date (never
  simply the highest content_id), skipping an incomplete newer story
  automatically.
- generate_reel.py's --latest CLI path, mirroring the existing
  --content-id path (mocked ReelService, no real OpenAI/ffmpeg calls).
- A static guard: the Reel path never references run_daily.py or
  StoryPipeline, regardless of selection mode.
"""
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

import generate_reel
from services.reel_service import ReelService
from tests.test_reel_service_generate import _write_minimal_story_assets


def _entry(content_id, created_date, folder, title="Test Story", character=None):
    return {
        "content_id": content_id,
        "created_date": created_date,
        "title": title,
        "folder": str(folder),
        "character": character or {"name": "Pip", "species": "Squirrel"},
        "reel": {"generated": False, "video": ""},
    }


class GetLatestEligibleStoryTests(unittest.TestCase):

    def _make_service(self):

        with patch("services.reel_service.ContentLibraryService"), \
             patch("services.reel_service.OpenAITTSService"), \
             patch("services.reel_service.ReelImageService"), \
             patch("services.reel_service.BrandLoader.load", return_value={}):
            return ReelService()

    def test_picks_the_most_recently_created_eligible_story(self):

        with TemporaryDirectory() as tmp:

            older = Path(tmp) / "older"
            newer = Path(tmp) / "newer"
            _write_minimal_story_assets(older)
            _write_minimal_story_assets(newer)

            service = self._make_service()
            service.library.get_all_stories.return_value = [
                _entry("KS-000001", "2026-07-01", older),
                _entry("KS-000002", "2026-08-15", newer),
            ]

            result = service.get_latest_eligible_story()

            self.assertEqual(result["content_id"], "KS-000002")

    def test_selection_is_by_created_date_not_highest_content_id(self):
        """The higher-numbered Content ID is deliberately the OLDER
        story here -- selection must still land on the one with the
        later created_date."""

        with TemporaryDirectory() as tmp:

            higher_id_older = Path(tmp) / "higher_id_older"
            lower_id_newer = Path(tmp) / "lower_id_newer"
            _write_minimal_story_assets(higher_id_older)
            _write_minimal_story_assets(lower_id_newer)

            service = self._make_service()
            service.library.get_all_stories.return_value = [
                _entry("KS-000099", "2026-01-01", higher_id_older),
                _entry("KS-000003", "2026-08-20", lower_id_newer),
            ]

            result = service.get_latest_eligible_story()

            self.assertEqual(result["content_id"], "KS-000003")

    def test_newest_story_missing_assets_is_skipped(self):
        """The newest-by-date story has no assets on disk at all (folder
        doesn't exist) -- selection must fall through to the next-newest
        story that actually has everything a Reel needs."""

        with TemporaryDirectory() as tmp:

            complete_older = Path(tmp) / "complete_older"
            _write_minimal_story_assets(complete_older)

            incomplete_newest = Path(tmp) / "does_not_exist_on_disk"

            service = self._make_service()
            service.library.get_all_stories.return_value = [
                _entry("KS-000001", "2026-07-01", complete_older),
                _entry("KS-000002", "2026-08-25", incomplete_newest),
            ]

            result = service.get_latest_eligible_story()

            self.assertEqual(result["content_id"], "KS-000001")

    def test_newest_story_with_partial_assets_on_disk_is_skipped(self):
        """Folder exists but is missing required Reel assets (e.g. no
        cover/slides yet -- a story still mid-generation) -- also
        skipped in favour of the next-newest eligible one."""

        with TemporaryDirectory() as tmp:

            complete_older = Path(tmp) / "complete_older"
            _write_minimal_story_assets(complete_older)

            partial_newer = Path(tmp) / "partial_newer"
            partial_newer.mkdir()
            (partial_newer / "story.json").write_text("{}", encoding="utf-8")
            # No cover.png/cover_final.png, no slide_*.png -- incomplete.

            service = self._make_service()
            service.library.get_all_stories.return_value = [
                _entry("KS-000001", "2026-07-01", complete_older),
                _entry("KS-000002", "2026-08-25", partial_newer),
            ]

            result = service.get_latest_eligible_story()

            self.assertEqual(result["content_id"], "KS-000001")

    def test_no_eligible_story_returns_none(self):

        service = self._make_service()
        service.library.get_all_stories.return_value = [
            _entry("KS-000001", "2026-07-01", Path("does/not/exist")),
        ]

        self.assertIsNone(service.get_latest_eligible_story())

    def test_empty_library_returns_none(self):

        service = self._make_service()
        service.library.get_all_stories.return_value = []

        self.assertIsNone(service.get_latest_eligible_story())

    def test_ties_on_created_date_break_on_higher_content_id(self):

        with TemporaryDirectory() as tmp:

            first = Path(tmp) / "first"
            second = Path(tmp) / "second"
            _write_minimal_story_assets(first)
            _write_minimal_story_assets(second)

            service = self._make_service()
            service.library.get_all_stories.return_value = [
                _entry("KS-000005", "2026-08-20", first),
                _entry("KS-000007", "2026-08-20", second),
            ]

            result = service.get_latest_eligible_story()

            self.assertEqual(result["content_id"], "KS-000007")

    def test_malformed_created_date_does_not_crash_or_win(self):

        with TemporaryDirectory() as tmp:

            good = Path(tmp) / "good"
            bad_date = Path(tmp) / "bad_date"
            _write_minimal_story_assets(good)
            _write_minimal_story_assets(bad_date)

            service = self._make_service()
            service.library.get_all_stories.return_value = [
                _entry("KS-000001", "2026-08-20", good),
                _entry("KS-000002", "not-a-date", bad_date),
            ]

            result = service.get_latest_eligible_story()

            self.assertEqual(result["content_id"], "KS-000001")

    def test_reuses_list_reel_eligible_stories_not_a_separate_rule_set(self):
        """Eligibility must mean exactly the same thing here as it does
        for the interactive picker -- verified by mocking list_reel_
        eligible_stories() itself and confirming get_latest_eligible_
        story() is driven entirely by its output."""

        service = self._make_service()
        service.list_reel_eligible_stories = MagicMock(return_value=[
            _entry("KS-000001", "2026-01-01", Path("a")),
            _entry("KS-000002", "2026-08-20", Path("b")),
        ])

        result = service.get_latest_eligible_story()

        service.list_reel_eligible_stories.assert_called_once()
        self.assertEqual(result["content_id"], "KS-000002")


class RunLatestNonInteractiveCliTests(unittest.TestCase):
    """Mirrors TestRunNonInteractive in test_generate_reel_cli.py for
    the new --latest path -- ReelService is fully mocked, so no real
    OpenAI/ffmpeg call is ever made."""

    def test_no_eligible_story_exits_cleanly_without_generating(self):

        service = MagicMock()
        service.get_latest_eligible_story.return_value = None

        with self.assertRaises(SystemExit) as ctx:
            generate_reel.run_latest_non_interactive(service)

        self.assertEqual(ctx.exception.code, 1)
        service.generate.assert_not_called()

    def test_resolves_and_generates_the_latest_eligible_story(self):

        entry = {
            "content_id": "KS-000008",
            "title": "Bella's Honest Choice",
            "created_date": "2026-08-30",
            "character": {"name": "Bella", "species": "Bear"},
            "folder": "output/some_folder",
        }
        service = MagicMock()
        service.get_latest_eligible_story.return_value = entry
        service.generate.return_value = Path("output/some_folder/reel.mp4")

        with patch("generate_reel.probe_video_metadata", return_value={}), \
             patch("builtins.input", side_effect=AssertionError("must not prompt")):
            generate_reel.run_latest_non_interactive(service)

        service.generate.assert_called_once_with(
            content_id="KS-000008",
            overwrite=True,
            music_track=None,
        )

    def test_never_posts_to_instagram(self):

        entry = {
            "content_id": "KS-000008", "title": "T", "created_date": "2026-08-30",
            "character": {"name": "Bella", "species": "Bear"}, "folder": "f",
        }
        service = MagicMock()
        service.get_latest_eligible_story.return_value = entry
        service.generate.return_value = Path("f/reel.mp4")

        with patch("generate_reel.probe_video_metadata", return_value={}):
            generate_reel.run_latest_non_interactive(service)

        for called in service.method_calls:
            self.assertNotIn("instagram", called[0].lower())

    def test_reel_generation_error_exits_cleanly(self):

        from services.reel_service import ReelGenerationError

        entry = {
            "content_id": "KS-000008", "title": "T", "created_date": "2026-08-30",
            "character": {"name": "Bella", "species": "Bear"}, "folder": "f",
        }
        service = MagicMock()
        service.get_latest_eligible_story.return_value = entry
        service.generate.side_effect = ReelGenerationError("tts failed")

        with self.assertRaises(SystemExit) as ctx:
            generate_reel.run_latest_non_interactive(service)

        self.assertEqual(ctx.exception.code, 1)


class ParseArgsAndRoutingTests(unittest.TestCase):
    """Extends TestParseArgs/TestMainRouting in test_generate_reel_cli.py
    for the new --latest flag, without touching those existing tests."""

    def test_latest_flag_is_parsed(self):

        args = generate_reel.parse_args(["--latest"])
        self.assertTrue(args.latest)
        self.assertIsNone(args.content_id)

    def test_no_flags_latest_defaults_to_false(self):

        args = generate_reel.parse_args([])
        self.assertFalse(args.latest)

    def test_content_id_and_latest_are_mutually_exclusive(self):

        with self.assertRaises(SystemExit):
            generate_reel.parse_args(["--content-id", "KS-000001", "--latest"])

    @patch("generate_reel.run_latest_non_interactive")
    @patch("generate_reel.run_non_interactive")
    @patch("generate_reel.run_interactive")
    @patch("generate_reel.ReelService")
    @patch("generate_reel.check_ffmpeg_available", return_value=True)
    def test_latest_flag_routes_to_run_latest_non_interactive(
        self, mock_ffmpeg, mock_service_cls, mock_interactive, mock_non_interactive, mock_latest,
    ):
        generate_reel.main(["--latest"])

        mock_latest.assert_called_once_with(mock_service_cls.return_value)
        mock_non_interactive.assert_not_called()
        mock_interactive.assert_not_called()

    @patch("generate_reel.run_latest_non_interactive")
    @patch("generate_reel.run_non_interactive")
    @patch("generate_reel.run_interactive")
    @patch("generate_reel.ReelService")
    @patch("generate_reel.check_ffmpeg_available", return_value=True)
    def test_content_id_still_routes_to_non_interactive_not_latest(
        self, mock_ffmpeg, mock_service_cls, mock_interactive, mock_non_interactive, mock_latest,
    ):
        generate_reel.main(["--content-id", "KS-000001"])

        mock_non_interactive.assert_called_once_with(mock_service_cls.return_value, "KS-000001")
        mock_latest.assert_not_called()
        mock_interactive.assert_not_called()


class ExistingContentIdCliBehaviourStillIntactTests(unittest.TestCase):
    """Explicit backward-compatibility guard for `python generate_reel.py
    --content-id KS-000001` after the --latest refactor of run_non_
    interactive's shared internals."""

    def test_selects_exact_content_id_no_prompt_no_music(self):

        entry = {
            "content_id": "KS-000001",
            "title": "Pip's Colourful Help",
            "created_date": "2026-07-18",
            "character": {"name": "Pip", "species": "Squirrel"},
            "folder": "output/some_folder",
        }
        service = MagicMock()
        service.library.get_story.return_value = entry
        service.generate.return_value = Path("output/some_folder/reel.mp4")

        with patch("generate_reel.probe_video_metadata", return_value={}), \
             patch("builtins.input", side_effect=AssertionError("must not prompt")):
            generate_reel.run_non_interactive(service, "KS-000001")

        service.generate.assert_called_once_with(
            content_id="KS-000001",
            overwrite=True,
            music_track=None,
        )

    def test_unknown_content_id_still_exits_cleanly(self):

        service = MagicMock()
        service.library.get_story.return_value = None

        with self.assertRaises(SystemExit) as ctx:
            generate_reel.run_non_interactive(service, "KS-999999")

        self.assertEqual(ctx.exception.code, 1)
        service.generate.assert_not_called()

    def test_entry_without_character_or_created_date_does_not_crash(self):
        """Real entries always have these fields, but the summary must
        not blow up on a minimal/legacy entry either."""

        entry = {"content_id": "KS-000001", "title": "T", "folder": "f"}
        service = MagicMock()
        service.library.get_story.return_value = entry
        service.generate.return_value = Path("f/reel.mp4")

        with patch("generate_reel.probe_video_metadata", return_value={}):
            generate_reel.run_non_interactive(service, "KS-000001")

        service.generate.assert_called_once_with(
            content_id="KS-000001", overwrite=True, music_track=None,
        )


class NoDailyPipelineReferenceTests(unittest.TestCase):
    """Static guard: neither the Reel CLI nor the Reel service module
    text-references the daily pipeline's entry points, in either
    selection mode."""

    def test_generate_reel_cli_does_not_reference_daily_pipeline(self):

        source = Path("generate_reel.py").read_text(encoding="utf-8")

        for forbidden in ["run_daily", "StoryPipeline", "story_agent", "StoryAgent"]:
            self.assertNotIn(forbidden, source)

    def test_reel_service_does_not_reference_daily_pipeline(self):

        source = Path("services/reel_service.py").read_text(encoding="utf-8")

        for forbidden in ["run_daily", "StoryPipeline", "EmailService", "GeminiService"]:
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
