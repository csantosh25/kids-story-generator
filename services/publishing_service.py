from pathlib import Path


class PublishingService:

    def generate(self, story, assets):

        file = assets.output_folder / "publish.md"

        hashtags = " ".join(story.publishing.hashtags)

        content = f"""# {story.story_info.title}

## Theme

{story.story_info.theme}

---

## Moral

{story.story_info.moral}

---

## Instagram Caption (Short)

{story.publishing.instagram_caption_short}

---

## Instagram Caption (Long)

{story.publishing.instagram_caption_long}

---

## Hashtags

{hashtags}

---

## First Comment

{story.publishing.first_comment}

---

## Alt Text

{story.publishing.alt_text}

---

## Call To Action

{story.publishing.call_to_action}

---

## Parent Discussion Question

{story.publishing.parent_question}

---

## Best Posting Time

{story.publishing.best_posting_time}
"""

        file.write_text(content, encoding="utf-8")

        print(f"✅ Publishing guide saved: {file}")

        return file