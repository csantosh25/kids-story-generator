import json
import shutil
import subprocess
from pathlib import Path

from models.story_models import StoryPackage
from services.brand_loader import BrandLoader
from services.content_library_service import ContentLibraryService
from services.openai_tts_service import OpenAITTSService


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
    failing with an obscure downstream error."""

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


# =====================================================================
# Reel script (NO additional AI call -- built deterministically from the
# already-generated StoryPackage data)
# =====================================================================

DEFAULT_HOOK_WORD_LIMIT = 15
DEFAULT_PROBLEM_WORD_LIMIT = 18
DEFAULT_ADVENTURE_WORD_LIMIT = 16
DEFAULT_PAYOFF_WORD_LIMIT = 12
DEFAULT_CTA_WORD_LIMIT = 20

MIN_TARGET_WORDS = 60
MAX_TARGET_WORDS = 90

MIN_DURATION_SECONDS = 20
MAX_DURATION_SECONDS = 35

WORDS_PER_SECOND = 2.5  # ~150 wpm average narration pace


def trim_to_words(text, max_words):

    text = (text or "").strip()

    if not text:
        return ""

    words = text.split()

    if len(words) <= max_words:
        return text

    return " ".join(words[:max_words]) + "..."


def _pick_middle_slide_text(slides):

    index = len(slides) // 2

    return slides[index].text.strip()


def build_reel_script(story: StoryPackage, instagram_handle: str = "@bedtime01fables"):
    """Builds a short discovery/teaser script from an already-generated
    StoryPackage. Makes no API calls -- purely reuses existing story text
    (title, publishing.hook, moral, slide excerpts)."""

    title = story.story_info.title
    character_name = story.character_sheet.main_character.name

    hook = trim_to_words(story.publishing.hook, DEFAULT_HOOK_WORD_LIMIT)

    if not hook:
        hook = trim_to_words(
            f"Something happens to {character_name} today...",
            DEFAULT_HOOK_WORD_LIMIT,
        )

    problem_excerpt = trim_to_words(story.slides[0].text, DEFAULT_PROBLEM_WORD_LIMIT)
    adventure_excerpt = trim_to_words(
        _pick_middle_slide_text(story.slides), DEFAULT_ADVENTURE_WORD_LIMIT
    )

    story_segment = " ".join(part for part in [problem_excerpt, adventure_excerpt] if part)

    payoff = trim_to_words(story.story_info.moral, DEFAULT_PAYOFF_WORD_LIMIT)

    if not payoff:
        payoff = "Then everything felt better."

    cta = trim_to_words(
        f"Would you help a friend too? Read the full story of "
        f"{title} with your little one. Follow {instagram_handle} "
        f"for more little stories.",
        DEFAULT_CTA_WORD_LIMIT,
    )

    full_narration = " ".join(part for part in [hook, story_segment, payoff, cta] if part)

    word_count = len(full_narration.split())

    duration_target = max(
        MIN_DURATION_SECONDS,
        min(MAX_DURATION_SECONDS, round(word_count / WORDS_PER_SECOND)),
    )

    return {
        "hook": hook,
        "story": story_segment,
        "payoff": payoff,
        "cta": cta,
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

    for field in ("hook", "story", "payoff", "cta"):
        if not script.get(field, "").strip():
            warnings.append(f"'{field}' is empty.")

    return (len(warnings) == 0, warnings)


def save_reel_script(script, folder: Path) -> Path:

    path = folder / "reel_script.json"

    path.write_text(
        json.dumps(script, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    return path


# =====================================================================
# Scene selection & timing
# =====================================================================

DEFAULT_MAX_STORY_SLIDES_IN_REEL = 3  # + cover = up to 4 images total


def select_scene_images(cover_path: Path, slide_paths, max_slides=DEFAULT_MAX_STORY_SLIDES_IN_REEL):
    """Picks cover + an evenly-spaced subset of slide images (first,
    middle(s), last) so a 6-slide story doesn't cram 7 quick cuts into a
    ~25s teaser. Never regenerates or alters any image."""

    if not slide_paths:
        return [cover_path]

    if len(slide_paths) <= max_slides:
        chosen = list(slide_paths)
    else:
        total = len(slide_paths)
        positions = sorted(set(
            round(i * (total - 1) / (max_slides - 1)) for i in range(max_slides)
        ))
        chosen = [slide_paths[i] for i in positions]

    return [cover_path] + chosen


def compute_scene_durations(total_duration, image_count):
    """Cover gets a short, hook-length slice (avoids lingering on the
    title-heavy cover); remaining time is split evenly across the story
    images."""

    if image_count <= 1:
        return [total_duration]

    cover_duration = max(2.0, min(4.0, total_duration * 0.15))

    remaining = max(total_duration - cover_duration, image_count - 1)

    other_duration = remaining / (image_count - 1)

    return [round(cover_duration, 2)] + [round(other_duration, 2)] * (image_count - 1)


# =====================================================================
# Captions
# =====================================================================

DEFAULT_MAX_WORDS_PER_CAPTION = 6


def _chunk_words(words, max_words=DEFAULT_MAX_WORDS_PER_CAPTION):

    return [
        words[i:i + max_words]
        for i in range(0, len(words), max_words)
    ]


def build_caption_cues(script, total_duration, max_words_per_caption=DEFAULT_MAX_WORDS_PER_CAPTION):
    """Breaks the script into short, timed caption phrases (never a full
    paragraph at once), spread proportionally across the segments in the
    HOOK -> STORY -> PAYOFF -> CTA order."""

    segments = [
        ("hook", script["hook"]),
        ("story", script["story"]),
        ("payoff", script["payoff"]),
        ("cta", script["cta"]),
    ]

    total_words = sum(len(text.split()) for _, text in segments) or 1

    cues = []
    t = 0.0

    for _, text in segments:

        words = text.split()

        if not words:
            continue

        seg_duration = total_duration * (len(words) / total_words)

        chunks = _chunk_words(words, max_words_per_caption)

        chunk_duration = seg_duration / len(chunks)

        for chunk in chunks:

            start = round(t, 2)
            end = round(t + chunk_duration, 2)

            cues.append({
                "text": " ".join(chunk),
                "start": start,
                "end": end,
            })

            t += chunk_duration

    return cues


# =====================================================================
# FFmpeg command construction (pure string/list building -- testable
# without ever invoking ffmpeg)
# =====================================================================

def escape_drawtext(text):
    """Escapes text for safe use inside an ffmpeg drawtext filter, which
    is itself embedded inside a filter_complex string."""

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
    width=1080,
    height=1920,
    pad_color="0xF6EACB",
):
    """Builds the full ffmpeg argv list for a single-pass render: subtle
    zoompan motion per image, hard-cut concat, burned-in captions, and an
    optional low-volume background-music mix under the narration."""

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
            f"d={frames}:s=1080x1350:fps={fps},"
            f"pad={width}:{height}:0:(oh-ih)/2:color={pad_color},"
            f"setsar=1[v{i}]"
        )

        scene_labels.append(f"[v{i}]")

    concat_inputs = "".join(scene_labels)

    filters.append(
        f"{concat_inputs}concat=n={len(scene_labels)}:v=1:a=0[vraw]"
    )

    # Burned-in captions: chained drawtext filters, each windowed to its
    # own time range, centered in the lower-middle safe area.
    caption_label = "vraw"

    for i, cue in enumerate(caption_cues):

        next_label = f"vcap{i}"

        safe_text = escape_drawtext(cue["text"])

        filters.append(
            f"[{caption_label}]drawtext="
            f"fontfile='{font_path.as_posix()}':"
            f"text='{safe_text}':"
            f"fontsize=64:fontcolor=white:"
            f"box=1:boxcolor=black@0.5:boxborderw=24:"
            f"x=(w-text_w)/2:y=h*0.72:"
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


def probe_video_metadata(path: Path):
    """Best-effort ffprobe lookup for duration/width/height. Reel generation
    has already succeeded by the time this runs, so any failure here just
    means less metadata to print -- never raises."""

    ffprobe_bin = shutil.which("ffprobe")

    if ffprobe_bin is None:
        return {}

    try:
        result = subprocess.run(
            [
                ffprobe_bin,
                "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=width,height:format=duration",
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

    if streams and "width" in streams[0] and "height" in streams[0]:
        metadata["width"] = streams[0]["width"]
        metadata["height"] = streams[0]["height"]

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
    # reel narration already exists)
    # -----------------------------------------------------------------

    def _get_or_generate_narration(self, folder: Path, script, force=False):

        narration_path = folder / "reel_narration.mp3"

        if narration_path.exists() and narration_path.stat().st_size > 0 and not force:
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

        cover_path, slide_paths = discover_story_images(folder)

        story = load_story_package(folder)

        script = build_reel_script(story, instagram_handle=self.brand.get("instagram_handle", "@bedtime01fables"))

        save_reel_script(script, folder)

        narration_path = self._get_or_generate_narration(folder, script, force=force_narration)

        scene_images = select_scene_images(cover_path, slide_paths)
        scene_durations = compute_scene_durations(script["duration_target"], len(scene_images))
        total_duration = sum(scene_durations)

        caption_cues = build_caption_cues(script, total_duration)

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

        try:
            run_ffmpeg_command(command)
        except (FFmpegNotAvailableError, ReelGenerationError):
            if output_path.exists():
                output_path.unlink()
            raise

        if not output_path.exists() or output_path.stat().st_size == 0:
            raise ReelGenerationError(
                "ffmpeg reported success but no valid reel.mp4 was produced."
            )

        # Only now, after a verified successful render, update the
        # Content Library -- never mark reel.generated on a failed attempt.
        self.library.update_reel(content_id, output_path)

        return output_path
