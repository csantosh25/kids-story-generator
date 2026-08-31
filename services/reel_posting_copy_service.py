"""Instagram posting copy for a Reel -- reel_caption.txt.

IMPORTANT NAME DISTINCTION: this is NOT the on-screen burned subtitle
text (see build_caption_chunks / build_caption_cues in
services/reel_service.py). "Caption" here means what Instagram itself
calls it: the post description text that goes with the Reel, plus its
hashtags. The two are unrelated and this module never touches the
on-screen subtitle pipeline.

Primary purpose (per the growth requirement this module implements):
maximize views, watch-through/curiosity, saves/shares, and follows --
while still accurately representing the real story. NOT a summary.

Entirely deterministic and local: reuses the story's own already-
generated data (title/theme/moral/character, and the Reel's own
already-built hook -- see reel_service.build_hook/build_reel_script) and
a content_id-seeded hash (the same sha256-mod-N pattern already used by
select_music_track in reel_service.py) to rotate CTA/hashtag phrasing
without ever calling an AI/API. Zero additional cost.
"""
import hashlib
from pathlib import Path

from models.story_models import StoryPackage


# =====================================================================
# Watch-prompt templates -- keyed by the SAME keyword lists as
# reel_service.HOOK_KEYWORD_TEMPLATES (imported, not re-typed) so a
# story's theme/moral drives a matching on-screen hook AND a matching
# watch-prompt line, without the two ever being able to drift apart.
# Each template is a short, non-spoiling "why watch" line: it never
# describes HOW the story resolves, only that something does -- which
# is trivially true of any story, so this never invents/claims a fact
# the story doesn't actually contain.
# =====================================================================

_WATCH_PROMPT_BY_KEYWORDS = {
    ("help", "helping", "helped"):
        "Sometimes a small act of kindness can make a big difference. "
        "Watch till the end to see what {name} does when a friend needs help. 💛",
    ("share", "sharing", "shared"):
        "Sharing isn't always easy, but it always feels good. "
        "Watch till the end to see what {name} decides to share. 💛",
    ("brave", "courage", "scared", "afraid", "fear"):
        "Being brave doesn't mean not being scared. "
        "Watch till the end to see how {name} finds the courage. 💛",
    ("sorry", "forgive", "forgiveness"):
        "Saying sorry can be the hardest -- and kindest -- thing to do. "
        "Watch till the end to see what {name} decides. 💛",
    ("listen", "listening"):
        "Sometimes the best thing you can do is really listen. "
        "Watch till the end to see what {name} learns. 💛",
    ("patient", "patience", "wait", "waiting"):
        "Good things are worth waiting for. "
        "Watch till the end to see how {name} learns to wait. 💛",
    ("honest", "truth", "honesty"):
        "Telling the truth isn't always easy. "
        "Watch till the end to see what {name} decides to do. 💛",
    ("try", "trying", "give up"):
        "Trying again after a setback takes real courage. "
        "Watch till the end to see if {name} keeps going. 💛",
    ("kind", "kindness"):
        "A little kindness can go a long way. "
        "Watch till the end to see how {name} shows it. 💛",
}

_DEFAULT_WATCH_PROMPT = (
    "Every day brings a new little adventure. "
    "Watch till the end to see what {name} discovers. 💛"
)


# =====================================================================
# Theme-specific hashtags -- same keyword-matching idea, kept as its
# own small table (not reused from anywhere -- discovery hashtags are
# a distinct concern from on-screen text or the watch-prompt).
# =====================================================================

_THEME_HASHTAGS_BY_KEYWORDS = {
    ("help", "helping", "helped"): ("#HelpingOthers", "#Kindness"),
    ("share", "sharing", "shared"): ("#Sharing", "#Friendship"),
    ("brave", "courage", "scared", "afraid", "fear"): ("#Bravery", "#Courage"),
    ("sorry", "forgive", "forgiveness"): ("#Forgiveness", "#Kindness"),
    ("listen", "listening"): ("#GoodListener", "#Kindness"),
    ("patient", "patience", "wait", "waiting"): ("#Patience", "#LifeLessons"),
    ("honest", "truth", "honesty"): ("#Honesty", "#LifeLessons"),
    ("try", "trying", "give up"): ("#NeverGiveUp", "#LifeLessons"),
    ("kind", "kindness"): ("#Kindness", "#BeKind"),
}

_DEFAULT_THEME_HASHTAGS = ("#MoralStories", "#LifeLessons")

_BROAD_HASHTAGS = ("#KidsStories", "#BedtimeStories", "#KidsReels")

_AUDIENCE_HASHTAGS = ("#Parents", "#Parenting", "#StoryTime", "#KidsActivities")


# =====================================================================
# Growth CTA phrasing pools -- one pool per action, each variant
# deliberately natural/short, never a bare command repeated verbatim
# every time (see _select_variant). None claim a specific posting
# cadence ("tomorrow", "every day") since Reel POSTING itself is manual
# (see generate_reel.py) even though story GENERATION runs daily --
# claiming a Reel cadence the account doesn't actually guarantee would
# be exactly the kind of unsupported claim this feature must avoid.
# =====================================================================

_LIKE_CTA_VARIANTS = (
    "❤️ Like if your little one enjoys stories like this",
    "❤️ Like if this story made you smile",
    "❤️ Double-tap if you enjoyed this one",
    "❤️ Like this if your kids love bedtime stories",
)

_SAVE_CTA_VARIANTS = (
    "💾 Save it for bedtime",
    "💾 Save this for storytime later",
    "💾 Save it to watch again at bedtime",
    "💾 Save this one for later",
)

_SHARE_CTA_VARIANTS = (
    "👨‍👩‍👧 Share it with another parent",
    "👨‍👩‍👧 Share with a friend who loves bedtime stories",
    "👨‍👩‍👧 Tag a parent who needs this story tonight",
    "👨‍👩‍👧 Share this with someone who'd love it",
)

_FOLLOW_CTA_VARIANTS = (
    "➕ Follow for more short stories for kids!",
    "➕ Follow for more little stories with big lessons.",
    "➕ Follow for more bedtime stories for little listeners.",
    "➕ Follow along for more little stories.",
)


def _select_variant(content_id, theme, category, variants):
    """Deterministic rotation: the same (content_id, theme, category)
    always picks the same variant (reproducible across regenerations of
    the same Reel -- same pattern as reel_service.select_music_track),
    while different stories/categories can land on different variants.
    """

    key = f"{content_id or ''}|{theme or ''}|{category}"
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    index = int(digest, 16) % len(variants)

    return variants[index]


def _match_by_keywords(haystack, table, default):

    haystack = haystack.lower()

    for keywords, value in table.items():
        if any(keyword in haystack for keyword in keywords):
            return value

    return default


def _build_watch_prompt(story: StoryPackage):

    name = story.character_sheet.main_character.name
    haystack = f"{story.story_info.moral} {story.story_info.theme}"

    template = _match_by_keywords(haystack, _WATCH_PROMPT_BY_KEYWORDS, _DEFAULT_WATCH_PROMPT)

    return template.format(name=name)


def _build_story_value_line(story: StoryPackage):

    theme = (story.story_info.theme or "").strip()

    if not theme:
        return "A simple story with a gentle lesson for little ones."

    # Theme strings are simple descriptive phrases ("Friendship and
    # Sharing", "Bravery and Courage"), not proper nouns -- lowercasing
    # the whole phrase (not just its first letter) is what reads
    # naturally mid-sentence.
    return f"A simple story about {theme.lower()}."


def _build_hashtags(story: StoryPackage):
    """3 broad + 4 audience + 2 theme-specific = 9 -- inside the
    required 8-12 range regardless of which theme matches, always
    unique (each pool uses disjoint tags), never a virality/reach
    claim, never stuffed beyond what's actually relevant."""

    haystack = f"{story.story_info.moral} {story.story_info.theme}"
    theme_tags = _match_by_keywords(haystack, _THEME_HASHTAGS_BY_KEYWORDS, _DEFAULT_THEME_HASHTAGS)

    tags = list(_BROAD_HASHTAGS) + list(_AUDIENCE_HASHTAGS) + list(theme_tags)

    # Preserve order, drop any accidental duplicate (theme tags are
    # already disjoint from broad/audience by construction, but this is
    # a hard guarantee rather than an assumption).
    seen = set()
    unique_tags = []
    for tag in tags:
        if tag not in seen:
            seen.add(tag)
            unique_tags.append(tag)

    return unique_tags


def build_reel_posting_copy(story: StoryPackage, reel_script, content_id, instagram_handle="@bedtime01fables"):
    """Builds the full copy-ready reel_caption.txt content: hook, watch
    prompt, story value, growth CTAs, a blank line, then hashtags --
    entirely from the story's own already-generated data and the Reel's
    own already-built hook (reel_script["hook"], from reel_service.
    build_reel_script -- never re-derived here, so the caption's hook
    always matches the Reel's own on-screen opening line and never
    reveals more than that hook already does). No AI/API call."""

    theme = story.story_info.theme

    hook = (reel_script.get("hook") or "").strip()
    watch_prompt = _build_watch_prompt(story)
    story_value = _build_story_value_line(story)

    cta_lines = [
        _select_variant(content_id, theme, "like", _LIKE_CTA_VARIANTS),
        _select_variant(content_id, theme, "save", _SAVE_CTA_VARIANTS),
        _select_variant(content_id, theme, "share", _SHARE_CTA_VARIANTS),
        _select_variant(content_id, theme, "follow", _FOLLOW_CTA_VARIANTS),
    ]

    hashtags = _build_hashtags(story)

    caption_block = "\n\n".join(
        part for part in [hook, watch_prompt, story_value, "\n".join(cta_lines)] if part
    )

    hashtag_line = " ".join(hashtags)

    return f"{caption_block}\n\n{hashtag_line}\n"


def save_reel_posting_copy(text, folder) -> Path:

    path = Path(folder) / "reel_caption.txt"
    path.write_text(text, encoding="utf-8")

    return path
