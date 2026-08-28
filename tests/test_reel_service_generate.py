import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from services.reel_service import (
    ReelService,
    ReelGenerationError,
)


def _write_minimal_story_assets(folder: Path):
    """Writes a real, minimal story.json + cover + slide images to disk so
    ReelService.generate() exercises its actual file-loading and asset
    discovery code, not a mock of it."""

    folder.mkdir(parents=True, exist_ok=True)

    story = {
        "story_info": {
            "title": "Test Story",
            "subtitle": "A subtitle",
            "theme": "kindness",
            "target_age": "3-5",
            "reading_time": "3 min",
            "moral": "Be kind to others.",
        },
        "character_sheet": {
            "main_character": {
                "name": "Pip",
                "species": "fox",
                "appearance": "orange fur",
                "personality": "curious",
            },
            "supporting_characters": [],
        },
        "cover": {
            "prompt": "a fox",
            "negative_prompt": "",
            "style": "",
            "title_position": "top",
        },
        "slides": [
            {
                "page": 1, "title": "T1",
                "text": "Pip found a problem to solve today.",
                "background_color": "#fff", "visual_theme": "",
                "icon": "", "speaker_notes": "",
            },
            {
                "page": 2, "title": "T2",
                "text": "Pip went on an adventure to fix it.",
                "background_color": "#fff", "visual_theme": "",
                "icon": "", "speaker_notes": "",
            },
            {
                "page": 3, "title": "T3",
                "text": "Pip felt proud at the end.",
                "background_color": "#fff", "visual_theme": "",
                "icon": "", "speaker_notes": "",
            },
        ],
        "instagram": {"caption": "", "hashtags": []},
        "email": {"subject": "", "preview": ""},
        "youtube": {"title": "", "description": "", "keywords": []},
        "publishing": {
            "hook": "Pip has a big problem today!",
            "instagram_caption_short": "",
            "instagram_caption_long": "",
            "hashtags": [],
            "first_comment": "",
            "alt_text": "",
            "call_to_action": "",
            "best_posting_time": "",
            "parent_question": "",
        },
    }

    (folder / "story.json").write_text(json.dumps(story), encoding="utf-8")
    (folder / "cover_final.png").write_bytes(b"fake-png")
    (folder / "slide_1.png").write_bytes(b"fake-png")
    (folder / "slide_2.png").write_bytes(b"fake-png")
    (folder / "slide_3.png").write_bytes(b"fake-png")


def _fake_ffmpeg_writes_output(command):
    """Stand-in for run_ffmpeg_command: writes a placeholder file at the
    real output path (the last argv element) instead of invoking ffmpeg."""

    output = Path(command[-1])
    output.write_bytes(b"fake-mp4-bytes")
    return MagicMock(returncode=0)


class ReelServiceGenerateTests(unittest.TestCase):
    """Exercises ReelService.generate() end-to-end against real temp-dir
    story assets, with only the true external boundaries mocked: the
    Content Library backing store, OpenAI TTS, and the ffmpeg/ffprobe
    subprocess calls. No real API or subprocess call is ever made."""

    def _make_service(self, folder, content_id="KS-000001"):

        with patch("services.reel_service.ContentLibraryService"), \
             patch("services.reel_service.OpenAITTSService"), \
             patch("services.reel_service.BrandLoader.load", return_value={}):

            service = ReelService()

        service.library.get_story.return_value = {
            "content_id": content_id,
            "title": "Test Story",
            "folder": str(folder),
        }

        # Default TTS behaviour: actually write bytes to the requested
        # narration path, like the real OpenAITTSService.generate() would.
        def fake_tts_generate(text, output_file):
            Path(output_file).write_bytes(b"fake-mp3-bytes")
            return output_file

        service.tts.generate.side_effect = fake_tts_generate

        return service

    def test_success_updates_library_only_after_valid_render(self):

        with TemporaryDirectory() as tmp:

            folder = Path(tmp) / "story"
            _write_minimal_story_assets(folder)

            service = self._make_service(folder)

            with patch("services.reel_service.check_ffmpeg_available", return_value=True), \
                 patch("services.reel_service.run_ffmpeg_command", side_effect=_fake_ffmpeg_writes_output), \
                 patch("services.reel_service.probe_video_metadata", return_value={
                     "width": 1080, "height": 1920, "duration_seconds": 25.0,
                 }):

                result = service.generate(content_id="KS-000001")

            self.assertEqual(result, folder / "reel.mp4")
            self.assertTrue(result.exists())
            service.library.update_reel.assert_called_once_with("KS-000001", folder / "reel.mp4")

    def test_unknown_content_id_fails_without_touching_ffmpeg(self):

        with TemporaryDirectory() as tmp:

            folder = Path(tmp) / "story"

            service = self._make_service(folder)
            service.library.get_story.return_value = None

            with patch("services.reel_service.check_ffmpeg_available", return_value=True), \
                 patch("services.reel_service.run_ffmpeg_command") as mock_ffmpeg:

                with self.assertRaises(ReelGenerationError):
                    service.generate(content_id="KS-999999")

            mock_ffmpeg.assert_not_called()
            service.library.update_reel.assert_not_called()

    def test_ffmpeg_failure_does_not_update_library(self):

        with TemporaryDirectory() as tmp:

            folder = Path(tmp) / "story"
            _write_minimal_story_assets(folder)

            service = self._make_service(folder)

            with patch("services.reel_service.check_ffmpeg_available", return_value=True), \
                 patch("services.reel_service.run_ffmpeg_command", side_effect=ReelGenerationError("ffmpeg boom")):

                with self.assertRaises(ReelGenerationError):
                    service.generate(content_id="KS-000001")

            service.library.update_reel.assert_not_called()
            self.assertFalse((folder / "reel.mp4").exists())

    def test_tts_failure_does_not_update_library(self):

        with TemporaryDirectory() as tmp:

            folder = Path(tmp) / "story"
            _write_minimal_story_assets(folder)

            service = self._make_service(folder)
            service.tts.generate.side_effect = RuntimeError("tts boom")

            with patch("services.reel_service.check_ffmpeg_available", return_value=True), \
                 patch("services.reel_service.run_ffmpeg_command") as mock_ffmpeg:

                with self.assertRaises(ReelGenerationError):
                    service.generate(content_id="KS-000001")

            mock_ffmpeg.assert_not_called()
            service.library.update_reel.assert_not_called()

    def test_wrong_dimensions_fails_and_does_not_update_library(self):

        with TemporaryDirectory() as tmp:

            folder = Path(tmp) / "story"
            _write_minimal_story_assets(folder)

            service = self._make_service(folder)

            with patch("services.reel_service.check_ffmpeg_available", return_value=True), \
                 patch("services.reel_service.run_ffmpeg_command", side_effect=_fake_ffmpeg_writes_output), \
                 patch("services.reel_service.probe_video_metadata", return_value={
                     "width": 720, "height": 1280, "duration_seconds": 25.0,
                 }):

                with self.assertRaises(ReelGenerationError):
                    service.generate(content_id="KS-000001")

            service.library.update_reel.assert_not_called()
            self.assertFalse((folder / "reel.mp4").exists())

    def test_missing_probe_metadata_fails_and_does_not_update_library(self):
        """If ffprobe is unavailable/failed, width/height/duration can't be
        verified -- this must fail closed, not silently accept the file."""

        with TemporaryDirectory() as tmp:

            folder = Path(tmp) / "story"
            _write_minimal_story_assets(folder)

            service = self._make_service(folder)

            with patch("services.reel_service.check_ffmpeg_available", return_value=True), \
                 patch("services.reel_service.run_ffmpeg_command", side_effect=_fake_ffmpeg_writes_output), \
                 patch("services.reel_service.probe_video_metadata", return_value={}):

                with self.assertRaises(ReelGenerationError):
                    service.generate(content_id="KS-000001")

            service.library.update_reel.assert_not_called()
            self.assertFalse((folder / "reel.mp4").exists())

    def test_duration_out_of_range_fails_and_does_not_update_library(self):

        with TemporaryDirectory() as tmp:

            folder = Path(tmp) / "story"
            _write_minimal_story_assets(folder)

            service = self._make_service(folder)

            with patch("services.reel_service.check_ffmpeg_available", return_value=True), \
                 patch("services.reel_service.run_ffmpeg_command", side_effect=_fake_ffmpeg_writes_output), \
                 patch("services.reel_service.probe_video_metadata", return_value={
                     "width": 1080, "height": 1920, "duration_seconds": 90.0,
                 }):

                with self.assertRaises(ReelGenerationError):
                    service.generate(content_id="KS-000001")

            service.library.update_reel.assert_not_called()

    def test_missing_story_folder_fails_before_any_generation(self):

        service = self._make_service(Path("does/not/exist"))

        with patch("services.reel_service.check_ffmpeg_available", return_value=True), \
             patch("services.reel_service.run_ffmpeg_command") as mock_ffmpeg:

            with self.assertRaises(Exception):
                service.generate(content_id="KS-000001")

        mock_ffmpeg.assert_not_called()
        service.library.update_reel.assert_not_called()


if __name__ == "__main__":
    unittest.main()
