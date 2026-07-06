import os
from dotenv import load_dotenv

load_dotenv()

# =========================
# API KEYS
# =========================

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# =========================
# EMAIL
# =========================

GMAIL_EMAIL = os.getenv("GMAIL_EMAIL")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")

# =========================
# STORY SETTINGS
# =========================

DEFAULT_AGE_GROUP = "3-6 years"

DEFAULT_DURATION = "2 minutes"

DEFAULT_SCENE_COUNT = 6

# =========================
# IMAGE SETTINGS
# =========================

IMAGE_STYLE = "Pixar-quality 3D"

IMAGE_RATIO = "9:16"

IMAGE_SIZE = "1080x1920"

# =========================
# BRAND SETTINGS
# =========================

CHANNEL_NAME = "Dreamy Bedtime Stories"

LOGO_ENABLED = False

OUTPUT_FOLDER = "output"

IMAGE_WIDTH = 1080

IMAGE_HEIGHT = 1920

SMTP_SERVER = "smtp.gmail.com"

SMTP_PORT = 465

EMAIL_USERNAME = "c.santosh.2586@gmail.com"

EMAIL_PASSWORD = "nstivmrrlpunmlbg"

EMAIL_TO = "c.santosh.2586@gmail.com"