import base64
from pathlib import Path

from services.openai_service import OpenAIService

service = OpenAIService()

prompt = """
A fluffy white bunny wearing blue star pajamas,
sleeping peacefully inside a cozy burrow,
warm lantern light,
Pixar-quality 3D,
vertical composition,
Instagram cover,
high quality.
"""

print("Generating image...")

image_b64 = service.generate_cover(prompt)

output_dir = Path("output/images")
output_dir.mkdir(parents=True, exist_ok=True)

image_path = output_dir / "cover.png"

with open(image_path, "wb") as f:
    f.write(base64.b64decode(image_b64))

print(f"Image saved to {image_path}")