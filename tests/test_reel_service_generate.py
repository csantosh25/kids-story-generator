import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from PIL import Image

from services.reel_service import (
    ReelService,
    ReelGenerationError,
)
from services.reel_image_service import ReelImageGenerationError


def _write_minimal_story_assets(folder: Path):
    """Writes a real, minimal story.json + cover + slide images to disk so
    ReelService.generate() exercises its actual file-loading, asset
    discovery, and PIL image-processing code -- not a mock of it. The
    cover files must be real, decodable PNGs (not placeholder bytes),
    since materialize_scene_images() now actually opens/crops them."""

    folder.mkdir(parents=True, exist_ok=True)

    story = {
        "story_info": {
            "title": "Test Story",
            "subtitle": "A subtitle",
            "theme": "kindness",
            "target_age": "3-5",
            "reading_time": "3 min",
            "moral": "Being kind makes everyone happy.",
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
                "text": "Pip saw a friend who felt sad and alone. Pip wanted to help.",
                "background_color": "#FDE9D9", "visual_theme": "",
                "icon": "", "speaker_notes": "",
            },
            {
                "page": 2, "title": "T2",
                "text": "Pip went on an adventure to find the lost toy.",
                "background_color": "#FEF8F0", "visual_theme": "",
                "icon": "", "speaker_notes": "",
            },
            {
                "page": 3, "title": "T3",
                "text": "Pip felt proud and happy at the end of the day.",
                "background_color": "#F7DEBE", "visual_theme": "",
                "icon": "", "speaker_notes": "",
            },
        ],
        "instagram": {"caption": "", "hashtags": []},
        "email": {"subject": "", "preview": ""},
        "youtube": {"title": "", "description": "", "keywords": []},
        "publishing": {
            "hook": "",
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

    Image.new("RGB", (1080, 1350), "#EED9B8").save(folder / "cover.png")
    Image.new("RGB", (1080, 1350), "#EED9B8").save(folder / "cover_final.png")

    # Slide PNGs only need to exist for the eligibility/completeness check
    # (discover_story_images) -- the Reel pipeline never opens carousel
    # slide PNGs as images (see reel_service module docstring), so
    # placeholder bytes are fine here.
    (folder / "slide_1.png").write_bytes(b"fake-png")
    (folder / "slide_2.png").write_bytes(b"fake-png")
    (folder / "slide_3.png").write_bytes(b"fake-png")


def _fake_ffmpeg_writes_output(command):
    """Stand-in for run_ffmpeg_command: writes a placeholder file at the
    real output path (the last argv element) instead of invoking ffmpeg."""

    output = Path(command[-1])
    output.write_bytes(b"fake-mp4-bytes")
    return MagicMock(returncode=0)


_VALID_METADATA = {
    "width": 1080, "height": 1920, "duration_seconds": 25.0, "has_audio": True,
}


_FAKE_SCENE_COLORS = ["#C9A0DC", "#6EC6FF", "#8BC34A", "#FF8A65"]


def _fake_ensure_scenes_writing_real_images(folder):
    """Returns a function suitable as ReelImageService.ensure_scenes'
    side_effect: writes a REAL, decodable, DISTINCT-per-scene PNG for each
    requested beat (since materialize_scene_images() actually opens/crops
    these with PIL, and the temporary V3 diagnostic instrumentation in
    reel_diagnostics.py asserts generated scenes aren't identical to each
    other) and returns the same {"slide_index", "image_path"} shape the
    real ReelImageService returns -- without ever calling OpenAI."""

    def fake_ensure_scenes(story, content_id, beat_indices, beat_texts, **kwargs):
        target_folder = kwargs.get("folder", folder)
        results = []
        for position, slide_index in enumerate(beat_indices):
            path = target_folder / f"reel_scene_{slide_index + 1:02d}.png"
            color = _FAKE_SCENE_COLORS[position % len(_FAKE_SCENE_COLORS)]
            Image.new("RGB", (1024, 1536), color).save(path)
            results.append({"slide_index": slide_index, "image_path": path})
        return results

    return fake_ensure_scenes


class ReelServiceGenerateTests(unittest.TestCase):
    """Exercises ReelService.generate() end-to-end against real temp-dir
    story assets, with only the true external boundaries mocked: the
    Content Library backing store, OpenAI TTS, the Reel image-generation
    service, and the ffmpeg/ffprobe subprocess calls. No real API or
    subprocess call is ever made."""

    def _make_service(self, folder, content_id="KS-000001"):

        with patch("services.reel_service.ContentLibraryService"), \
             patch("services.reel_service.OpenAITTSService"), \
             patch("services.reel_service.ReelImageService"), \
             patch("services.reel_service.BrandLoader.load", return_value={}):

            service = ReelService()

        service.library.get_story.return_value = {
            "content_id": content_id,
            "title": "Test Story",
            "folder": str(folder),
        }

        # Default TTS behaviour: actually write bytes to the requested
        # narration path, like the real OpenAITTSService.generate() would.
        # Accepts **kwargs (e.g. voice/instructions) since ReelService
        # passes those through to the real service -- see
        # tests/test_reel_music_and_voice.py for assertions on their
        # actual values.
        def fake_tts_generate(text, output_file, **kwargs):
            Path(output_file).write_bytes(b"fake-mp3-bytes")
            return output_file

        service.tts.generate.side_effect = fake_tts_generate

        # Default image-service behaviour: write real, decodable PNGs for
        # each requested beat scene (no real OpenAI image call).
        service.images.ensure_scenes.side_effect = _fake_ensure_scenes_writing_real_images(folder)

        return service

    def test_success_updates_library_only_after_valid_render(self):

        with TemporaryDirectory() as tmp:

            folder = Path(tmp) / "story"
            _write_minimal_story_assets(folder)

            service = self._make_service(folder)

            with patch("services.reel_service.check_ffmpeg_available", return_value=True), \
                 patch("services.reel_service.run_ffmpeg_command", side_effect=_fake_ffmpeg_writes_output), \
                 patch("services.reel_service.probe_video_metadata", return_value=_VALID_METADATA):

                result = service.generate(content_id="KS-000001")

            self.assertEqual(result, folder / "reel.mp4")
            self.assertTrue(result.exists())
            service.library.update_reel.assert_called_once_with("KS-000001", folder / "reel.mp4")

    def test_success_materializes_full_bleed_scene_images(self):
        """The rendered scene images fed to ffmpeg must already be exactly
        1080x1920 (full-bleed cover crop + full-bleed illustrated beat
        scenes) -- no more 1080x1350-on-cream-padding, and not the same
        single image repeated for every scene."""

        with TemporaryDirectory() as tmp:

            folder = Path(tmp) / "story"
            _write_minimal_story_assets(folder)

            service = self._make_service(folder)

            captured_commands = []

            def fake_ffmpeg(command):
                captured_commands.append(command)
                return _fake_ffmpeg_writes_output(command)

            with patch("services.reel_service.check_ffmpeg_available", return_value=True), \
                 patch("services.reel_service.run_ffmpeg_command", side_effect=fake_ffmpeg), \
                 patch("services.reel_service.probe_video_metadata", return_value=_VALID_METADATA):

                service.generate(content_id="KS-000001")

            # V4 renders each scene independently (one run_ffmpeg_command
            # call per scene clip), so PNG inputs are spread across
            # several captured commands rather than one -- collect them
            # all.
            image_paths = [
                Path(arg)
                for command in captured_commands
                for arg in command
                if str(arg).endswith(".png")
            ]

            self.assertTrue(image_paths)

            for path in image_paths:
                with Image.open(path) as image:
                    self.assertEqual(image.size, (1080, 1920))

            # More than one distinct image file was used (cover + at least
            # one illustrated beat scene) -- not the same single image
            # repeated for the whole Reel.
            self.assertGreater(len(set(image_paths)), 1)

    def test_image_service_called_with_matching_beat_indices_and_texts(self):

        with TemporaryDirectory() as tmp:

            folder = Path(tmp) / "story"
            _write_minimal_story_assets(folder)

            service = self._make_service(folder)

            with patch("services.reel_service.check_ffmpeg_available", return_value=True), \
                 patch("services.reel_service.run_ffmpeg_command", side_effect=_fake_ffmpeg_writes_output), \
                 patch("services.reel_service.probe_video_metadata", return_value=_VALID_METADATA):

                service.generate(content_id="KS-000001")

            service.images.ensure_scenes.assert_called_once()
            _, kwargs = service.images.ensure_scenes.call_args

            self.assertEqual(kwargs["content_id"], "KS-000001")
            self.assertLessEqual(len(kwargs["beat_indices"]), 3)
            self.assertEqual(len(kwargs["beat_indices"]), len(kwargs["beat_texts"]))
            self.assertEqual(kwargs["folder"], folder)

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

    def test_image_generation_failure_does_not_update_library(self):
        """Mirrors the TTS/ffmpeg failure contract: if Reel scene image
        generation fails, the whole Reel attempt fails and the Content
        Library is left unchanged -- no silent fallback to a flat visual."""

        with TemporaryDirectory() as tmp:

            folder = Path(tmp) / "story"
            _write_minimal_story_assets(folder)

            service = self._make_service(folder)
            service.images.ensure_scenes.side_effect = ReelImageGenerationError("image API boom")

            with patch("services.reel_service.check_ffmpeg_available", return_value=True), \
                 patch("services.reel_service.run_ffmpeg_command") as mock_ffmpeg:

                with self.assertRaises(ReelGenerationError):
                    service.generate(content_id="KS-000001")

            mock_ffmpeg.assert_not_called()
            service.library.update_reel.assert_not_called()
            self.assertFalse((folder / "reel.mp4").exists())

    def test_missing_illustrated_scene_fails_before_ffmpeg(self):
        """Defensive check: if ensure_scenes returns fewer scenes than
        there are beat segments (a broken/incomplete mock or a future
        regression), materialize_scene_images must fail loudly rather
        than silently rendering with a missing visual."""

        with TemporaryDirectory() as tmp:

            folder = Path(tmp) / "story"
            _write_minimal_story_assets(folder)

            service = self._make_service(folder)
            service.images.ensure_scenes.side_effect = None
            service.images.ensure_scenes.return_value = []  # no scenes at all

            with patch("services.reel_service.check_ffmpeg_available", return_value=True), \
                 patch("services.reel_service.run_ffmpeg_command") as mock_ffmpeg:

                with self.assertRaises(Exception):
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
                     "width": 720, "height": 1280, "duration_seconds": 25.0, "has_audio": True,
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
                     "width": 1080, "height": 1920, "duration_seconds": 90.0, "has_audio": True,
                 }):

                with self.assertRaises(ReelGenerationError):
                    service.generate(content_id="KS-000001")

            service.library.update_reel.assert_not_called()

    def test_missing_audio_fails_and_does_not_update_library(self):

        with TemporaryDirectory() as tmp:

            folder = Path(tmp) / "story"
            _write_minimal_story_assets(folder)

            service = self._make_service(folder)

            with patch("services.reel_service.check_ffmpeg_available", return_value=True), \
                 patch("services.reel_service.run_ffmpeg_command", side_effect=_fake_ffmpeg_writes_output), \
                 patch("services.reel_service.probe_video_metadata", return_value={
                     "width": 1080, "height": 1920, "duration_seconds": 25.0, "has_audio": False,
                 }):

                with self.assertRaises(ReelGenerationError):
                    service.generate(content_id="KS-000001")

            service.library.update_reel.assert_not_called()
            self.assertFalse((folder / "reel.mp4").exists())

    def test_missing_story_folder_fails_before_any_generation(self):

        service = self._make_service(Path("does/not/exist"))

        with patch("services.reel_service.check_ffmpeg_available", return_value=True), \
             patch("services.reel_service.run_ffmpeg_command") as mock_ffmpeg:

            with self.assertRaises(Exception):
                service.generate(content_id="KS-000001")

        mock_ffmpeg.assert_not_called()
        service.library.update_reel.assert_not_called()

    def test_stale_narration_is_regenerated_when_script_text_changes(self):
        """A leftover reel_narration.mp3 from a run with different script
        text (e.g. after this story's content or the Reel script logic
        changed) must NOT be silently reused -- it would narrate the wrong
        words for the new captions."""

        with TemporaryDirectory() as tmp:

            folder = Path(tmp) / "story"
            _write_minimal_story_assets(folder)

            (folder / "reel_narration.mp3").write_bytes(b"stale-old-audio")
            (folder / "reel_narration.txt").write_text("some old narration text", encoding="utf-8")

            service = self._make_service(folder)

            with patch("services.reel_service.check_ffmpeg_available", return_value=True), \
                 patch("services.reel_service.run_ffmpeg_command", side_effect=_fake_ffmpeg_writes_output), \
                 patch("services.reel_service.probe_video_metadata", return_value=_VALID_METADATA):

                service.generate(content_id="KS-000001")

            service.tts.generate.assert_called_once()
            self.assertEqual(
                (folder / "reel_narration.mp3").read_bytes(), b"fake-mp3-bytes"
            )

    def test_matching_narration_is_reused_not_regenerated(self):

        with TemporaryDirectory() as tmp:

            folder = Path(tmp) / "story"
            _write_minimal_story_assets(folder)

            service = self._make_service(folder)

            # First run writes reel_narration.mp3 + reel_narration.txt for
            # the current script text.
            with patch("services.reel_service.check_ffmpeg_available", return_value=True), \
                 patch("services.reel_service.run_ffmpeg_command", side_effect=_fake_ffmpeg_writes_output), \
                 patch("services.reel_service.probe_video_metadata", return_value=_VALID_METADATA):

                service.generate(content_id="KS-000001")

            service.tts.generate.reset_mock()

            # Second run against the same, unchanged story: narration text
            # will be identical, so TTS must not be called again.
            with patch("services.reel_service.check_ffmpeg_available", return_value=True), \
                 patch("services.reel_service.run_ffmpeg_command", side_effect=_fake_ffmpeg_writes_output), \
                 patch("services.reel_service.probe_video_metadata", return_value=_VALID_METADATA):

                service.generate(content_id="KS-000001", overwrite=True)

            service.tts.generate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
