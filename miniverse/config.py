"""
Miniverse Configuration

Loads configuration from environment variables with sensible defaults.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env file if it exists
load_dotenv()


class Config:
    """Application configuration loaded from environment variables."""

    # LLM Provider Configuration
    LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "openai")
    LLM_MODEL: str = os.getenv("LLM_MODEL", "gpt-5-nano")

    # API Keys
    ANTHROPIC_API_KEY: str | None = os.getenv("ANTHROPIC_API_KEY")
    OPENAI_API_KEY: str | None = os.getenv("OPENAI_API_KEY")
    OPENROUTER_API_KEY: str | None = os.getenv("OPENROUTER_API_KEY")
    OPENAI_API_BASE: str | None = os.getenv("OPENAI_API_BASE")

    # Database Configuration
    DATABASE_URL: str = os.getenv("DATABASE_URL", "postgresql://localhost/miniverse")

    # Simulation Configuration
    DEFAULT_TICK_COUNT: int = int(os.getenv("DEFAULT_TICK_COUNT", "50"))
    TICK_DURATION_SECONDS: int = int(os.getenv("TICK_DURATION_SECONDS", "10"))

    # Logging
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

    # Project Paths
    PROJECT_ROOT: Path = Path(__file__).parent.parent
    PROMPTS_DIR: Path = PROJECT_ROOT / "examples" / "prompts"

    @classmethod
    def validate(cls) -> None:
        """Validate configuration and raise errors if required values are missing."""
        if cls.LLM_PROVIDER == "anthropic" and not cls.ANTHROPIC_API_KEY:
            raise ValueError(
                "ANTHROPIC_API_KEY is required when using the 'anthropic' provider"
            )

        if cls.LLM_PROVIDER == "openai":
            # Check if using OpenRouter (detected by base URL)
            is_openrouter = (
                cls.OPENAI_API_BASE and "openrouter.ai" in cls.OPENAI_API_BASE
            )

            if is_openrouter:
                # OpenRouter validation
                if not cls.OPENROUTER_API_KEY:
                    raise ValueError(
                        "OPENROUTER_API_KEY is required when using OpenRouter"
                    )
                if not cls.OPENROUTER_API_KEY.startswith("sk-or-v1-"):
                    raise ValueError("OpenRouter API key must start with 'sk-or-v1-'")
            else:
                # Standard OpenAI validation
                if not cls.OPENAI_API_KEY:
                    raise ValueError(
                        "OPENAI_API_KEY is required when using the 'openai' provider"
                    )

    @classmethod
    def display(cls) -> str:
        """Return a formatted string showing current configuration."""
        lines = [
            "Miniverse Configuration:",
            f"  LLM Provider: {cls.LLM_PROVIDER}",
            f"  LLM Model: {cls.LLM_MODEL}",
        ]

        if cls.OPENAI_API_BASE:
            lines.append(f"  OpenAI API Base: {cls.OPENAI_API_BASE}")

        lines.extend(
            [
                f"  Database: {cls.DATABASE_URL}",
                f"  Default Ticks: {cls.DEFAULT_TICK_COUNT}",
                f"  Tick Duration: {cls.TICK_DURATION_SECONDS}s",
            ]
        )
        return "\n".join(lines)
