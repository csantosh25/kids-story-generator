"""Regression tests for StoryAgent.generate_story()'s retry loop and the
backward-compatible publishing.parent_question default.

Gemini is fully mocked: no network calls are made.
"""
import copy
import json
import unittest
from unittest.mock import MagicMock, patch

from agents.story_agent import StoryAgent, StoryGenerationError
from models.story_models import DEFAULT_PARENT_QUESTION, StoryPackage


SLIDE_TEXT = (
    "Pip the fox walked to the big green park. "
    "Pip saw a small bird sitting all alone. "
    "The bird looked sad and could not fly. "
    "Pip sat down next to the bird. "
    "Pip asked the bird what was wrong today."
)

LAST_SLIDE_TEXT = (
    "Pip and the bird played in the park all day. "
    "The bird felt safe and glad to have a friend. "
    "Pip felt proud and happy inside. "
    "Being kind is the best way to make a friend. "
    "They laughed together until it was time to go home."
)


def _valid_story_dict():
    slides = [
        {
            "page": page,
            "title": f"Part {page}",
            "text": LAST_SLIDE_TEXT if page == 4 else SLIDE_TEXT,
            "background_color": "#FDE9D9",
            "visual_theme": "park",
            "icon": "leaf",
            "speaker_notes": "",
        }
        for page in range(1, 5)
    ]

    return {
        "story_info": {
            "title": "Pip Helps a Friend",
            "subtitle": "A story about kindness",
            "theme": "kindness",
            "target_age": "3-6",
            "reading_time": "2 minutes",
            "moral": "Kindness makes friends.",
        },
        "character_sheet": {
            "main_character": {
                "name": "Model Name",
                "species": "model species",
                "appearance": "model appearance",
                "personality": "model personality",
            },
            "supporting_characters": [],
        },
        "cover": {
            "prompt": "a fox and a bird",
            "negative_prompt": "text",
            "style": "Pixar 3D",
            "title_position": "bottom",
        },
        "slides": slides,
        "instagram": {"caption": "caption", "hashtags": ["#KidsStories"]},
        "email": {"subject": "subject", "preview": "preview"},
        "youtube": {"title": "title", "description": "desc", "keywords": ["story"]},
        "publishing": {
            "hook": "hook",
            "instagram_caption_short": "short",
            "instagram_caption_long": "long",
            "hashtags": ["#a", "#b", "#c", "#d", "#e"],
            "first_comment": "comment",
            "alt_text": "alt",
            "call_to_action": "cta",
            "best_posting_time": "3:00 AM random",
            "parent_question": "How did Pip help the bird?",
        },
    }


def _story_without_parent_question():
    story = _valid_story_dict()
    del story["publishing"]["parent_question"]
    return story


STORY_CONTEXT = {
    "day": "Wednesday",
    "category": "Kindness",
    "learning_goal": "Helping others",
    "story_style": "Adventure",
    "topic": "Helping a friend",
    "character": {
        "name": "Pip",
        "species": "fox",
        "appearance": "orange fur and a blue scarf",
        "personality": ["kind", "brave"],
        "home": "Wonderwood Valley",
        "catchphrase": "Let's help!",
    },
}


def _response(payload):
    response = MagicMock()
    response.text = payload if isinstance(payload, str) else json.dumps(payload)
    return response


def _make_agent(responses):
    with patch("agents.story_agent.genai") as genai:
        agent = StoryAgent()

    agent.client = MagicMock()
    agent.client.models.generate_content.side_effect = [
        _response(payload) for payload in responses
    ]
    return agent


def _prompts_sent(agent):
    return [
        call.kwargs["contents"]
        for call in agent.client.models.generate_content.call_args_list
    ]


class ParentQuestionDefaultTests(unittest.TestCase):

    def test_missing_parent_question_uses_default(self):
        story = StoryPackage.model_validate(_story_without_parent_question())

        self.assertEqual(story.publishing.parent_question, DEFAULT_PARENT_QUESTION)

    def test_null_or_blank_parent_question_uses_default(self):
        for value in (None, "", "   "):
            data = _valid_story_dict()
            data["publishing"]["parent_question"] = value

            story = StoryPackage.model_validate(data)

            self.assertEqual(story.publishing.parent_question, DEFAULT_PARENT_QUESTION)

    def test_model_generated_parent_question_is_preserved(self):
        story = StoryPackage.model_validate(_valid_story_dict())

        self.assertEqual(story.publishing.parent_question, "How did Pip help the bird?")

    def test_other_required_publishing_fields_still_required(self):
        data = _valid_story_dict()
        del data["publishing"]["first_comment"]

        with self.assertRaises(ValueError):
            StoryPackage.model_validate(data)


class StoryAgentRetryTests(unittest.TestCase):

    def test_missing_parent_question_succeeds_first_attempt(self):
        agent = _make_agent([_story_without_parent_question()])

        story = agent.generate_story(copy.deepcopy(STORY_CONTEXT))

        self.assertEqual(agent.client.models.generate_content.call_count, 1)
        self.assertEqual(story.publishing.parent_question, DEFAULT_PARENT_QUESTION)

    def test_canonical_character_and_posting_time_enforced(self):
        agent = _make_agent([_valid_story_dict()])

        story = agent.generate_story(copy.deepcopy(STORY_CONTEXT))

        main = story.character_sheet.main_character
        self.assertEqual(main.name, "Pip")
        self.assertEqual(main.species, "fox")
        self.assertEqual(main.appearance, "orange fur and a blue scarf")
        self.assertEqual(main.personality, "kind, brave")
        self.assertEqual(
            story.publishing.best_posting_time,
            "7:00–9:00 PM IST — our established Instagram posting window.",
        )

    def test_retries_after_invalid_json_and_succeeds(self):
        agent = _make_agent(['{"story_info": {', _valid_story_dict()])

        story = agent.generate_story(copy.deepcopy(STORY_CONTEXT))

        self.assertEqual(agent.client.models.generate_content.call_count, 2)
        self.assertEqual(story.story_info.title, "Pip Helps a Friend")

        first_prompt, retry_prompt = _prompts_sent(agent)
        self.assertNotIn("previous response was rejected", first_prompt)
        self.assertIn("previous response was rejected", retry_prompt)
        self.assertIn("not valid JSON", retry_prompt)

    def test_retries_after_schema_error_and_succeeds(self):
        broken = _valid_story_dict()
        del broken["publishing"]["alt_text"]

        agent = _make_agent([broken, _valid_story_dict()])

        story = agent.generate_story(copy.deepcopy(STORY_CONTEXT))

        self.assertEqual(agent.client.models.generate_content.call_count, 2)
        self.assertIsInstance(story, StoryPackage)
        self.assertIn("publishing.alt_text", _prompts_sent(agent)[1])

    def test_retries_after_story_validator_failure_and_succeeds(self):
        too_few_slides = _valid_story_dict()
        too_few_slides["slides"] = too_few_slides["slides"][:2]

        agent = _make_agent([too_few_slides, _valid_story_dict()])

        story = agent.generate_story(copy.deepcopy(STORY_CONTEXT))

        self.assertEqual(agent.client.models.generate_content.call_count, 2)
        self.assertEqual(len(story.slides), 4)
        self.assertIn("between 4 and 6 slides", _prompts_sent(agent)[1])

    def test_retries_after_empty_response_and_succeeds(self):
        agent = _make_agent(["", _valid_story_dict()])

        story = agent.generate_story(copy.deepcopy(STORY_CONTEXT))

        self.assertEqual(agent.client.models.generate_content.call_count, 2)
        self.assertIsInstance(story, StoryPackage)

    def test_retry_guidance_does_not_accumulate(self):
        broken = _valid_story_dict()
        del broken["publishing"]["alt_text"]

        agent = _make_agent(["not json", broken, _valid_story_dict()])

        agent.generate_story(copy.deepcopy(STORY_CONTEXT))

        third_prompt = _prompts_sent(agent)[2]
        self.assertEqual(third_prompt.count("previous response was rejected"), 1)
        self.assertNotIn("not valid JSON", third_prompt)

    def test_raises_clear_error_after_three_failed_attempts(self):
        broken = _valid_story_dict()
        del broken["publishing"]["alt_text"]

        agent = _make_agent(["not json", broken, broken])

        with self.assertRaises(StoryGenerationError) as ctx:
            agent.generate_story(copy.deepcopy(STORY_CONTEXT))

        self.assertEqual(agent.client.models.generate_content.call_count, 3)
        message = str(ctx.exception)
        self.assertIn("after 3 attempts", message)
        self.assertIn("publishing.alt_text", message)

    def test_final_error_is_still_a_runtime_error(self):
        agent = _make_agent(["not json"] * 3)

        with self.assertRaises(RuntimeError):
            agent.generate_story(copy.deepcopy(STORY_CONTEXT))

    def test_failure_logs_do_not_include_raw_model_output(self):
        secret_ish = "UNIQUE-RAW-OUTPUT-MARKER"
        broken = _valid_story_dict()
        broken["publishing"]["hashtags"] = secret_ish  # wrong type

        agent = _make_agent([broken] * 3)

        with patch("builtins.print") as fake_print:
            with self.assertRaises(StoryGenerationError) as ctx:
                agent.generate_story(copy.deepcopy(STORY_CONTEXT))

        logged = " ".join(str(arg) for call in fake_print.call_args_list for arg in call.args)
        self.assertIn("publishing.hashtags", logged)
        self.assertNotIn(secret_ish, logged)
        self.assertNotIn(secret_ish, str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
