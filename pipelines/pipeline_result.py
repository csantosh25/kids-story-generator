from dataclasses import dataclass
from pathlib import Path

from models.story_models import StoryPackage


@dataclass
class PipelineResult:

    story: StoryPackage

    output_folder: Path

    cover_image: Path