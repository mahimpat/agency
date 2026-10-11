"""Thin wrapper around the Claude API used by every agency command."""

from importlib.resources import files

import anthropic

MODEL = "claude-opus-5-5"

_client = None


def client() -> anthropic.Anthropic:
    # Reads ANTHROPIC_API_KEY (or an `ant auth login` profile) from the environment.
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client


def load_prompt(name: str) -> str:
    return files("agency.prompts").joinpath(f"{name}.md").read_text(encoding="utf-8")


class RefusalError(RuntimeError):
    pass


def generate(system_prompt: str, user_content: str, effort: str = "medium") -> str:
    """Run one request and return the text of Claude's answer.

    Streams so long deliverables don't hit HTTP timeouts. `fallbacks="default"`
    retries on Anthropic's recommended model if a safety classifier declines.
    """
    with client().beta.messages.stream(
        model=MODEL,
        max_tokens=64000,
        system=system_prompt,
        messages=[{"role": "user", "content": user_content}],
        output_config={"effort": effort},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    ) as stream:
        message = stream.get_final_message()

    if message.stop_reason == "refusal":
        raise RefusalError("The model declined this request. Review the input and try again.")
    if message.stop_reason == "max_tokens":
        raise RuntimeError("Output hit max_tokens and was cut off. Shorten the input.")

    return "".join(block.text for block in message.content if block.type == "text").strip()
