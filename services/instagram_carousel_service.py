"""Builds an Instagram image CAROUSEL (never a Reel/video) from a story
the daily pipeline already generated -- see .github/workflows/
publish-story-instagram.yml and services/instagram_publisher.py.

Reuses the story's own generated images exactly as the daily pipeline
saved them (services/asset_manager.py):

    output/<folder>/cover_final.png   (falls back to cover.png)
    output/<folder>/slide_1.png ... slide_N.png

Nothing is regenerated and no Reel asset is ever used.

The ONLY adaptation made: Instagram's Content Publishing API accepts
JPEG only (Meta docs, IG User Media reference: "Format: JPEG", 8 MB
max, aspect ratio within 4:5..1.91:1, width 320..1440, sRGB). The daily
pipeline saves PNGs, so each image is re-encoded to a temporary JPEG --
same pixels, same 1080x1350 (4:5) size. Padding/resizing only happens
if a future image ever falls outside Instagram's limits, which today's
CarouselRenderer/CoverDesigner (both 1080x1350) never produce.
"""
import hashlib
import json
import re
from pathlib import Path

from PIL import Image

from services.brand_loader import BrandLoader


# Current Meta limits (Content Publishing + IG User Media reference).
MIN_CAROUSEL_ITEMS = 2
MAX_CAROUSEL_ITEMS = 20
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MIN_ASPECT_RATIO = 4 / 5
MAX_ASPECT_RATIO = 1.91
MIN_WIDTH = 320
MAX_WIDTH = 1440

MAX_CAPTION_CHARS = 2200
MAX_HASHTAGS = 30

# Kept well under MAX_HASHTAGS on purpose -- a short, relevant set.
CAPTION_HASHTAG_LIMIT = 15

_DEFAULT_HASHTAGS = (
    "#Bedtime01Fables",
    "#BedtimeStories",
    "#KidsStories",
    "#StoryTime",
    "#MoralStories",
)

_HASHTAG_PATTERN = re.compile(r"#\w+", re.UNICODE)


class CarouselAssetError(RuntimeError):
    pass


def _slide_number(path: Path):
    digits = "".join(ch for ch in path.stem if ch.isdigit())
    return int(digits) if digits else 0


def select_carousel_images(folder: Path):
    """Returns [cover, slide_1, slide_2, ...] -- the intended reading
    order. Slides sort numerically (slide_10 after slide_9), matching
    services/reel_service.discover_story_images. Only slide_<n>.png
    files count, so Reel files such as reel_scene_01.png never leak in.

    Raises CarouselAssetError if the cover or slides are missing or the
    count is outside Instagram's carousel limits."""

    folder = Path(folder)

    if not folder.is_dir():
        raise CarouselAssetError(f"Story folder not found: {folder}")

    cover = folder / "cover_final.png"

    if not cover.exists():
        cover = folder / "cover.png"

    if not cover.exists():
        raise CarouselAssetError(
            f"Story folder {folder} has no cover_final.png or cover.png."
        )

    slides = sorted(
        (p for p in folder.glob("slide_*.png") if re.fullmatch(r"slide_\d+", p.stem)),
        key=_slide_number,
    )

    if not slides:
        raise CarouselAssetError(
            f"Story folder {folder} has no slide_<n>.png images."
        )

    numbers = [_slide_number(p) for p in slides]

    if numbers != list(range(1, len(numbers) + 1)):
        raise CarouselAssetError(
            f"Story folder {folder} has non-contiguous slides {numbers}; "
            f"refusing to publish an out-of-order carousel."
        )

    images = [cover] + slides

    validate_carousel_count(len(images))

    return images


def validate_carousel_count(count):

    if count < MIN_CAROUSEL_ITEMS:
        raise CarouselAssetError(
            f"A carousel needs at least {MIN_CAROUSEL_ITEMS} images, got {count}."
        )

    if count > MAX_CAROUSEL_ITEMS:
        raise CarouselAssetError(
            f"Instagram carousels are limited to {MAX_CAROUSEL_ITEMS} images, "
            f"got {count}. Refusing to silently drop story pages."
        )


def _pad_to_ratio(image, ratio, fill):
    """Letterboxes (never crops) `image` to `ratio` (width/height)."""

    width, height = image.size

    if width / height < ratio:
        new_size = (round(height * ratio), height)
    else:
        new_size = (width, round(width / ratio))

    canvas = Image.new("RGB", new_size, fill)
    canvas.paste(image, ((new_size[0] - width) // 2, (new_size[1] - height) // 2))
    return canvas


def prepare_jpeg(source: Path, destination: Path, target_ratio=None, fill="#F6EACB"):
    """Re-encodes one story PNG as an Instagram-compliant JPEG.

    Returns a dict describing the result (path, size, bytes, adapted)."""

    with Image.open(source) as opened:
        image = opened.convert("RGBA")

    flattened = Image.new("RGB", image.size, "white")
    flattened.paste(image, mask=image.split()[3])
    image = flattened

    adapted = []

    width, height = image.size
    ratio = width / height

    if target_ratio is None:
        target_ratio = min(max(ratio, MIN_ASPECT_RATIO), MAX_ASPECT_RATIO)

    if abs(ratio - target_ratio) > 0.005:
        image = _pad_to_ratio(image, target_ratio, fill)
        adapted.append(f"padded to aspect {target_ratio:.3f}")

    if image.size[0] > MAX_WIDTH:
        scale = MAX_WIDTH / image.size[0]
        image = image.resize((MAX_WIDTH, round(image.size[1] * scale)), Image.LANCZOS)
        adapted.append(f"resized to width {MAX_WIDTH}")

    if image.size[0] < MIN_WIDTH:
        scale = MIN_WIDTH / image.size[0]
        image = image.resize((MIN_WIDTH, round(image.size[1] * scale)), Image.LANCZOS)
        adapted.append(f"resized to width {MIN_WIDTH}")

    destination.parent.mkdir(parents=True, exist_ok=True)

    for quality in (92, 85, 75, 65):
        image.save(destination, "JPEG", quality=quality, optimize=True)
        if destination.stat().st_size <= MAX_IMAGE_BYTES:
            break
    else:
        raise CarouselAssetError(
            f"{source.name} is still over {MAX_IMAGE_BYTES} bytes as JPEG."
        )

    return {
        "source": str(source),
        "path": destination,
        "size": image.size,
        "bytes": destination.stat().st_size,
        "adapted": adapted,
    }


def prepare_carousel_jpegs(images, work_dir: Path):
    """Converts every selected image, in order, to work_dir/NN.jpg.
    Every slide is made to share the cover's aspect ratio because
    Instagram crops all carousel items to the first image's ratio."""

    work_dir = Path(work_dir)
    prepared = []
    target_ratio = None

    for index, source in enumerate(images, start=1):

        result = prepare_jpeg(
            Path(source),
            work_dir / f"{index:02d}.jpg",
            target_ratio=target_ratio,
        )

        if target_ratio is None:
            target_ratio = result["size"][0] / result["size"][1]

        prepared.append(result)

    return prepared


# ---------------------------------------------------------------------
# Caption
# ---------------------------------------------------------------------

def load_story_json(folder: Path):
    return json.loads((Path(folder) / "story.json").read_text(encoding="utf-8"))


def _strip_hashtags(text):
    return re.sub(r"\s+", " ", _HASHTAG_PATTERN.sub("", text or "")).strip()


def _clean_hashtag(tag):

    tag = (tag or "").strip()

    if not tag:
        return None

    if not tag.startswith("#"):
        tag = "#" + tag

    match = _HASHTAG_PATTERN.fullmatch(tag.replace(" ", ""))

    return match.group(0) if match else None


def build_hashtags(story):

    candidates = []
    candidates += story.get("instagram", {}).get("hashtags", []) or []
    candidates += story.get("publishing", {}).get("hashtags", []) or []
    candidates += list(_DEFAULT_HASHTAGS)

    seen = set()
    tags = []

    for raw in candidates:

        tag = _clean_hashtag(raw)

        if not tag or tag.lower() in seen:
            continue

        seen.add(tag.lower())
        tags.append(tag)

    return tags[:CAPTION_HASHTAG_LIMIT]


def build_caption(story, brand=None):
    """Title, short description, a child-friendly lesson, a swipe prompt,
    a follow line and hashtags. Deterministic for a given story.json --
    the publisher relies on that to recognise its own post when
    recovering from an uncertain publish result."""

    brand = brand or BrandLoader.load()

    info = story.get("story_info", {})
    publishing = story.get("publishing", {})

    title = (info.get("title") or "").strip()

    description = _strip_hashtags(
        publishing.get("instagram_caption_short")
        or info.get("subtitle")
        or story.get("instagram", {}).get("caption", "")
    )

    lesson = (info.get("moral") or "").strip()
    footer = brand.get("footer_text", "").strip()
    hashtags = build_hashtags(story)

    lines = [f"📖 {title}"]

    if description:
        lines += ["", description]

    if lesson:
        lines += ["", f"🌟 Lesson: {lesson}"]

    lines += ["", "👉 Swipe to read the whole story with your little one."]

    if footer:
        lines += [footer]

    hashtag_line = " ".join(hashtags)

    caption = "\n".join(lines) + "\n\n" + hashtag_line

    if len(caption) > MAX_CAPTION_CHARS and description:
        overflow = len(caption) - MAX_CAPTION_CHARS + 1
        lines[lines.index(description)] = (
            description[: max(0, len(description) - overflow)].rstrip() + "…"
        )
        caption = "\n".join(lines) + "\n\n" + hashtag_line

    if len(caption) > MAX_CAPTION_CHARS:
        raise CarouselAssetError("Caption is longer than Instagram allows.")

    if caption.count("@") > 20:
        raise CarouselAssetError("Caption has more than 20 @ tags.")

    return caption


def caption_fingerprint(caption):
    """Whitespace-insensitive hash, so recovery can match our caption
    against what Instagram returns even if it normalises line breaks."""

    normalised = " ".join((caption or "").split())
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()
