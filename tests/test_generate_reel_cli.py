import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import generate_reel
from services.reel_service import (
    FFmpegNotAvailableError,
    MissingStoryAssetsError,
    ReelGenerationError,
)


class TestParseArgs(unittest.TestCase):

    def test_no_content_id_defaults_to_none(self):

        args = generate_reel.parse_args([])

        self.assertIsNone(args.content_id)

    def test_content_id_flag_is_parsed(self):

        args = generate_reel.parse_args(["--content-id", "KS-000001"])

        self.assertEqual(args.content_id, "KS-000001")


class TestRunNonInteractive(unittest.TestCase):
    """Exercises the --content-id path in isolation: ReelService and
    ContentLibraryService are mocked out, so no real OpenAI TTS/ffmpeg
    calls are ever made."""

    def _service_with_entry(self, entry):

        service = MagicMock()
        service.library.get_story.return_value = entry
        return service

    def test_unknown_content_id_exits_without_generating(self):

        service = self._service_with_entry(None)

        with self.assertRaises(SystemExit) as ctx:
            generate_reel.run_non_interactive(service, "KS-999999")

        self.assertEqual(ctx.exception.code, 1)
        service.generate.assert_not_called()

    def test_selects_exact_content_id_no_prompt_no_music(self):

        entry = {
            "content_id": "KS-000001",
            "title": "Pip's Colourful Help",
            "folder": "output/some_folder",
        }
        service = self._service_with_entry(entry)
        service.generate.return_value = Path("output/some_folder/reel.mp4")

        with patch("generate_reel.probe_video_metadata", return_value={}), \
             patch("builtins.input", side_effect=AssertionError("must not prompt")):

            generate_reel.run_non_interactive(service, "KS-000001")

        service.generate.assert_called_once_with(
            content_id="KS-000001",
            overwrite=True,
            music_track=None,
        )

    def test_updates_library_only_after_successful_generation(self):
        """The CLI itself must not call update_reel directly -- that only
        happens inside ReelService.generate after a verified render, and
        the CLI must not update it again nor bypass that ordering."""

        entry = {
            "content_id": "KS-000001",
            "title": "Pip's Colourful Help",
            "folder": "output/some_folder",
        }
        service = self._service_with_entry(entry)
        service.generate.return_value = Path("output/some_folder/reel.mp4")

        with patch("generate_reel.probe_video_metadata", return_value={}):
            generate_reel.run_non_interactive(service, "KS-000001")

        service.library.update_reel.assert_not_called()

    def test_ffmpeg_not_available_exits_cleanly(self):

        entry = {"content_id": "KS-000001", "title": "T", "folder": "f"}
        service = self._service_with_entry(entry)
        service.generate.side_effect = FFmpegNotAvailableError("ffmpeg missing")

        with self.assertRaises(SystemExit) as ctx:
            generate_reel.run_non_interactive(service, "KS-000001")

        self.assertEqual(ctx.exception.code, 1)

    def test_missing_story_assets_exits_cleanly(self):

        entry = {"content_id": "KS-000001", "title": "T", "folder": "f"}
        service = self._service_with_entry(entry)
        service.generate.side_effect = MissingStoryAssetsError("missing assets")

        with self.assertRaises(SystemExit) as ctx:
            generate_reel.run_non_interactive(service, "KS-000001")

        self.assertEqual(ctx.exception.code, 1)

    def test_reel_generation_error_exits_cleanly(self):

        entry = {"content_id": "KS-000001", "title": "T", "folder": "f"}
        service = self._service_with_entry(entry)
        service.generate.side_effect = ReelGenerationError("tts failed")

        with self.assertRaises(SystemExit) as ctx:
            generate_reel.run_non_interactive(service, "KS-000001")

        self.assertEqual(ctx.exception.code, 1)

    def test_never_posts_to_instagram(self):

        entry = {
            "content_id": "KS-000001",
            "title": "Pip's Colourful Help",
            "folder": "output/some_folder",
        }
        service = self._service_with_entry(entry)
        service.generate.return_value = Path("output/some_folder/reel.mp4")

        with patch("generate_reel.probe_video_metadata", return_value={}):
            generate_reel.run_non_interactive(service, "KS-000001")

        # No Instagram-posting attribute should ever be touched by the CLI.
        for called in service.method_calls:
            self.assertNotIn("instagram", called[0].lower())


class TestMainRouting(unittest.TestCase):
    """Confirms --content-id routes to the non-interactive path and its
    absence preserves the existing interactive path, without making any
    real service calls."""

    @patch("generate_reel.run_non_interactive")
    @patch("generate_reel.run_interactive")
    @patch("generate_reel.ReelService")
    @patch("generate_reel.check_ffmpeg_available", return_value=True)
    def test_content_id_routes_to_non_interactive(
        self, mock_ffmpeg, mock_service_cls, mock_interactive, mock_non_interactive
    ):

        generate_reel.main(["--content-id", "KS-000001"])

        mock_non_interactive.assert_called_once_with(
            mock_service_cls.return_value, "KS-000001"
        )
        mock_interactive.assert_not_called()

    @patch("generate_reel.run_non_interactive")
    @patch("generate_reel.run_interactive")
    @patch("generate_reel.ReelService")
    @patch("generate_reel.check_ffmpeg_available", return_value=True)
    def test_no_content_id_routes_to_interactive(
        self, mock_ffmpeg, mock_service_cls, mock_interactive, mock_non_interactive
    ):

        generate_reel.main([])

        mock_interactive.assert_called_once_with(mock_service_cls.return_value)
        mock_non_interactive.assert_not_called()

    @patch("generate_reel.ReelService")
    @patch("generate_reel.check_ffmpeg_available", return_value=False)
    def test_missing_ffmpeg_exits_before_touching_service(
        self, mock_ffmpeg, mock_service_cls
    ):

        with self.assertRaises(SystemExit) as ctx:
            generate_reel.main([])

        self.assertEqual(ctx.exception.code, 1)
        mock_service_cls.assert_not_called()


if __name__ == "__main__":
    unittest.main()
