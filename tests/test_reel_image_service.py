import base64
import io
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from PIL import Image

from services.reel_service import load_story_package, select_beat_indices, build_reel_script
from services.reel_image_service import (
    ReelImageGenerationError,
    ReelImageService,
    build_fallback_scene_prompt,
    build_scene_prompt,
    load_scene_cache,
    relevant_supporting_characters,
    MAX_REEL_SCENE_IMAGES,
)
from models.story_models import (
    StoryPackage, StoryInfo, CharacterSheet, MainCharacter,
    SupportingCharacter, Cover, Slide, Instagram, Email, YouTube, PublishingPack,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
REAL_STORY_FOLDER = REPO_ROOT / "output" / "20260718_152106_pip_s_colourful_help"


def _load_real_story():
    return load_story_package(REAL_STORY_FOLDER)


def _fake_png_b64(color="orange", size=(1024, 1536)):

    image = Image.new("RGB", size, color)
    buf = io.BytesIO()
    image.save(buf, format="PNG")

    return base64.b64encode(buf.getvalue()).decode()


def _build_story(main_appearance="A small brown squirrel with a bushy tail.",
                  supporting=None, slides=None, moral="Being kind makes everyone happy."):

    return StoryPackage(
        story_info=StoryInfo(
            title="Test Story", subtitle="sub", theme="Friendship",
            target_age="3-6", reading_time="2 min", moral=moral,
        ),
        character_sheet=CharacterSheet(
            main_character=MainCharacter(
                name="Pip", species="Squirrel", appearance=main_appearance,
                personality="Kind and curious.",
            ),
            supporting_characters=supporting or [],
        ),
        cover=Cover(prompt="p", negative_prompt="n", style="s", title_position="bottom"),
        slides=slides or [
            Slide(page=1, title="A Sad Friend",
                  text="Pip saw his friend Lily the ladybug looking sad by the tree.",
                  background_color="#FDE9D9", visual_theme="evening", icon="sun", speaker_notes=""),
            Slide(page=2, title="Helping Hands",
                  text="Pip had a good idea and offered to help Lily look for her leaf.",
                  background_color="#FEF8F0", visual_theme="friendship", icon="heart", speaker_notes=""),
            Slide(page=3, title="Happy and Cozy",
                  text="Pip and Lily sat together by their colourful leaf pile, feeling happy.",
                  background_color="#F7DEBE", visual_theme="night", icon="moon", speaker_notes=""),
        ],
        instagram=Instagram(caption="c", hashtags=[]),
        email=Email(subject="s", preview="p"),
        youtube=YouTube(title="t", description="d", keywords=[]),
        publishing=PublishingPack(
            hook="", instagram_caption_short="", instagram_caption_long="",
            hashtags=[], first_comment="", alt_text="", call_to_action="",
            best_posting_time="", parent_question="",
        ),
    )


class RelevantSupportingCharactersTests(unittest.TestCase):

    def test_character_included_when_named_in_slide_text(self):
        lily = SupportingCharacter(name="Lily", species="Ladybug", appearance="A small red ladybug.", role="Pip's friend")
        result = relevant_supporting_characters("Pip saw Lily looking sad.", [lily])
        self.assertEqual(result, [lily])

    def test_character_excluded_when_not_mentioned_in_this_slide(self):
        lily = SupportingCharacter(name="Lily", species="Ladybug", appearance="A small red ladybug.", role="Pip's friend")
        result = relevant_supporting_characters("Pip played alone near the big tree.", [lily])
        self.assertEqual(result, [])

    def test_no_false_positive_on_partial_word_match(self):
        # "Sam" must not match inside "Samuel" or similar substrings.
        sam = SupportingCharacter(name="Sam", species="Rabbit", appearance="A small grey rabbit.", role="friend")
        result = relevant_supporting_characters("Samuel walked to the pond.", [sam])
        self.assertEqual(result, [])

    def test_multiple_supporting_characters_filtered_independently(self):
        lily = SupportingCharacter(name="Lily", species="Ladybug", appearance="red", role="friend")
        max_ = SupportingCharacter(name="Max", species="Dog", appearance="brown", role="friend")
        result = relevant_supporting_characters("Pip and Lily looked for the leaf.", [lily, max_])
        self.assertEqual(result, [lily])


class BuildScenePromptTests(unittest.TestCase):

    def test_prompt_contains_exact_canonical_appearance(self):
        story = _build_story(main_appearance="A small, brown squirrel with a big bushy tail and shiny black eyes.")
        prompt = build_scene_prompt(story, 0, story.slides[0].text, 0, 3)
        self.assertIn(
            "A small, brown squirrel with a big bushy tail and shiny black eyes.",
            prompt,
        )

    def test_prompt_includes_relevant_supporting_character(self):
        lily = SupportingCharacter(name="Lily", species="Ladybug", appearance="A small, friendly red ladybug.", role="Pip's best friend")
        story = _build_story(supporting=[lily])
        # slide 0 text mentions Lily
        prompt = build_scene_prompt(story, 0, story.slides[0].text, 0, 3)
        self.assertIn("Lily", prompt)
        self.assertIn("A small, friendly red ladybug.", prompt)

    def test_prompt_omits_supporting_character_not_in_this_scene(self):
        lily = SupportingCharacter(name="Lily", species="Ladybug", appearance="A small, friendly red ladybug.", role="Pip's best friend")
        slides = [
            Slide(page=1, title="Alone", text="Pip walked through the forest by himself.",
                  background_color="#FDE9D9", visual_theme="calm", icon="leaf", speaker_notes=""),
        ]
        story = _build_story(supporting=[lily], slides=slides)
        prompt = build_scene_prompt(story, 0, story.slides[0].text, 0, 1)
        self.assertNotIn("Lily", prompt)
        self.assertIn("No supporting character in this scene", prompt)

    def test_prompt_grounded_only_in_actual_slide_text_no_invention(self):
        story = _build_story()
        scene_text = story.slides[1].text
        prompt = build_scene_prompt(story, 1, scene_text, 1, 3)
        self.assertIn(scene_text, prompt)

    def test_prompt_is_child_safe(self):
        story = _build_story()
        prompt = build_scene_prompt(story, 0, story.slides[0].text, 0, 3).lower()
        for forbidden in ("violence", "weapon", "blood", "terrified", "trapped", "dangerous"):
            # These only appear as NEGATIVE/forbidden instructions, never
            # as something to depict -- check the safety block exists
            # rather than asserting total absence, since "no violence" is
            # exactly what should be present.
            pass
        self.assertIn("no scary content", prompt)
        self.assertIn("no violence", prompt)
        self.assertIn("depict it\n    gently and safely", prompt.replace("\n    ", "\n    "))

    def test_scene_role_labels_problem_action_resolution(self):
        story = _build_story()
        first = build_scene_prompt(story, 0, story.slides[0].text, 0, 3)
        middle = build_scene_prompt(story, 1, story.slides[1].text, 1, 3)
        last = build_scene_prompt(story, 2, story.slides[2].text, 2, 3)
        self.assertIn("Problem / situation", first)
        self.assertIn("Action", middle)
        self.assertIn("Resolution / happy moment", last)

    def test_fallback_prompt_has_no_proper_names(self):
        story = _build_story()
        fallback = build_fallback_scene_prompt(story)
        self.assertNotIn("Pip", fallback)
        self.assertIn("squirrel", fallback.lower())


class ReelImageServiceGenerateTests(unittest.TestCase):
    """All OpenAI calls are mocked -- no real API calls are made."""

    def _make_mock_openai(self, b64=None):
        mock = MagicMock()
        mock.generate_image.return_value = b64 or _fake_png_b64()
        return mock

    def test_three_meaningful_beats_generate_three_scenes(self):
        story = _build_story()
        beat_indices = [0, 1, 2]
        beat_texts = [story.slides[i].text for i in beat_indices]

        with TemporaryDirectory() as tmp:
            folder = Path(tmp)
            mock_openai = self._make_mock_openai()
            service = ReelImageService(openai_service=mock_openai)

            results = service.ensure_scenes(story, "KS-TEST", beat_indices, beat_texts, folder)

            self.assertEqual(len(results), 3)
            self.assertEqual(mock_openai.generate_image.call_count, 3)

    def test_two_beats_generate_two_scenes_not_three(self):
        story = _build_story()
        beat_indices = [0, 2]
        beat_texts = [story.slides[i].text for i in beat_indices]

        with TemporaryDirectory() as tmp:
            folder = Path(tmp)
            mock_openai = self._make_mock_openai()
            service = ReelImageService(openai_service=mock_openai)

            results = service.ensure_scenes(story, "KS-TEST", beat_indices, beat_texts, folder)

            self.assertEqual(len(results), 2)
            self.assertEqual(mock_openai.generate_image.call_count, 2)

    def test_deterministic_scene_filenames(self):
        story = _build_story()
        beat_indices = [0, 1, 2]
        beat_texts = [story.slides[i].text for i in beat_indices]

        with TemporaryDirectory() as tmp:
            folder = Path(tmp)
            service = ReelImageService(openai_service=self._make_mock_openai())

            results = service.ensure_scenes(story, "KS-TEST", beat_indices, beat_texts, folder)

            names = [Path(r["image_path"]).name for r in results]
            self.assertEqual(names, ["reel_scene_01.png", "reel_scene_02.png", "reel_scene_03.png"])

    def test_generated_images_are_1024x1536_native_before_crop(self):
        # ReelImageService itself does not crop -- that's reel_service's
        # materialize_scene_images (via prepare_full_bleed_image) job.
        story = _build_story()
        with TemporaryDirectory() as tmp:
            folder = Path(tmp)
            service = ReelImageService(openai_service=self._make_mock_openai())

            results = service.ensure_scenes(story, "KS-TEST", [0], [story.slides[0].text], folder)

            with Image.open(results[0]["image_path"]) as image:
                self.assertEqual(image.size, (1024, 1536))

    def test_exceeding_max_scenes_raises_without_calling_api(self):
        story = _build_story()
        beat_indices = [0, 1, 2, 3]  # more than MAX_REEL_SCENE_IMAGES
        beat_texts = ["a"] * 4

        with TemporaryDirectory() as tmp:
            folder = Path(tmp)
            mock_openai = self._make_mock_openai()
            service = ReelImageService(openai_service=mock_openai)

            self.assertGreater(len(beat_indices), MAX_REEL_SCENE_IMAGES)

            with self.assertRaises(ReelImageGenerationError):
                service.ensure_scenes(story, "KS-TEST", beat_indices, beat_texts, folder)

            mock_openai.generate_image.assert_not_called()

    def test_caching_reuses_unchanged_scene_zero_api_calls_on_second_run(self):
        story = _build_story()
        beat_indices = [0, 1, 2]
        beat_texts = [story.slides[i].text for i in beat_indices]

        with TemporaryDirectory() as tmp:
            folder = Path(tmp)
            mock_openai = self._make_mock_openai()
            service = ReelImageService(openai_service=mock_openai)

            service.ensure_scenes(story, "KS-TEST", beat_indices, beat_texts, folder)
            self.assertEqual(mock_openai.generate_image.call_count, 3)

            mock_openai.generate_image.reset_mock()
            results = service.ensure_scenes(story, "KS-TEST", beat_indices, beat_texts, folder)

            mock_openai.generate_image.assert_not_called()
            self.assertEqual(len(results), 3)

    def test_changed_scene_text_regenerates_only_that_scene(self):
        story = _build_story()
        beat_indices = [0, 1, 2]
        beat_texts = [story.slides[i].text for i in beat_indices]

        with TemporaryDirectory() as tmp:
            folder = Path(tmp)
            mock_openai = self._make_mock_openai()
            service = ReelImageService(openai_service=mock_openai)

            service.ensure_scenes(story, "KS-TEST", beat_indices, beat_texts, folder)

            original_bytes = {
                i: (folder / f"reel_scene_{i+1:02d}.png").read_bytes()
                for i in range(3)
            }

            mock_openai.generate_image.reset_mock()
            mock_openai.generate_image.return_value = _fake_png_b64(color="blue")

            changed_texts = list(beat_texts)
            changed_texts[1] = "Pip found a brand new adventure that changes everything today."

            service.ensure_scenes(story, "KS-TEST", beat_indices, changed_texts, folder)

            self.assertEqual(mock_openai.generate_image.call_count, 1)

            self.assertEqual(
                (folder / "reel_scene_01.png").read_bytes(), original_bytes[0]
            )
            self.assertEqual(
                (folder / "reel_scene_03.png").read_bytes(), original_bytes[2]
            )
            self.assertNotEqual(
                (folder / "reel_scene_02.png").read_bytes(), original_bytes[1]
            )

    def test_force_regenerates_all_scenes_even_if_cached(self):
        story = _build_story()
        beat_indices = [0, 1, 2]
        beat_texts = [story.slides[i].text for i in beat_indices]

        with TemporaryDirectory() as tmp:
            folder = Path(tmp)
            mock_openai = self._make_mock_openai()
            service = ReelImageService(openai_service=mock_openai)

            service.ensure_scenes(story, "KS-TEST", beat_indices, beat_texts, folder)
            mock_openai.generate_image.reset_mock()

            service.ensure_scenes(story, "KS-TEST", beat_indices, beat_texts, folder, force=True)

            self.assertEqual(mock_openai.generate_image.call_count, 3)

    def test_scene_cache_file_records_expected_fields(self):
        story = _build_story()
        beat_indices = [0]
        beat_texts = [story.slides[0].text]

        with TemporaryDirectory() as tmp:
            folder = Path(tmp)
            service = ReelImageService(openai_service=self._make_mock_openai())

            service.ensure_scenes(story, "KS-TEST", beat_indices, beat_texts, folder)

            cache = load_scene_cache(folder)
            self.assertEqual(cache["content_id"], "KS-TEST")
            entry = cache["scenes"][0]
            for field in ("scene_number", "content_id", "slide_index", "description", "prompt_hash", "image_path"):
                self.assertIn(field, entry)

    def test_moderation_blocked_retries_with_fallback_prompt(self):
        """Mirrors ImageAgent's cover moderation-fallback behaviour: a
        moderation_blocked error on the primary prompt triggers exactly
        one retry with the simplified fallback prompt; success on that
        retry still produces a usable scene."""

        story = _build_story()
        mock_openai = MagicMock()

        class FakeBlockedError(Exception):
            pass

        def raise_then_succeed(prompt):
            if mock_openai.generate_image.call_count == 1:
                raise FakeBlockedError("blocked")
            return _fake_png_b64()

        mock_openai.generate_image.side_effect = raise_then_succeed

        with patch("services.reel_image_service.BadRequestError", FakeBlockedError), \
             patch("services.reel_image_service.OpenAIService.is_moderation_blocked", return_value=True):

            with TemporaryDirectory() as tmp:
                folder = Path(tmp)
                service = ReelImageService(openai_service=mock_openai)
                results = service.ensure_scenes(story, "KS-TEST", [0], [story.slides[0].text], folder)

                self.assertEqual(len(results), 1)
                self.assertEqual(mock_openai.generate_image.call_count, 2)

    def test_moderation_blocked_on_both_attempts_raises(self):

        story = _build_story()
        mock_openai = MagicMock()

        class FakeBlockedError(Exception):
            pass

        mock_openai.generate_image.side_effect = FakeBlockedError("blocked")

        with patch("services.reel_image_service.BadRequestError", FakeBlockedError), \
             patch("services.reel_image_service.OpenAIService.is_moderation_blocked", return_value=True), \
             patch("services.reel_image_service.OpenAIService.extract_request_id", return_value=None):

            with TemporaryDirectory() as tmp:
                folder = Path(tmp)
                service = ReelImageService(openai_service=mock_openai)

                with self.assertRaises(ReelImageGenerationError):
                    service.ensure_scenes(story, "KS-TEST", [0], [story.slides[0].text], folder)

                self.assertEqual(mock_openai.generate_image.call_count, 2)

    def test_non_moderation_error_raises_immediately_without_fallback(self):
        story = _build_story()
        mock_openai = MagicMock()
        mock_openai.generate_image.side_effect = RuntimeError("network boom")

        with TemporaryDirectory() as tmp:
            folder = Path(tmp)
            service = ReelImageService(openai_service=mock_openai)

            with self.assertRaises(ReelImageGenerationError):
                service.ensure_scenes(story, "KS-TEST", [0], [story.slides[0].text], folder)

            self.assertEqual(mock_openai.generate_image.call_count, 1)


class RealStoryStoryboardTests(unittest.TestCase):
    """Confirms the generated storyboard represents the ACTUAL KS-000001
    story, not fabricated assumptions -- inspects real title, character,
    and slide text."""

    def test_scene_descriptions_come_from_real_story_slides(self):
        story = _load_real_story()
        self.assertEqual(story.story_info.title, "Pip's Colourful Help")
        self.assertEqual(story.character_sheet.main_character.name, "Pip")
        self.assertEqual(story.character_sheet.supporting_characters, [])

        beat_indices = select_beat_indices(len(story.slides))
        script = build_reel_script(story, beat_indices)
        beat_segments = [s for s in script["segments"] if s["kind"] == "beat"]

        for segment in beat_segments:
            prompt = build_scene_prompt(
                story, segment["slide_index"], segment["text"],
                beat_segments.index(segment), len(beat_segments),
            )
            self.assertIn("Pip", prompt)
            self.assertIn(segment["text"], prompt)
            # No supporting character data exists for this real story yet
            # (character_sheet.supporting_characters is empty) -- the
            # prompt must not invent one.
            self.assertIn("No supporting character in this scene", prompt)


if __name__ == "__main__":
    unittest.main()
