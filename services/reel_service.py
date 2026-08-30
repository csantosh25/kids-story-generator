import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from models.story_models import StoryPackage
from services.brand_loader import BrandLoader
from services.content_library_service import ContentLibraryService
from services.openai_tts_service import OpenAITTSService
from services.reel_diagnostics import verify_rendered_video_has_scene_changes
from services.reel_image_service import ReelImageGenerationError, ReelImageService
from utils.text_layout import wrap_text_to_width


# =====================================================================
# FFmpeg availability
# =====================================================================

FFMPEG_BIN = "ffmpeg"


def check_ffmpeg_available():
    """Returns True if the ffmpeg binary is on PATH. Does not run ffmpeg."""

    return shutil.which(FFMPEG_BIN) is not None


class FFmpegNotAvailableError(RuntimeError):
    pass


class MissingStoryAssetsError(RuntimeError):
    pass


class ReelGenerationError(RuntimeError):
    pass


# =====================================================================
# Story / asset discovery (reuses existing output folders -- no new
# generation of story, cover, or carousel images)
# =====================================================================

def load_story_package(folder: Path) -> StoryPackage:

    story_json = folder / "story.json"

    if not story_json.exists():
        raise MissingStoryAssetsError(
            f"story.json not found in {folder} -- this story cannot be used for a Reel."
        )

    data = json.loads(story_json.read_text(encoding="utf-8"))

    return StoryPackage(**data)


def discover_story_images(folder: Path):
    """Returns (cover_path, [slide_paths_in_order]). Raises
    MissingStoryAssetsError listing exactly what's missing, rather than
    failing with an obscure downstream error.

    NOTE: the carousel slide_*.png files themselves are only used here as
    an "is this story fully generated" completeness check. They are NOT
    used as Reel visuals -- see the module docstring above
    select_beat_indices() for why."""

    missing = []

    cover_path = folder / "cover_final.png"

    if not cover_path.exists():
        # Fall back to the raw AI cover if the final composited one is
        # somehow missing, rather than failing outright.
        cover_path = folder / "cover.png"

    if not cover_path.exists():
        missing.append("cover_final.png (or cover.png)")

    slide_paths = sorted(
        folder.glob("slide_*.png"),
        key=lambda p: int("".join(ch for ch in p.stem if ch.isdigit()) or 0),
    )

    if not slide_paths:
        missing.append("slide_*.png (no carousel slide images found)")

    if missing:
        raise MissingStoryAssetsError(
            f"Story folder {folder} is missing required assets: "
            f"{', '.join(missing)}. This story cannot be used for a Reel "
            f"until the daily pipeline has fully generated it."
        )

    return cover_path, slide_paths


def select_reel_hero_image(folder: Path, discovered_cover_path: Path) -> Path:
    """Prefers the raw AI cover art for the Reel's full-bleed hero crop
    over cover_final.png. cover_final.png ADDITIONALLY bakes a title
    overlay into the bottom of the 4:5 image (see CoverDesigner);
    centre-cropping that up to a 9:16 frame can clip the title text
    sideways, whereas the Reel renders its own hook caption instead (see
    build_hook/build_reel_script), so that overlay isn't needed and the
    raw art is strictly safer to crop. Falls back to whatever discover_
    story_images already found if the raw cover isn't on disk.

    KNOWN LIMITATION (V6): the raw cover.png itself can still contain
    AI-hallucinated title/subtitle text baked directly into the
    illustration by the image model -- this is a daily cover-generation
    pipeline concern (CoverDesigner / the image prompt), not something
    the Reel pipeline can edit or crop around without either another AI
    call (out of scope/cost) or risking cropping out the main subject
    (crop_to_fill already uses the full available margin for a clean
    9:16 "cover" fit -- there is no further slack to bias the crop
    without zooming in past that fit). The Reel's only available
    mitigation is presentational: keep the cover on screen only briefly
    (see REEL_OPENING_COVER_MAX_FRACTION / REEL_CLOSING_COVER_MAX_
    FRACTION below) and let the 3 illustrated, text-free beat scenes
    carry most of the Reel's screen time instead."""

    raw_cover = folder / "cover.png"

    if raw_cover.exists():
        return raw_cover

    return discovered_cover_path


# =====================================================================
# Reel script (NO additional AI call -- built deterministically from the
# already-generated StoryPackage data)
# =====================================================================

DEFAULT_HOOK_WORD_LIMIT = 12
DEFAULT_BEAT_WORD_LIMIT = 20
DEFAULT_PAYOFF_WORD_LIMIT = 10

MIN_TARGET_WORDS = 55
MAX_TARGET_WORDS = 90

MIN_DURATION_SECONDS = 20
MAX_DURATION_SECONDS = 30

WORDS_PER_SECOND = 2.5  # ~150 wpm average narration pace

# Keyword -> curiosity-hook template, matched against THIS story's own
# moral/theme (both always-populated StoryInfo fields). Never invents an
# event the story doesn't contain -- it only rephrases the story's own,
# already-established moral as a short question. Order matters: the
# first matching template wins, so more specific themes are listed first.
HOOK_KEYWORD_TEMPLATES = [
    (("help", "helping", "helped"), "Can {name} help a friend today?"),
    (("share", "sharing", "shared"), "Will {name} learn to share today?"),
    (("brave", "courage", "scared", "afraid", "fear"), "Can {name} be brave today?"),
    (("sorry", "forgive", "forgiveness"), "Will {name} say sorry today?"),
    (("listen", "listening"), "Will {name} learn to listen?"),
    (("patient", "patience", "wait", "waiting"), "Can {name} learn to wait?"),
    (("honest", "truth", "honesty"), "Will {name} tell the truth?"),
    (("try", "trying", "give up"), "Will {name} keep trying?"),
    (("kind", "kindness"), "Will {name} be kind today?"),
]

DEFAULT_HOOK_TEMPLATE = "What will {name} discover today?"


def trim_to_words(text, max_words):

    text = (text or "").strip()

    if not text:
        return ""

    words = text.split()

    if len(words) <= max_words:
        return text

    return " ".join(words[:max_words]) + "..."


_SENTENCE_SPLIT_RE = re.compile(r"[^.!?]+[.!?]+|[^.!?]+$")


def trim_to_sentences(text, max_words):
    """Excerpts up to max_words worth of COMPLETE sentences from `text`,
    never cutting a sentence in half and never appending '...'. Used for
    story-beat narration (pulled from full carousel-slide paragraphs) so
    it reads as clean, short, complete statements per the story system's
    own simple-English style, instead of a word-count chop that trails
    off mid-sentence."""

    text = (text or "").strip()

    if not text:
        return ""

    sentences = [s.strip() for s in _SENTENCE_SPLIT_RE.findall(text) if s.strip()]

    if not sentences:
        return trim_to_words(text, max_words)

    chosen = []
    word_count = 0

    for sentence in sentences:

        sentence_words = len(sentence.split())

        if chosen and word_count + sentence_words > max_words:
            break

        chosen.append(sentence)
        word_count += sentence_words

        if word_count >= max_words:
            break

    if not chosen:
        # Even the first sentence alone exceeds max_words -- trim it by
        # words rather than return nothing.
        return trim_to_words(sentences[0], max_words)

    return " ".join(chosen)


def build_hook(story: StoryPackage):
    """Builds a short (5-10 word), curiosity-driven opening line for the
    Reel, deterministically, from THIS story's own data only.

    Prefers an already-authored publishing.hook when one exists and is
    short. Real Content Library stories can leave publishing.hook empty
    (it's an optional/defaulted field), so falls back to a keyword-matched
    template grounded in story_info.moral -- a REQUIRED field that always
    reflects what actually happens in this specific story, unlike a
    generic "Something happens to {name} today..." placeholder."""

    name = story.character_sheet.main_character.name

    authored = (story.publishing.hook or "").strip()

    if authored and len(authored.split()) <= 12:
        return authored

    haystack = f"{story.story_info.moral} {story.story_info.theme}".lower()

    for keywords, template in HOOK_KEYWORD_TEMPLATES:
        if any(keyword in haystack for keyword in keywords):
            return template.format(name=name)

    return DEFAULT_HOOK_TEMPLATE.format(name=name)


def build_reel_script(story: StoryPackage, beat_indices, instagram_handle: str = "@bedtime01fables"):
    """Builds a short discovery/teaser script from an already-generated
    StoryPackage. Makes no API calls -- purely reuses existing story text
    (title, moral, publishing.hook, and the story beats selected by
    select_beat_indices()).

    Returns a "segments" list, ordered exactly as the Reel will play:
    [cover/hook, beat 0, beat 1, ..., cover/payoff+CTA]. This same list
    drives scene image selection, per-scene duration, AND caption timing
    downstream, so what's said always matches what's on screen (see
    materialize_scene_images / compute_scene_durations / build_caption_
    cues)."""

    hook = trim_to_words(build_hook(story), DEFAULT_HOOK_WORD_LIMIT)

    beat_pairs = [
        (i, trim_to_sentences(story.slides[i].text, DEFAULT_BEAT_WORD_LIMIT))
        for i in beat_indices
    ]
    beat_pairs = [(i, text) for i, text in beat_pairs if text]

    beats = [text for _, text in beat_pairs]

    payoff = trim_to_words(story.story_info.moral, DEFAULT_PAYOFF_WORD_LIMIT)

    if not payoff:
        payoff = "Then everything felt better."

    # Deliberately short and fixed-form per the low-cost/no-hard-sell Reel
    # style -- NOT built from publishing copy, which is written for a
    # carousel caption, not an 8-second spoken CTA.
    cta = f"Follow {instagram_handle} for another little story."

    segments = [{"kind": "cover", "text": hook}]

    for slide_index, text in beat_pairs:
        segments.append({"kind": "beat", "slide_index": slide_index, "text": text})

    segments.append({"kind": "cover", "text": f"{payoff} {cta}".strip()})

    full_narration = " ".join(part for part in [hook, *beats, payoff, cta] if part)

    word_count = len(full_narration.split())

    duration_target = max(
        MIN_DURATION_SECONDS,
        min(MAX_DURATION_SECONDS, round(word_count / WORDS_PER_SECOND)),
    )

    return {
        "hook": hook,
        "beats": beats,
        "payoff": payoff,
        "cta": cta,
        "segments": segments,
        "full_narration": full_narration,
        "duration_target": duration_target,
    }


def validate_reel_script(script):
    """Soft validation -- returns (ok, warnings). Does not raise, since
    natural story text lengths can't always land exactly in range without
    an extra AI rewrite pass, which this system deliberately avoids."""

    warnings = []

    word_count = len(script["full_narration"].split())

    if not (MIN_TARGET_WORDS <= word_count <= MAX_TARGET_WORDS):
        warnings.append(
            f"Narration is {word_count} words (target {MIN_TARGET_WORDS}-"
            f"{MAX_TARGET_WORDS})."
        )

    if not script.get("hook", "").strip():
        warnings.append("'hook' is empty.")

    if not script.get("beats"):
        warnings.append("No story beats were selected.")

    if not script.get("payoff", "").strip():
        warnings.append("'payoff' is empty.")

    if not script.get("cta", "").strip():
        warnings.append("'cta' is empty.")

    return (len(warnings) == 0, warnings)


def save_reel_script(script, folder: Path) -> Path:

    path = folder / "reel_script.json"

    path.write_text(
        json.dumps(script, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    return path


# =====================================================================
# Scene selection & visual composition
#
# IMPORTANT: the carousel's slide_*.png files are dense text cards (brand
# header, title, a full paragraph of body text, footer) rendered on a
# near-uniform pale background -- see CarouselRenderer. They are NOT
# illustrated scenes, so feeding them through the same zoom/crop pipeline
# as the AI-illustrated cover produced a Reel that looked like "the same
# cover art, zoomed differently" even though 4 distinct files were used:
# the text cards' content is illegible at Reel scale and their pale
# backgrounds visually blend with the cover's own padding colour.
#
# V3: rather than falling back to a flat colour card per beat (V2), each
# selected story beat now gets its own dedicated, AI-illustrated Reel
# scene (see services/reel_image_service.py) -- up to
# DEFAULT_MAX_STORY_SLIDES_IN_REEL new images total, cached across runs.
# The existing cover is still reused (never regenerated) to open and
# close the Reel. Every scene image, illustrated or the cover, is
# converted to a full-bleed 1080x1920 frame the same way: crop_to_fill()
# below -- no padding, no distortion.
# =====================================================================

DEFAULT_MAX_STORY_SLIDES_IN_REEL = 3  # + cover(open) + cover(outro)

TARGET_WIDTH = 1080
TARGET_HEIGHT = 1920


# =====================================================================
# V6: pacing -- keep the (title-heavy) cover brief at both ends so the
# Reel reads as a short story, not a slideshow that lingers on the
# cover. Passed to compute_scene_durations as first_scene_max_fraction/
# last_scene_max_fraction (see build_reel_script's fixed [cover,
# beat..., cover] segment order -- index 0 and the last index are always
# the two cover segments). The opening is capped tighter than the
# closing: the hook just needs to land quickly and hand off to the
# story, while the closing carries the resolution's payoff line AND the
# CTA, so it gets a little more breathing room. Still fully derived from
# narration word counts (see compute_scene_durations), never a fixed/
# decoupled timer -- and this also happens to be the Reel's only lever
# for the known cover-baked-AI-text issue (see module docstring near
# select_reel_hero_image): it can't edit the cover art itself (that's
# the daily pipeline's job, out of scope here), but it CAN make sure the
# Reel doesn't dwell on it, favouring the 3 illustrated beat scenes
# instead.
# =====================================================================

REEL_OPENING_COVER_MAX_FRACTION = 0.12
REEL_CLOSING_COVER_MAX_FRACTION = 0.18


# =====================================================================
# Reel narration voice -- a single, consistent, warm FEMALE voice so the
# account (@bedtime01fables) develops a recognisable narration identity
# across Reels. "coral" is one of the voices the installed OpenAI SDK's
# Voice type actually supports (openai.types.audio.speech_create_params.
# Voice: alloy, ash, ballad, coral, echo, sage, shimmer, verse, marin,
# cedar, or a custom voice id) and is documented/characterised as a warm,
# friendly, natural, conversational voice -- fitting for gentle bedtime
# storytelling without sounding dramatic or ad-like. This constant is
# scoped to the Reel pipeline only: the daily story pipeline's
# narration.mp3 (services/narration_service.py) does not pass a `voice`
# and keeps using OpenAITTSService's own unrelated default.
# =====================================================================

REEL_NARRATION_VOICE = "coral"

REEL_NARRATION_INSTRUCTIONS = (
    "Warm, calm, gentle storytelling voice for a young child's bedtime "
    "story. Natural, unhurried pace, comforting and friendly tone -- "
    "never dramatic, never like an advertisement."
)


def select_beat_indices(num_slides, max_beats=DEFAULT_MAX_STORY_SLIDES_IN_REEL):
    """Deterministically picks up to `max_beats` slide indices, evenly
    spaced from first to last, to represent the story's problem/action/
    resolution beats. For a 6-slide story this picks slides 1, 3, 6
    (0-indexed: 0, 2, 5) -- matching "cover + slide 1 + slide 3/4 + slide
    6" for a 6-slide story. Never random; same input always gives the
    same output."""

    if num_slides <= 0:
        return []

    if num_slides <= max_beats:
        return list(range(num_slides))

    positions = sorted(set(
        round(i * (num_slides - 1) / (max_beats - 1)) for i in range(max_beats)
    ))

    return positions


def crop_to_fill(image: Image.Image, target_width=TARGET_WIDTH, target_height=TARGET_HEIGHT):
    """Scales `image` up (preserving aspect ratio -- never distorted)
    until it fully covers a target_width x target_height frame, then
    centre-crops the overflow. This "cover" fit turns the existing 4:5
    cover art into a full-bleed 9:16 Reel frame with zero empty padding,
    replacing the old pad-to-fill approach that left large unused cream
    bars top and bottom. Centre-cropping is a deterministic, no-ML
    approximation of "keep the main subject visible": cover art is
    composed with its subject centred, so this keeps the character(s) in
    frame without needing face detection."""

    src_w, src_h = image.size

    scale = max(target_width / src_w, target_height / src_h)

    scaled_w = max(target_width, round(src_w * scale))
    scaled_h = max(target_height, round(src_h * scale))

    resized = image.resize((scaled_w, scaled_h), Image.Resampling.LANCZOS)

    left = (scaled_w - target_width) // 2
    top = (scaled_h - target_height) // 2

    return resized.crop((left, top, left + target_width, top + target_height))


def prepare_full_bleed_image(source_image_path: Path, output_path: Path,
                              width=TARGET_WIDTH, height=TARGET_HEIGHT) -> Path:
    """Materializes a full-bleed 1080x1920 Reel frame from ANY existing
    source image (the cover art, or a dedicated Reel scene illustration
    from ReelImageService) -- centre-cropped, never distorted, never
    padded. Generic on purpose: the cover and the illustrated beat scenes
    go through the exact same, single crop-fit path."""

    with Image.open(source_image_path) as source:
        filled = crop_to_fill(source.convert("RGB"), width, height)
        filled.save(output_path)

    return output_path


def materialize_scene_images(segments, hero_image_path: Path, work_dir: Path, illustrated_images):
    """Turns each script segment into an actual 1080x1920 PNG on disk.
    This is the only step that touches PIL/image files; everything
    downstream (build_ffmpeg_command) just sees a flat list of already-
    correctly-shaped images, exactly like before this change.

    `illustrated_images` maps slide_index -> Path for each "beat" segment
    and MUST already contain an entry for every beat segment present in
    `segments` (see ReelImageService.ensure_scenes) -- there is no
    silent visual fallback if a required illustration is missing; that's
    treated as a hard Reel-generation failure, exactly like a missing
    narration file would be."""

    cover_image_path = work_dir / "reel_scene_cover.png"
    prepare_full_bleed_image(hero_image_path, cover_image_path)

    paths = []

    for segment in segments:

        if segment["kind"] == "cover":
            paths.append(cover_image_path)
            continue

        slide_index = segment["slide_index"]
        source_path = illustrated_images.get(slide_index)

        if source_path is None:
            raise MissingStoryAssetsError(
                f"No illustrated Reel scene image available for slide "
                f"{slide_index} -- ReelImageService must generate/cache "
                f"one for every beat segment before rendering."
            )

        full_bleed_path = work_dir / f"{Path(source_path).stem}_fullbleed.png"
        prepare_full_bleed_image(source_path, full_bleed_path)
        paths.append(full_bleed_path)

    return paths


def compute_scene_durations(word_counts, total_duration, min_scene_seconds=1.6,
                             first_scene_max_fraction=None, last_scene_max_fraction=None):
    """Allocates total_duration across scenes proportionally to each
    scene's own narration word count (so a scene lasts as long as what's
    being said over it, and transitions land on story-beat boundaries --
    not a fixed timer), with a floor so no scene flashes by too fast.
    Floored durations are rescaled back down so the total still sums to
    exactly total_duration.

    V6: `first_scene_max_fraction`/`last_scene_max_fraction` (both
    default None -- off, identical to the pre-V6 behaviour) optionally
    cap the FIRST and LAST scene's share of total_duration -- in
    practice always the opening/closing cover (see build_reel_script's
    fixed [cover, beat..., cover] segment order) -- so the Reel doesn't
    linger on the (title-heavy) cover. Still fully deterministic and
    still derived from narration word counts, not a fixed/decoupled
    timer: any time trimmed off a capped end is handed to the story
    beats in between, in proportion to their own existing share, before
    the same total-duration rescale as always. A no-op unless there are
    at least 3 scenes (cover + >=1 beat + cover) -- with only 1-2 scenes
    there's no "story" to redistribute into."""

    if not word_counts:
        return []

    total_words = sum(word_counts)

    if total_words == 0:
        equal = total_duration / len(word_counts)
        return [round(equal, 2)] * len(word_counts)

    raw = [
        max(min_scene_seconds, total_duration * (wc / total_words))
        for wc in word_counts
    ]

    if len(raw) > 2 and (first_scene_max_fraction or last_scene_max_fraction):

        capped_indices = set()
        excess = 0.0

        if first_scene_max_fraction:
            cap = max(min_scene_seconds, total_duration * first_scene_max_fraction)
            if raw[0] > cap:
                excess += raw[0] - cap
                raw[0] = cap
                capped_indices.add(0)

        if last_scene_max_fraction:
            cap = max(min_scene_seconds, total_duration * last_scene_max_fraction)
            last_index = len(raw) - 1
            if raw[last_index] > cap:
                excess += raw[last_index] - cap
                raw[last_index] = cap
                capped_indices.add(last_index)

        if excess > 0:

            middle_indices = [i for i in range(len(raw)) if i not in capped_indices]
            middle_total = sum(raw[i] for i in middle_indices)

            if middle_total > 0:
                for i in middle_indices:
                    raw[i] += excess * (raw[i] / middle_total)

    scale = total_duration / sum(raw)

    return [round(d * scale, 2) for d in raw]


# =====================================================================
# Captions -- rebuilt to measure actual rendered pixel width (via the
# existing utils.text_layout helper already used by the carousel/cover
# renderers) instead of a fixed word count, so a caption can never be
# wider than the safe on-screen area and never gets clipped.
#
# V5.1: a real (non-mocked) ffmpeg render proved that a PIXEL-SAFE chunk
# could still render clipped edge-to-edge in the actual MP4. Root cause
# (confirmed by rendering real frames and measuring pixels, not just
# reasoning about it): the two-line join below used to build the
# multi-line `text=` value with the literal two-character escape
# sequence "\n" (backslash + n). ffmpeg's drawtext does NOT turn that
# into a line break -- it strips the backslash and leaves a bare "n"
# glued onto the surrounding words, collapsing what should have been two
# short wrapped lines into one much longer single line. That single line
# was then centered via text_w and, being far wider than the safe
# width, overflowed the 1080px frame on both sides -- exactly the
# "beginning of the sentence cut off" symptom observed in production.
# The fix (verified with a real ffmpeg render + pixel bounding-box
# check) is to join lines with an ACTUAL newline character instead --
# see build_final_assembly_command below. PIL's own measured text width
# was cross-checked against ffmpeg's real rendered pixel width for the
# same font/string and tracks within single-digit pixels, so the pixel-
# width safety check here was never the problem.
# =====================================================================

CAPTION_FONT_SIZE = 64
CAPTION_SAFE_WIDTH_PX = 900  # ~90px margin each side of 1080px (80-100px target)
CAPTION_MAX_LINES = 2

# Reel captions are short on-screen phrases, not narration transcripts:
# each caption chunk is also capped at this many words (on top of the
# pixel-width fit above), and a chunk never spans a sentence boundary
# (see build_caption_chunks_for_text) -- so a short sentence in the
# story's own text becomes one clean caption, and a longer one splits
# into a couple of short ones, without inventing or rewriting any story
# content.
CAPTION_MAX_WORDS_PER_CHUNK = 6

# V6: a real render (V5.1's own pixel-safe fix) still produced captions
# like "Pip the squirrel played near a" and "'Don't worry, Lily,' he
# said with" -- technically within the pixel/word limits, but ending on
# an obviously incomplete grammatical fragment. A caption chunk must
# never end on one of these words WHEN MORE TEXT REMAINS after it (see
# build_caption_chunks) -- if it's genuinely the sentence's own last
# word, ending there is unavoidable and correct (nothing to drop or
# rewrite). This is a fixed, explicit list rather than real NLP/grammar
# analysis -- deliberately simple, local, and deterministic.
#
# Base list as given; the possessive determiners (his/her/their/its/
# our/your/my) and a few more prepositions (at/for/by) were added after
# an offline real-render check against the actual KS-000001 story
# surfaced "Pip and Lily sat by their" / "pretty leaf pile." -- "their"
# is exactly as dangling as "the" or "a" but wasn't in the original
# list. Deliberately NOT adding words like "that"/"this"/"it", which CAN
# validly end a complete sentence ("I know that.").
CAPTION_WEAK_TRAILING_WORDS = {
    "a", "an", "the", "to", "of", "in", "on", "near", "with", "at", "for", "by",
    "and", "or", "but", "said", "was", "is", "are", "he", "she", "they",
    "his", "her", "their", "its", "our", "your", "my",
}

# Lower-middle safe area: low enough to read as "captions", high enough
# to stay clear of Instagram's own bottom UI chrome (like/comment/share
# bar), which can cover the very bottom of the frame.
CAPTION_ANCHOR_Y_FRACTION = 0.72


def _dummy_draw():

    return ImageDraw.Draw(Image.new("RGB", (1, 1)))


def _fits_within_lines(draw, words, font, max_width_px, max_lines):

    if not words:
        return True

    lines = wrap_text_to_width(draw, " ".join(words), font, max_width_px)

    return len(lines) <= max_lines


def _ends_on_weak_word(words):
    """True if the last word of `words` (ignoring trailing punctuation
    like commas/periods/quote marks) is one of CAPTION_WEAK_TRAILING_
    WORDS -- e.g. "...near a" or "...he said with" both end weak."""

    if not words:
        return False

    core = re.sub(r"[^A-Za-z]+$", "", words[-1])

    return core.lower() in CAPTION_WEAK_TRAILING_WORDS


def build_caption_chunks(text, font, max_width_px=CAPTION_SAFE_WIDTH_PX, max_lines=CAPTION_MAX_LINES,
                          max_words_per_chunk=CAPTION_MAX_WORDS_PER_CHUNK):
    """Splits `text` into a sequence of caption chunks. Each chunk is
    guaranteed (via measured pixel width at the real caption font/size) to
    render as at most `max_lines` lines that each fit within
    max_width_px -- so a caption can never be horizontally clipped and
    never needs '...' to hide overflow -- AND capped at `max_words_per_
    chunk` words, so a chunk reads as a short on-screen phrase rather
    than a long narration transcript even when it would otherwise fit
    within max_width_px/max_lines. Any overflow simply starts a new,
    later-timed chunk instead of being dropped.

    V6: within that pixel/word-capped budget, a chunk also prefers NOT
    to end on a weak continuation word (see CAPTION_WEAK_TRAILING_WORDS)
    when more text follows it -- it backs off to the longest shorter
    boundary that doesn't, so "Pip the squirrel played near a [big
    tree]" becomes "Pip the squirrel played" / "near a big tree."
    instead. Sentence/phrase structure decides the boundary first;
    pixel measurement (already enforced above) is the safety net, not
    the primary driver."""

    draw = _dummy_draw()
    words = text.split()

    chunks = []
    remaining = words

    while remaining:

        lo, hi, best = 1, min(len(remaining), max_words_per_chunk), 1

        while lo <= hi:

            mid = (lo + hi) // 2

            if _fits_within_lines(draw, remaining[:mid], font, max_width_px, max_lines):
                best = mid
                lo = mid + 1
            else:
                hi = mid - 1

        # Only meaningful to avoid a weak ending if there's a NEXT chunk
        # to carry the deferred word(s) into -- if this chunk already
        # consumes everything left, there's nothing to defer to, and
        # backing off would just drop words rather than say them later.
        if best < len(remaining):

            natural = best

            while natural > 1 and _ends_on_weak_word(remaining[:natural]):
                natural -= 1

            best = natural

        chosen = remaining[:best]
        lines = wrap_text_to_width(draw, " ".join(chosen), font, max_width_px)

        chunks.append({"lines": lines, "words": chosen})

        remaining = remaining[best:]

    return chunks


def _merge_dangling_punctuation_fragments(sentences):
    """A closing quote (or similar) placed right after terminal
    punctuation -- e.g. '...are you okay?"' -- can split off as its own
    punctuation-only "sentence" from _SENTENCE_SPLIT_RE (it contains no
    letters/digits, so it isn't itself a real narrated word). Left alone
    that becomes its own one-"word" caption chunk that's just a bare
    quote mark -- a dangling-punctuation caption. Merges any such
    fragment onto the neighbouring sentence instead; nothing is dropped,
    only regrouped, so the exact original characters are preserved."""

    merged = []

    for sentence in sentences:
        if merged and not re.search(r"[A-Za-z0-9]", sentence):
            merged[-1] = merged[-1] + sentence
        else:
            merged.append(sentence)

    return merged


def build_caption_chunks_for_text(text, font, max_width_px=CAPTION_SAFE_WIDTH_PX, max_lines=CAPTION_MAX_LINES,
                                   max_words_per_chunk=CAPTION_MAX_WORDS_PER_CHUNK):
    """Splits `text` into short, Reel-style caption chunks: first at
    SENTENCE boundaries (reusing the same _SENTENCE_SPLIT_RE already used
    by trim_to_sentences, so a caption chunk never runs two separate
    sentences together), then each sentence through build_caption_chunks
    for its pixel-width/line-count/word-count safety. A short sentence in
    the story's own text (common in this project's simple-English style)
    becomes exactly one clean caption; a longer one splits into a couple
    of short ones. Purely local/deterministic re-chunking of the text
    that's already there -- no AI call, nothing invented, nothing
    reworded."""

    sentences = [s.strip() for s in _SENTENCE_SPLIT_RE.findall(text) if s.strip()]
    sentences = _merge_dangling_punctuation_fragments(sentences)

    if not sentences:
        return build_caption_chunks(text, font, max_width_px, max_lines, max_words_per_chunk)

    chunks = []

    for sentence in sentences:
        chunks.extend(
            build_caption_chunks(sentence, font, max_width_px, max_lines, max_words_per_chunk)
        )

    return chunks


def build_caption_cues(segments, scene_durations, font_path,
                        max_width_px=CAPTION_SAFE_WIDTH_PX, max_lines=CAPTION_MAX_LINES):
    """Builds timed caption cues scene-by-scene: each segment's own text
    is wrapped/chunked and timed entirely within that segment's own scene
    duration. Captions therefore always change exactly when the visual
    changes (a real story-beat boundary), never on an unrelated fixed
    timer."""

    if len(segments) != len(scene_durations):
        raise ValueError("segments and scene_durations must be the same length")

    font = ImageFont.truetype(str(font_path), CAPTION_FONT_SIZE)

    cues = []
    t = 0.0

    for segment, duration in zip(segments, scene_durations):

        scene_start = t
        words = segment["text"].split()

        if not words:
            t = scene_start + duration
            continue

        chunks = build_caption_chunks_for_text(segment["text"], font, max_width_px, max_lines)
        chunk_word_total = sum(len(c["words"]) for c in chunks) or 1

        for chunk in chunks:

            chunk_duration = duration * (len(chunk["words"]) / chunk_word_total)

            start = round(t, 2)
            end = round(min(t + chunk_duration, scene_start + duration), 2)

            cues.append({
                "lines": chunk["lines"],
                "text": " ".join(chunk["words"]),
                "start": start,
                "end": end,
            })

            t += chunk_duration

        t = scene_start + duration  # avoid float drift accumulating across scenes

    return cues


# =====================================================================
# FFmpeg command construction (pure string/list building -- testable
# without ever invoking ffmpeg)
# =====================================================================

def escape_drawtext(text):
    """Escapes a single line of text for safe use inside an ffmpeg
    drawtext filter, which is itself embedded inside a filter_complex
    string. Must be applied per-line (BEFORE joining multi-line captions
    with an actual newline character -- see build_final_assembly_command
    -- never the literal two-character '\\n' sequence, which ffmpeg's
    drawtext does not treat as a line break)."""

    text = text.replace("\\", "\\\\")
    text = text.replace(":", "\\:")
    text = text.replace("'", "’")  # avoid unescaped single quotes entirely
    text = text.replace("%", "\\%")

    return text


# V4 rendering architecture
# =====================================================================
# V3 built ONE filter_complex containing a separate `zoompan` filter
# instance per scene, all feeding a single `concat`. A forensic
# investigation (real ffmpeg, real production code, reproducible locally)
# proved this construction has a real defect on the ffmpeg build used in
# production: with 2+ zoompan instances sharing one filter graph before
# concat, every scene after the first collapses into the first scene's
# content -- the final video shows only the opening cover for its entire
# duration, even though the ffmpeg *inputs* are provably correct and
# distinct. Removing zoompan (or using only one instance) fixes it, which
# pinpoints zoompan+concat multi-instance interaction as the cause.
#
# V4 avoids ever having more than one zoompan filter in the same graph:
#
#   Stage 1 (per scene, N separate ffmpeg processes): render each scene
#   image into its own short, silent, video-only clip. Each process's
#   filter graph contains exactly ONE zoompan instance.
#
#   Stage 2 (one ffmpeg process, concat DEMUXER): losslessly stream-copy
#   the N clips into a single silent video. This is a container-level
#   operation with NO filter graph at all, so it cannot reintroduce the
#   defect.
#
#   Stage 3 (one ffmpeg process): take that single concatenated video +
#   narration (+ optional music), burn in captions (drawtext only -- never
#   implicated by the investigation) and mux audio, producing the final
#   reel.mp4.
# =====================================================================

def build_scene_clip_command(image_path: Path, duration, output_path: Path,
                              zoom_in, fps=25, width=TARGET_WIDTH, height=TARGET_HEIGHT):
    """Builds the ffmpeg argv for rendering ONE scene image into its own
    short, silent, video-only clip with independent zoom motion. Exactly
    one zoompan filter per process/graph -- see the V4 module notes
    above for why that matters."""

    if zoom_in:
        zoom_expr = "min(zoom+0.0012,1.15)"
    else:
        zoom_expr = "if(eq(on,1),1.15,max(zoom-0.0012,1.0))"

    frames = max(1, round(duration * fps))

    vf = (
        f"zoompan=z='{zoom_expr}':"
        f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
        f"d={frames}:s={width}x{height}:fps={fps},setsar=1"
    )

    return [
        FFMPEG_BIN, "-y",
        "-loop", "1", "-t", f"{duration:.3f}", "-i", str(image_path),
        "-vf", vf,
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-r", str(fps),
        "-t", f"{duration:.3f}",
        str(output_path),
    ]


def write_concat_list_file(clip_paths, list_path: Path) -> Path:
    """Writes an ffmpeg concat-demuxer list file. Paths are made absolute
    and escaped per the concat demuxer's own quoting rules (each path
    single-quoted; internal single quotes escaped as '\\''), independent
    of the OS path separator style."""

    lines = []

    for clip_path in clip_paths:
        absolute = Path(clip_path).resolve().as_posix()
        escaped = absolute.replace("'", "'\\''")
        lines.append(f"file '{escaped}'")

    list_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    return list_path


def build_concat_command(list_path: Path, output_path: Path):
    """Builds the ffmpeg argv that stitches pre-rendered scene clips
    together via the concat DEMUXER: a container-level stream copy, no
    filter graph, no re-encode -- so this step cannot reintroduce the
    zoompan+concat defect. Requires every clip to share identical codec/
    pixel-format/resolution/frame-rate, which build_scene_clip_command
    guarantees by construction."""

    return [
        FFMPEG_BIN, "-y",
        "-f", "concat", "-safe", "0", "-i", str(list_path),
        "-c", "copy",
        str(output_path),
    ]


def build_final_assembly_command(
    concatenated_video_path: Path,
    narration_path: Path,
    output_path: Path,
    caption_cues,
    font_path: Path,
    total_duration,
    music_path=None,
    music_volume=None,
    fps=25,
):
    """Builds the ffmpeg argv for the final assembly pass: ONE video
    input (the already-concatenated silent scene video) plus narration
    (+ optional music), burned-in captions, and audio muxing. The filter
    graph here only ever contains drawtext/volume/amix/afade filters --
    never zoompan -- so it cannot exhibit the defect this architecture
    was built to avoid.

    Music duration is never authoritative: `-stream_loop -1` makes the
    music input loop indefinitely so it always covers the full Reel even
    if the source track is shorter, and the amix filter's
    `duration=first` (paired with the narration always being input 1)
    plus the output-level `-t total_duration` below both trim it back
    down if the track is longer -- narration/video duration always wins."""

    if music_volume is None:
        music_volume = MUSIC_VOLUME_DEFAULT

    inputs = ["-i", str(concatenated_video_path), "-i", str(narration_path)]

    music_input_index = None

    if music_path is not None:
        music_input_index = 2
        # -stream_loop -1 loops the track indefinitely (see docstring)
        # rather than trying to compute an exact repeat count.
        inputs += ["-stream_loop", "-1", "-i", str(music_path)]

    filters = []

    # Burned-in captions: chained drawtext filters, each windowed to its
    # own time range, centered in the lower-middle safe area via ffmpeg's
    # own text_w/text_h (never a hardcoded x position, so this stays
    # correct regardless of the actual rendered text width).
    #
    # V5.1 fix: multi-line cues MUST be joined with a real newline BYTE
    # (chr(10)) -- NOT the two-character escape sequence "\n" (backslash
    # + n) used previously. Verified with a real ffmpeg render + pixel
    # bounding-box measurement: ffmpeg's drawtext does not treat a
    # literal backslash-n as a line break -- it silently drops the
    # backslash and leaves a bare "n" glued onto the surrounding words,
    # collapsing two short wrapped lines into one much longer single
    # line that, once centered via text_w, overflowed the 1080px frame
    # on both sides. That was the actual cause of the clipped captions
    # seen in the real production Reel (mocked-ffmpeg tests never
    # exercised a real 2-line render, so they never caught it). A real
    # newline byte here is correctly rendered by drawtext as an actual
    # line break, and text_w/text_h then correctly reflect the full
    # (possibly 2-line) block, so the block stays centered and the y
    # anchor stays fixed regardless of line count.
    caption_label = "0:v"

    # A colon inside the quoted fontfile value (e.g. a Windows drive
    # letter, "C:/...") isn't reliably parsed by ffmpeg's filter-option
    # parser even though it's quoted. Escaping it is a no-op on Linux
    # paths (which never contain a colon) but fixes local Windows
    # development/testing.
    safe_font_path = font_path.as_posix().replace(":", "\\:")

    for i, cue in enumerate(caption_cues):

        next_label = f"vcap{i}"

        safe_text = "\n".join(escape_drawtext(line) for line in cue["lines"])

        filters.append(
            f"[{caption_label}]drawtext="
            f"fontfile='{safe_font_path}':"
            f"text='{safe_text}':"
            f"fontsize={CAPTION_FONT_SIZE}:fontcolor=white:"
            f"line_spacing=8:"
            f"box=1:boxcolor=black@0.55:boxborderw=24:"
            f"x=(w-text_w)/2:y=(h*{CAPTION_ANCHOR_Y_FRACTION})-(text_h/2):"
            f"enable='between(t,{cue['start']:.2f},{cue['end']:.2f})'"
            f"[{next_label}]"
        )

        caption_label = next_label

    # No drawtext filters were added if there were no caption cues -- in
    # that case caption_label is still the raw "0:v" input reference, not
    # a filtergraph pad, so it must be mapped as an unfiltered stream.
    video_used_filtergraph = caption_label != "0:v"

    # Audio: narration always present; optional low-volume music mixed
    # underneath, never louder than narration (default ~10%), with a
    # short fade-in/out so it never starts or stops abruptly.
    if music_input_index is not None:

        fade_out_start = max(0.0, total_duration - MUSIC_FADE_OUT_SECONDS)

        filters.append("[1:a]volume=1.0[narr]")
        filters.append(
            f"[{music_input_index}:a]volume={music_volume},"
            f"afade=t=in:st=0:d={MUSIC_FADE_IN_SECONDS},"
            f"afade=t=out:st={fade_out_start:.2f}:d={MUSIC_FADE_OUT_SECONDS}"
            f"[music]"
        )
        filters.append(
            "[narr][music]amix=inputs=2:duration=first:dropout_transition=2[aout]"
        )

    else:

        filters.append("[1:a]anull[aout]")

    filter_complex = ";".join(filters)

    video_map = f"[{caption_label}]" if video_used_filtergraph else "0:v"

    command = [
        FFMPEG_BIN, "-y",
        *inputs,
        "-filter_complex", filter_complex,
        "-map", video_map,
        "-map", "[aout]",
        "-c:v", "libx264",
        "-c:a", "aac",
        "-pix_fmt", "yuv420p",
        "-r", str(fps),
        "-t", f"{total_duration:.3f}",
        "-movflags", "+faststart",
        str(output_path),
    ]

    return command


def render_reel_video(scene_images, scene_durations, narration_path: Path,
                       output_path: Path, caption_cues, font_path: Path,
                       work_dir: Path, music_path=None, music_volume=None, fps=25):
    """Orchestrates the full V4 three-stage render (see module notes
    above): per-scene clips -> concat-demuxer stitch -> final assembly
    with captions/audio. Raises FFmpegNotAvailableError/ReelGenerationError
    exactly like the old single-pass build_ffmpeg_command()+
    run_ffmpeg_command() pair did, so callers don't need to change their
    error handling.

    Intermediate clips live in `work_dir` (a fresh, dedicated
    subdirectory -- see ReelService.generate()) and are deleted on
    success; left in place on failure for diagnosis, matching the
    existing "don't silently retry, don't pollute on success" philosophy
    already used for reel.mp4 itself."""

    if len(scene_images) != len(scene_durations):
        raise ValueError("scene_images and scene_durations must be the same length")

    work_dir.mkdir(parents=True, exist_ok=True)

    clip_paths = []

    for i, (image_path, duration) in enumerate(zip(scene_images, scene_durations)):

        zoom_in = (i % 2 == 0)
        clip_path = work_dir / f"scene_{i:02d}.mp4"

        command = build_scene_clip_command(image_path, duration, clip_path, zoom_in, fps=fps)

        try:
            run_ffmpeg_command(command)
        except ReelGenerationError as error:
            raise ReelGenerationError(
                f"Rendering scene clip {i} ({image_path}) failed: {error}"
            ) from error

        if not clip_path.exists() or clip_path.stat().st_size == 0:
            raise ReelGenerationError(
                f"Scene clip {i} ({clip_path}) was not produced."
            )

        clip_paths.append(clip_path)

    concat_list_path = work_dir / "concat_list.txt"
    write_concat_list_file(clip_paths, concat_list_path)

    concatenated_path = work_dir / "concatenated_silent.mp4"
    concat_command = build_concat_command(concat_list_path, concatenated_path)

    try:
        run_ffmpeg_command(concat_command)
    except ReelGenerationError as error:
        raise ReelGenerationError(
            f"Concatenating {len(clip_paths)} scene clips failed: {error}"
        ) from error

    if not concatenated_path.exists() or concatenated_path.stat().st_size == 0:
        raise ReelGenerationError(
            f"Concatenated scene video {concatenated_path} was not produced."
        )

    total_duration = sum(scene_durations)

    final_command = build_final_assembly_command(
        concatenated_video_path=concatenated_path,
        narration_path=narration_path,
        output_path=output_path,
        caption_cues=caption_cues,
        font_path=font_path,
        total_duration=total_duration,
        music_path=music_path,
        music_volume=music_volume,
        fps=fps,
    )

    try:
        run_ffmpeg_command(final_command)
    except ReelGenerationError as error:
        raise ReelGenerationError(
            f"Final Reel assembly (captions + audio) failed: {error}"
        ) from error

    # Only clean up the intermediate clips after every stage has
    # succeeded -- a failure above leaves them in place for diagnosis.
    shutil.rmtree(work_dir, ignore_errors=True)

    return output_path


EXPECTED_WIDTH = TARGET_WIDTH
EXPECTED_HEIGHT = TARGET_HEIGHT


def validate_video_output(path: Path, metadata):
    """Hard gate run right after ffmpeg reports success: confirms the file
    on disk is actually a valid, correctly-shaped, audible Reel before the
    Content Library is ever updated. Raises ReelGenerationError (never
    updates the library) if anything here doesn't check out."""

    if not path.exists() or path.stat().st_size == 0:
        raise ReelGenerationError(
            f"{path} does not exist or is empty after ffmpeg reported success."
        )

    if "width" not in metadata or "height" not in metadata:
        raise ReelGenerationError(
            f"Could not verify {path} dimensions (ffprobe unavailable or "
            f"failed). Install ffprobe (bundled with ffmpeg) to validate "
            f"Reel output."
        )

    if metadata["width"] != EXPECTED_WIDTH or metadata["height"] != EXPECTED_HEIGHT:
        raise ReelGenerationError(
            f"{path} is {metadata['width']}x{metadata['height']}, expected "
            f"{EXPECTED_WIDTH}x{EXPECTED_HEIGHT}."
        )

    duration = metadata.get("duration_seconds")

    if duration is None:
        raise ReelGenerationError(
            f"Could not verify {path} duration (ffprobe unavailable or failed)."
        )

    if not (MIN_DURATION_SECONDS <= duration <= MAX_DURATION_SECONDS + 5):
        # +5s tolerance for container/encoder overhead beyond the script's
        # own duration_target, which is already clamped to [MIN, MAX].
        raise ReelGenerationError(
            f"{path} duration is {duration}s, expected roughly "
            f"{MIN_DURATION_SECONDS}-{MAX_DURATION_SECONDS}s."
        )

    if not metadata.get("has_audio"):
        raise ReelGenerationError(
            f"{path} has no audio stream -- narration appears to be missing."
        )


def probe_video_metadata(path: Path):
    """Best-effort ffprobe lookup for duration/width/height/audio
    presence. Reel generation has already succeeded by the time this
    normally runs, so a lookup failure just means less metadata -- never
    raises. validate_video_output (above) is what turns "couldn't verify"
    into a hard failure."""

    ffprobe_bin = shutil.which("ffprobe")

    if ffprobe_bin is None:
        return {}

    try:
        result = subprocess.run(
            [
                ffprobe_bin,
                "-v", "error",
                "-show_entries", "stream=codec_type,width,height:format=duration",
                "-of", "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode != 0:
            return {}
        data = json.loads(result.stdout)
    except Exception:
        return {}

    metadata = {}

    duration = data.get("format", {}).get("duration")

    if duration is not None:
        try:
            metadata["duration_seconds"] = round(float(duration), 2)
        except (TypeError, ValueError):
            pass

    streams = data.get("streams", [])

    video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)

    if video_stream is None and streams:
        # Backward-compatible fallback for callers/mocks that return a
        # stream dict without a codec_type field.
        video_stream = streams[0]

    if video_stream and "width" in video_stream and "height" in video_stream:
        metadata["width"] = video_stream["width"]
        metadata["height"] = video_stream["height"]

    metadata["has_audio"] = any(s.get("codec_type") == "audio" for s in streams)

    return metadata


def run_ffmpeg_command(command):
    """Actually invokes ffmpeg. Kept separate from build_ffmpeg_command so
    command construction can be unit-tested without running a subprocess."""

    if not check_ffmpeg_available():
        raise FFmpegNotAvailableError(
            "ffmpeg was not found on PATH. Install ffmpeg and verify with "
            "`ffmpeg -version` before generating a Reel."
        )

    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        raise ReelGenerationError(
            "ffmpeg failed while rendering the Reel.\n"
            f"Command: {' '.join(command)}\n"
            f"ffmpeg stderr (last 2000 chars):\n{result.stderr[-2000:]}"
        )

    return result


# =====================================================================
# Music (local, royalty-free tracks only -- no downloads, no AI music,
# zero extra API calls). The user drops licensed/royalty-free .mp3 files
# into assets/music/ (see assets/music/README.md) -- this module never
# creates, downloads, or generates any audio file there.
# =====================================================================

MUSIC_DIR = Path("assets/music")

MUSIC_VOLUME_DEFAULT = 0.10  # ~10% relative to narration -- narration always dominant
MUSIC_FADE_IN_SECONDS = 0.6
MUSIC_FADE_OUT_SECONDS = 1.0


def list_music_tracks():
    """All *.mp3 files directly in assets/music/, sorted for a stable,
    deterministic ordering -- select_music_track()'s hashed index depends
    on this order being the same across runs/machines."""

    if not MUSIC_DIR.exists():
        return []

    return sorted(MUSIC_DIR.glob("*.mp3"))


def probe_audio_duration(path: Path):
    """Best-effort ffprobe duration lookup for a local audio file.
    Returns None if the file is missing/empty, ffprobe is unavailable, or
    the file simply isn't a decodable audio file -- used to filter out an
    invalid optional music file BEFORE it ever reaches the ffmpeg render,
    so a bad track can never corrupt the Reel (see list_valid_music_
    tracks). Mirrors probe_video_metadata's "never raises, just returns
    less information" contract."""

    if not path.exists() or path.stat().st_size == 0:
        return None

    ffprobe_bin = shutil.which("ffprobe")

    if ffprobe_bin is None:
        return None

    try:
        result = subprocess.run(
            [
                ffprobe_bin,
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode != 0:
            return None
        data = json.loads(result.stdout)
        duration = data.get("format", {}).get("duration")
        return float(duration) if duration is not None else None
    except Exception:
        return None


def list_valid_music_tracks():
    """list_music_tracks(), filtered down to files that are actually
    present, non-empty, and ffprobe-decodable. A corrupt, empty, or
    otherwise unreadable file is silently skipped here -- one bad track
    must never crash Reel generation, and a still-valid track should be
    tried instead when one is available."""

    return [
        path for path in list_music_tracks()
        if probe_audio_duration(path) is not None
    ]


def select_music_track(content_id, tracks=None):
    """Deterministically selects ONE valid background-music track for a
    given content_id: hashing content_id (sha256, mod track count) means
    the same content_id always lands on the same track -- so a Reel never
    changes unexpectedly when regenerated -- while different content_ids
    can land on different tracks, rotating usage across the library.

    Returns None if no valid track is available; callers must treat that
    as "generate without music", never as an error (see module docstring
    and assets/music/README.md)."""

    if tracks is None:
        tracks = list_valid_music_tracks()

    if not tracks:
        return None

    digest = hashlib.sha256((content_id or "").encode("utf-8")).hexdigest()
    index = int(digest, 16) % len(tracks)

    return tracks[index]


# =====================================================================
# Orchestration
# =====================================================================

class ReelService:

    def __init__(self):

        self.library = ContentLibraryService()
        self.tts = OpenAITTSService()
        self.images = ReelImageService()
        self.brand = BrandLoader.load()

        self.font_path = (
            Path(__file__).resolve().parent.parent
            / "assets" / "fonts" / "Poppins-Bold.ttf"
        )

    # -----------------------------------------------------------------
    # Story selection
    # -----------------------------------------------------------------

    def list_reel_eligible_stories(self):
        """Only stories that actually exist on disk and have the assets a
        Reel needs. Each entry also reports whether a Reel already exists."""

        eligible = []

        for entry in self.library.get_all_stories():

            folder = Path(entry["folder"])

            if not folder.exists():
                continue

            try:
                discover_story_images(folder)
            except MissingStoryAssetsError:
                continue

            eligible.append(entry)

        return eligible

    # -----------------------------------------------------------------
    # Narration (reuses OpenAITTSService; skips regenerating if a valid
    # reel narration already exists for the CURRENT script text)
    # -----------------------------------------------------------------

    def _get_or_generate_narration(self, folder: Path, script, force=False):

        narration_path = folder / "reel_narration.mp3"
        narration_text_path = folder / "reel_narration.txt"

        # Cache key includes the voice, not just the text: if
        # REEL_NARRATION_VOICE ever changes (e.g. this female-voice
        # rollout, replacing whatever voice a prior run used), any
        # existing cached narration no longer matches this key and is
        # regenerated exactly once, rather than silently being reused
        # with the wrong voice. A cache file written before this key
        # format existed also simply won't match, which correctly forces
        # one regeneration too.
        cache_key = f"{REEL_NARRATION_VOICE}|{script['full_narration']}"

        cached_text = (
            narration_text_path.read_text(encoding="utf-8")
            if narration_text_path.exists() else None
        )

        already_valid = (
            narration_path.exists()
            and narration_path.stat().st_size > 0
            and cached_text == cache_key
        )

        if already_valid and not force:
            print(f"Reusing existing Reel narration: {narration_path}")
            return narration_path

        try:
            self.tts.generate(
                text=script["full_narration"],
                output_file=narration_path,
                voice=REEL_NARRATION_VOICE,
                instructions=REEL_NARRATION_INSTRUCTIONS,
            )
        except Exception as error:
            if narration_path.exists():
                narration_path.unlink()
            raise ReelGenerationError(
                f"Reel narration (TTS) failed: {error}"
            ) from error

        if not narration_path.exists() or narration_path.stat().st_size == 0:
            raise ReelGenerationError(
                "Reel narration (TTS) did not produce an audio file."
            )

        # Records exactly what text+voice this audio narrates, so a
        # future run only reuses it if neither has changed since.
        narration_text_path.write_text(cache_key, encoding="utf-8")

        return narration_path

    # -----------------------------------------------------------------
    # Background music (local files only -- see module docstring above
    # list_music_tracks(). Zero API calls, zero downloads.)
    # -----------------------------------------------------------------

    def _resolve_music_path(self, content_id, music_track, disable_music):
        """Resolves which local music track (if any) this Reel should
        use:

        - disable_music=True: no music, regardless of what's available
          (an explicit user choice -- see the interactive CLI).
        - music_track given: an explicit filename override (from the
          interactive CLI's track picker). Falls back to deterministic
          auto-selection if that specific file is missing or invalid,
          rather than failing the whole Reel over an optional asset.
        - Otherwise (the default): deterministic selection by content_id
          (see select_music_track) -- same content_id always picks the
          same track; different content_ids can rotate across the
          library. Returns None if assets/music/ has no valid tracks --
          a narration-only Reel is a normal, expected outcome, never an
          error."""

        if disable_music:
            return None

        valid_tracks = list_valid_music_tracks()

        if music_track:
            candidate = MUSIC_DIR / music_track
            if candidate in valid_tracks:
                return candidate
            print(
                f"⚠️ Requested music track '{music_track}' is missing or "
                f"invalid -- falling back to automatic track selection."
            )

        return select_music_track(content_id, tracks=valid_tracks)

    # -----------------------------------------------------------------
    # Main entry point
    # -----------------------------------------------------------------

    def generate(self, content_id, overwrite=False, music_track=None,
                 disable_music=False, force_narration=False, force_images=False):

        entry = self.library.get_story(content_id)

        if entry is None:
            raise ReelGenerationError(f"No story found in the Content Library for {content_id}.")

        folder = Path(entry["folder"])

        if not folder.exists():
            raise MissingStoryAssetsError(
                f"Story folder {folder} does not exist on disk."
            )

        output_path = folder / "reel.mp4"

        if output_path.exists() and not overwrite:
            raise ReelGenerationError(
                f"{output_path} already exists. Re-run with overwrite "
                f"confirmed to replace it."
            )

        if not check_ffmpeg_available():
            raise FFmpegNotAvailableError(
                "ffmpeg was not found on PATH. Install ffmpeg (see README) "
                "and verify with `ffmpeg -version` before generating a Reel."
            )

        print("📖 Loading existing story...")

        cover_path, slide_paths = discover_story_images(folder)

        story = load_story_package(folder)

        print(f"✅ Story found: {story.story_info.title}")
        print()

        hero_image_path = select_reel_hero_image(folder, cover_path)
        beat_indices = select_beat_indices(len(story.slides))

        script = build_reel_script(
            story, beat_indices,
            instagram_handle=self.brand.get("instagram_handle", "@bedtime01fables"),
        )

        # Resolved BEFORE saving the script so the actual selected track
        # (or the absence of one) is recorded in reel_script.json, and so
        # regenerating this same content_id later reproduces the same
        # pick (see select_music_track).
        music_path = self._resolve_music_path(content_id, music_track, disable_music)

        script["music"] = {
            "enabled": music_path is not None,
            "file": (f"assets/music/{music_path.name}" if music_path is not None else None),
            "volume": MUSIC_VOLUME_DEFAULT,
        }

        save_reel_script(script, folder)

        print("🎙️ Generating Reel narration...")

        narration_path = self._get_or_generate_narration(folder, script, force=force_narration)

        print("✅ Narration generated.")
        print()

        segments = script["segments"]
        word_counts = [len(segment["text"].split()) for segment in segments]
        scene_durations = compute_scene_durations(
            word_counts, script["duration_target"],
            first_scene_max_fraction=REEL_OPENING_COVER_MAX_FRACTION,
            last_scene_max_fraction=REEL_CLOSING_COVER_MAX_FRACTION,
        )

        beat_segments = [segment for segment in segments if segment["kind"] == "beat"]
        beat_indices_for_images = [segment["slide_index"] for segment in beat_segments]
        beat_texts_for_images = [segment["text"] for segment in beat_segments]

        print("🎨 Reel scene generation")
        print("   Existing cover: reused")

        try:
            scene_results = self.images.ensure_scenes(
                story=story,
                content_id=content_id,
                beat_indices=beat_indices_for_images,
                beat_texts=beat_texts_for_images,
                folder=folder,
                force=force_images,
            )
        except ReelImageGenerationError as error:
            raise ReelGenerationError(
                f"Reel scene image generation failed: {error}"
            ) from error

        print()

        illustrated_images = {
            result["slide_index"]: result["image_path"] for result in scene_results
        }

        scene_images = materialize_scene_images(segments, hero_image_path, folder, illustrated_images)

        caption_cues = build_caption_cues(segments, scene_durations, self.font_path)

        print("🎞️ Rendering Reel with FFmpeg...")

        if music_path is not None:
            print(f"   Background music: {music_path.name}")
        else:
            print("   Background music: none")

        clips_dir = folder / "reel_scene_clips"

        try:
            render_reel_video(
                scene_images=scene_images,
                scene_durations=scene_durations,
                narration_path=narration_path,
                output_path=output_path,
                caption_cues=caption_cues,
                font_path=self.font_path,
                work_dir=clips_dir,
                music_path=music_path,
                music_volume=MUSIC_VOLUME_DEFAULT,
            )
        except (FFmpegNotAvailableError, ReelGenerationError):
            if output_path.exists():
                output_path.unlink()
            raise

        # Permanent safeguard: confirms the RENDERED video actually shows
        # different content at different timestamps. A real production
        # defect (multiple zoompan filter instances collapsing every
        # scene into the first) previously slipped past 92 passing mocked
        # tests because none of them executed real ffmpeg end to end --
        # this catches that entire class of regression automatically.
        try:
            verify_rendered_video_has_scene_changes(output_path)
        except RuntimeError as error:
            if output_path.exists():
                output_path.unlink()
            raise ReelGenerationError(str(error)) from error

        metadata = probe_video_metadata(output_path)

        try:
            validate_video_output(output_path, metadata)
        except ReelGenerationError:
            if output_path.exists():
                output_path.unlink()
            raise

        print("✅ Reel generated.")
        print()
        print("📱 Video:")
        print(f"   {metadata['width']} x {metadata['height']}")
        print(f"   Duration: {metadata['duration_seconds']}s")
        print()

        # Only now, after a verified successful render AND a verified,
        # correctly-shaped, audible output file, update the Content
        # Library -- never mark reel.generated on a failed or invalid
        # attempt.
        self.library.update_reel(content_id, output_path)

        print("📦 Reel artifact ready.")

        return output_path
