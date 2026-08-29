"""V5.1 regression tests: caption positioning and caption length.

Root cause (found by actually rendering a real ffmpeg frame and
measuring its pixels -- see the module comment above the "Captions"
section in services/reel_service.py): multi-line caption cues used to be
joined with the literal two-character escape sequence "\\n" (backslash +
n) before being handed to ffmpeg's drawtext `text=` option. ffmpeg does
NOT treat that as a line break -- it drops the backslash and leaves a
bare "n" glued onto the surrounding words, collapsing two short wrapped
lines into one much longer line that, once centered via `text_w`,
overflowed the 1080px frame on both sides. That is what produced the
exact production symptom ("squirrel played near a big tree..." with the
leading "The" missing). Crucially, the OLD mocked-ffmpeg mocked test
suite never caught this, because no test rendered real 2-line captions
through real ffmpeg and inspected the actual pixels -- it only checked
that PIL's own wrapping fit within a pixel budget, which was never the
broken part.

This file therefore leans hard on REAL ffmpeg renders + PIL pixel
inspection, not just the pure-Python wrapping logic (see
tests/test_reel_scene_and_script.py for that). It also covers the
secondary V5.1 requirement: captions must read as short on-screen
phrases (2-7 words, prefer 3-6, max 2 lines, strong 1-line preference),
not full narration sentences -- all derived from the existing narration
text, zero additional API calls.
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
    build_final_assembly_command,
    render_reel_video,
    CAPTION_FONT_SIZE,
    CAPTION_MAX_LINES,
    CAPTION_MAX_WORDS_PER_CHUNK,
    CAPTION_SAFE_WIDTH_PX,
    TARGET_HEIGHT,
    TARGET_WIDTH,
)

FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None
FONT_PATH = Path("assets/fonts/Poppins-Bold.ttf").resolve()

# Deliberately difficult caption source texts (section 9 of the V5.1
# spec): long words, punctuation, apostrophes, a sentence long enough to
# require wrapping/splitting, and text that starts with the exact words
# that were observed clipped in the real production Reel.
TRICKY_TEXTS = [
    "The squirrel played near a big tree, watching the leaves fall down slowly.",
    "He saw his friend Lily looking worried and a little bit sad about everything.",
    "\"That's a wonderful, extraordinarily good idea,\" said Don't-worry Lily, smiling brightly.",
    "Together, they found the berries and shared them happily with their woodland friends.",
]


def _make_silent_mp3(path: Path, duration=2):

    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
         "-t", str(duration), "-q:a", "9", str(path)],
        capture_output=True,
    )


# =====================================================================
# Pure-Python: caption phrases are short and sentence-bounded.
# =====================================================================

class ShortCaptionPhraseTests(unittest.TestCase):

    def _font(self):
        return ImageFont.truetype(str(FONT_PATH), CAPTION_FONT_SIZE)

    def test_short_sentence_becomes_one_clean_chunk(self):
        """A sentence already inside the word budget must come through
        unchanged as a single chunk -- e.g. the project's own test-fixture
        sentence "Pip wanted to help." (see tests/test_reel_service_
        generate.py) should render as exactly that phrase, not be split
        or padded."""

        chunks = build_caption_chunks_for_text("Pip wanted to help.", self._font())

        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0]["words"], ["Pip", "wanted", "to", "help."])

    def test_chunks_never_exceed_the_max_word_cap(self):

        for text in TRICKY_TEXTS:
            for chunk in build_caption_chunks_for_text(text, self._font()):
                self.assertLessEqual(
                    len(chunk["words"]), CAPTION_MAX_WORDS_PER_CHUNK,
                    f"chunk {chunk['words']!r} from {text!r} exceeds the word cap",
                )

    def test_chunks_prefer_two_to_seven_words(self):
        """Target: 2-7 words per caption phrase (prefer 3-6). A trailing
        sentence-final remainder can legitimately be shorter (e.g. a
        single word), so this checks the upper bound strictly and the
        lower bound as a soft distribution check across many chunks."""

        all_chunks = [
            chunk
            for text in TRICKY_TEXTS
            for chunk in build_caption_chunks_for_text(text, self._font())
        ]

        for chunk in all_chunks:
            self.assertLessEqual(len(chunk["words"]), 7)

        short_chunks = [c for c in all_chunks if 2 <= len(c["words"]) <= 7]
        self.assertGreater(len(short_chunks) / len(all_chunks), 0.7)

    def test_chunks_never_span_a_sentence_boundary(self):
        """A single caption chunk must not run two sentences together --
        only the LAST word of a chunk may carry sentence-ending
        punctuation."""

        text = "Pip saw Lily. She looked worried. Pip wanted to help."
        chunks = build_caption_chunks_for_text(text, self._font())

        for chunk in chunks:
            words = chunk["words"]
            for word in words[:-1]:
                self.assertFalse(
                    re.search(r"[.!?]$", word),
                    f"chunk {words!r} runs past a sentence boundary mid-chunk",
                )

    def test_prefers_one_line_when_it_fits(self):

        chunks = build_caption_chunks_for_text(
            "Pip saw his worried friend.", self._font()
        )
        self.assertEqual(len(chunks), 1)
        self.assertEqual(len(chunks[0]["lines"]), 1)

    def test_never_exceeds_two_lines(self):

        for text in TRICKY_TEXTS:
            for chunk in build_caption_chunks_for_text(text, self._font()):
                self.assertLessEqual(len(chunk["lines"]), CAPTION_MAX_LINES)

    def test_does_not_invent_words_not_in_the_source_text(self):
        """Captions must be a re-chunking of the real narration text, not
        a rewrite -- every word in every chunk must have come from the
        original text (case/punctuation aside)."""

        text = "Together, they found the berries and shared them happily."
        source_words = set(w.strip(".,!?\"'").lower() for w in text.split())

        for chunk in build_caption_chunks_for_text(text, self._font()):
            for word in chunk["words"]:
                cleaned = word.strip(".,!?\"'").lower()
                self.assertIn(cleaned, source_words)


# =====================================================================
# Regression guard: the exact escaping bug must never come back.
# =====================================================================

class DrawtextNewlineRegressionTests(unittest.TestCase):

    def test_multiline_cue_is_joined_with_a_real_newline_byte(self):
        """Direct regression guard for the V5.1 root cause: the filter
        string handed to ffmpeg must contain an actual newline byte
        between wrapped lines, never the literal two-character "\\n"
        escape sequence (which ffmpeg's drawtext does not treat as a
        line break -- see the module docstring)."""

        caption_cues = [{
            "lines": ["The squirrel played", "near a big tree"],
            "text": "The squirrel played near a big tree",
            "start": 0.0,
            "end": 3.0,
        }]

        command = build_final_assembly_command(
            concatenated_video_path=Path("concat.mp4"),
            narration_path=Path("narration.mp3"),
            output_path=Path("out.mp4"),
            caption_cues=caption_cues,
            font_path=FONT_PATH,
            total_duration=3.0,
        )

        filter_complex = command[command.index("-filter_complex") + 1]

        self.assertIn("The squirrel played\nnear a big tree", filter_complex)
        self.assertNotIn("The squirrel played\\nnear a big tree", filter_complex)


# =====================================================================
# Real ffmpeg render + pixel inspection -- the actual acceptance test.
# =====================================================================

@unittest.skipUnless(FFMPEG_AVAILABLE, "ffmpeg not available on PATH")
class RealFfmpegCaptionRenderingTests(unittest.TestCase):

    # Hard floor: a caption must never come anywhere near the frame
    # edge. The spec's 80-100px target is the design goal; this is the
    # much looser "definitely not clipped" regression floor.
    MIN_SAFE_MARGIN_PX = 40

    def _sample_frame(self, video_path: Path, t: float, out_dir: Path):

        frame_path = out_dir / f"frame_{t}.png"
        subprocess.run(
            ["ffmpeg", "-y", "-ss", str(t), "-i", str(video_path),
             "-frames:v", "1", "-update", "1", str(frame_path)],
            capture_output=True,
        )
        return Image.open(frame_path).convert("RGB")

    def _caption_bbox(self, img: Image.Image):
        """Bounding box of everything that isn't the flat background
        colour, restricted to the lower half of the frame where captions
        live -- i.e. the visible painted extent of the caption box+text."""

        w, h = img.size
        px = img.load()
        bg = px[5, 5]

        def differs(p):
            return sum(abs(a - b) for a, b in zip(p, bg)) > 60

        minx, maxx, miny, maxy = w, -1, h, -1

        for y in range(h // 2, h):
            for x in range(0, w):
                if differs(px[x, y]):
                    minx = min(minx, x)
                    maxx = max(maxx, x)
                    miny = min(miny, y)
                    maxy = max(maxy, y)

        return (minx, maxx, miny, maxy) if maxx >= 0 else None

    def test_tricky_captions_never_touch_the_frame_edges(self):
        """The actual production-observed bug: renders every deliberately
        difficult caption text (long words, punctuation, apostrophes, a
        sentence requiring wrapping, and text starting with the exact
        words previously seen clipped) through the real pipeline and
        checks every rendered caption's visible pixels stay well inside
        the frame -- never touching or crossing x=0 / x=TARGET_WIDTH."""

        with TemporaryDirectory() as tmp:

            folder = Path(tmp)

            segments = [{"kind": "beat", "text": t} for t in TRICKY_TEXTS]
            scene_durations = [8.0] * len(TRICKY_TEXTS)
            total_duration = sum(scene_durations)

            cues = build_caption_cues(segments, scene_durations, FONT_PATH)
            self.assertTrue(cues, "expected at least one caption cue")

            colors = [(200, 80, 80), (80, 200, 80), (80, 80, 200), (200, 200, 80)]
            scene_images = []
            for i, color in enumerate(colors):
                path = folder / f"scene{i}.png"
                Image.new("RGB", (TARGET_WIDTH, TARGET_HEIGHT), color).save(path)
                scene_images.append(path)

            narration = folder / "narration.mp3"
            _make_silent_mp3(narration, duration=int(total_duration) + 1)

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

            failures = []

            for cue in cues:

                t = round((cue["start"] + cue["end"]) / 2, 2)
                img = self._sample_frame(output_path, t, folder)
                bbox = self._caption_bbox(img)

                self.assertIsNotNone(bbox, f"no caption pixels found at t={t} for {cue['lines']!r}")

                minx, maxx, miny, maxy = bbox

                if (
                    minx < self.MIN_SAFE_MARGIN_PX
                    or maxx > TARGET_WIDTH - self.MIN_SAFE_MARGIN_PX
                    or maxy >= TARGET_HEIGHT
                ):
                    failures.append((t, cue["lines"], bbox))

            self.assertEqual(
                failures, [],
                f"caption(s) rendered too close to or past a frame edge: {failures}",
            )

    def test_exact_reported_production_symptom_is_fixed(self):
        """Recreates the exact reported real-Reel symptom: a caption
        whose FULL text begins with "The squirrel played near a big
        tree..." previously rendered with "The" missing from the visible
        frame (clipped off the left edge). This asserts the leading word
        is not clipped: the caption's visible box starts comfortably
        inside the frame, not at/near x=0."""

        with TemporaryDirectory() as tmp:

            folder = Path(tmp)

            text = "The squirrel played near a big tree, watching the leaves fall down slowly."
            segments = [{"kind": "beat", "text": text}]
            scene_durations = [8.0]

            cues = build_caption_cues(segments, scene_durations, FONT_PATH)
            first_cue = cues[0]
            self.assertEqual(first_cue["lines"][0].split()[0], "The")

            scene_images = [folder / "scene0.png"]
            Image.new("RGB", (TARGET_WIDTH, TARGET_HEIGHT), (60, 120, 60)).save(scene_images[0])

            narration = folder / "narration.mp3"
            _make_silent_mp3(narration, duration=9)

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

            mid = round((first_cue["start"] + first_cue["end"]) / 2, 2)
            img = self._sample_frame(output_path, mid, folder)
            bbox = self._caption_bbox(img)

            self.assertIsNotNone(bbox)
            minx, maxx, miny, maxy = bbox

            self.assertGreaterEqual(
                minx, self.MIN_SAFE_MARGIN_PX,
                f"caption box starts at x={minx} -- 'The' is at risk of being clipped again",
            )
            self.assertLessEqual(maxx, TARGET_WIDTH - self.MIN_SAFE_MARGIN_PX)

    def test_captions_stay_in_lower_portion_and_never_extend_below_frame(self):

        with TemporaryDirectory() as tmp:

            folder = Path(tmp)

            segments = [{"kind": "beat", "text": t} for t in TRICKY_TEXTS]
            scene_durations = [6.0] * len(TRICKY_TEXTS)
            total_duration = sum(scene_durations)

            cues = build_caption_cues(segments, scene_durations, FONT_PATH)

            scene_images = [folder / "scene.png"]
            Image.new("RGB", (TARGET_WIDTH, TARGET_HEIGHT), (100, 100, 180)).save(scene_images[0])
            scene_images = scene_images * len(TRICKY_TEXTS)

            narration = folder / "narration.mp3"
            _make_silent_mp3(narration, duration=int(total_duration) + 1)

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

            for cue in cues:
                t = round((cue["start"] + cue["end"]) / 2, 2)
                img = self._sample_frame(output_path, t, folder)
                bbox = self._caption_bbox(img)
                self.assertIsNotNone(bbox)
                _, _, miny, maxy = bbox

                # Lower half of the frame, and comfortably clear of the
                # very bottom (Instagram UI chrome / frame edge).
                self.assertGreater(miny, TARGET_HEIGHT * 0.5)
                self.assertLess(maxy, TARGET_HEIGHT - 100)


# =====================================================================
# Caption timing invariants (still synchronized with narration after
# re-chunking into more, shorter cues).
# =====================================================================

class CaptionTimingTests(unittest.TestCase):

    def test_first_cue_starts_at_or_near_zero(self):

        segments = [{"kind": "beat", "text": t} for t in TRICKY_TEXTS]
        scene_durations = [6.0] * len(TRICKY_TEXTS)

        cues = build_caption_cues(segments, scene_durations, FONT_PATH)

        self.assertAlmostEqual(cues[0]["start"], 0.0, delta=0.01)

    def test_no_overlapping_or_gapped_cues_within_a_segment(self):

        segments = [{"kind": "beat", "text": TRICKY_TEXTS[0]}]
        scene_durations = [8.0]

        cues = build_caption_cues(segments, scene_durations, FONT_PATH)

        for prev, nxt in zip(cues, cues[1:]):
            self.assertAlmostEqual(prev["end"], nxt["start"], delta=0.01)

    def test_final_cue_ends_at_or_before_total_duration(self):

        segments = [{"kind": "beat", "text": t} for t in TRICKY_TEXTS]
        scene_durations = [6.0] * len(TRICKY_TEXTS)
        total_duration = sum(scene_durations)

        cues = build_caption_cues(segments, scene_durations, FONT_PATH)

        self.assertLessEqual(cues[-1]["end"], total_duration + 0.01)

    def test_more_cues_than_before_but_same_total_word_coverage(self):
        """Re-chunking into shorter phrases must still cover every word
        of the narration exactly once -- nothing dropped, nothing
        duplicated."""

        text = TRICKY_TEXTS[0]
        segments = [{"kind": "beat", "text": text}]
        scene_durations = [8.0]

        cues = build_caption_cues(segments, scene_durations, FONT_PATH)

        covered_words = [w for cue in cues for w in cue["text"].split()]
        self.assertEqual(covered_words, text.split())


if __name__ == "__main__":
    unittest.main()
