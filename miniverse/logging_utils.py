"""Logging utilities for Miniverse simulations.

Provides color-coded output to distinguish deterministic vs non-deterministic operations.
"""

import os
from enum import Enum


class Color(Enum):
    """ANSI color codes for terminal output."""

    # Colors for operation types
    BLUE = "\033[94m"  # Deterministic operations (physics, perception)
    YELLOW = "\033[93m"  # LLM calls (planner, executor, reflection)
    RED = "\033[91m"  # Errors and retries
    GREEN = "\033[92m"  # Success/completion
    CYAN = "\033[96m"  # Info/metadata

    # Formatting
    BOLD = "\033[1m"
    RESET = "\033[0m"


def colored(text: str, color: Color, bold: bool = False) -> str:
    """Wrap text in ANSI color codes if colors are enabled.

    Args:
        text: Text to colorize
        color: Color to apply
        bold: Whether to make text bold

    Returns:
        Colorized text if MINIVERSE_NO_COLOR is not set, otherwise plain text
    """
    if os.getenv("MINIVERSE_NO_COLOR"):
        return text

    prefix = color.value
    if bold:
        prefix = Color.BOLD.value + prefix

    return f"{prefix}{text}{Color.RESET.value}"


def log_deterministic(message: str) -> None:
    """Log a deterministic operation (blue)."""
    print(colored(message, Color.BLUE))


def log_llm(message: str) -> None:
    """Log an LLM operation (yellow)."""
    print(colored(message, Color.YELLOW))


def log_error(message: str) -> None:
    """Log an error or retry (red)."""
    print(colored(message, Color.RED))


def log_success(message: str) -> None:
    """Log a success (green)."""
    print(colored(message, Color.GREEN))


def log_info(message: str) -> None:
    """Log metadata/info (cyan)."""
    print(colored(message, Color.CYAN))


# Markers for operation types (color-blind accessible)
LOG_TAG_DETERMINISTIC = "[•]"  # Deterministic / rule-based step (no LLM)
LOG_TAG_LLM = "[LLM]"  # Actual LLM call will occur
LOG_TAG_ERROR = "[!]"  # Error/retry
LOG_TAG_SUCCESS = "[✓]"  # Success
LOG_TAG_INFO = "[i]"  # Information
