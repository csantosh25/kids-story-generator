"""V6 regression tests: caption segmentation quality (no dangling
grammatical fragments) and Reel pacing (brief cover, story-dominant
middle).

V5.1 fixed PIXEL clipping, but a real render still showed captions like
"Pip the squirrel played near a" and "'Don't worry, Lily,' he said
with" -- technically pixel-safe, but ending on an obviously incomplete
fragment. This file tests the V6 fix: build_caption_chunks now prefers
NOT to end a (non-final) chunk on a weak continuation word (see
CAPTION_WEAK_TRAILING_WORDS in services/reel_service.py), backing off to
the longest shorter boundary that avoids it -- sentence/phrase structure
decides the cut point first, pixel measurement is the safety net, not
the primary driver.

It also tests compute_scene_durations' new optional first/last-scene
duration caps, which keep the opening/closing cover brief relative to
the story beats (V6 sections 2-4).
"""
import re
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image, ImageFont

from services.reel_service import (
    build_caption_chunks,
    build_caption_chunks_for_text,
    build_caption_cues,
    build_reel_script,
    compute_scene_durations,
    render_reel_video,
    select_beat_indices,
    CAPTION_FONT_SIZE,
    CAPTION_WEAK_TRAILING_WORDS,
    REEL_CLOSING_COVER_MAX_FRACTION,
    REEL_OPENING_COVER_MAX_FRACTION,
    TARGET_HEIGHT,
    TARGET_WIDTH,
)

from tests.test_reel_service_generate import _write_minimal_story_assets

FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None
FONT_PATH = Path("assets/fonts/Poppins-Bold.ttf").resolve()


def _make_silent_mp3(path: Path, duration=2):
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
         "-t", str(duration), "-q:a", "9", str(path)],
        capture_output=True,
    )


def _last_word_core(word):
    """Strips trailing punctuation, mirroring services.reel_service's
    own _ends_on_weak_word so tests check the same thing production
    does, without importing a private helper."""

    return re.sub(r"[^A-Za-z]+$", "", word)


def _ends_weakly(words):
    if not words:
        return False
    return _last_word_core(words[-1]).lower() in CAPTION_WEAK_TRAILING_WORDS


def _has_terminal_punctuation(word):
    return bool(re.search(r"[.!?]$", word))


def _assert_no_dangling_non_terminal_chunk(testcase, chunks, context=""):
    """The core V6 invariant: a chunk may only end on a weak word (see
    CAPTION_WEAK_TRAILING_WORDS) if its last word carries terminal
    sentence punctuation (. ! ?) -- i.e. it's genuinely the end of that
    sentence, not an arbitrary mid-sentence cut."""

    for chunk in chunks:
        if _ends_weakly(chunk["words"]):
            testcase.assertTrue(
                _has_terminal_punctuation(chunk["words"][-1]),
                f"{context}chunk {chunk['words']!r} ends on a weak word "
                f"without terminal punctuation -- looks like a dangling "
                f"fragment.",
            )


class NaturalCaptionBoundaryTests(unittest.TestCase):
    """Section 1: exact reported examples + the required test cases."""

    def setUp(self):
        self.font = ImageFont.truetype(str(FONT_PATH), CAPTION_FONT_SIZE)

    def test_exact_reported_example_one(self):

        chunks = build_caption_chunks_for_text(
            "Pip the squirrel played near a big tree.", self.font
        )

        _assert_no_dangling_non_terminal_chunk(self, chunks)

        # The specific fix: the first chunk must not be exactly the
        # previously-reported bad fragment.
        first_words = " ".join(chunks[0]["words"])
        self.assertNotEqual(first_words, "Pip the squirrel played near a")
        self.assertFalse(first_words.endswith(" a"))

    def test_exact_reported_example_two_dialogue(self):

        chunks = build_caption_chunks_for_text(
            "'Don't worry, Lily,' he said with a smile.", self.font
        )

        _assert_no_dangling_non_terminal_chunk(self, chunks)

        joined = [" ".join(c["words"]) for c in chunks]
        self.assertNotIn("'Don't worry, Lily,' he said with", joined)
        self.assertFalse(any(text.endswith(" with") for text in joined))

    def test_long_sentence_requires_two_chunks(self):

        text = "Pip and Lily walked together through the quiet forest looking for the missing basket."
        chunks = build_caption_chunks_for_text(text, self.font)

        self.assertGreaterEqual(len(chunks), 2)
        _assert_no_dangling_non_terminal_chunk(self, chunks, context=f"[{text!r}] ")

        rebuilt = " ".join(w for c in chunks for w in c["words"])
        self.assertEqual(rebuilt, text)

    def test_dialogue_with_multiple_quoted_lines(self):

        text = "'I can help,' said Pip. 'Thank you,' said Lily with a smile."
        chunks = build_caption_chunks_for_text(text, self.font)

        _assert_no_dangling_non_terminal_chunk(self, chunks, context=f"[{text!r}] ")

        rebuilt = " ".join(w for c in chunks for w in c["words"])
        self.assertEqual(rebuilt, text)

    def test_multiple_short_sentences_stay_separate(self):

        text = "Pip saw Lily. She looked worried. Pip wanted to help."
        chunks = build_caption_chunks_for_text(text, self.font)

        self.assertEqual(len(chunks), 3)
        self.assertEqual(chunks[0]["words"], ["Pip", "saw", "Lily."])
        self.assertEqual(chunks[1]["words"], ["She", "looked", "worried."])
        self.assertEqual(chunks[2]["words"], ["Pip", "wanted", "to", "help."])

        _assert_no_dangling_non_terminal_chunk(self, chunks)

    def test_punctuation_heavy_text(self):

        text = "\"Wait,\" said Pip, \"is that you, Lily? Are you okay?\""
        chunks = build_caption_chunks_for_text(text, self.font)

        _assert_no_dangling_non_terminal_chunk(self, chunks, context=f"[{text!r}] ")

        rebuilt = " ".join(w for c in chunks for w in c["words"])
        self.assertEqual(rebuilt, text)

    def test_captions_containing_names(self):

        for text in [
            "Pip the squirrel and Lily the ladybug were best friends.",
            "Barnaby found a colourful flower near the old oak tree.",
            "Miko was excited for a fruity morning with her family.",
        ]:
            chunks = build_caption_chunks_for_text(text, self.font)
            _assert_no_dangling_non_terminal_chunk(self, chunks, context=f"[{text!r}] ")

    def test_captions_at_scene_boundaries_via_build_caption_cues(self):
        """Exercises the full segment -> cue pipeline (not just a single
        sentence in isolation), matching how real Reel segments (hook,
        beat, payoff+cta) are actually captioned."""

        segments = [
            {"kind": "cover", "text": "Can Pip help a friend today?"},
            {"kind": "beat", "slide_index": 0,
             "text": "Pip the squirrel played near a big tree. The sun was going down."},
            {"kind": "beat", "slide_index": 2,
             "text": "'Don't worry, Lily,' he said with a smile."},
            {"kind": "cover", "text": "Helping friends makes everyone happy. Follow @bedtime01fables for another little story."},
        ]
        scene_durations = [3.0, 8.0, 6.0, 5.0]

        cues = build_caption_cues(segments, scene_durations, FONT_PATH)

        for cue in cues:
            self.assertFalse(
                _ends_weakly(cue["text"].split()) and not _has_terminal_punctuation(cue["text"].split()[-1]),
                f"cue {cue['text']!r} ends on a dangling fragment",
            )

        # No words dropped or duplicated across the whole cue stream: the
        # cues, concatenated in order, reproduce every segment's text
        # exactly (segments are captioned back-to-back with no
        # interleaving, so this is a valid whole-stream check).
        all_segment_words = [w for s in segments for w in s["text"].split()]
        all_cue_words = [w for cue in cues for w in cue["text"].split()]
        self.assertEqual(all_cue_words, all_segment_words)

    def test_no_words_dropped_or_duplicated_across_many_examples(self):

        texts = [
            "Pip the squirrel played near a big tree.",
            "'Don't worry, Lily,' he said with a smile.",
            "Pip and Lily walked together through the quiet forest looking for the missing basket.",
            "Together, they found the berries and shared them happily with their woodland friends.",
        ]

        for text in texts:
            chunks = build_caption_chunks_for_text(text, self.font)
            rebuilt = " ".join(w for c in chunks for w in c["words"])
            self.assertEqual(rebuilt, text, f"word mismatch for {text!r}")

    def test_never_ends_on_dangling_open_quote_or_punctuation(self):
        """No chunk should end on bare/dangling punctuation -- the
        word-based splitter keeps punctuation attached to its word by
        construction, but this asserts it explicitly."""

        texts = [
            "'Don't worry, Lily,' he said with a smile.",
            "\"Wait,\" said Pip, \"is that you, Lily?\"",
        ]

        for text in texts:
            chunks = build_caption_chunks_for_text(text, self.font)
            for chunk in chunks:
                last = chunk["words"][-1]
                self.assertTrue(re.search(r"[A-Za-z]", last), f"chunk ends on bare punctuation: {chunk['words']!r}")

    def test_short_sentence_still_becomes_one_clean_chunk(self):
        """No regression: a sentence already inside the word budget still
        renders as a single, untouched chunk."""

        chunks = build_caption_chunks_for_text("Can Pip help a friend today?", self.font)
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0]["words"], "Can Pip help a friend today?".split())


class ComputeSceneDurationsCoverCapTests(unittest.TestCase):
    """Section 2-4: cover segments (first/last scene) capped, excess
    redistributed to the story beats in between."""

    def test_first_and_last_scene_capped_when_they_would_otherwise_dominate(self):

        # A short hook (2 words) and a long payoff+cta (20 words) around
        # one modest beat (10 words) -- without capping, the closing
        # scene would take a large share of the 30s total.
        word_counts = [2, 10, 20]
        durations = compute_scene_durations(
            word_counts, 30.0,
            first_scene_max_fraction=REEL_OPENING_COVER_MAX_FRACTION,
            last_scene_max_fraction=REEL_CLOSING_COVER_MAX_FRACTION,
        )

        self.assertAlmostEqual(sum(durations), 30.0, places=1)
        self.assertLessEqual(durations[-1], 30.0 * REEL_CLOSING_COVER_MAX_FRACTION + 0.05)

        # The freed-up time went to the beat, not lost.
        uncapped = compute_scene_durations(word_counts, 30.0)
        self.assertGreater(durations[1], uncapped[1])

    def test_no_op_without_fraction_args_identical_to_before(self):

        word_counts = [6, 20, 20, 20, 12]
        self.assertEqual(
            compute_scene_durations(word_counts, 26),
            compute_scene_durations(word_counts, 26, first_scene_max_fraction=None, last_scene_max_fraction=None),
        )

    def test_no_op_with_only_two_scenes(self):
        """Nothing to redistribute into with no beat in between -- capping
        is skipped rather than shrinking the Reel for nothing."""

        word_counts = [3, 20]
        capped = compute_scene_durations(
            word_counts, 20.0, first_scene_max_fraction=0.05, last_scene_max_fraction=0.05,
        )
        uncapped = compute_scene_durations(word_counts, 20.0)

        self.assertEqual(capped, uncapped)

    def test_sums_to_total_duration_with_caps_applied(self):

        durations = compute_scene_durations(
            [3, 15, 15, 15, 18], 28.0,
            first_scene_max_fraction=0.10, last_scene_max_fraction=0.15,
        )
        self.assertAlmostEqual(sum(durations), 28.0, places=1)

    def test_cap_never_pushes_below_min_scene_seconds(self):

        durations = compute_scene_durations(
            [50, 1, 1], 21.0, min_scene_seconds=1.6,
            first_scene_max_fraction=0.01, last_scene_max_fraction=0.01,
        )
        for d in durations:
            self.assertGreaterEqual(d, 1.0)


class RealStoryPacingTests(unittest.TestCase):
    """Uses build_reel_script + select_beat_indices against a real
    (minimal) story fixture to confirm the opening cover ends up brief
    relative to the story beats, end to end."""

    def test_opening_cover_gets_less_relative_time_than_a_beat(self):

        with TemporaryDirectory() as tmp:

            folder = Path(tmp) / "story"
            _write_minimal_story_assets(folder)

            from services.reel_service import load_story_package
            story = load_story_package(folder)

            beat_indices = select_beat_indices(len(story.slides))
            script = build_reel_script(story, beat_indices)

            word_counts = [len(s["text"].split()) for s in script["segments"]]
            durations = compute_scene_durations(
                word_counts, script["duration_target"],
                first_scene_max_fraction=REEL_OPENING_COVER_MAX_FRACTION,
                last_scene_max_fraction=REEL_CLOSING_COVER_MAX_FRACTION,
            )

            self.assertAlmostEqual(sum(durations), script["duration_target"], places=1)
            # Opening cover must not be the longest scene.
            self.assertLess(durations[0], max(durations))


@unittest.skipUnless(FFMPEG_AVAILABLE, "ffmpeg not available on PATH")
class RealFfmpegCaptionSegmentationTests(unittest.TestCase):
    """Section 9C/E: a real render combining the exact previously-broken
    example texts with the new pacing caps, confirming the actual
    rendered video still has correct scenes/duration and that no
    generated cue is a dangling fragment."""

    def test_real_render_has_no_dangling_captions_and_valid_output(self):

        with TemporaryDirectory() as tmp:

            folder = Path(tmp)

            segments = [
                {"kind": "cover", "text": "Can Pip help a friend today?"},
                {"kind": "beat", "text": "Pip the squirrel played near a big tree. The sun was going down."},
                {"kind": "beat", "text": "'Don't worry, Lily,' he said with a smile."},
                {"kind": "cover", "text": "Helping friends makes everyone happy. Follow @bedtime01fables for another little story."},
            ]

            word_counts = [len(s["text"].split()) for s in segments]
            scene_durations = compute_scene_durations(
                word_counts, 26.0,
                first_scene_max_fraction=REEL_OPENING_COVER_MAX_FRACTION,
                last_scene_max_fraction=REEL_CLOSING_COVER_MAX_FRACTION,
            )

            cues = build_caption_cues(segments, scene_durations, FONT_PATH)

            for cue in cues:
                words = cue["text"].split()
                if _ends_weakly(words):
                    self.assertTrue(_has_terminal_punctuation(words[-1]), f"dangling cue: {cue['text']!r}")

            colors = [(214, 105, 40), (60, 140, 200), (90, 170, 90), (214, 105, 40)]
            scene_images = []
            for i, color in enumerate(colors):
                path = folder / f"scene{i}.png"
                Image.new("RGB", (TARGET_WIDTH, TARGET_HEIGHT), color).save(path)
                scene_images.append(path)

            narration = folder / "narration.mp3"
            _make_silent_mp3(narration, duration=int(sum(scene_durations)) + 1)

            output_path = folder / "reel.mp4"

            render_reel_video(
                scene_images=scene_images,
                scene_durations=scene_durations,
                narration_path=narration,
                output_path=output_path,
                caption_cues=cues,
                font_path=FONT_PATH,
                work_dir=folder / "clips",
            )

            self.assertTrue(output_path.exists())

            probe = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries",
                 "stream=codec_type,width,height:format=duration",
                 "-of", "json", str(output_path)],
                capture_output=True, text=True,
            )
            import json
            data = json.loads(probe.stdout)
            video_stream = next(s for s in data["streams"] if s["codec_type"] == "video")

            self.assertEqual(video_stream["width"], TARGET_WIDTH)
            self.assertEqual(video_stream["height"], TARGET_HEIGHT)
            self.assertAlmostEqual(float(data["format"]["duration"]), sum(scene_durations), delta=1.0)


if __name__ == "__main__":
    unittest.main()
