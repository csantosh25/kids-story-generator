import inspect
import unittest

import generate_reel
import services.reel_service as reel_service


# Anything that would let the Reel path reach the daily story pipeline,
# email, Gemini, or OpenAI image generation. If any of these strings show
# up in the Reel entry point or service module's own source, this is a
# regression: the Reel path could then generate a new story and/or send
# the daily email, exactly like the bug this suite guards against.
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
    "openai_service",
    "images.generate",
]


class TestReelPathIsolation(unittest.TestCase):
    """Static, source-level regression guard for the Reel execution path.
    Complements test_reel_service_generate.py's behavioural tests: this
    proves the *code itself* never even references the daily pipeline's
    collaborators, rather than just proving today's call graph avoids
    them at runtime."""

    def _assert_source_clean(self, module):

        source = inspect.getsource(module).lower()

        for forbidden in FORBIDDEN_SUBSTRINGS:
            self.assertNotIn(
                forbidden,
                source,
                f"{module.__name__} source references forbidden symbol '{forbidden}'",
            )

    def test_generate_reel_cli_is_isolated(self):

        self._assert_source_clean(generate_reel)

    def test_reel_service_module_is_isolated(self):

        self._assert_source_clean(reel_service)

    def test_reel_service_does_not_import_daily_pipeline_collaborators(self):
        """Checks the module's resolved namespace (not just source text),
        so this also catches an indirect re-export."""

        names = set(dir(reel_service))

        for forbidden_name in (
            "StoryPipeline", "StoryAgent", "ThemeService", "EmailService",
        ):
            self.assertNotIn(forbidden_name, names)

    def test_generate_reel_cli_does_not_import_daily_pipeline_collaborators(self):

        names = set(dir(generate_reel))

        for forbidden_name in (
            "StoryPipeline", "StoryAgent", "ThemeService", "EmailService",
        ):
            self.assertNotIn(forbidden_name, names)


if __name__ == "__main__":
    unittest.main()
