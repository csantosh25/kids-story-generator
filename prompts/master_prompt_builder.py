from pathlib import Path


class PromptBuilder:

    def build(self):

        files = [
            "system_prompt.txt",
            "writing_rules.txt",
            "character_rules.txt",
            "image_rules.txt",
            "social_media_rules.txt",
            "output_schema.txt",
        ]

        prompt = ""

        for file in files:

            prompt += Path(
                f"prompts/{file}"
            ).read_text(encoding="utf-8")

            prompt += "\n\n"

        return prompt