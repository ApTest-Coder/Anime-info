"""
Logging configuration for Anime Hindi Dub Bot

Logs go to STDOUT only. On Render the ephemeral disk is wiped on every
deploy, so writing many per-run log files just fills up an already
ephemeral filesystem. Container platforms capture stdout natively.
"""

import logging
import sys


def setup_logging(level: int = logging.INFO) -> logging.Logger:
    """Configure the root logger once with a single stdout handler."""
    root = logging.getLogger()
    root.setLevel(level)

    has_stdout_handler = any(
        isinstance(handler, logging.StreamHandler)
        and handler.stream is sys.stdout
        for handler in root.handlers
    )
    if not has_stdout_handler:
        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(level)
        handler.setFormatter(
            logging.Formatter(
                '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                datefmt='%Y-%m-%d %H:%M:%S',
            )
        )
        root.addHandler(handler)

    # Convenience handle kept for backwards compatibility.
    return logging.getLogger('anime_hindi_dub_bot')


logger = setup_logging()
