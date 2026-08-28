import inspect
import unittest

import generate_reel
import services.reel_service as reel_service
import services.reel_image_service as reel_image_service


# Anything that would let the Reel path reach the daily story pipeline,
# email, Gemini, or the daily cover/carousel generators. If any of these
# strings show up in the Reel entry point or service modules' own source,
# this is a regression: the Reel path could then generate a new story,
# send the daily email, or touch the daily cover/carousel generation code,
# exactly like the bug this suite guards against.
#
# NOTE (V3): "openai_service" and "images.generate" are deliberately NOT
# in this list -- reel_image_service.py legitimately reuses the existing
# services.openai_service.OpenAIService client for dedicated Reel scene
# illustrations (see test_never_calls_daily_cover_generation below for
# the more precise check: it may call generate_image(), never
# generate_cover()).
FORBIDDEN_SUBSTRINGS = [
    "run_daily",
    "storypipeline",
    "storyagent",
    "generate_story",
    "themeservice",
    "get_story_context",
    "emailservice",
    "genai",
    "gemini",
    "cover_prompt_agent",
    "cover_designer",
    "carousel_renderer",
    "publishing_service",
]

REEL_MODULES = [generate_reel, reel_service, reel_image_service]


class TestReelPathIsolation(unittest.TestCase):
    """Static, source-level regression guard for the Reel execution path.
    Complements the behavioural tests in test_reel_service_generate.py and
    test_reel_image_service.py: this proves the *code itself* never even
    references the daily pipeline's collaborators, rather than just
    proving today's call graph avoids them at runtime."""

    def _assert_source_clean(self, module):

        source = inspect.getsource(module).lower()

        for forbidden in FORBIDDEN_SUBSTRINGS:
            self.assertNotIn(
                forbidden,
                source,
                f"{module.__name__} source references forbidden symbol '{forbidden}'",
            )

    def test_all_reel_modules_are_isolated(self):

        for module in REEL_MODULES:
            with self.subTest(module=module.__name__):
                self._assert_source_clean(module)

    def test_reel_modules_do_not_import_daily_pipeline_collaborators(self):
        """Checks each module's resolved namespace (not just source text),
        so this also catches an indirect re-export."""

        forbidden_names = ("StoryPipeline", "StoryAgent", "ThemeService",
                            "EmailService", "PublishingService",
                            "CoverDesigner", "CarouselRenderer",
                            "CoverPromptAgent")

        for module in REEL_MODULES:
            names = set(dir(module))
            with self.subTest(module=module.__name__):
                for forbidden_name in forbidden_names:
                    self.assertNotIn(forbidden_name, names)

    def test_never_calls_daily_cover_generation(self):
        """The Reel image path may call OpenAIService.generate_image()
        (dedicated Reel scenes) but must NEVER call
        OpenAIService.generate_cover() -- that would regenerate the daily
        cover, which V3 explicitly must not do."""

        source = inspect.getsource(reel_image_service)

        self.assertNotIn("generate_cover(", source)
        self.assertIn("generate_image(", source)

    def test_reel_image_service_creates_no_second_image_client(self):
        """ReelImageService must reuse services.openai_service.OpenAIService
        rather than constructing its own OpenAI client."""

        source = inspect.getsource(reel_image_service)

        self.assertIn("from services.openai_service import OpenAIService", source)
        self.assertNotIn("OpenAI(", source)  # no direct `openai.OpenAI(...)` construction


if __name__ == "__main__":
    unittest.main()
