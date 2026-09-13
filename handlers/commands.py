"""
Telegram command handlers for Anime Hindi Dub Bot.

Commands:
    /start                    -- card-style welcome message
    /help                     -- card-style help
    /anime <anime name>       -- search + card-style anime info

UI follows the small-caps Telegram "card" aesthetic.
"""

import html

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from services.anime_scraper import (
    AnimeInfo,
    format_anime_info,
    get_anime_info,
    get_poster_url,
    sc,
)
from utils.logger import logger

# ------------------------------------------------------------
# Shared UI bits
# ------------------------------------------------------------

DIVIDER = "✦━━━━━━━━━━━━━━━━━━━━━━━━✦"

FALLBACK_NAME = "Friend"


def _first_name(update: Update) -> str:
    user = update.effective_user
    if user is None:
        return FALLBACK_NAME
    return (user.first_name or FALLBACK_NAME).strip() or FALLBACK_NAME


def _user_id(update: Update) -> str:
    user = update.effective_user
    return str(user.id) if user else "unknown"


def _is_valid_url(value: str) -> bool:
    if not value:
        return False
    return value.startswith("http://") or value.startswith("https://")


def _split_text(text: str, limit: int = 4096) -> list[str]:
    """Split a caption/text on newline boundaries so HTML stays intact."""
    if len(text) <= limit:
        return [text]
    parts: list[str] = []
    current = ""
    for line in text.split("\n"):
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) <= limit:
            current = candidate
        else:
            if current:
                parts.append(current)
            current = line
    if current:
        parts.append(current)
    return parts


# ============================================================
# START COMMAND
# ============================================================

async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Handle /start with the new card layout (HTML parse mode)."""
    if not update.message:
        return

    logger.info("Start command received from user %s", _user_id(update))

    name = html.escape(_first_name(update))

    message = (
        "◆ <b>ANIME HINDI DUB BOT</b> ◆\n"
        f"{DIVIDER}\n\n"
        f"Namaste {name}! 👋\n"
        "Hindi-dubbed anime ki complete info lo.\n\n"
        f"{DIVIDER}\n"
        f"➥ {sc('Main kya kar sakta hoon')}:-\n"
        f"{DIVIDER}\n\n"
        "   ▸ Anime Poster\n"
        "   ▸ Hindi Dub Status\n"
        "   ▸ Seasons & Episodes\n"
        "   ▸ Languages / Audio\n"
        "   ▸ Genres & Studio\n\n"
        f"{DIVIDER}\n"
        f"➥ {sc('Use Kaise Karein')}:-\n"
        f"{DIVIDER}\n\n"
        "   /anime &lt;name&gt;\n\n"
        f"   💡 {sc('Examples')}:\n"
        "      ▸ /anime Naruto\n"
        "      ▸ /anime Solo Leveling\n\n"
        f"{DIVIDER}\n"
        f"⌬ {sc('Powered by')}:- Anime Hindi Dub Bot"
    )

    await update.message.reply_text(message, parse_mode=ParseMode.HTML)


# ============================================================
# HELP COMMAND
# ============================================================

async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Handle /help with the card layout."""
    if not update.message:
        return

    logger.info("Help command received from user %s", _user_id(update))

    message = (
        "◆ <b>ANIME HINDI DUB BOT</b> ◆\n"
        f"{DIVIDER}\n"
        f"➥ {sc('Commands')}:-\n"
        "   /start        Welcome message\n"
        "   /help         Help aur examples\n"
        "   /anime &lt;name&gt;   Anime search\n\n"
        f"{DIVIDER}\n"
        f"➥ {sc('Examples')}:-\n"
        "   ▸ /anime Naruto\n"
        "   ▸ /anime Solo Leveling\n"
        "   ▸ /anime Naruto Movie\n\n"
        f"{DIVIDER}\n"
        f"➥ {sc('Result me aayega')}:-\n"
        "   ▸ Anime poster\n"
        "   ▸ Hindi dub status\n"
        "   ▸ Seasons & episodes\n"
        "   ▸ Languages / audio\n"
        "   ▸ Genres & studio\n\n"
        f"{DIVIDER}\n"
        f"➥ {sc('Tips')}:-\n"
        "   • Sahi English spelling likho\n"
        "   • Short/common naam try karo\n"
        "   • Movie ke liye naam ke saath Movie likho\n\n"
        f"{DIVIDER}\n"
        f"⌬ {sc('Powered by')}:- Anime Hindi Dub Bot"
    )

    await update.message.reply_text(message, parse_mode=ParseMode.HTML)


# ============================================================
# ANIME COMMAND
# ============================================================

def anime_usage_message() -> str:
    """Usage card for /anime without arguments."""
    return (
        "◆ <b>ANIME SEARCH</b> ◆\n"
        f"{DIVIDER}\n\n"
        "❌ Ye command aise use karo:\n"
        "   /anime &lt;anime name&gt;\n\n"
        f"{DIVIDER}\n"
        f"➥ {sc('Examples')}:-\n"
        "   ▸ /anime Naruto\n"
        "   ▸ /anime Solo Leveling\n\n"
        f"{DIVIDER}\n"
        f"➥ {sc('Tips')}:-\n"
        "   • Sahi English spelling likho\n"
        "   • Chhota naam likho"
    )


def anime_not_found_message(anime_name: str) -> str:
    """Card shown when an anime search returns nothing."""
    title = html.escape(anime_name)
    return (
        "◆ <b>ANIME SEARCH</b> ◆\n"
        f"{DIVIDER}\n\n"
        f"😕 Anime nahi mila:\n"
        f"   {title}\n\n"
        f"{DIVIDER}\n"
        f"➥ {sc('Try')}:-\n"
        "   • Another spelling\n"
        "   • English title\n"
        "   • Short/common title\n"
        "   • Add Movie if it is a movie"
    )


async def anime_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Handle /anime <anime name>."""
    if not update.message:
        return

    chat = update.effective_chat
    chat_type = chat.type if chat else "unknown"
    logger.info(
        "Anime command received from user %s in %s chat",
        _user_id(update),
        chat_type,
    )

    if not context.args or not " ".join(context.args).strip():
        logger.info("Anime command called without a name")
        await update.message.reply_text(
            anime_usage_message(),
            parse_mode=ParseMode.HTML,
        )
        return

    anime_name = " ".join(context.args).strip()

    # Loading message: edited into the result (no poster) or deleted
    # (poster sent) so there is no flash of a stale "Searching…" text.
    loading_message = await update.message.reply_text("🔍 Searching…")

    try:
        anime_info = await get_anime_info(anime_name)

        if not anime_info:
            logger.info("Anime not found: %s", anime_name)
            try:
                await loading_message.edit_text(
                    anime_not_found_message(anime_name),
                    parse_mode=ParseMode.HTML,
                )
            except Exception as exc:
                logger.debug("Could not edit loading message: %s", exc)
            return

        logger.info("Successfully returned anime info: %s", anime_name)
        await send_anime_with_poster(
            update,
            anime_info,
            loading_message=loading_message,
        )

    except Exception as exc:
        logger.exception(
            "Error processing anime command for '%s': %s",
            anime_name,
            exc,
        )
        try:
            await loading_message.delete()
        except Exception:
            pass
        await update.message.reply_text(
            "❌ Error: Unable to fetch anime information.\n\n"
            "Ye temporary problem ho sakti hai.\n"
            "Thodi der baad dobara try karo."
        )


# ============================================================
# SEND ANIME INFORMATION (poster + caption in ONE message)
# ============================================================

async def _safe_delete(message) -> None:
    if message is None:
        return
    try:
        await message.delete()
    except Exception as exc:
        logger.debug("Could not delete message: %s", exc)


async def send_anime_with_poster(
    update: Update,
    anime_info: AnimeInfo,
    loading_message=None,
) -> None:
    """Send the result, preferring photo+caption over plain text.

    - Poster available + caption <= 1024 chars -> one photo message.
    - Poster available + caption >  1024 chars -> photo with the first
      part as caption, remaining parts replied to that photo.
    - No poster / poster failed                -> loading message is
      edited into the full text result.
    """
    if not update.message:
        return

    try:
        caption = format_anime_info(anime_info)
    except Exception as exc:
        logger.exception("Anime formatter failed: %s", exc)
        try:
            await loading_message.edit_text(
                "❌ Unable to format anime information."
            )
        except Exception:
            pass
        return

    if not caption or not caption.strip():
        logger.error("Anime information formatter returned empty text.")
        try:
            await loading_message.edit_text(
                "❌ Anime information empty aa rahi hai."
            )
        except Exception:
            pass
        return

    poster_url = get_poster_url(anime_info)

    if poster_url and _is_valid_url(poster_url):
        try:
            caption_parts = _split_text(caption, limit=1024)

            photo_message = await update.message.reply_photo(
                photo=poster_url,
                caption=caption_parts[0],
                parse_mode=ParseMode.HTML,
            )
            await _safe_delete(loading_message)
            logger.debug("Poster + caption sent in one message.")

            # Remaining parts are replied to the photo message itself.
            for part in caption_parts[1:]:
                await photo_message.reply_text(
                    part,
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True,
                )
            return

        except Exception as exc:
            logger.warning(
                "Poster sending failed, falling back to text: %s",
                exc,
            )

    # No poster (or poster failed): turn the loading message into result.
    text_parts = _split_text(caption, limit=4096)
    try:
        await loading_message.edit_text(
            text_parts[0],
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    except Exception as exc:
        logger.debug("Could not edit loading message: %s", exc)
        await update.message.reply_text(
            text_parts[0],
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )

    for part in text_parts[1:]:
        await update.message.reply_text(
            part,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )

    logger.debug("Anime information sent successfully.")
