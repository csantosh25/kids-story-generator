"""Tests for services/instagram_carousel_service.py -- image selection,
carousel ordering/count validation, JPEG preparation and captions."""
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image

from services.instagram_carousel_service import (
    MAX_CAROUSEL_ITEMS,
    MAX_CAPTION_CHARS,
    MAX_HASHTAGS,
    CarouselAssetError,
    build_caption,
    build_hashtags,
    caption_fingerprint,
    prepare_carousel_jpegs,
    select_carousel_images,
    validate_carousel_count,
)


BRAND = {"footer_text": "Follow for more bedtime stories 🌙"}


def make_story_json(title="Bella Bear Helps Grandma"):
    return {
        "story_info": {
            "title": title,
            "subtitle": "A Cozy Afternoon of Kindness",
            "theme": "Kindness and Family",
            "moral": "Helping others, especially family, makes everyone happy.",
        },
        "instagram": {
            "caption": "Bella loves helping! #KidsStories",
            "hashtags": ["#Bedtime01Fables", "#BellaBear", "#KidsStories"],
        },
        "publishing": {
            "instagram_caption_short": "Bella helps Grandma sort her yarn. #FamilyTime",
            "hashtags": ["#kidsstories", "KindnessMatters", "#Family Time", "#"],
        },
    }


def write_story_folder(folder: Path, slides=5, size=(432, 540), cover="cover_final.png",
                       extra_files=(), story=None):
    """A real story folder laid out exactly like services/asset_manager.py
    writes it, with small but real PNGs."""

    folder.mkdir(parents=True, exist_ok=True)
    (folder / "story.json").write_text(
        json.dumps(story or make_story_json(), ensure_ascii=False), encoding="utf-8"
    )

    if cover:
        Image.new("RGB", size, "#5B4B8A").save(folder / cover)

    for page in range(1, slides + 1):
        Image.new("RGB", size, "#F6EACB").save(folder / f"slide_{page}.png")

    for name in extra_files:
        Image.new("RGB", (100, 100), "red").save(folder / name)

    return folder


class SelectCarouselImagesTests(unittest.TestCase):

    def test_cover_then_slides_in_numeric_reading_order(self):
        with TemporaryDirectory() as tmp:
            folder = write_story_folder(Path(tmp) / "story", slides=11)

            images = select_carousel_images(folder)

            self.assertEqual(images[0].name, "cover_final.png")
            self.assertEqual(
                [p.name for p in images[1:]],
                [f"slide_{n}.png" for n in range(1, 12)],  # slide_10 after slide_9
            )

    def test_ignores_reel_and_non_slide_files(self):
        with TemporaryDirectory() as tmp:
            folder = write_story_folder(
                Path(tmp) / "story", slides=3,
                extra_files=("reel_scene_01.png", "reel_scene_cover.png",
                             "slide_2_fullbleed.png", "cover.png"),
            )

            names = [p.name for p in select_carousel_images(folder)]

            self.assertEqual(names, ["cover_final.png", "slide_1.png", "slide_2.png", "slide_3.png"])

    def test_falls_back_to_raw_cover(self):
        with TemporaryDirectory() as tmp:
            folder = write_story_folder(Path(tmp) / "s", slides=2, cover="cover.png")
            self.assertEqual(select_carousel_images(folder)[0].name, "cover.png")

    def test_missing_cover_fails(self):
        with TemporaryDirectory() as tmp:
            folder = write_story_folder(Path(tmp) / "s", slides=2, cover=None)
            with self.assertRaises(CarouselAssetError):
                select_carousel_images(folder)

    def test_missing_slides_fails(self):
        with TemporaryDirectory() as tmp:
            folder = write_story_folder(Path(tmp) / "s", slides=0)
            with self.assertRaises(CarouselAssetError):
                select_carousel_images(folder)

    def test_gap_in_slides_fails(self):
        with TemporaryDirectory() as tmp:
            folder = write_story_folder(Path(tmp) / "s", slides=4)
            (folder / "slide_2.png").unlink()
            with self.assertRaisesRegex(CarouselAssetError, "non-contiguous"):
                select_carousel_images(folder)

    def test_missing_folder_fails(self):
        with self.assertRaises(CarouselAssetError):
            select_carousel_images(Path("does/not/exist"))


class CarouselCountTests(unittest.TestCase):

    def test_limits(self):
        validate_carousel_count(2)
        validate_carousel_count(MAX_CAROUSEL_ITEMS)

        with self.assertRaises(CarouselAssetError):
            validate_carousel_count(1)

        with self.assertRaisesRegex(CarouselAssetError, "Refusing to silently drop"):
            validate_carousel_count(MAX_CAROUSEL_ITEMS + 1)

    def test_too_many_slides_rejected_not_truncated(self):
        with TemporaryDirectory() as tmp:
            folder = write_story_folder(Path(tmp) / "s", slides=MAX_CAROUSEL_ITEMS, size=(40, 50))
            with self.assertRaises(CarouselAssetError):
                select_carousel_images(folder)


class PrepareJpegTests(unittest.TestCase):

    def test_png_becomes_same_size_jpeg_in_order(self):
        with TemporaryDirectory() as tmp:
            folder = write_story_folder(Path(tmp) / "s", slides=3)
            prepared = prepare_carousel_jpegs(select_carousel_images(folder), Path(tmp) / "work")

            self.assertEqual([p["path"].name for p in prepared], ["01.jpg", "02.jpg", "03.jpg", "04.jpg"])

            for item in prepared:
                with Image.open(item["path"]) as image:
                    self.assertEqual(image.format, "JPEG")
                    self.assertEqual(image.mode, "RGB")
                    self.assertEqual(image.size, (432, 540))
                self.assertEqual(item["adapted"], [])

            # The original story assets are untouched.
            self.assertTrue((folder / "slide_1.png").exists())
            self.assertFalse(list(folder.glob("*.jpg")))

    def test_out_of_range_aspect_is_padded_not_cropped(self):
        with TemporaryDirectory() as tmp:
            folder = Path(tmp) / "s"
            folder.mkdir()
            Image.new("RGB", (500, 1000), "blue").save(folder / "tall.png")   # 1:2, too tall

            prepared = prepare_carousel_jpegs([folder / "tall.png"], Path(tmp) / "w")

            width, height = prepared[0]["size"]
            self.assertAlmostEqual(width / height, 0.8, places=2)
            self.assertEqual(height, 1000)
            self.assertTrue(prepared[0]["adapted"])

    def test_wide_images_are_downscaled_and_slides_match_cover_ratio(self):
        with TemporaryDirectory() as tmp:
            folder = Path(tmp) / "s"
            folder.mkdir()
            Image.new("RGB", (2000, 2000), "blue").save(folder / "a.png")
            Image.new("RGBA", (1080, 1350), (0, 0, 0, 0)).save(folder / "b.png")

            first, second = prepare_carousel_jpegs([folder / "a.png", folder / "b.png"], Path(tmp) / "w")

            self.assertEqual(first["size"], (1440, 1440))
            self.assertAlmostEqual(second["size"][0] / second["size"][1], 1.0, places=2)


class CaptionTests(unittest.TestCase):

    def test_caption_contents(self):
        caption = build_caption(make_story_json(), brand=BRAND)

        self.assertTrue(caption.startswith("📖 Bella Bear Helps Grandma"))
        self.assertIn("Bella helps Grandma sort her yarn.", caption)
        self.assertIn("🌟 Lesson: Helping others, especially family, makes everyone happy.", caption)
        self.assertIn("Swipe", caption)
        self.assertIn("Follow for more bedtime stories", caption)
        self.assertLessEqual(len(caption), MAX_CAPTION_CHARS)

        body, hashtag_line = caption.rsplit("\n\n", 1)
        self.assertNotIn("#", body)  # description hashtags moved to the tag line
        self.assertIn("#BellaBear", hashtag_line)

    def test_hashtags_deduplicated_cleaned_and_capped(self):
        tags = build_hashtags(make_story_json())

        lowered = [t.lower() for t in tags]
        self.assertEqual(len(lowered), len(set(lowered)))
        self.assertIn("#KindnessMatters", tags)
        self.assertIn("#FamilyTime", tags)
        self.assertNotIn("#", tags)
        self.assertLessEqual(len(tags), MAX_HASHTAGS)

        story = make_story_json()
        story["publishing"]["hashtags"] = [f"#tag{n}" for n in range(60)]
        self.assertLessEqual(len(build_hashtags(story)), MAX_HASHTAGS)

    def test_overlong_description_is_truncated(self):
        story = make_story_json()
        story["publishing"]["instagram_caption_short"] = "word " * 1000

        caption = build_caption(story, brand=BRAND)

        self.assertLessEqual(len(caption), MAX_CAPTION_CHARS)
        self.assertIn("🌟 Lesson:", caption)

    def test_deterministic_and_fingerprint_ignores_whitespace(self):
        a = build_caption(make_story_json(), brand=BRAND)
        b = build_caption(make_story_json(), brand=BRAND)

        self.assertEqual(a, b)
        self.assertEqual(caption_fingerprint(a), caption_fingerprint(a.replace("\n", " \n ")))
        self.assertNotEqual(
            caption_fingerprint(a),
            caption_fingerprint(build_caption(make_story_json("Other"), brand=BRAND)),
        )


if __name__ == "__main__":
    unittest.main()
