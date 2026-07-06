from pydantic import BaseModel


class CharacterSheet(BaseModel):
    name: str
    appearance: str


class Scene(BaseModel):
    scene: int
    text: str
    background_color: str
    image_prompt: str


class Story(BaseModel):
    title: str
    story: str
    moral: str
    music: str
    voiceover: str
    cover_image_prompt: str
    character_sheet: CharacterSheet
    scenes: list[Scene]