from typing import List
from pydantic import BaseModel, Field, field_validator


class StoryInfo(BaseModel):
    title: str
    subtitle: str
    theme: str
    target_age: str
    reading_time: str
    moral: str


class MainCharacter(BaseModel):
    name: str
    species: str
    appearance: str
    personality: str


class SupportingCharacter(BaseModel):
    name: str
    species: str
    appearance: str
    role: str = ""


class CharacterSheet(BaseModel):
    main_character: MainCharacter
    # Optional and defaulted for backward compatibility: existing story.json
    # files and any code that builds a CharacterSheet without this field
    # continue to work unchanged.
    supporting_characters: List[SupportingCharacter] = Field(default_factory=list)


class Cover(BaseModel):
    prompt: str
    negative_prompt: str
    style: str
    title_position: str
    # Concise, structured fields used to build the actual image-generation
    # prompt (see CoverPromptBuilder), instead of reusing full slide text.
    # Defaulted for backward compatibility with older story data.
    setting: str = ""
    visual_action: str = ""
    visual_object: str = ""
    mood: str = ""


class Slide(BaseModel):
    page: int
    title: str
    text: str
    background_color: str
    visual_theme: str
    icon: str
    speaker_notes: str


class Instagram(BaseModel):
    caption: str
    hashtags: List[str]


class Email(BaseModel):
    subject: str
    preview: str


class YouTube(BaseModel):
    title: str
    description: str
    keywords: List[str]


DEFAULT_PARENT_QUESTION = (
    "What was your favorite part of the story, and what would you have "
    "done if you were there?"
)


class PublishingPack(BaseModel):
    # Defaulted for backward compatibility: story.json files generated
    # before this field existed (e.g. reused by the Reel generator) can
    # still be loaded.
    hook: str = ""
    instagram_caption_short: str
    instagram_caption_long: str
    hashtags: List[str]
    first_comment: str
    alt_text: str
    call_to_action: str
    best_posting_time: str
    # Defaulted for backward compatibility: Gemini occasionally omits this
    # field (or returns it null/blank), which previously failed the whole
    # StoryPackage. A model-provided question is always kept as-is.
    parent_question: str = DEFAULT_PARENT_QUESTION

    @field_validator("parent_question", mode="before")
    @classmethod
    def _default_blank_parent_question(cls, value):
        if value is None or (isinstance(value, str) and not value.strip()):
            return DEFAULT_PARENT_QUESTION
        return value

class StoryPackage(BaseModel):
    story_info: StoryInfo
    character_sheet: CharacterSheet
    cover: Cover
    slides: List[Slide]
    instagram: Instagram
    email: Email
    youtube: YouTube
    publishing: PublishingPack