"""
Regression tests for the V4 Reel rendering architecture.

V3's rendering built ONE ffmpeg filter_complex containing a separate
`zoompan` filter instance per scene, all feeding a single `concat`. A
forensic investigation (real ffmpeg, real production code, reproducible
locally -- see the V3 forensic report) proved this construction has a
real defect: with 2+ zoompan instances sharing one filter graph before
concat, every scene after the first collapses into the first scene's
content. 92 mocked unit tests passed throughout because none of them
executed real ffmpeg end to end.

V4 (see render_reel_video() in services/reel_service.py) renders each
scene independently to its own clip (one zoompan instance per ffmpeg
process), stitches the clips with the concat DEMUXER (no filter graph at
all), then applies captions/audio in one final pass (drawtext only, never
zoompan).

Part A: pure-Python proof that scene ordering/mapping is correct (no
ffmpeg). Part B (skipped if ffmpeg unavailable): actually invokes real
ffmpeg via render_reel_video() and inspects the actual rendered MP4 --
this is the level of test that would have caught V3's defect, and now
proves V4 fixes it.
"""
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from PIL import Image

from services.reel_service import (
    ReelGenerationError,
    materialize_scene_images,
    render_reel_video,
    TARGET_WIDTH,
    TARGET_HEIGHT,
)

FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None
FONT_PATH = Path("assets/fonts/Poppins-Bold.ttf").resolve()


class MaterializeSceneOrderingTests(unittest.TestCase):
    """Part A: pure-Python proof that the scene list handed to the
    renderer is correctly ordered and mapped -- no ffmpeg involved."""

    def test_ordered_scene_list_is_cover_scene1_scene2_scene3_cover(self):

        with tempfile.TemporaryDirectory() as tmp:

            folder = Path(tmp)

            cover_src = folder / "cover_src.png"
            Image.new("RGB", (1080, 1350), "orange").save(cover_src)

            scene_srcs = {}
            colors = {0: "red", 2: "green", 5: "blue"}

            for slide_index, color in colors.items():
                path = folder / f"reel_scene_{slide_index}.png"
                Image.new("RGB", (1024, 1536), color).save(path)
                scene_srcs[slide_index] = path

            segments = [
                {"kind": "cover", "text": "hook"},
                {"kind": "beat", "slide_index": 0, "text": "beat0"},
                {"kind": "beat", "slide_index": 2, "text": "beat2"},
                {"kind": "beat", "slide_index": 5, "text": "beat5"},
                {"kind": "cover", "text": "outro"},
            ]

            scene_images = materialize_scene_images(segments, cover_src, folder, scene_srcs)

            self.assertEqual(len(scene_images), 5)

            colors_seen = []
            for path in scene_images:
                with Image.open(path) as image:
                    self.assertEqual(image.size, (TARGET_WIDTH, TARGET_HEIGHT))
                    colors_seen.append(image.getpixel((TARGET_WIDTH // 2, TARGET_HEIGHT // 2)))

            self.assertEqual(colors_seen[0], colors_seen[4])
            self.assertNotEqual(colors_seen[0], colors_seen[1])
            self.assertNotEqual(colors_seen[1], colors_seen[2])
            self.assertNotEqual(colors_seen[2], colors_seen[3])
            self.assertNotEqual(colors_seen[1], colors_seen[3])
            self.assertFalse(all(c == colors_seen[0] for c in colors_seen))

    def test_slide_index_to_image_mapping_is_exact(self):

        with tempfile.TemporaryDirectory() as tmp:

            folder = Path(tmp)
            cover_src = folder / "cover_src.png"
            Image.new("RGB", (1080, 1350), (10, 10, 10)).save(cover_src)

            scene_srcs = {}
            for slide_index, color in [(0, (255, 0, 0)), (2, (0, 255, 0)), (5, (0, 0, 255))]:
                path = folder / f"reel_scene_{slide_index}.png"
                Image.new("RGB", (1024, 1536), color).save(path)
                scene_srcs[slide_index] = path

            segments = [
                {"kind": "cover", "text": "hook"},
                {"kind": "beat", "slide_index": 5, "text": "beat5"},
                {"kind": "beat", "slide_index": 0, "text": "beat0"},
                {"kind": "beat", "slide_index": 2, "text": "beat2"},
                {"kind": "cover", "text": "outro"},
            ]

            scene_images = materialize_scene_images(segments, cover_src, folder, scene_srcs)

            with Image.open(scene_images[1]) as image:
                self.assertEqual(image.getpixel((TARGET_WIDTH // 2, TARGET_HEIGHT // 2)), (0, 0, 255))

            with Image.open(scene_images[2]) as image:
                self.assertEqual(image.getpixel((TARGET_WIDTH // 2, TARGET_HEIGHT // 2)), (255, 0, 0))

            with Image.open(scene_images[3]) as image:
                self.assertEqual(image.getpixel((TARGET_WIDTH // 2, TARGET_HEIGHT // 2)), (0, 255, 0))


def _make_silent_narration(path: Path, duration=10):

    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
         "-t", str(duration), "-q:a", "9", str(path)],
        capture_output=True,
    )


def _sample_center_color(video_path: Path, t: float, folder: Path):

    frame_path = folder / f"frame_{t}.png"

    subprocess.run(
        ["ffmpeg", "-y", "-ss", str(t), "-i", str(video_path),
         "-frames:v", "1", str(frame_path)],
        capture_output=True,
    )

    with Image.open(frame_path) as image:
        image = image.convert("RGB")
        return image.resize((1, 1)).getpixel((0, 0))


@unittest.skipUnless(FFMPEG_AVAILABLE, "ffmpeg not available on PATH")
class RealFfmpegRenderingTests(unittest.TestCase):
    """Part B: actually invokes real ffmpeg via the production
    render_reel_video() function -- proves the RENDERED VIDEO shows
    genuinely different content per scene, in the right order, with
    working captions across scene boundaries, and correct failure
    handling. No ffmpeg call is mocked in this class."""

    def _build_five_scene_setup(self, folder: Path, scene_seconds=2.0):
        """cover(orange) -> red -> green -> blue -> cover(orange), 2s
        each = 10s total -- matches the exact scenario used to first
        reproduce the V3 defect."""

        cover = folder / "cover.png"
        Image.new("RGB", (TARGET_WIDTH, TARGET_HEIGHT), (110, 35, 4)).save(cover)

        scene1 = folder / "scene1.png"
        Image.new("RGB", (TARGET_WIDTH, TARGET_HEIGHT), (255, 0, 0)).save(scene1)

        scene2 = folder / "scene2.png"
        Image.new("RGB", (TARGET_WIDTH, TARGET_HEIGHT), (0, 255, 0)).save(scene2)

        scene3 = folder / "scene3.png"
        Image.new("RGB", (TARGET_WIDTH, TARGET_HEIGHT), (0, 0, 255)).save(scene3)

        scene_images = [cover, scene1, scene2, scene3, cover]
        scene_durations = [scene_seconds] * 5

        narration = folder / "narration.mp3"
        _make_silent_narration(narration, duration=int(sum(scene_durations)) + 1)

        return scene_images, scene_durations, narration

    def test_rendered_video_shows_distinct_scenes_at_each_timestamp(self):
        """The exact scenario that reproduced the V3 defect: 5 scenes,
        maximally distinct colors, real render_reel_video(). Every
        sampled scene must be visually distinct from the one before it."""

        with tempfile.TemporaryDirectory() as tmp:

            folder = Path(tmp)
            scene_images, scene_durations, narration = self._build_five_scene_setup(folder)

            output_path = folder / "reel.mp4"
            clips_dir = folder / "clips"

            render_reel_video(
                scene_images=scene_images,
                scene_durations=scene_durations,
                narration_path=narration,
                output_path=output_path,
                caption_cues=[],
                font_path=FONT_PATH,
                work_dir=clips_dir,
            )

            self.assertTrue(output_path.exists())

            # Mid-point of each 2s scene: 1, 3, 5, 7, 9.
            colors = [_sample_center_color(output_path, t, folder) for t in (1, 3, 5, 7, 9)]

            self.assertNotEqual(colors[0], colors[1], "cover != scene1")
            self.assertNotEqual(colors[1], colors[2], "scene1 != scene2")
            self.assertNotEqual(colors[2], colors[3], "scene2 != scene3")
            self.assertNotEqual(colors[3], colors[4], "scene3 != closing cover")
            self.assertFalse(
                all(c == colors[0] for c in colors),
                f"All sampled frames identical -- V3 defect has regressed. colors={colors}",
            )

    def test_rendered_video_follows_cover_scene1_scene2_scene3_cover_order(self):
        """Explicit order check: not just "scenes differ from neighbours"
        but the SPECIFIC intended sequence appears at the right times."""

        with tempfile.TemporaryDirectory() as tmp:

            folder = Path(tmp)
            scene_images, scene_durations, narration = self._build_five_scene_setup(folder)

            output_path = folder / "reel.mp4"

            render_reel_video(
                scene_images=scene_images,
                scene_durations=scene_durations,
                narration_path=narration,
                output_path=output_path,
                caption_cues=[],
                font_path=FONT_PATH,
                work_dir=folder / "clips",
            )

            def is_close(actual, expected, tol=40):
                return all(abs(a - e) <= tol for a, e in zip(actual, expected))

            self.assertTrue(is_close(_sample_center_color(output_path, 1, folder), (110, 35, 4)), "t=1s should be cover")
            self.assertTrue(is_close(_sample_center_color(output_path, 3, folder), (255, 0, 0)), "t=3s should be scene1 (red)")
            self.assertTrue(is_close(_sample_center_color(output_path, 5, folder), (0, 255, 0)), "t=5s should be scene2 (green)")
            self.assertTrue(is_close(_sample_center_color(output_path, 7, folder), (0, 0, 255)), "t=7s should be scene3 (blue)")
            self.assertTrue(is_close(_sample_center_color(output_path, 9, folder), (110, 35, 4)), "t=9s should be closing cover")

    def test_captions_render_correctly_across_a_scene_boundary(self):
        """A single caption cue whose enable window straddles a scene cut
        (1.5s-3.5s across the 2s cover->scene1 boundary) must still be
        visible on both sides of the cut -- proving caption timing
        applies to the final concatenated timeline, not per-clip."""

        with tempfile.TemporaryDirectory() as tmp:

            folder = Path(tmp)
            scene_images, scene_durations, narration = self._build_five_scene_setup(folder)

            caption_cues = [{
                "lines": ["Hello there"],
                "text": "Hello there",
                "start": 1.5,
                "end": 3.5,
            }]

            output_path = folder / "reel.mp4"

            render_reel_video(
                scene_images=scene_images,
                scene_durations=scene_durations,
                narration_path=narration,
                output_path=output_path,
                caption_cues=caption_cues,
                font_path=FONT_PATH,
                work_dir=folder / "clips",
            )

            # Caption box (boxcolor=black@0.55) + white text is centered
            # around y=h*0.72 and only as wide as the text itself, so
            # sample a NARROW centered band there -- averaging the full
            # frame width would dilute the box/text against a much larger
            # unboxed background. Compare a captioned frame against an
            # UNCAPTIONED frame from the SAME scene (comparing across
            # scenes with different base colors isn't a valid brightness
            # comparison), using colour distance rather than assuming a
            # specific direction (white text can make a region brighter,
            # not darker, depending on how much of the sample it covers).
            def caption_band_color(t):
                frame_path = folder / f"cap_{t}.png"
                subprocess.run(
                    ["ffmpeg", "-y", "-ss", str(t), "-i", str(output_path),
                     "-frames:v", "1", str(frame_path)],
                    capture_output=True,
                )
                with Image.open(frame_path) as image:
                    image = image.convert("RGB")
                    w, h = image.size
                    band = image.crop((int(w * 0.35), int(h * 0.685), int(w * 0.65), int(h * 0.755)))
                    return band.resize((1, 1)).getpixel((0, 0))

            def color_distance(a, b):
                return sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5

            cover_no_caption = caption_band_color(0.2)     # cover scene, before cue starts
            cover_with_caption = caption_band_color(1.8)   # cover scene, cue active
            scene1_with_caption = caption_band_color(2.8)  # scene1, cue still active (ends 3.5)
            scene1_no_caption = caption_band_color(3.8)    # scene1, cue has ended

            self.assertGreater(
                color_distance(cover_no_caption, cover_with_caption), 15,
                f"caption should visibly change the frame during the cover portion of its "
                f"window: no-caption={cover_no_caption} with-caption={cover_with_caption}",
            )
            self.assertGreater(
                color_distance(scene1_no_caption, scene1_with_caption), 15,
                f"caption should visibly change the frame during the scene1 portion of its "
                f"window: no-caption={scene1_no_caption} with-caption={scene1_with_caption}",
            )

    def test_one_bad_scene_image_fails_the_whole_render_and_cleans_nothing_prematurely(self):
        """If one scene clip fails to render (e.g. a missing source
        image), the whole render must fail -- no reel.mp4, and the
        intermediate clips directory must be LEFT for diagnosis (not
        silently cleaned up), matching the "never partially succeed"
        philosophy already used for TTS/image-generation failures.

        Uses a MISSING file (not a corrupt-but-present one): on this
        ffmpeg build, `-loop 1` on a corrupt PNG retries decoding for the
        full clip duration before failing (60+ seconds observed) --
        pathologically slow for a unit test and not the scenario being
        tested here (a missing/moved file fails immediately at open)."""

        with tempfile.TemporaryDirectory() as tmp:

            folder = Path(tmp)
            scene_images, scene_durations, narration = self._build_five_scene_setup(folder)

            # Reference a scene image that doesn't exist on disk.
            scene_images = list(scene_images)
            scene_images[1] = folder / "does_not_exist.png"

            output_path = folder / "reel.mp4"
            clips_dir = folder / "clips"

            with self.assertRaises(ReelGenerationError):
                render_reel_video(
                    scene_images=scene_images,
                    scene_durations=scene_durations,
                    narration_path=narration,
                    output_path=output_path,
                    caption_cues=[],
                    font_path=FONT_PATH,
                    work_dir=clips_dir,
                )

            self.assertFalse(output_path.exists())
            # The clips directory is left in place (not cleaned up) so a
            # human/CI artifact upload can inspect which clip failed.
            self.assertTrue(clips_dir.exists())

    def test_successful_render_produces_valid_1080x1920_video(self):
        """Full acceptance check on the actual rendered file: exists,
        non-empty, exactly 1080x1920, has a video AND audio stream, and
        the intermediate clips directory is cleaned up after success."""

        with tempfile.TemporaryDirectory() as tmp:

            folder = Path(tmp)
            scene_images, scene_durations, narration = self._build_five_scene_setup(folder, scene_seconds=5.0)

            output_path = folder / "reel.mp4"
            clips_dir = folder / "clips"

            render_reel_video(
                scene_images=scene_images,
                scene_durations=scene_durations,
                narration_path=narration,
                output_path=output_path,
                caption_cues=[],
                font_path=FONT_PATH,
                work_dir=clips_dir,
            )

            self.assertTrue(output_path.exists())
            self.assertGreater(output_path.stat().st_size, 0)
            self.assertFalse(clips_dir.exists(), "intermediate clips should be cleaned up after success")

            probe = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries",
                 "stream=codec_type,width,height:format=duration",
                 "-of", "json", str(output_path)],
                capture_output=True, text=True,
            )

            import json
            data = json.loads(probe.stdout)
            streams = data["streams"]

            video_stream = next(s for s in streams if s["codec_type"] == "video")
            audio_stream = next((s for s in streams if s["codec_type"] == "audio"), None)

            self.assertEqual(video_stream["width"], TARGET_WIDTH)
            self.assertEqual(video_stream["height"], TARGET_HEIGHT)
            self.assertIsNotNone(audio_stream, "final render must have an audio stream")

            duration = float(data["format"]["duration"])
            self.assertAlmostEqual(duration, sum(scene_durations), delta=1.0)


class ReelServiceRenderFailureIntegrationTests(unittest.TestCase):
    """Mocked-ffmpeg-level tests confirming ReelService.generate() itself
    still only updates the Content Library after a fully successful
    render, using the new render_reel_video() call. Complements the real
    ffmpeg tests above."""

    def test_render_reel_video_failure_does_not_update_library(self):

        from services.reel_service import ReelService

        with tempfile.TemporaryDirectory() as tmp:

            folder = Path(tmp) / "story"
            folder.mkdir()

            import json
            story = {
                "story_info": {"title": "T", "subtitle": "s", "theme": "kindness",
                                "target_age": "3-5", "reading_time": "2 min", "moral": "Be kind."},
                "character_sheet": {"main_character": {"name": "Pip", "species": "fox",
                                                         "appearance": "orange", "personality": "kind"},
                                     "supporting_characters": []},
                "cover": {"prompt": "p", "negative_prompt": "", "style": "", "title_position": "top"},
                "slides": [
                    {"page": 1, "title": "T1", "text": "Pip felt sad and alone today.",
                     "background_color": "#FDE9D9", "visual_theme": "", "icon": "", "speaker_notes": ""},
                ],
                "instagram": {"caption": "", "hashtags": []},
                "email": {"subject": "", "preview": ""},
                "youtube": {"title": "", "description": "", "keywords": []},
                "publishing": {"hook": "", "instagram_caption_short": "", "instagram_caption_long": "",
                                "hashtags": [], "first_comment": "", "alt_text": "", "call_to_action": "",
                                "best_posting_time": "", "parent_question": ""},
            }
            (folder / "story.json").write_text(json.dumps(story), encoding="utf-8")
            Image.new("RGB", (1080, 1350), "orange").save(folder / "cover.png")
            Image.new("RGB", (1080, 1350), "orange").save(folder / "cover_final.png")
            (folder / "slide_1.png").write_bytes(b"fake")

            with patch("services.reel_service.ContentLibraryService"), \
                 patch("services.reel_service.OpenAITTSService"), \
                 patch("services.reel_service.ReelImageService"), \
                 patch("services.reel_service.BrandLoader.load", return_value={}):
                service = ReelService()

            service.library.get_story.return_value = {
                "content_id": "KS-000001", "title": "T", "folder": str(folder),
            }
            service.tts.generate.side_effect = lambda text, output_file, **kwargs: Path(output_file).write_bytes(b"fake-mp3")

            def fake_ensure_scenes(story, content_id, beat_indices, beat_texts, **kwargs):
                results = []
                for slide_index in beat_indices:
                    p = folder / f"reel_scene_{slide_index + 1:02d}.png"
                    Image.new("RGB", (1024, 1536), "purple").save(p)
                    results.append({"slide_index": slide_index, "image_path": p})
                return results

            service.images.ensure_scenes.side_effect = fake_ensure_scenes

            with patch("services.reel_service.check_ffmpeg_available", return_value=True), \
                 patch("services.reel_service.render_reel_video", side_effect=ReelGenerationError("clip render boom")):

                with self.assertRaises(ReelGenerationError):
                    service.generate(content_id="KS-000001")

            service.library.update_reel.assert_not_called()
            self.assertFalse((folder / "reel.mp4").exists())


if __name__ == "__main__":
    unittest.main()
