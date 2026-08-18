import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

SESSION_DIR = BASE_DIR / "session"
DATA_DIR = BASE_DIR / "data"
LOG_DIR = BASE_DIR / "logs"
CHROMIUM_PROFILE_DIR = SESSION_DIR / "chromium-profile"
CHROMIUM_COOKIES_FILE = SESSION_DIR / "chromium-cookies.json"
INSTAGRAPI_SESSION_FILE = SESSION_DIR / "instagrapi.json"

_chromium_candidates = sorted((BASE_DIR / ".browsers").glob("chromium-*/chrome-linux64/chrome"))
CHROMIUM_EXECUTABLE = _chromium_candidates[-1] if _chromium_candidates else None
DATABASE_PATH = DATA_DIR / "bot.sqlite3"
INSTAGRAM_CHALLENGE_FILE = DATA_DIR / "instagram-challenge-required"

BOT_NAME = os.getenv("BOT_NAME", "ineffa").strip() or "ineffa"
PREFIX = os.getenv("BOT_PREFIX", os.getenv("PREFIX", ".")).strip() or "."

for directory in (SESSION_DIR, DATA_DIR, LOG_DIR, CHROMIUM_PROFILE_DIR):
    directory.mkdir(parents=True, exist_ok=True)
