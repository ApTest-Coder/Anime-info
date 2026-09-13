"""
Anime Hindi Dub Bot -- main entry point.

Starts an embedded HTTP health server (Render probes the web dyno)
and then runs the Telegram bot via long polling.
"""

import json
import os
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from telegram.ext import Application, CommandHandler, MessageHandler, filters

from config import TELEGRAM_BOT_TOKEN
from handlers.commands import anime_command, help_command, start_command
from handlers.errors import error_handler, handle_invalid_command
from utils.logger import logger

START_TIME = time.time()
KEEP_ALIVE_INTERVAL = 10 * 60  # seconds


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ("/", "/health"):
            uptime = round(time.time() - START_TIME, 2)
            body = json.dumps(
                {"status": "ok", "uptime": uptime}
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        return


def start_health_server() -> None:
    port = int(os.environ.get("PORT", "10000"))
    server = ThreadingHTTPServer(("0.0.0.0", port), HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    logger.info("Health server started on port %d", port)


def start_keep_alive() -> None:
    """Self-ping every 10 minutes.

    Render free tier can idle after ~15 minutes without inbound
    traffic. When RENDER_EXTERNAL_URL is set (Render dashboard) the
    bot pings its own public /health endpoint to keep the service
    warm. Without it we only hit the local endpoint; for a truly
    external ping also point cron-job.org / UptimeRobot at
    `https://<your-app>.onrender.com/health`.
    """
    external_url = os.environ.get("RENDER_EXTERNAL_URL", "").strip().rstrip("/")
    port = int(os.environ.get("PORT", "10000"))
    target = (
        f"{external_url}/health"
        if external_url
        else f"http://127.0.0.1:{port}/health"
    )

    if not external_url:
        logger.info(
            "RENDER_EXTERNAL_URL not set - keep-alive will only hit the "
            "local health endpoint. Use an external uptime monitor for "
            "real Render wake-ups."
        )

    def _loop() -> None:
        while True:
            time.sleep(KEEP_ALIVE_INTERVAL)
            try:
                with urllib.request.urlopen(target, timeout=10) as response:
                    logger.debug(
                        "Keep-alive ping: HTTP %d",
                        response.status,
                    )
            except Exception as exc:
                logger.debug("Keep-alive ping failed: %s", exc)

    thread = threading.Thread(target=_loop, daemon=True)
    thread.start()
    logger.info(
        "Keep-alive started (every %ds -> %s)",
        KEEP_ALIVE_INTERVAL,
        target,
    )


def main() -> None:
    logger.info("Starting Anime Hindi Dub Bot...")

    try:
        start_health_server()
        start_keep_alive()

        application = (
            Application.builder()
            .token(TELEGRAM_BOT_TOKEN)
            .build()
        )

        application.add_handler(CommandHandler("start", start_command))
        application.add_handler(CommandHandler("help", help_command))
        application.add_handler(CommandHandler("anime", anime_command))

        # Fallback for any unrecognized command.
        application.add_handler(
            MessageHandler(filters.COMMAND, handle_invalid_command)
        )

        # Single error handler; it internally routes Forbidden errors
        # (user blocked the bot) to a quiet warning path.
        application.add_error_handler(error_handler)

        logger.info("Bot initialized successfully")
        logger.info("Starting Telegram polling...")

        application.run_polling(
            allowed_updates=["message", "edited_message"]
        )

    except Exception as exc:
        logger.critical("Critical error: %s", exc, exc_info=True)
        raise


if __name__ == "__main__":
    main()
