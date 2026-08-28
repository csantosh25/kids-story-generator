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
    """Prefers the raw (textless) AI cover art for the Reel's full-bleed
    hero crop over cover_final.png. cover_final.png bakes a title overlay
    into the bottom of the 4:5 image (see CoverDesigner); centre-cropping
    that up to a 9:16 frame can clip the title text sideways, whereas the
    Reel renders its own hook caption instead (see build_hook/build_
    reel_script), so the baked title isn't needed and the textless art is
    strictly safer to crop. Falls back to whatever discover_story_images
    already found if the raw cover isn't on disk."""

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
# Since generating new illustrations per Reel is explicitly out of scope
# (cost), the only genuinely usable illustrated asset per story is the
# cover. The design here leans on that fact honestly instead of pretending
# the slides are cinematic: the cover provides the one real "scene" (used
# to open AND close the Reel, with different narration each time so it
# doesn't feel repeated), and each selected story beat is instead given
# its own full-bleed colour card built from that beat's own
# background_color (already authored per-slide by the daily pipeline, so
# it's real, existing story data -- not a new asset) -- giving genuine,
# deterministic, zero-cost visual variation between beats.
# =====================================================================

DEFAULT_MAX_STORY_SLIDES_IN_REEL = 3  # + cover(open) + cover(outro)

TARGET_WIDTH = 1080
TARGET_HEIGHT = 1920


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


def prepare_cover_scene_image(hero_image_path: Path, output_path: Path,
                               width=TARGET_WIDTH, height=TARGET_HEIGHT) -> Path:
    """Materializes a full-bleed 1080x1920 Reel frame from the existing
    cover artwork -- centre-cropped, never distorted, never padded."""

    with Image.open(hero_image_path) as source:
        filled = crop_to_fill(source.convert("RGB"), width, height)
        filled.save(output_path)

    return output_path


def prepare_beat_card_image(background_color: str, output_path: Path,
                             width=TARGET_WIDTH, height=TARGET_HEIGHT) -> Path:
    """Builds a full-bleed 1080x1920 solid-colour Reel frame from a story
    beat's own background_color. Zero new AI calls, zero new art assets --
    this is existing story data (authored per-slide by the daily
    pipeline), just not the dense text-card PNG built from it."""

    Image.new("RGB", (width, height), background_color).save(output_path)

    return output_path


def materialize_scene_images(segments, hero_image_path: Path, story: StoryPackage, work_dir: Path):
    """Turns each script segment into an actual 1080x1920 PNG on disk.
    This is the only step that touches PIL/image files; everything
    downstream (build_ffmpeg_command) just sees a flat list of already-
    correctly-shaped images, exactly like before this change."""

    cover_image_path = work_dir / "reel_scene_cover.png"
    prepare_cover_scene_image(hero_image_path, cover_image_path)

    paths = []

    for segment in segments:

        if segment["kind"] == "cover":
            paths.append(cover_image_path)
            continue

        slide = story.slides[segment["slide_index"]]
        beat_path = work_dir / f"reel_scene_beat_{segment['slide_index']}.png"
        prepare_beat_card_image(slide.background_color, beat_path)
        paths.append(beat_path)

    return paths


def compute_scene_durations(word_counts, total_duration, min_scene_seconds=1.6):
    """Allocates total_duration across scenes proportionally to each
    scene's own narration word count (so a scene lasts as long as what's
    being said over it, and transitions land on story-beat boundaries --
    not a fixed timer), with a floor so no scene flashes by too fast.
    Floored durations are rescaled back down so the total still sums to
    exactly total_duration."""

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

    scale = total_duration / sum(raw)

    return [round(d * scale, 2) for d in raw]


# =====================================================================
# Captions -- rebuilt to measure actual rendered pixel width (via the
# existing utils.text_layout helper already used by the carousel/cover
# renderers) instead of a fixed word count, so a caption can never be
# wider than the safe on-screen area and never gets clipped.
# =====================================================================

CAPTION_FONT_SIZE = 64
CAPTION_SAFE_WIDTH_PX = 860  # within the 850-900px safe-width target
CAPTION_MAX_LINES = 2

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


def build_caption_chunks(text, font, max_width_px=CAPTION_SAFE_WIDTH_PX, max_lines=CAPTION_MAX_LINES):
    """Splits `text` into a sequence of caption chunks. Each chunk is
    guaranteed (via measured pixel width at the real caption font/size) to
    render as at most `max_lines` lines that each fit within
    max_width_px -- so a caption can never be horizontally clipped and
    never needs '...' to hide overflow. Any overflow simply starts a new,
    later-timed chunk instead of being dropped."""

    draw = _dummy_draw()
    words = text.split()

    chunks = []
    remaining = words

    while remaining:

        lo, hi, best = 1, len(remaining), 1

        while lo <= hi:

            mid = (lo + hi) // 2

            if _fits_within_lines(draw, remaining[:mid], font, max_width_px, max_lines):
                best = mid
                lo = mid + 1
            else:
                hi = mid - 1

        chosen = remaining[:best]
        lines = wrap_text_to_width(draw, " ".join(chosen), font, max_width_px)

        chunks.append({"lines": lines, "words": chosen})

        remaining = remaining[best:]

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

        chunks = build_caption_chunks(segment["text"], font, max_width_px, max_lines)
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
    with the literal '\\n' separator drawtext expects), since escaping
    would otherwise double the backslash in that separator."""

    text = text.replace("\\", "\\\\")
    text = text.replace(":", "\\:")
    text = text.replace("'", "’")  # avoid unescaped single quotes entirely
    text = text.replace("%", "\\%")

    return text


def build_ffmpeg_command(
    scene_images,
    scene_durations,
    narration_path: Path,
    output_path: Path,
    caption_cues,
    font_path: Path,
    music_path=None,
    music_volume=0.10,
    fps=25,
    width=TARGET_WIDTH,
    height=TARGET_HEIGHT,
):
    """Builds the full ffmpeg argv list for a single-pass render: subtle
    zoompan motion per image, hard-cut concat, burned-in captions, and an
    optional low-volume background-music mix under the narration.

    All scene_images are expected to already be exactly width x height
    (see materialize_scene_images) -- so unlike the old pad-to-fill
    approach, no letterboxing/padding filter is needed here at all."""

    if len(scene_images) != len(scene_durations):
        raise ValueError("scene_images and scene_durations must be the same length")

    inputs = []

    for image_path, duration in zip(scene_images, scene_durations):
        inputs += ["-loop", "1", "-t", f"{duration:.3f}", "-i", str(image_path)]

    narration_input_index = len(scene_images)

    inputs += ["-i", str(narration_path)]

    music_input_index = None

    if music_path is not None:
        music_input_index = narration_input_index + 1
        inputs += ["-i", str(music_path)]

    filters = []

    scene_labels = []

    for i, duration in enumerate(scene_durations):

        frames = max(1, round(duration * fps))

        # Slow, centered zoom (alternates in/out per scene for subtle
        # variety); zoompan itself preserves the source image's content --
        # no cropping/distortion beyond the deliberate slow zoom.
        zoom_in = (i % 2 == 0)

        if zoom_in:
            zoom_expr = "min(zoom+0.0012,1.15)"
        else:
            zoom_expr = "if(eq(on,1),1.15,max(zoom-0.0012,1.0))"

        filters.append(
            f"[{i}:v]"
            f"zoompan=z='{zoom_expr}':"
            f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
            f"d={frames}:s={width}x{height}:fps={fps},"
            f"setsar=1[v{i}]"
        )

        scene_labels.append(f"[v{i}]")

    concat_inputs = "".join(scene_labels)

    filters.append(
        f"{concat_inputs}concat=n={len(scene_labels)}:v=1:a=0[vraw]"
    )

    # Burned-in captions: chained drawtext filters, each windowed to its
    # own time range, centered in the lower-middle safe area. Multi-line
    # cues (see build_caption_chunks) join their lines with the literal
    # 2-character '\n' sequence drawtext renders as a manual line break;
    # text_w/text_h reflect the full (possibly 2-line) block, so the block
    # stays centered and the y anchor stays fixed regardless of line count.
    caption_label = "vraw"

    for i, cue in enumerate(caption_cues):

        next_label = f"vcap{i}"

        safe_text = "\\n".join(escape_drawtext(line) for line in cue["lines"])

        filters.append(
            f"[{caption_label}]drawtext="
            f"fontfile='{font_path.as_posix()}':"
            f"text='{safe_text}':"
            f"fontsize={CAPTION_FONT_SIZE}:fontcolor=white:"
            f"line_spacing=8:"
            f"box=1:boxcolor=black@0.55:boxborderw=24:"
            f"x=(w-text_w)/2:y=(h*{CAPTION_ANCHOR_Y_FRACTION})-(text_h/2):"
            f"enable='between(t,{cue['start']:.2f},{cue['end']:.2f})'"
            f"[{next_label}]"
        )

        caption_label = next_label

    video_out_label = caption_label

    # Audio: narration always present; optional low-volume music mixed
    # underneath, never louder than narration (default ~10%).
    if music_input_index is not None:

        filters.append(f"[{narration_input_index}:a]volume=1.0[narr]")
        filters.append(f"[{music_input_index}:a]volume={music_volume}[music]")
        filters.append(
            "[narr][music]amix=inputs=2:duration=first:dropout_transition=2[aout]"
        )

        audio_out_label = "aout"

    else:

        filters.append(f"[{narration_input_index}:a]anull[aout]")

        audio_out_label = "aout"

    filter_complex = ";".join(filters)

    total_duration = sum(scene_durations)

    command = [
        FFMPEG_BIN, "-y",
        *inputs,
        "-filter_complex", filter_complex,
        "-map", f"[{video_out_label}]",
        "-map", f"[{audio_out_label}]",
        "-c:v", "libx264",
        "-c:a", "aac",
        "-pix_fmt", "yuv420p",
        "-r", str(fps),
        "-t", f"{total_duration:.3f}",
        "-movflags", "+faststart",
        str(output_path),
    ]

    return command


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
# Music (local, royalty-free tracks only -- no downloads, no AI music)
# =====================================================================

MUSIC_DIR = Path("assets/music")


def list_music_tracks():

    if not MUSIC_DIR.exists():
        return []

    return sorted(MUSIC_DIR.glob("*.mp3"))


# =====================================================================
# Orchestration
# =====================================================================

class ReelService:

    def __init__(self):

        self.library = ContentLibraryService()
        self.tts = OpenAITTSService()
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

        cached_text = (
            narration_text_path.read_text(encoding="utf-8")
            if narration_text_path.exists() else None
        )

        already_valid = (
            narration_path.exists()
            and narration_path.stat().st_size > 0
            and cached_text == script["full_narration"]
        )

        if already_valid and not force:
            print(f"Reusing existing Reel narration: {narration_path}")
            return narration_path

        try:
            self.tts.generate(text=script["full_narration"], output_file=narration_path)
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

        # Records exactly what text this audio narrates, so a future run
        # only reuses it if the script hasn't changed in the meantime.
        narration_text_path.write_text(script["full_narration"], encoding="utf-8")

        return narration_path

    # -----------------------------------------------------------------
    # Main entry point
    # -----------------------------------------------------------------

    def generate(self, content_id, overwrite=False, music_track=None, force_narration=False):

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

        save_reel_script(script, folder)

        print("🎙️ Generating Reel narration...")

        narration_path = self._get_or_generate_narration(folder, script, force=force_narration)

        print("✅ Narration generated.")
        print()

        segments = script["segments"]
        word_counts = [len(segment["text"].split()) for segment in segments]
        scene_durations = compute_scene_durations(word_counts, script["duration_target"])

        scene_images = materialize_scene_images(segments, hero_image_path, story, folder)

        caption_cues = build_caption_cues(segments, scene_durations, self.font_path)

        music_path = None

        if music_track is not None:
            candidate = MUSIC_DIR / music_track
            if candidate.exists():
                music_path = candidate

        command = build_ffmpeg_command(
            scene_images=scene_images,
            scene_durations=scene_durations,
            narration_path=narration_path,
            output_path=output_path,
            caption_cues=caption_cues,
            font_path=self.font_path,
            music_path=music_path,
        )

        print("🎞️ Rendering Reel with FFmpeg...")

        try:
            run_ffmpeg_command(command)
        except (FFmpegNotAvailableError, ReelGenerationError):
            if output_path.exists():
                output_path.unlink()
            raise

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
