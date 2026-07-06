from typing import List
from pydantic import BaseModel


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


class CharacterSheet(BaseModel):
    main_character: MainCharacter


class Cover(BaseModel):
    prompt: str
    negative_prompt: str
    style: str
    title_position: str


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

class PublishingPack(BaseModel):
    instagram_caption_short: str
    instagram_caption_long: str
    hashtags: List[str]
    first_comment: str
    alt_text: str
    call_to_action: str
    best_posting_time: str
    parent_question: str

class StoryPackage(BaseModel):
    story_info: StoryInfo
    character_sheet: CharacterSheet
    cover: Cover
    slides: List[Slide]
    instagram: Instagram
    email: Email
    youtube: YouTube
    publishing: PublishingPack