"""Claude API helpers shared by all agents."""

import base64
from importlib.resources import files
from pathlib import Path
from typing import TypeVar

import anthropic
from pydantic import BaseModel

MODEL = "claude-opus-5-5"
# Server-side fallback: if a safety classifier declines, the API retries on
# Anthropic's recommended fallback model instead of returning a refusal.
FALLBACK_BETA = "server-side-fallback-2026-07-01"

T = TypeVar("T", bound=BaseModel)

_client = None


class RefusalError(RuntimeError):
    pass


def client() -> anthropic.Anthropic:
    # Reads ANTHROPIC_API_KEY (or an `ant auth login` profile) from the environment.
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client


def load_text(folder: str, name: str) -> str:
    return files(f"letters.{folder}").joinpath(f"{name}.md").read_text(encoding="utf-8")


def ask(system: str, content: list | str, output: type[T], effort: str = "medium") -> T:
    """One request whose answer is validated into the `output` Pydantic model."""
    response = client().beta.messages.parse(
        model=MODEL,
        max_tokens=16000,
        system=system,
        messages=[{"role": "user", "content": content}],
        output_format=output,
        output_config={"effort": effort},
        betas=[FALLBACK_BETA],
        fallbacks="default",
    )
    if response.stop_reason == "refusal":
        raise RefusalError("The model declined this request; it needs a human look.")
    if response.stop_reason == "max_tokens" or response.parsed_output is None:
        raise RuntimeError("The model's answer was cut off or unreadable; try again.")
    return response.parsed_output


def image_block(path: str | Path) -> dict:
    path = Path(path)
    media_type = {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
        ".webp": "image/webp", ".gif": "image/gif",
    }.get(path.suffix.lower())
    if media_type is None:
        raise ValueError(f"Unsupported image type: {path.suffix} (use jpg, png, webp or gif)")
    data = base64.standard_b64encode(path.read_bytes()).decode("utf-8")
    return {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}}
