"""Tests for reel_caption.txt -- the copy-ready Instagram POSTING copy
(hook + watch prompt + story value + growth CTAs + hashtags), entirely
distinct from the on-screen burned subtitle text tested in
test_reel_caption_rendering.py / test_reel_caption_segmentation.py.

Primary purpose being tested: growth (views, watch-through, saves,
shares, follows) while staying accurate to the real story and never
revealing the ending -- all deterministic/local, zero additional API
calls.
"""
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import yaml
from PIL import Image

from models.story_models import StoryPackage
from services.reel_service import (
    ReelService,
    build_hook,
    build_reel_script,
    select_beat_indices,
)
from services.reel_posting_copy_service import build_reel_posting_copy

from tests.test_reel_service_generate import (
    _fake_ensure_scenes_writing_real_images,
    _fake_ffmpeg_writes_output,
    _write_minimal_story_assets,
    _VALID_METADATA,
)

WORKFLOW_PATH = Path(".github/workflows/generate-reel.yml")


def _story(title="Test Story", theme="Kindness", moral="Being kind makes everyone happy.",
           character_name="Pip", species="Squirrel", hook=""):

    return StoryPackage(**{
        "story_info": {
            "title": title, "subtitle": "A subtitle", "theme": theme,
            "target_age": "3-5", "reading_time": "3 min", "moral": moral,
        },
        "character_sheet": {
            "main_character": {
                "name": character_name, "species": species,
                "appearance": "soft fur", "personality": "curious",
            },
            "supporting_characters": [],
        },
        "cover": {"prompt": "a fox", "negative_prompt": "", "style": "", "title_position": "top"},
        "slides": [
            {"page": 1, "title": "T1", "text": f"{character_name} found a problem to solve today.",
             "background_color": "#fff", "visual_theme": "", "icon": "", "speaker_notes": ""},
            {"page": 2, "title": "T2", "text": f"{character_name} went on an adventure to fix it.",
             "background_color": "#fff", "visual_theme": "", "icon": "", "speaker_notes": ""},
            {"page": 3, "title": "T3", "text": f"{character_name} felt proud at the end.",
             "background_color": "#fff", "visual_theme": "", "icon": "", "speaker_notes": ""},
        ],
        "instagram": {"caption": "", "hashtags": []},
        "email": {"subject": "", "preview": ""},
        "youtube": {"title": "", "description": "", "keywords": []},
        "publishing": {
            "hook": hook, "instagram_caption_short": "", "instagram_caption_long": "",
            "hashtags": [], "first_comment": "", "alt_text": "", "call_to_action": "",
            "best_posting_time": "", "parent_question": "",
        },
    })


def _script_for(story):
    beat_indices = select_beat_indices(len(story.slides))
    return build_reel_script(story, beat_indices)


class CopyStructureTests(unittest.TestCase):
    """Items 9, 10, 11: hashtags in the same file, separated by a blank
    line, no section-header labels."""

    def setUp(self):
        self.story = _story()
        self.copy = build_reel_posting_copy(self.story, _script_for(self.story), "KS-000001")

    def test_hashtags_are_in_the_same_file(self):
        self.assertIn("#", self.copy)

    def test_caption_and_hashtags_separated_by_a_blank_line(self):

        lines = self.copy.rstrip("\n").split("\n")
        hashtag_line_index = next(i for i, l in enumerate(lines) if l.startswith("#"))

        self.assertEqual(lines[hashtag_line_index - 1], "")

    def test_no_caption_or_hashtags_labels(self):

        self.assertNotIn("Caption:", self.copy)
        self.assertNotIn("Hashtags:", self.copy)
        self.assertNotIn("CAPTION:", self.copy.upper().replace("HASHTAGS:", ""))

    def test_hashtag_line_is_a_single_line_at_the_end(self):

        lines = self.copy.rstrip("\n").split("\n")
        self.assertTrue(lines[-1].startswith("#"))
        # Every token on that line is a hashtag -- it's not mixed with
        # prose.
        for token in lines[-1].split():
            self.assertTrue(token.startswith("#"), f"non-hashtag token on hashtag line: {token!r}")


class HookTests(unittest.TestCase):
    """Items 1, 2, 3: curiosity hook, grounded in the real story, ending
    not revealed."""

    def test_caption_starts_with_the_reels_own_hook(self):
        """The posting-copy hook must be exactly the Reel's own
        on-screen hook (reel_script['hook']) -- never re-derived, so it
        can never diverge or invent something the Reel doesn't say."""

        story = _story(character_name="Pip")
        script = _script_for(story)
        copy = build_reel_posting_copy(story, script, "KS-000001")

        first_line = copy.split("\n")[0]

        self.assertEqual(first_line, script["hook"])
        self.assertEqual(first_line, build_hook(story))

    def test_hook_is_grounded_in_real_character_name(self):

        story = _story(character_name="Bella")
        script = _script_for(story)
        copy = build_reel_posting_copy(story, script, "KS-000002")

        self.assertIn("Bella", copy.split("\n")[0])

    def test_ending_is_not_revealed(self):
        """A distinctive, made-up plot-resolution detail placed in the
        story's moral/final slide must never leak into the hook or
        watch-prompt sections -- those are built from templates + the
        character name only, never from resolution-specific text."""

        story = _story(
            character_name="Pip",
            moral="Pip found the hidden treasure and became the hero of Wonderwood Valley.",
        )
        script = _script_for(story)
        copy = build_reel_posting_copy(story, script, "KS-000001")

        hook_and_watch_prompt = "\n".join(copy.split("\n")[:3])

        self.assertNotIn("hidden treasure", hook_and_watch_prompt)
        self.assertNotIn("became the hero", hook_and_watch_prompt)


class CtaTests(unittest.TestCase):
    """Items 4, 5, 6: growth CTA present, follow present, save/share
    present."""

    def setUp(self):
        self.story = _story()
        self.copy = build_reel_posting_copy(self.story, _script_for(self.story), "KS-000001")

    def test_contains_like_cta(self):
        # Rotated wording includes natural Instagram-native phrasing like
        # "Double-tap" (not always the literal word "Like") -- the ❤️
        # marker is what's actually consistent across every variant.
        self.assertIn("❤️", self.copy)

    def test_contains_save_cta(self):
        self.assertIn("💾", self.copy)

    def test_contains_share_cta(self):
        self.assertIn("👨‍👩‍👧", self.copy)

    def test_contains_follow_cta(self):
        self.assertIn("Follow", self.copy)
        self.assertIn("➕", self.copy)

    def test_follow_cta_never_claims_an_unverified_posting_cadence(self):
        """Reel POSTING is manual (see generate_reel.py) even though
        story generation runs daily -- the follow CTA must not claim a
        Reel-specific cadence ("tomorrow", "every day") the account
        doesn't actually guarantee."""

        for _ in range(30):
            copy = build_reel_posting_copy(self.story, _script_for(self.story), f"KS-{_:06d}")
            follow_line = next(l for l in copy.split("\n") if "Follow" in l)
            self.assertNotIn("tomorrow", follow_line.lower())
            self.assertNotIn("every day", follow_line.lower())
            self.assertNotIn("daily", follow_line.lower())


class StoryValueTests(unittest.TestCase):
    """Items 7, 8: theme/moral accurately represented, no invented
    facts."""

    def test_theme_is_represented(self):

        story = _story(theme="Bravery and Courage")
        script = _script_for(story)
        copy = build_reel_posting_copy(story, script, "KS-000001")

        self.assertIn("bravery", copy.lower())

    def test_no_invented_character_names(self):
        """Only the story's own real character name may appear as a
        named character anywhere in the generated copy."""

        story = _story(character_name="Zara")
        script = _script_for(story)
        copy = build_reel_posting_copy(story, script, "KS-000001")

        # Sanity: the real name IS present.
        self.assertIn("Zara", copy)

        # No other common placeholder/invented name templates leaked in
        # (a regression guard against ever hardcoding a different
        # example name like "Pip" into a template by mistake).
        for other_name in ["Pip", "Bella", "Milo", "Luna"]:
            self.assertNotIn(other_name, copy)


class HashtagTests(unittest.TestCase):
    """Items 12, 13: 8-12 hashtags, no duplicates."""

    def test_hashtag_count_within_target_range(self):

        for theme in ["Kindness", "Sharing", "Bravery and Courage", "Honesty", "Unknown Theme"]:
            with self.subTest(theme=theme):
                story = _story(theme=theme)
                copy = build_reel_posting_copy(story, _script_for(story), "KS-000001")
                hashtags = copy.strip().split("\n")[-1].split()
                self.assertGreaterEqual(len(hashtags), 8)
                self.assertLessEqual(len(hashtags), 12)

    def test_no_duplicate_hashtags(self):

        story = _story(theme="Kindness")
        copy = build_reel_posting_copy(story, _script_for(story), "KS-000001")
        hashtags = copy.strip().split("\n")[-1].split()

        self.assertEqual(len(hashtags), len(set(hashtags)))

    def test_hashtags_are_relevant_not_stuffed_or_viral_claims(self):

        story = _story(theme="Kindness")
        copy = build_reel_posting_copy(story, _script_for(story), "KS-000001")
        hashtags = copy.strip().split("\n")[-1].split()

        for forbidden in ["#viral", "#fyp", "#explore", "#trending", "#foryou", "#guaranteed"]:
            self.assertNotIn(forbidden, [h.lower() for h in hashtags])


class DeterminismTests(unittest.TestCase):
    """Items 14, 15: same story -> deterministic output; different
    stories -> appropriately different copy."""

    def test_same_content_id_and_story_is_fully_deterministic(self):

        story = _story()
        script = _script_for(story)

        copy1 = build_reel_posting_copy(story, script, "KS-000001")
        copy2 = build_reel_posting_copy(story, script, "KS-000001")

        self.assertEqual(copy1, copy2)

    def test_different_content_ids_can_produce_different_cta_phrasing(self):

        story = _story()
        script = _script_for(story)

        variants = {
            build_reel_posting_copy(story, script, f"KS-{i:06d}")
            for i in range(20)
        }

        self.assertGreater(len(variants), 1)

    def test_different_stories_produce_different_hooks(self):

        story_a = _story(character_name="Pip", theme="Kindness")
        story_b = _story(character_name="Zara", theme="Bravery and Courage")

        copy_a = build_reel_posting_copy(story_a, _script_for(story_a), "KS-000001")
        copy_b = build_reel_posting_copy(story_b, _script_for(story_b), "KS-000002")

        self.assertNotEqual(copy_a.split("\n")[0], copy_b.split("\n")[0])


class NoApiCallTests(unittest.TestCase):
    """Item 16: no additional API calls."""

    def test_module_imports_no_ai_client(self):

        source = Path("services/reel_posting_copy_service.py").read_text(encoding="utf-8")

        for forbidden in ["openai", "OpenAI", "gemini", "Gemini", "requests.", "httpx.", "urllib"]:
            self.assertNotIn(forbidden, source)

    def test_reel_generate_image_and_tts_call_counts_unaffected(self):
        """End-to-end: adding posting-copy generation to ReelService.
        generate() must not add any TTS or image-generation call beyond
        the existing baseline."""

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
                service.generate(content_id="KS-000001")

            self.assertEqual(service.tts.generate.call_count, 1)
            service.images.ensure_scenes.assert_called_once()


class ReelGenerationUnchangedAndFileWrittenTests(unittest.TestCase):
    """Item 17: existing Reel generation still works exactly as before;
    reel_caption.txt is now written alongside reel_script.json/reel.mp4."""

    def _make_service(self, folder, content_id="KS-000001"):

        with patch("services.reel_service.ContentLibraryService"), \
             patch("services.reel_service.OpenAITTSService"), \
             patch("services.reel_service.ReelImageService"), \
             patch("services.reel_service.BrandLoader.load", return_value={}):
            service = ReelService()

        service.library.get_story.return_value = {
            "content_id": content_id, "title": "Test Story", "folder": str(folder),
        }

        def fake_tts_generate(text, output_file, **kwargs):
            Path(output_file).write_bytes(b"fake-mp3-bytes")
            return output_file

        service.tts.generate.side_effect = fake_tts_generate
        service.images.ensure_scenes.side_effect = _fake_ensure_scenes_writing_real_images(folder)

        return service

    def test_reel_caption_txt_is_written_and_reel_mp4_still_produced(self):

        with TemporaryDirectory() as tmp:

            folder = Path(tmp) / "story"
            _write_minimal_story_assets(folder)
            service = self._make_service(folder)

            with patch("services.reel_service.check_ffmpeg_available", return_value=True), \
                 patch("services.reel_service.run_ffmpeg_command", side_effect=_fake_ffmpeg_writes_output), \
                 patch("services.reel_service.probe_video_metadata", return_value=_VALID_METADATA):
                result = service.generate(content_id="KS-000001")

            self.assertTrue(result.exists())
            self.assertTrue((folder / "reel.mp4").exists())
            self.assertTrue((folder / "reel_script.json").exists())

            caption_path = folder / "reel_caption.txt"
            self.assertTrue(caption_path.exists())

            text = caption_path.read_text(encoding="utf-8")
            self.assertIn("#", text)
            self.assertTrue(len(text.strip()) > 0)

    def test_existing_reel_script_json_still_has_its_original_fields(self):
        """Adding the new file must not change reel_script.json's own
        structure."""

        with TemporaryDirectory() as tmp:

            folder = Path(tmp) / "story"
            _write_minimal_story_assets(folder)
            service = self._make_service(folder)

            with patch("services.reel_service.check_ffmpeg_available", return_value=True), \
                 patch("services.reel_service.run_ffmpeg_command", side_effect=_fake_ffmpeg_writes_output), \
                 patch("services.reel_service.probe_video_metadata", return_value=_VALID_METADATA):
                service.generate(content_id="KS-000001")

            script = json.loads((folder / "reel_script.json").read_text(encoding="utf-8"))

            for field in ["hook", "beats", "payoff", "cta", "segments", "full_narration", "duration_target", "music"]:
                self.assertIn(field, script)


class ArtifactBundleTests(unittest.TestCase):
    """Item 18: reel_caption.txt is included in the primary GitHub
    artifact (the "Upload reel.mp4" step)."""

    def test_primary_artifact_includes_reel_caption_txt(self):

        with open(WORKFLOW_PATH, encoding="utf-8") as f:
            data = yaml.safe_load(f)

        steps = data["jobs"]["generate-reel"]["steps"]
        upload = next(s for s in steps if s["name"] == "Upload reel.mp4")

        path_value = upload["with"]["path"]

        self.assertIn("reel_caption.txt", path_value)
        self.assertIn("reel.mp4", path_value)


if __name__ == "__main__":
    unittest.main()
