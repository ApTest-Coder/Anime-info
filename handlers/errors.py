"""
Error handling and logging for Telegram bot.

- Generic errors are logged with the affected user id and surfaced
  back to the user.
- Forbidden errors (user blocked the bot) are handled separately so
  they don't spam the error log.
- handle_invalid_command is a fallback for any unknown /command.
"""

import logging
from typing import Optional

from telegram import Update
from telegram.error import Forbidden
from telegram.ext import ContextTypes

logger = logging.getLogger("anime_hindi_dub_bot.errors")


def _sender_id(update: Optional[Update]) -> str:
    """Best-effort identifier for the user that triggered the error."""
    if update is None:
        return "unknown"
    if update.effective_user is not None:
        return str(update.effective_user.id)
    if update.effective_chat is not None:
        return f"chat:{update.effective_chat.id}"
    return "unknown"


async def error_handler(
    update: Optional[Update],
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """
    Log any unhandled exception and notify the user.

    Args:
        update: Telegram update
        context: Telegram context with error
    """
    # Forbidden errors are handled quietly (user blocked the bot).
    if isinstance(context.error, Forbidden):
        await handle_forbidden(update, context)
        return

    logger.error(
        "Exception while handling an update from user %s: %s",
        _sender_id(update),
        context.error,
        exc_info=context.error,
    )

    # Send error message to user if update is available
    if update and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "❌ An error occurred while processing your request. "
                "Please try again later or contact support."
            )
        except Exception as exc:
            logger.error("Failed to send error message: %s", exc)


async def handle_forbidden(
    update: Optional[Update],
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """
    Handle Forbidden errors quietly (e.g. user blocked the bot).

    Telegram returns Forbidden when the bot cannot send a message to a
    user; we only log a warning instead of a full stack trace.
    """
    logger.warning(
        "Forbidden (user %s likely blocked the bot): %s",
        _sender_id(update),
        context.error,
    )


async def handle_invalid_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """
    Fallback for unrecognized commands.

    Args:
        update: Telegram update
        context: Telegram context
    """
    if update.message:
        logger.warning(
            "Invalid command '%s' received from user %s",
            update.message.text,
            _sender_id(update),
        )
        await update.message.reply_text(
            "❓ Sorry, I didn't recognize that command.\n\n"
            "Use /help to see available commands."
        )
