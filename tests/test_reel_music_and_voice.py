"""Tests for the Reel voice + background-music improvements:

- REEL_NARRATION_VOICE (a warm, consistent FEMALE voice) is passed to
  OpenAITTSService, and the voice is part of the narration cache key so a
  pre-existing (pre-voice-change) cache is regenerated exactly once.
- Local-only background music: deterministic content_id-based selection,
  graceful handling of an empty/missing directory and of an invalid
  track, ~10% mix volume, fade in/out, and looping/trimming to the
  narration/video duration (never the other way around).
- No additional OpenAI image or TTS calls are introduced by any of this.

Only true external boundaries (OpenAI TTS, the Reel image service, and
the Content Library backing store) are mocked; ffmpeg/ffprobe are real
where available (guarded by FFMPEG_AVAILABLE), matching the project's
existing V4 real-ffmpeg test pattern.
"""
import hashlib
import json
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from services.reel_service import (
    ReelService,
    build_final_assembly_command,
    list_music_tracks,
    list_valid_music_tracks,
    probe_audio_duration,
    render_reel_video,
    select_music_track,
    MUSIC_FADE_IN_SECONDS,
    MUSIC_FADE_OUT_SECONDS,
    MUSIC_VOLUME_DEFAULT,
    REEL_NARRATION_INSTRUCTIONS,
    REEL_NARRATION_VOICE,
    TARGET_HEIGHT,
    TARGET_WIDTH,
)

from tests.test_reel_service_generate import (
    _fake_ensure_scenes_writing_real_images,
    _fake_ffmpeg_writes_output,
    _write_minimal_story_assets,
    _VALID_METADATA,
)

FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None
FONT_PATH = Path("assets/fonts/Poppins-Bold.ttf").resolve()


def _make_silent_mp3(path: Path, duration=2):

    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
            "-t", str(duration), "-q:a", "9", str(path),
        ],
        capture_output=True,
    )


# =====================================================================
# select_music_track / list_valid_music_tracks (pure selection logic)
# =====================================================================

class SelectMusicTrackTests(unittest.TestCase):

    def test_no_tracks_returns_none(self):
        self.assertIsNone(select_music_track("KS-000001", tracks=[]))

    def test_same_content_id_selects_same_track_every_time(self):

        tracks = [Path(f"assets/music/t{i}.mp3") for i in range(5)]
        picks = {select_music_track("KS-000042", tracks=tracks) for _ in range(20)}

        self.assertEqual(len(picks), 1)

    def test_selection_is_always_one_of_the_given_tracks(self):

        tracks = [Path("assets/music/a.mp3"), Path("assets/music/b.mp3")]
        selected = select_music_track("KS-000001", tracks=tracks)

        self.assertIn(selected, tracks)

    def test_different_content_ids_can_rotate_across_tracks(self):

        tracks = [Path(f"assets/music/t{i}.mp3") for i in range(8)]
        picks = {select_music_track(f"KS-{n:06d}", tracks=tracks) for n in range(30)}

        # If every content_id landed on the same track, selection would
        # not actually be varying by content_id -- a real regression.
        self.assertGreater(len(picks), 1)

    def test_matches_documented_sha256_hash_formula(self):

        tracks = [Path("a.mp3"), Path("b.mp3"), Path("c.mp3")]
        content_id = "KS-000007"

        digest = hashlib.sha256(content_id.encode("utf-8")).hexdigest()
        expected = tracks[int(digest, 16) % len(tracks)]

        self.assertEqual(select_music_track(content_id, tracks=tracks), expected)


class ListValidMusicTracksTests(unittest.TestCase):

    def test_empty_directory_returns_no_tracks(self):

        with TemporaryDirectory() as tmp:
            with patch("services.reel_service.MUSIC_DIR", Path(tmp)):
                self.assertEqual(list_music_tracks(), [])
                self.assertEqual(list_valid_music_tracks(), [])

    def test_missing_directory_returns_no_tracks(self):

        with patch("services.reel_service.MUSIC_DIR", Path("does/not/exist_music_dir")):
            self.assertEqual(list_music_tracks(), [])
            self.assertEqual(list_valid_music_tracks(), [])

    def test_probe_audio_duration_none_for_missing_file(self):
        self.assertIsNone(probe_audio_duration(Path("does/not/exist.mp3")))

    def test_probe_audio_duration_none_for_empty_file(self):

        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "empty.mp3"
            path.write_bytes(b"")
            self.assertIsNone(probe_audio_duration(path))

    @unittest.skipUnless(FFMPEG_AVAILABLE, "ffmpeg not available on PATH")
    def test_corrupt_file_is_skipped_valid_file_is_kept(self):

        with TemporaryDirectory() as tmp:

            folder = Path(tmp)
            good = folder / "good.mp3"
            bad = folder / "bad.mp3"

            _make_silent_mp3(good, duration=2)
            bad.write_bytes(b"this is not a real mp3 file at all")

            with patch("services.reel_service.MUSIC_DIR", folder):
                all_tracks = list_music_tracks()
                valid_tracks = list_valid_music_tracks()

            self.assertEqual(sorted(p.name for p in all_tracks), ["bad.mp3", "good.mp3"])
            self.assertEqual([p.name for p in valid_tracks], ["good.mp3"])

    @unittest.skipUnless(FFMPEG_AVAILABLE, "ffmpeg not available on PATH")
    def test_probe_audio_duration_returns_real_duration(self):

        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "clip.mp3"
            _make_silent_mp3(path, duration=3)
            duration = probe_audio_duration(path)

        self.assertIsNotNone(duration)
        self.assertAlmostEqual(duration, 3.0, delta=0.5)


# =====================================================================
# build_final_assembly_command -- pure argv/string construction, no
# ffmpeg execution needed.
# =====================================================================

class BuildFinalAssemblyCommandMusicTests(unittest.TestCase):

    def _base_kwargs(self, music_path=None, music_volume=None, total_duration=24.0):

        return dict(
            concatenated_video_path=Path("concat.mp4"),
            narration_path=Path("narration.mp3"),
            output_path=Path("out.mp4"),
            caption_cues=[],
            font_path=Path("font.ttf"),
            total_duration=total_duration,
            music_path=music_path,
            music_volume=music_volume,
        )

    def _filter_complex(self, command):
        return command[command.index("-filter_complex") + 1]

    def test_no_music_has_no_stream_loop_and_no_amix(self):

        command = build_final_assembly_command(**self._base_kwargs())

        self.assertNotIn("-stream_loop", command)
        filter_complex = self._filter_complex(command)
        self.assertNotIn("amix", filter_complex)
        self.assertIn("anull", filter_complex)

    def test_music_input_loops_indefinitely_so_it_never_runs_out(self):
        """-stream_loop -1 is how a shorter track is made to cover the
        whole Reel (item 5: loop/extend, never shorten the Reel)."""

        music_path = Path("assets/music/gentle.mp3")
        command = build_final_assembly_command(
            **self._base_kwargs(music_path=music_path)
        )

        self.assertIn("-stream_loop", command)
        loop_index = command.index("-stream_loop")

        self.assertEqual(command[loop_index + 1], "-1")
        self.assertEqual(command[loop_index + 2], "-i")
        # str(Path(...)) uses the platform's own separator (backslash on
        # Windows) -- compare against that rather than a hardcoded
        # forward-slash literal.
        self.assertEqual(command[loop_index + 3], str(music_path))

    def test_music_defaults_to_ten_percent_volume(self):

        self.assertEqual(MUSIC_VOLUME_DEFAULT, 0.10)

        command = build_final_assembly_command(
            **self._base_kwargs(music_path=Path("assets/music/gentle.mp3"))
        )

        self.assertIn(f"volume={MUSIC_VOLUME_DEFAULT}", self._filter_complex(command))

    def test_custom_music_volume_is_honoured(self):

        command = build_final_assembly_command(
            **self._base_kwargs(music_path=Path("assets/music/gentle.mp3"), music_volume=0.25)
        )

        self.assertIn("volume=0.25", self._filter_complex(command))

    def test_music_has_short_fade_in_and_fade_out(self):

        command = build_final_assembly_command(
            **self._base_kwargs(music_path=Path("assets/music/gentle.mp3"), total_duration=24.0)
        )

        filter_complex = self._filter_complex(command)

        self.assertIn(f"afade=t=in:st=0:d={MUSIC_FADE_IN_SECONDS}", filter_complex)

        expected_fade_out_start = 24.0 - MUSIC_FADE_OUT_SECONDS
        self.assertIn(
            f"afade=t=out:st={expected_fade_out_start:.2f}:d={MUSIC_FADE_OUT_SECONDS}",
            filter_complex,
        )

    def test_fade_out_start_never_goes_negative_on_a_very_short_reel(self):

        command = build_final_assembly_command(
            **self._base_kwargs(music_path=Path("assets/music/gentle.mp3"), total_duration=0.3)
        )

        self.assertIn("afade=t=out:st=0.00:", self._filter_complex(command))

    def test_narration_stays_at_full_volume_and_is_mixed_with_music(self):

        command = build_final_assembly_command(
            **self._base_kwargs(music_path=Path("assets/music/gentle.mp3"))
        )
        filter_complex = self._filter_complex(command)

        self.assertIn("[1:a]volume=1.0[narr]", filter_complex)
        self.assertIn("amix=inputs=2:duration=first", filter_complex)

    def test_output_t_flag_caps_total_duration_regardless_of_music(self):
        """Item 5: music must never determine/extend the Reel's duration
        -- the output-level -t flag (paired with looping above) is what
        guarantees that in both directions."""

        command = build_final_assembly_command(
            **self._base_kwargs(music_path=Path("assets/music/gentle.mp3"), total_duration=24.0)
        )

        t_index = command.index("-t")
        self.assertEqual(command[t_index + 1], "24.000")


# =====================================================================
# render_reel_video -- real ffmpeg, real audio mixing (Part of the
# "Real FFmpeg Verification" requirement: proves music genuinely gets
# looped/trimmed to the video's own duration, never the other way
# around, using actual ffprobe measurements on the rendered file).
# =====================================================================

@unittest.skipUnless(FFMPEG_AVAILABLE, "ffmpeg not available on PATH")
class RealFfmpegMusicRenderingTests(unittest.TestCase):

    def _build_scene_setup(self, folder: Path, scene_seconds=3.0, narration_seconds=None):

        cover = folder / "cover.png"
        scene1 = folder / "scene1.png"

        from PIL import Image
        Image.new("RGB", (TARGET_WIDTH, TARGET_HEIGHT), (110, 35, 4)).save(cover)
        Image.new("RGB", (TARGET_WIDTH, TARGET_HEIGHT), (0, 200, 0)).save(scene1)

        scene_images = [cover, scene1, cover]
        scene_durations = [scene_seconds] * 3
        total_duration = sum(scene_durations)

        narration = folder / "narration.mp3"
        subprocess.run(
            [
                "ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
                "-t", str(narration_seconds or int(total_duration) + 1),
                "-q:a", "9", str(narration),
            ],
            capture_output=True,
        )

        return scene_images, scene_durations, narration, total_duration

    def _probe(self, path: Path):

        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "stream=codec_type,width,height:format=duration",
                "-of", "json", str(path),
            ],
            capture_output=True, text=True,
        )
        return json.loads(result.stdout)

    def test_shorter_music_is_looped_to_cover_the_full_reel(self):

        with TemporaryDirectory() as tmp:

            folder = Path(tmp)
            scene_images, scene_durations, narration, total_duration = self._build_scene_setup(folder)

            music = folder / "music.mp3"
            _make_silent_mp3(music, duration=1)  # much shorter than total_duration

            output_path = folder / "reel.mp4"

            render_reel_video(
                scene_images=scene_images,
                scene_durations=scene_durations,
                narration_path=narration,
                output_path=output_path,
                caption_cues=[],
                font_path=FONT_PATH,
                work_dir=folder / "clips",
                music_path=music,
            )

            data = self._probe(output_path)
            duration = float(data["format"]["duration"])

            # The Reel's own duration is unaffected by the much-shorter
            # music track -- it's driven by scenes/narration only.
            self.assertAlmostEqual(duration, total_duration, delta=1.0)
            streams = data["streams"]
            self.assertTrue(any(s["codec_type"] == "audio" for s in streams))
            self.assertTrue(any(s["codec_type"] == "video" for s in streams))

    def test_longer_music_is_trimmed_and_never_extends_the_reel(self):

        with TemporaryDirectory() as tmp:

            folder = Path(tmp)
            scene_images, scene_durations, narration, total_duration = self._build_scene_setup(folder)

            music = folder / "music.mp3"
            _make_silent_mp3(music, duration=int(total_duration) + 20)  # much longer

            output_path = folder / "reel.mp4"

            render_reel_video(
                scene_images=scene_images,
                scene_durations=scene_durations,
                narration_path=narration,
                output_path=output_path,
                caption_cues=[],
                font_path=FONT_PATH,
                work_dir=folder / "clips",
                music_path=music,
            )

            data = self._probe(output_path)
            duration = float(data["format"]["duration"])

            self.assertAlmostEqual(duration, total_duration, delta=1.0)
            self.assertLess(duration, total_duration + 5)

    def test_full_production_style_render_scenes_captions_and_music_together(self):
        """Section 11 ("Real FFmpeg Verification"): one real render
        combining multiple scenes, burned-in captions, AND background
        music in a single pass -- then verifies via ffprobe that the
        output is 1080x1920, has both a video and an audio stream, and
        that its final duration lands in the 20-30s Reel target even
        though the music track's own length doesn't match that window.
        (Narration content here is synthetic silence -- no real
        OPENAI_API_KEY is configured in this environment -- but this
        proves the same mixing/caption/scene pipeline real narration
        would flow through; REEL_NARRATION_VOICE="coral" is verified
        separately in ReelServiceVoiceTests since that's a request
        parameter, not something inspectable in rendered audio.)"""

        with TemporaryDirectory() as tmp:

            folder = Path(tmp)

            from PIL import Image
            cover = folder / "cover.png"
            scene1 = folder / "scene1.png"
            scene2 = folder / "scene2.png"
            Image.new("RGB", (TARGET_WIDTH, TARGET_HEIGHT), (110, 35, 4)).save(cover)
            Image.new("RGB", (TARGET_WIDTH, TARGET_HEIGHT), (255, 0, 0)).save(scene1)
            Image.new("RGB", (TARGET_WIDTH, TARGET_HEIGHT), (0, 0, 255)).save(scene2)

            scene_images = [cover, scene1, scene2, cover]
            scene_durations = [6.0, 6.0, 6.0, 6.0]  # 24s total -- in the 20-30s target
            total_duration = sum(scene_durations)

            narration = folder / "narration.mp3"
            subprocess.run(
                ["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
                 "-t", str(int(total_duration) + 1), "-q:a", "9", str(narration)],
                capture_output=True,
            )

            music = folder / "music.mp3"
            _make_silent_mp3(music, duration=5)  # shorter than the Reel -- must loop

            caption_cues = [
                {"lines": ["Once upon a time..."], "text": "Once upon a time...", "start": 1.0, "end": 5.0},
                {"lines": ["A gentle bedtime story."], "text": "A gentle bedtime story.", "start": 7.0, "end": 11.0},
                {"lines": ["The end."], "text": "The end.", "start": 19.0, "end": 23.0},
            ]

            output_path = folder / "reel.mp4"

            render_reel_video(
                scene_images=scene_images,
                scene_durations=scene_durations,
                narration_path=narration,
                output_path=output_path,
                caption_cues=caption_cues,
                font_path=FONT_PATH,
                work_dir=folder / "clips",
                music_path=music,
            )

            self.assertTrue(output_path.exists())

            data = self._probe(output_path)
            streams = data["streams"]

            video_stream = next(s for s in streams if s["codec_type"] == "video")
            audio_stream = next((s for s in streams if s["codec_type"] == "audio"), None)

            duration = float(data["format"]["duration"])

            self.assertEqual(video_stream["width"], TARGET_WIDTH)
            self.assertEqual(video_stream["height"], TARGET_HEIGHT)
            self.assertIsNotNone(audio_stream, "final render must have an audio stream")
            self.assertGreaterEqual(duration, 20.0)
            self.assertLessEqual(duration, 30.0)

    def test_no_music_still_renders_a_valid_reel(self):

        with TemporaryDirectory() as tmp:

            folder = Path(tmp)
            scene_images, scene_durations, narration, total_duration = self._build_scene_setup(folder)

            output_path = folder / "reel.mp4"

            render_reel_video(
                scene_images=scene_images,
                scene_durations=scene_durations,
                narration_path=narration,
                output_path=output_path,
                caption_cues=[],
                font_path=FONT_PATH,
                work_dir=folder / "clips",
                music_path=None,
            )

            self.assertTrue(output_path.exists())
            data = self._probe(output_path)
            self.assertTrue(any(s["codec_type"] == "audio" for s in data["streams"]))


# =====================================================================
# ReelService.generate() -- music selection/recording end to end.
# Everything except ffmpeg/ffprobe subprocess calls is mocked exactly
# like tests/test_reel_service_generate.py already does; ffmpeg itself
# is mocked here too (fast, no real render needed to prove selection
# logic and reel_script.json bookkeeping).
# =====================================================================

class ReelServiceMusicSelectionTests(unittest.TestCase):

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

        def fake_tts_generate(text, output_file, **kwargs):
            Path(output_file).write_bytes(b"fake-mp3-bytes")
            return output_file

        service.tts.generate.side_effect = fake_tts_generate
        service.images.ensure_scenes.side_effect = _fake_ensure_scenes_writing_real_images(folder)

        return service

    def _generate(self, service, content_id="KS-000001", **kwargs):

        with patch("services.reel_service.check_ffmpeg_available", return_value=True), \
             patch("services.reel_service.run_ffmpeg_command", side_effect=_fake_ffmpeg_writes_output), \
             patch("services.reel_service.probe_video_metadata", return_value=_VALID_METADATA):
            return service.generate(content_id=content_id, **kwargs)

    def test_empty_music_directory_generates_without_music(self):

        with TemporaryDirectory() as tmp:

            folder = Path(tmp) / "story"
            _write_minimal_story_assets(folder)
            service = self._make_service(folder)

            with patch("services.reel_service.MUSIC_DIR", Path(tmp) / "no_music_here"):
                self._generate(service)

            script = json.loads((folder / "reel_script.json").read_text())

            self.assertFalse(script["music"]["enabled"])
            self.assertIsNone(script["music"]["file"])
            self.assertEqual(script["music"]["volume"], MUSIC_VOLUME_DEFAULT)
            self.assertTrue((folder / "reel.mp4").exists())

    @unittest.skipUnless(FFMPEG_AVAILABLE, "ffmpeg not available on PATH")
    def test_single_valid_track_is_selected_and_recorded(self):

        with TemporaryDirectory() as tmp:

            folder = Path(tmp) / "story"
            _write_minimal_story_assets(folder)
            service = self._make_service(folder)

            music_dir = Path(tmp) / "music"
            music_dir.mkdir()
            _make_silent_mp3(music_dir / "gentle.mp3", duration=2)

            with patch("services.reel_service.MUSIC_DIR", music_dir):
                self._generate(service)

            script = json.loads((folder / "reel_script.json").read_text())

            self.assertTrue(script["music"]["enabled"])
            self.assertEqual(script["music"]["file"], "assets/music/gentle.mp3")
            self.assertAlmostEqual(script["music"]["volume"], 0.10)

    @unittest.skipUnless(FFMPEG_AVAILABLE, "ffmpeg not available on PATH")
    def test_same_content_id_selects_same_track_across_regenerations(self):

        with TemporaryDirectory() as tmp:

            folder = Path(tmp) / "story"
            _write_minimal_story_assets(folder)

            music_dir = Path(tmp) / "music"
            music_dir.mkdir()
            for name in ["a.mp3", "b.mp3", "c.mp3"]:
                _make_silent_mp3(music_dir / name, duration=2)

            service = self._make_service(folder)
            with patch("services.reel_service.MUSIC_DIR", music_dir):
                self._generate(service)
            first = json.loads((folder / "reel_script.json").read_text())["music"]["file"]

            service2 = self._make_service(folder)
            with patch("services.reel_service.MUSIC_DIR", music_dir):
                self._generate(service2, overwrite=True)
            second = json.loads((folder / "reel_script.json").read_text())["music"]["file"]

            self.assertEqual(first, second)

    @unittest.skipUnless(FFMPEG_AVAILABLE, "ffmpeg not available on PATH")
    def test_different_content_ids_can_rotate_across_tracks(self):

        with TemporaryDirectory() as tmp:

            music_dir = Path(tmp) / "music"
            music_dir.mkdir()
            for name in [f"t{i}.mp3" for i in range(6)]:
                _make_silent_mp3(music_dir / name, duration=2)

            choices = set()

            for i in range(10):

                content_id = f"KS-{i:06d}"
                folder = Path(tmp) / f"story{i}"
                _write_minimal_story_assets(folder)

                service = self._make_service(folder, content_id=content_id)

                with patch("services.reel_service.MUSIC_DIR", music_dir):
                    self._generate(service, content_id=content_id)

                choice = json.loads((folder / "reel_script.json").read_text())["music"]["file"]
                choices.add(choice)

            self.assertGreater(len(choices), 1)

    @unittest.skipUnless(FFMPEG_AVAILABLE, "ffmpeg not available on PATH")
    def test_invalid_explicit_track_falls_back_without_crashing(self):

        with TemporaryDirectory() as tmp:

            folder = Path(tmp) / "story"
            _write_minimal_story_assets(folder)
            service = self._make_service(folder)

            music_dir = Path(tmp) / "music"
            music_dir.mkdir()
            _make_silent_mp3(music_dir / "gentle.mp3", duration=2)

            with patch("services.reel_service.MUSIC_DIR", music_dir):
                self._generate(service, music_track="does_not_exist.mp3")

            self.assertTrue((folder / "reel.mp4").exists())

            script = json.loads((folder / "reel_script.json").read_text())
            self.assertTrue(script["music"]["enabled"])
            self.assertEqual(script["music"]["file"], "assets/music/gentle.mp3")

    def test_disable_music_skips_even_when_valid_tracks_exist(self):

        with TemporaryDirectory() as tmp:

            folder = Path(tmp) / "story"
            _write_minimal_story_assets(folder)
            service = self._make_service(folder)

            music_dir = Path(tmp) / "music"
            music_dir.mkdir()
            if FFMPEG_AVAILABLE:
                _make_silent_mp3(music_dir / "gentle.mp3", duration=2)

            with patch("services.reel_service.MUSIC_DIR", music_dir):
                self._generate(service, disable_music=True)

            script = json.loads((folder / "reel_script.json").read_text())
            self.assertFalse(script["music"]["enabled"])
            self.assertIsNone(script["music"]["file"])

    def test_corrupt_music_file_does_not_crash_reel_generation(self):
        """An unreadable/corrupt optional music file must never take the
        whole Reel down with it -- it's filtered out and the Reel
        proceeds narration-only."""

        with TemporaryDirectory() as tmp:

            folder = Path(tmp) / "story"
            _write_minimal_story_assets(folder)
            service = self._make_service(folder)

            music_dir = Path(tmp) / "music"
            music_dir.mkdir()
            (music_dir / "corrupt.mp3").write_bytes(b"this is not a real mp3 file")

            with patch("services.reel_service.MUSIC_DIR", music_dir):
                self._generate(service)

            self.assertTrue((folder / "reel.mp4").exists())

            script = json.loads((folder / "reel_script.json").read_text())
            self.assertFalse(script["music"]["enabled"])

    @unittest.skipUnless(FFMPEG_AVAILABLE, "ffmpeg not available on PATH")
    def test_one_valid_track_survives_alongside_a_corrupt_one(self):
        """Item 7: skip an invalid track and try another available one."""

        with TemporaryDirectory() as tmp:

            folder = Path(tmp) / "story"
            _write_minimal_story_assets(folder)
            service = self._make_service(folder)

            music_dir = Path(tmp) / "music"
            music_dir.mkdir()
            (music_dir / "aaa_corrupt.mp3").write_bytes(b"not real audio")
            _make_silent_mp3(music_dir / "zzz_good.mp3", duration=2)

            with patch("services.reel_service.MUSIC_DIR", music_dir):
                self._generate(service)

            script = json.loads((folder / "reel_script.json").read_text())
            self.assertTrue(script["music"]["enabled"])
            self.assertEqual(script["music"]["file"], "assets/music/zzz_good.mp3")

    def test_music_selection_costs_zero_api_calls(self):
        """Music is entirely local: adding it must not add any TTS or
        image-generation calls beyond the existing baseline."""

        with TemporaryDirectory() as tmp:

            folder = Path(tmp) / "story"
            _write_minimal_story_assets(folder)
            service = self._make_service(folder)

            music_dir = Path(tmp) / "music"
            music_dir.mkdir()
            if FFMPEG_AVAILABLE:
                _make_silent_mp3(music_dir / "gentle.mp3", duration=2)

            with patch("services.reel_service.MUSIC_DIR", music_dir):
                self._generate(service)

            self.assertEqual(service.tts.generate.call_count, 1)
            service.images.ensure_scenes.assert_called_once()
            _, kwargs = service.images.ensure_scenes.call_args
            self.assertLessEqual(len(kwargs["beat_indices"]), 3)


# =====================================================================
# ReelService.generate() -- FEMALE narration voice wiring.
# =====================================================================

class ReelServiceVoiceTests(unittest.TestCase):

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

        def fake_tts_generate(text, output_file, **kwargs):
            Path(output_file).write_bytes(b"fake-mp3-bytes")
            return output_file

        service.tts.generate.side_effect = fake_tts_generate
        service.images.ensure_scenes.side_effect = _fake_ensure_scenes_writing_real_images(folder)

        return service

    def _generate(self, service, content_id="KS-000001", **kwargs):

        with patch("services.reel_service.check_ffmpeg_available", return_value=True), \
             patch("services.reel_service.run_ffmpeg_command", side_effect=_fake_ffmpeg_writes_output), \
             patch("services.reel_service.probe_video_metadata", return_value=_VALID_METADATA):
            return service.generate(content_id=content_id, **kwargs)

    def test_female_voice_is_passed_to_the_tts_service(self):

        with TemporaryDirectory() as tmp:

            folder = Path(tmp) / "story"
            _write_minimal_story_assets(folder)
            service = self._make_service(folder)

            self._generate(service)

            service.tts.generate.assert_called_once()
            _, kwargs = service.tts.generate.call_args

            self.assertEqual(kwargs["voice"], REEL_NARRATION_VOICE)
            self.assertEqual(kwargs["voice"], "coral")
            self.assertEqual(kwargs["instructions"], REEL_NARRATION_INSTRUCTIONS)

    def test_only_one_tts_call_when_narration_is_not_cached(self):

        with TemporaryDirectory() as tmp:

            folder = Path(tmp) / "story"
            _write_minimal_story_assets(folder)
            service = self._make_service(folder)

            self._generate(service)

            self.assertEqual(service.tts.generate.call_count, 1)

    def test_matching_voice_cache_is_reused_not_regenerated(self):

        with TemporaryDirectory() as tmp:

            folder = Path(tmp) / "story"
            _write_minimal_story_assets(folder)
            service = self._make_service(folder)

            self._generate(service)
            service.tts.generate.reset_mock()

            self._generate(service, overwrite=True)

            service.tts.generate.assert_not_called()

    def test_stale_pre_voice_change_cache_is_regenerated_exactly_once(self):
        """A reel_narration.txt written before the voice became part of
        the cache key (i.e. plain narration text, no voice marker) must
        not be silently reused under the new female voice."""

        with TemporaryDirectory() as tmp:

            folder = Path(tmp) / "story"
            _write_minimal_story_assets(folder)

            from services.reel_service import build_reel_script, load_story_package, select_beat_indices

            story = load_story_package(folder)
            script = build_reel_script(story, select_beat_indices(len(story.slides)))

            (folder / "reel_narration.mp3").write_bytes(b"old-format-cached-audio")
            (folder / "reel_narration.txt").write_text(script["full_narration"], encoding="utf-8")

            service = self._make_service(folder)

            self._generate(service)

            service.tts.generate.assert_called_once()
            self.assertEqual((folder / "reel_narration.mp3").read_bytes(), b"fake-mp3-bytes")

    def test_image_generation_call_count_unaffected_by_music_addition(self):

        with TemporaryDirectory() as tmp:

            folder = Path(tmp) / "story"
            _write_minimal_story_assets(folder)
            service = self._make_service(folder)

            music_dir = Path(tmp) / "music"
            music_dir.mkdir()
            if FFMPEG_AVAILABLE:
                _make_silent_mp3(music_dir / "gentle.mp3", duration=2)

            with patch("services.reel_service.MUSIC_DIR", music_dir):
                self._generate(service)

            service.images.ensure_scenes.assert_called_once()


if __name__ == "__main__":
    unittest.main()
