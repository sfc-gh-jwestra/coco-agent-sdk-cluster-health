"""Centralized prompt management for the Cluster Health Agent."""

from pathlib import Path


class PromptManager:
    """Loads and renders prompt templates from the app/prompts/ directory."""

    _PROMPTS_DIR = Path(__file__).parent / "prompts"

    @classmethod
    def load(cls, name: str, **kwargs) -> str:
        """Load a prompt template by filename and render with kwargs.

        Args:
            name: Filename of the prompt template (e.g., "health_check.txt").
            **kwargs: Variables to substitute into the template using str.format().

        Returns:
            The rendered prompt string.

        Raises:
            FileNotFoundError: If the prompt file does not exist.
            KeyError: If a required template variable is missing from kwargs.
        """
        path = cls._PROMPTS_DIR / name
        template = path.read_text()
        return template.format(**kwargs) if kwargs else template

    @classmethod
    def list_prompts(cls) -> list[str]:
        """List all available prompt template filenames."""
        return [f.name for f in cls._PROMPTS_DIR.glob("*.txt")]
