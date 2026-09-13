"""
Configuration module for Anime Hindi Dub Bot

Loads environment variables and provides configuration constants.
Secrets are NEVER hardcoded here -- they come from .env / process
environment only (gitignored).
"""

import os

from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Telegram Bot Configuration
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')

# Server Configuration (for Render deployment)
PORT = int(os.getenv('PORT', '10000'))

# Source website configuration
# Single source of truth for the site being scraped. The scraper uses
# this URL; keep it in sync with the actual scraper source.
SOURCE_BASE_URL = os.getenv('SOURCE_BASE_URL', 'https://www.rareanimes.mov')
ANIME_MIRCHI_BASE_URL = SOURCE_BASE_URL
ANIME_MIRCHI_SEARCH_URL = f"{SOURCE_BASE_URL}/?s={{query}}"

# Request Configuration
REQUEST_TIMEOUT = int(os.getenv('REQUEST_TIMEOUT', '12'))  # seconds
MAX_RETRIES = int(os.getenv('MAX_RETRIES', '2'))
RETRY_DELAY = int(os.getenv('RETRY_DELAY', '1'))  # seconds

# Bot Configuration
BOT_NAME = 'Anime Hindi Dub Bot'
COMMAND_PREFIX = '/'

# Validation
if not TELEGRAM_BOT_TOKEN:
    raise ValueError(
        "TELEGRAM_BOT_TOKEN is not set. "
        "Set it in Render env panel or local .env file."
    )
