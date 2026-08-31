import unittest
from pathlib import Path

from PIL import Image, ImageFont

from services.reel_service import (
    build_caption_chunks,
    build_caption_cues,
    build_hook,
    build_reel_script,
    compute_scene_durations,
    crop_to_fill,
    load_story_package,
    prepare_full_bleed_image,
    select_beat_indices,
    select_reel_hero_image,
    CAPTION_FONT_SIZE,
    CAPTION_MAX_LINES,
    CAPTION_SAFE_WIDTH_PX,
    MAX_DURATION_SECONDS,
    MIN_DURATION_SECONDS,
    TARGET_HEIGHT,
    TARGET_WIDTH,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

# Section 21 of the spec: exercise the real, existing KS-000001 story data
# rather than a fabricated structure, so scene selection is proven against
# what the daily pipeline actually produces.
REAL_STORY_FOLDER = REPO_ROOT / "output" / "20260718_152106_pip_s_colourful_help"
REAL_FONT_PATH = REPO_ROOT / "assets" / "fonts" / "Poppins-Bold.ttf"


def _load_real_story():
    return load_story_package(REAL_STORY_FOLDER)


class SelectBeatIndicesTests(unittest.TestCase):

    def test_six_slides_matches_spec_example(self):
        # Spec: "For a 6-slide story, prefer: cover + slide 1 + slide 3/4
        # + slide 6" -- i.e. 0-indexed slides 0, 2, 5.
        self.assertEqual(select_beat_indices(6), [0, 2, 5])

    def test_five_slides_matches_spec_example(self):
        # Spec: "For a 5-slide story, prefer: cover + slide 1 + slide 3 +
        # slide 5" -- i.e. 0-indexed slides 0, 2, 4.
        self.assertEqual(select_beat_indices(5), [0, 2, 4])

    def test_fewer_slides_than_max_uses_all(self):
        self.assertEqual(select_beat_indices(2), [0, 1])
        self.assertEqual(select_beat_indices(1), [0])

    def test_zero_slides_returns_empty(self):
        self.assertEqual(select_beat_indices(0), [])

    def test_deterministic_across_repeated_calls(self):
        results = {tuple(select_beat_indices(6)) for _ in range(20)}
        self.assertEqual(len(results), 1)

    def test_indices_never_duplicate_or_out_of_range(self):
        for num_slides in range(1, 12):
            indices = select_beat_indices(num_slides)
            self.assertEqual(len(indices), len(set(indices)))
            for i in indices:
                self.assertGreaterEqual(i, 0)
                self.assertLess(i, num_slides)


class BuildHookTests(unittest.TestCase):

    def test_uses_short_authored_hook_when_present(self):
        story = _load_real_story()
        story.publishing.hook = "A tiny leaf goes missing in the forest."
        self.assertEqual(
            build_hook(story), "A tiny leaf goes missing in the forest."
        )

    def test_falls_back_to_grounded_template_when_hook_missing(self):
        # The real KS-000001 story.json has an EMPTY publishing.hook (this
        # is what production data actually looks like -- confirmed by
        # inspecting the real file rather than assuming). The old fallback
        # ("Something happens to Pip today...") was too generic; the new
        # one must be grounded in the story's own moral/theme and mention
        # the real character name.
        story = _load_real_story()
        self.assertEqual(story.publishing.hook, "")

        hook = build_hook(story)

        self.assertIn("Pip", hook)
        self.assertNotIn("Something happens to", hook)

    def test_hook_is_short(self):
        story = _load_real_story()
        hook = build_hook(story)
        self.assertLessEqual(len(hook.split()), 10)
        self.assertGreaterEqual(len(hook.split()), 4)

    def test_hook_grounded_in_moral_does_not_invent_unrelated_events(self):
        story = _load_real_story()
        story.publishing.hook = ""
        story.story_info.moral = "It is good to be brave even when you feel scared."
        story.story_info.theme = "Courage"

        hook = build_hook(story)

        self.assertIn("brave", hook.lower())


class BuildReelScriptTests(unittest.TestCase):

    def test_segments_align_with_beat_indices(self):
        story = _load_real_story()
        beat_indices = select_beat_indices(len(story.slides))

        script = build_reel_script(story, beat_indices)

        beat_segments = [s for s in script["segments"] if s["kind"] == "beat"]
        self.assertEqual([s["slide_index"] for s in beat_segments], beat_indices)

        for segment in beat_segments:
            self.assertIn(segment["text"], story.slides[segment["slide_index"]].text)

    def test_first_and_last_segment_are_cover(self):
        story = _load_real_story()
        beat_indices = select_beat_indices(len(story.slides))
        script = build_reel_script(story, beat_indices)

        self.assertEqual(script["segments"][0]["kind"], "cover")
        self.assertEqual(script["segments"][-1]["kind"], "cover")

    def test_narration_word_count_within_target(self):
        story = _load_real_story()
        beat_indices = select_beat_indices(len(story.slides))
        script = build_reel_script(story, beat_indices)

        word_count = len(script["full_narration"].split())
        self.assertLessEqual(word_count, 90)
        self.assertGreaterEqual(word_count, 40)

    def test_duration_target_within_bounds(self):
        story = _load_real_story()
        beat_indices = select_beat_indices(len(story.slides))
        script = build_reel_script(story, beat_indices)

        self.assertGreaterEqual(script["duration_target"], MIN_DURATION_SECONDS)
        self.assertLessEqual(script["duration_target"], MAX_DURATION_SECONDS)

    def test_beat_excerpts_never_contain_ellipsis(self):
        # Beat narration is sentence-trimmed (trim_to_sentences), never
        # word-chopped with a trailing "...".
        story = _load_real_story()
        beat_indices = select_beat_indices(len(story.slides))
        script = build_reel_script(story, beat_indices)

        for beat_text in script["beats"]:
            self.assertNotIn("...", beat_text)

    def test_cta_is_short_and_uses_handle(self):
        story = _load_real_story()
        beat_indices = select_beat_indices(len(story.slides))
        script = build_reel_script(story, beat_indices, instagram_handle="@bedtime01fables")

        self.assertIn("@bedtime01fables", script["cta"])
        self.assertLessEqual(len(script["cta"].split()), 10)


class CoverCropTests(unittest.TestCase):

    def test_crop_to_fill_produces_exact_target_size_no_distortion(self):
        # A 4:5 portrait source (matches real cover_final.png/cover.png
        # dimensions) must crop-fill to exactly 1080x1920 -- full-bleed,
        # no padding bars.
        source = Image.new("RGB", (1080, 1350), "red")
        result = crop_to_fill(source, TARGET_WIDTH, TARGET_HEIGHT)
        self.assertEqual(result.size, (TARGET_WIDTH, TARGET_HEIGHT))

    def test_crop_to_fill_handles_landscape_source_too(self):
        source = Image.new("RGB", (1600, 900), "blue")
        result = crop_to_fill(source, TARGET_WIDTH, TARGET_HEIGHT)
        self.assertEqual(result.size, (TARGET_WIDTH, TARGET_HEIGHT))

    def test_prepare_full_bleed_image_against_real_cover_art(self):
        # End-to-end against the ACTUAL cover.png shipped for KS-000001,
        # not a synthetic stand-in.
        import tempfile

        hero = select_reel_hero_image(REAL_STORY_FOLDER, REAL_STORY_FOLDER / "cover_final.png")
        self.assertEqual(hero.name, "cover.png")

        with tempfile.TemporaryDirectory() as tmp:
            out_path = Path(tmp) / "scene.png"
            prepare_full_bleed_image(hero, out_path)

            with Image.open(out_path) as result:
                self.assertEqual(result.size, (TARGET_WIDTH, TARGET_HEIGHT))

    def test_select_reel_hero_image_falls_back_when_raw_cover_missing(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            final_cover = folder / "cover_final.png"
            Image.new("RGB", (1080, 1350), "green").save(final_cover)

            hero = select_reel_hero_image(folder, final_cover)
            self.assertEqual(hero, final_cover)


class ComputeSceneDurationsTests(unittest.TestCase):

    def test_sums_to_total_duration(self):
        durations = compute_scene_durations([6, 20, 20, 20, 12], 26)
        self.assertAlmostEqual(sum(durations), 26, places=1)

    def test_more_words_gets_more_time(self):
        durations = compute_scene_durations([5, 30], 20)
        self.assertGreater(durations[1], durations[0])

    def test_empty_word_counts_returns_empty(self):
        self.assertEqual(compute_scene_durations([], 25), [])

    def test_zero_word_scene_still_gets_a_floor(self):
        durations = compute_scene_durations([0, 20], 20, min_scene_seconds=1.6)
        self.assertGreaterEqual(durations[0], 1.0)


class CaptionChunkTests(unittest.TestCase):

    def setUp(self):
        self.font = ImageFont.truetype(str(REAL_FONT_PATH), CAPTION_FONT_SIZE)

    def _dummy_draw(self):
        from PIL import ImageDraw
        return ImageDraw.Draw(Image.new("RGB", (1, 1)))

    def test_no_line_exceeds_safe_width(self):
        text = (
            "Pip had a good idea. 'Don't worry, Lily,' he said with a "
            "smile. 'We can look for it together! Two friends are better "
            "than one.'"
        )
        chunks = build_caption_chunks(text, self.font)
        draw = self._dummy_draw()

        for chunk in chunks:
            self.assertLessEqual(len(chunk["lines"]), CAPTION_MAX_LINES)
            for line in chunk["lines"]:
                width = draw.textbbox((0, 0), line, font=self.font)[2]
                self.assertLessEqual(width, CAPTION_SAFE_WIDTH_PX)

    def test_all_words_preserved_across_chunks_no_truncation(self):
        text = "Follow @bedtime01fables for another little story about kindness and sharing today"
        chunks = build_caption_chunks(text, self.font)

        rebuilt = " ".join(word for chunk in chunks for word in chunk["words"])
        self.assertEqual(rebuilt, text)

        for chunk in chunks:
            for line in chunk["lines"]:
                self.assertNotIn("...", line)

    def test_short_text_produces_single_chunk(self):
        chunks = build_caption_chunks("Can Pip help a friend today?", self.font)
        self.assertEqual(len(chunks), 1)
        self.assertLessEqual(len(chunks[0]["lines"]), CAPTION_MAX_LINES)

    def test_empty_text_produces_no_chunks(self):
        self.assertEqual(build_caption_chunks("", self.font), [])


class BuildCaptionCuesTests(unittest.TestCase):

    def test_cues_stay_within_their_own_scene_window(self):
        # Transitions must land on scene (story-beat) boundaries: no cue
        # may start before, or end after, the scene it belongs to.
        segments = [
            {"kind": "cover", "text": "Can Pip help a friend today?"},
            {"kind": "beat", "slide_index": 0, "text": "Pip saw a friend who felt sad and alone."},
            {"kind": "cover", "text": "Being kind makes everyone happy. Follow @bedtime01fables for another little story."},
        ]
        scene_durations = [3.0, 8.0, 6.0]

        cues = build_caption_cues(segments, scene_durations, REAL_FONT_PATH)

        boundaries = [0.0]
        for d in scene_durations:
            boundaries.append(boundaries[-1] + d)

        cue_index = 0
        for scene_i, (start, end) in enumerate(zip(boundaries, boundaries[1:])):
            words_in_scene = len(segments[scene_i]["text"].split())
            if words_in_scene == 0:
                continue
            # Every cue whose start falls within [start, end) belongs to
            # this scene and must not run past its end (small rounding
            # tolerance for float division).
            while cue_index < len(cues) and cues[cue_index]["start"] < end - 0.001:
                self.assertGreaterEqual(cues[cue_index]["start"], start - 0.01)
                self.assertLessEqual(cues[cue_index]["end"], end + 0.01)
                cue_index += 1

    def test_mismatched_lengths_raise(self):
        with self.assertRaises(ValueError):
            build_caption_cues(
                [{"kind": "cover", "text": "hi"}], [1.0, 2.0], REAL_FONT_PATH
            )

    def test_no_cue_line_exceeds_safe_width(self):
        from PIL import ImageDraw
        font = ImageFont.truetype(str(REAL_FONT_PATH), CAPTION_FONT_SIZE)
        draw = ImageDraw.Draw(Image.new("RGB", (1, 1)))

        segments = [
            {"kind": "beat", "slide_index": 0, "text": (
                "Pip had a good idea. 'Don't worry, Lily,' he said with a "
                "smile. 'We can look for it together!"
            )},
        ]
        cues = build_caption_cues(segments, [8.0], REAL_FONT_PATH)

        for cue in cues:
            self.assertLessEqual(len(cue["lines"]), CAPTION_MAX_LINES)
            for line in cue["lines"]:
                width = draw.textbbox((0, 0), line, font=font)[2]
                self.assertLessEqual(width, CAPTION_SAFE_WIDTH_PX)


if __name__ == "__main__":
    unittest.main()
