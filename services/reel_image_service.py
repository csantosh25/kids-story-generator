import base64
import hashlib
import json
import re
from pathlib import Path

from openai import BadRequestError

from services.openai_service import OpenAIService


class ReelImageGenerationError(RuntimeError):
    pass


# Hard cost-control ceiling: existing cover + at most this many NEW
# illustrations per Reel. Enforced defensively even though
# select_beat_indices() (services/reel_service.py) already caps the
# number of beats at this same value.
MAX_REEL_SCENE_IMAGES = 3

SCENE_CACHE_FILENAME = "reel_scenes.json"

_ROLE_FIRST = "Problem / situation"
_ROLE_MIDDLE = "Action"
_ROLE_LAST = "Resolution / happy moment"
_ROLE_ONLY = "Key story moment"


def _scene_role(position, total):
    """Maps a beat's position (0-indexed) in the selected sequence to its
    narrative role -- problem, action, or resolution -- matching the
    story's own natural arc (the beats themselves are picked by
    select_beat_indices() as first/middle/last real story slides)."""

    if total <= 1:
        return _ROLE_ONLY

    if position == 0:
        return _ROLE_FIRST

    if position == total - 1:
        return _ROLE_LAST

    return _ROLE_MIDDLE


def _mentions_character(text, name):

    if not name:
        return False

    return re.search(rf"\b{re.escape(name)}\b", text or "", re.IGNORECASE) is not None


def relevant_supporting_characters(slide_text, supporting_characters):
    """Returns only the supporting characters whose name is actually
    mentioned in THIS slide's text -- so a scene only includes a
    supporting character where the real story text says they're present,
    rather than forcing every supporting character into every scene."""

    return [
        character for character in supporting_characters
        if _mentions_character(slide_text, character.name)
    ]


def _supporting_character_block(characters):

    if not characters:
        return "No supporting character in this scene. Show only the main character."

    sections = []

    for character in characters:

        role = character.role or "a close friend in this story"

        sections.append(
            f"Name: {character.name}\n"
            f"Species: {character.species}\n"
            f"Appearance: {character.appearance}\n"
            f"Role: {role}"
        )

    return (
        "Include this/these supporting character(s), interacting "
        "naturally with the main character. Preserve their exact "
        "appearance, species, and colors. Do not invent any character "
        "beyond those listed here.\n\n" + "\n\n".join(sections)
    )


def build_scene_prompt(story, slide_index, scene_text, position, total):
    """Builds ONE Reel scene illustration prompt, grounded entirely in
    this story's own canonical data:

    - the main character's EXACT canonical appearance (never rewritten,
      shortened, or paraphrased)
    - only supporting characters actually mentioned in this specific
      slide's real text
    - the slide's own title/visual_theme (already-authored structured
      fields, same philosophy as CoverPromptBuilder) plus `scene_text`,
      which callers pass in as the same sentence-bounded excerpt already
      used for this beat's narration/captions -- so the image, the
      narration, and the caption for a given beat all describe the same
      real moment (never invented).

    Deliberately mirrors CoverPromptBuilder's structure (character block,
    strict content requirements, negative prompt) for visual/style
    consistency with the existing cover, without importing from or
    modifying that cover-only module."""

    character = story.character_sheet.main_character
    slide = story.slides[slide_index]

    supporting = relevant_supporting_characters(
        slide.text, story.character_sheet.supporting_characters
    )
    supporting_block = _supporting_character_block(supporting)

    role = _scene_role(position, total)

    prompt = f"""
    Create a children's storybook illustration for ONE scene inside an
    ongoing story (not a book cover).

    CHILDREN'S STORYBOOK ILLUSTRATION

    Friendly animal characters. Wholesome, everyday setting. Warm,
    colorful, Pixar-quality 3D storybook style suitable for young
    children aged 4-7. This illustration must match the SAME character
    designs, species, colors, and friendly art style as the rest of this
    story's illustrations (including its cover) -- consistent
    proportions, consistent warm lighting and tone.

    MAIN CHARACTER (visually dominant)

    Name: {character.name}
    Species: {character.species}
    Appearance: {character.appearance}

    SUPPORTING CHARACTER(S)

    {supporting_block}

    SCENE MOMENT ({role})

    Scene title: {slide.title}
    Mood: {slide.visual_theme or "warm and gentle"}
    What happens in this moment: {scene_text}

    If this moment involves any problem, sadness, or conflict, depict it
    GENTLY and safely -- for example, a character looking a little
    worried or thoughtful. NEVER scared, threatened, trapped, hurt, or in
    danger. Every character must appear safe and cared for at all times.

    COMPOSITION

    Portrait orientation. The main character (and any listed supporting
    character) clearly visible, in natural proportion, performing the
    scene's action -- do not let any character fill the entire frame.
    Keep the background simple and readable, not cluttered.

    STRICT CONTENT REQUIREMENTS

    - Children's storybook illustration only, in a wholesome everyday setting.
    - Friendly animal characters, safe and gentle expressions.
    - No text, no title, no subtitle, no words, no letters, no numbers, no logo, no watermark.
    - No scary content, no violence, no injury, no weapons, no dangerous behavior, no adult themes.
    - Nothing frightening, threatening, or unsafe for a young child.

    NEGATIVE PROMPT

    text, title, subtitle, words, letters, numbers, logo, watermark,
    scary content, violence, weapons, blood, injury, dangerous behavior,
    adult content, photorealism, 3D realism, anime style, dark or scary
    imagery, extra unrelated characters, random unrelated objects
    """

    return prompt.strip()


def build_fallback_scene_prompt(story):
    """A substantially simpler, more generic prompt used only after the
    primary prompt's OUTPUT was blocked by the provider's moderation
    system -- mirrors CoverPromptBuilder.build_fallback's approach.
    Deliberately avoids proper names/story specifics to maximize the
    chance of a safe, benign result."""

    character = story.character_sheet.main_character

    return (
        f"A wholesome children's storybook illustration of a friendly "
        f"{character.species.lower()}, in a bright, gentle everyday "
        f"setting, looking happy and safe. Warm daylight. Soft, colorful "
        f"3D storybook style. No text, no words, no letters, no numbers, "
        f"no logo, no watermark. No scary content, no violence, no "
        f"weapons."
    )


def _prompt_hash(prompt):

    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]


def load_scene_cache(folder: Path):

    path = folder / SCENE_CACHE_FILENAME

    if not path.exists():
        return {"content_id": None, "scenes": []}

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"content_id": None, "scenes": []}


def save_scene_cache(folder: Path, cache):

    path = folder / SCENE_CACHE_FILENAME

    path.write_text(
        json.dumps(cache, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    return path


def _cached_entry(cache, content_id, slide_index):

    if cache.get("content_id") != content_id:
        return None

    for entry in cache.get("scenes", []):
        if entry.get("slide_index") == slide_index:
            return entry

    return None


def _upsert_cache_entry(cache, content_id, entry):

    cache["content_id"] = content_id

    scenes = cache.setdefault("scenes", [])

    for i, existing in enumerate(scenes):
        if existing.get("slide_index") == entry["slide_index"]:
            scenes[i] = entry
            return

    scenes.append(entry)


class ReelImageService:
    """Generates (and caches) a small number of dedicated, illustrated
    Reel scenes -- separate from, and never touching, the daily cover
    generator. Reuses the project's existing OpenAI image-generation
    client/config (services.openai_service.OpenAIService) rather than
    creating a second image-generation client."""

    def __init__(self, openai_service=None):

        self.service = openai_service or OpenAIService()

    def ensure_scenes(self, story, content_id, beat_indices, beat_texts, folder: Path, force=False):
        """Ensures an illustrated PNG exists for each of `beat_indices`
        (paired 1:1, in order, with `beat_texts`), reusing a cached image
        whenever the story/scene/prompt haven't changed since it was
        generated, and calling the image API only for scenes that are
        new or changed.

        Returns an ordered list of {"slide_index", "image_path"} dicts.

        Raises ReelImageGenerationError if a required scene can't be
        generated -- callers should treat this exactly like a TTS or
        ffmpeg failure: the whole Reel attempt fails and the Content
        Library must not be updated."""

        if len(beat_indices) != len(beat_texts):
            raise ValueError("beat_indices and beat_texts must be the same length")

        if len(beat_indices) > MAX_REEL_SCENE_IMAGES:
            raise ReelImageGenerationError(
                f"Requested {len(beat_indices)} Reel scenes, exceeding the "
                f"hard cap of {MAX_REEL_SCENE_IMAGES} image-generation "
                f"calls per Reel."
            )

        cache = load_scene_cache(folder)

        total = len(beat_indices)
        api_calls = 0
        reused = 0
        results = []

        for position, (slide_index, scene_text) in enumerate(zip(beat_indices, beat_texts)):

            prompt = build_scene_prompt(story, slide_index, scene_text, position, total)
            prompt_hash = _prompt_hash(prompt)

            scene_number = position + 1
            image_path = folder / f"reel_scene_{scene_number:02d}.png"

            cached = _cached_entry(cache, content_id, slide_index)

            already_valid = (
                cached is not None
                and cached.get("prompt_hash") == prompt_hash
                and image_path.exists()
                and image_path.stat().st_size > 0
            )

            if already_valid and not force:
                reused += 1
                results.append({"slide_index": slide_index, "image_path": image_path})
                continue

            image_bytes = self._generate_image_bytes(story, prompt)
            image_path.write_bytes(image_bytes)
            api_calls += 1

            _upsert_cache_entry(cache, content_id, {
                "scene_number": scene_number,
                "content_id": content_id,
                "slide_index": slide_index,
                "description": scene_text,
                "prompt_hash": prompt_hash,
                "image_path": image_path.name,
            })

            results.append({"slide_index": slide_index, "image_path": image_path})

        save_scene_cache(folder, cache)

        print(f"   New Reel scenes: {len(results)}")
        print(f"   Image API calls: {api_calls}")

        if reused:
            print(f"   Reused scenes: {reused}")

        return results

    def _generate_image_bytes(self, story, prompt):

        try:

            image_b64 = self.service.generate_image(prompt)

        except BadRequestError as error:

            if not OpenAIService.is_moderation_blocked(error):
                raise ReelImageGenerationError(
                    f"Reel scene image generation failed: {error}"
                ) from error

            print("⚠️ Reel scene image generation was blocked by the image safety system.")
            print("🔄 Trying a simplified safe scene prompt...")

            fallback_prompt = build_fallback_scene_prompt(story)

            try:

                image_b64 = self.service.generate_image(fallback_prompt)

            except BadRequestError as fallback_error:

                if not OpenAIService.is_moderation_blocked(fallback_error):
                    raise ReelImageGenerationError(
                        f"Reel scene image generation failed on fallback: {fallback_error}"
                    ) from fallback_error

                request_id = OpenAIService.extract_request_id(fallback_error)

                raise ReelImageGenerationError(
                    "Reel scene image generation was blocked by the "
                    "provider's safety system on both the primary and the "
                    "simplified fallback prompt. "
                    f"Request ID: {request_id or 'unavailable'}"
                ) from fallback_error

            print("✅ Reel scene generated using fallback visual prompt.")

        except Exception as error:

            raise ReelImageGenerationError(
                f"Reel scene image generation failed: {error}"
            ) from error

        return base64.b64decode(image_b64)
