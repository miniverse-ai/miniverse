"""Helper utilities for LLM-related error handling and retries."""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from typing import Any, Callable, Sequence, TypeVar

from mirascope import llm
from pydantic import BaseModel, ValidationError
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt


ModelT = TypeVar("ModelT", bound=BaseModel)


@dataclass(slots=True)
class ValidationFeedback:
    """Structured feedback for retrying failed LLM schema outputs."""

    llm_text: str
    issues: Sequence[str]


def _truncate_preview(value: Any, *, limit: int = 80) -> str:
    """Return a compact preview of the offending input value."""

    if value is None:
        return "null"
    text = repr(value)
    if len(text) > limit:
        return text[: limit - 3] + "..."
    return text


def inject_validation_feedback(error: ValidationError) -> ValidationFeedback:
    """Produce guidance for the model plus structured issues for logging.

    Converts Pydantic ValidationError into human-readable feedback that gets injected
    into retry prompts. This allows LLMs to self-correct schema violations rather than
    failing immediately. Includes field paths, error types, and input previews.
    """

    issues: list[str] = []
    # Extract individual error details from Pydantic validation. Each error contains:
    # - loc: field path (e.g., ["steps", 0, "description"])
    # - msg: error message (e.g., "field required")
    # - type: error type (e.g., "missing", "string_type")
    # - input: the offending value that failed validation
    for err in error.errors(include_url=False):  # pragma: no branch - typically small
        # Convert field path to dot notation for readability (steps.0.description)
        loc = ".".join(str(part) for part in err.get("loc", [])) or "root"
        msg = err.get("msg", "validation error")
        err_type = err.get("type")
        preview = None
        if "input" in err:
            # Truncate input preview to avoid bloating feedback with large values
            preview = _truncate_preview(err.get("input"))

        # Build human-readable error description with field path, message, type, and value
        details = f"{loc}: {msg}"
        if err_type:
            details += f" [type={err_type}]"
        if preview not in (None, ""):
            details += f" | received={preview}"
        issues.append(details)

    # Safety net for empty error lists (shouldn't happen but defensive)
    if not issues:
        issues.append("root: response did not match the expected schema")

    # Construct feedback text for LLM retry. Instructs model to fix specific issues
    # without code fences or explanations - just corrected JSON.
    instructions = [
        "Your previous JSON response failed to validate against the required schema.",
        "Produce a corrected response that strictly matches the schema.",
        "Do not include explanations or code fences—return only valid JSON.",
        "Issues detected:",
    ]
    instructions.extend(f"- {issue}" for issue in issues)

    return ValidationFeedback(llm_text="\n".join(instructions), issues=issues)


def _log_validation_failure(
    *,
    model_name: str,
    attempt: int,
    max_attempts: int,
    feedback: ValidationFeedback,
) -> None:
    """Print user-facing diagnostics for a failed validation attempt."""

    print(
        f"LLM schema validation failed for {model_name} "
        f"(attempt {attempt}/{max_attempts})."
    )
    for issue in feedback.issues:
        print(f"    - {issue}")


async def call_llm_with_retries(
    *,
    system_prompt: str,
    user_prompt: str,
    llm_provider: str,
    llm_model: str,
    response_model: type[ModelT],
    max_attempts: int = 3,
    feedback_builder: Callable[[ValidationError], ValidationFeedback] = inject_validation_feedback,
) -> ModelT:
    """Invoke a structured LLM call with validation-aware retries.

    Makes LLM calls with automatic retry on validation errors. On failure, injects
    validation feedback into prompt to guide model toward correct schema. After max
    retries exhausted, re-raises final exception to caller.

    Key design: Validation feedback is appended to original prompt rather than replacing
    it, so model retains full context while seeing what needs correction.
    """

    # Combine system and user prompts with double newline separator. This simplifies
    # retry logic - we append feedback to combined prompt rather than managing multiple
    # prompt components separately.
    prompt_sections = [section.strip() for section in (system_prompt, user_prompt) if section.strip()]
    base_prompt = "\n\n".join(prompt_sections)

    # Track validation feedback across retries. Starts None (no feedback), gets populated
    # after first failure, then carries forward to subsequent retries.
    feedback_payload: ValidationFeedback | None = None

    # Define LLM call using Mirascope decorator. The decorator handles provider-specific
    # API calls, response parsing, and schema validation. response_model triggers Pydantic
    # validation on LLM output - raises ValidationError if output doesn't match schema.
    # Use json_mode only for litellm (OpenRouter) which doesn't support native tool calling
    # on most models. OpenAI and Anthropic work better with native structured output.
    use_json_mode = llm_provider == "litellm"

    # Temperature: env var override, else 0.7 (persona simulation standard per PersonaLLM)
    temperature = float(os.getenv("LLM_TEMPERATURE", "0.7"))

    @llm.call(
        provider=llm_provider,
        model=llm_model,
        response_model=response_model,
        json_mode=use_json_mode,
        call_params={"temperature": temperature},
    )
    async def _invoke(prompt: str) -> str:
        return prompt

    attempt_number = 0
    # AsyncRetrying from tenacity handles retry logic. retry_if_exception_type(ValidationError)
    # means only validation errors trigger retry - other exceptions (network, auth) propagate
    # immediately. stop_after_attempt(max_attempts) limits retry count. reraise=True ensures
    # final exception propagates to caller after exhausting retries.
    async for attempt in AsyncRetrying(
        retry=retry_if_exception_type(ValidationError),
        stop=stop_after_attempt(max_attempts),
        reraise=True,
    ):
        with attempt:
            attempt_number += 1
            if attempt_number > 1:
                # Log retry attempts for debugging. First attempt doesn't log to reduce noise.
                print(
                    f"LLM retry {attempt_number}/{max_attempts} for {response_model.__name__};"
                    " attempting schema correction."
                )
            # Append validation feedback to base prompt (if available). First attempt uses
            # base prompt only; subsequent attempts include feedback from previous failure.
            # Feedback explains what was wrong and how to fix it.
            final_prompt = (
                base_prompt
                if feedback_payload is None
                else f"{base_prompt}\n\n{feedback_payload.llm_text}"
            )
            try:
                # Wrap LLM call in timeout to prevent hanging. 120s timeout is generous
                # (typical LLM calls complete in 1-5s) but allows for large responses
                # or slow providers. Timeout raises asyncio.TimeoutError, not ValidationError,
                # so doesn't trigger retry.
                return await asyncio.wait_for(_invoke(final_prompt), timeout=120)
            except ValidationError as exc:  # pragma: no cover - retry path
                # Validation failed - generate feedback for next retry. Feedback builder
                # extracts field paths, error messages, and input previews from Pydantic error.
                feedback_payload = feedback_builder(exc)
                _log_validation_failure(
                    model_name=response_model.__name__,
                    attempt=attempt_number,
                    max_attempts=max_attempts,
                    feedback=feedback_payload,
                )
                # Re-raise to trigger tenacity retry. Tenacity catches exception, checks
                # retry conditions, and either retries or re-raises.
                raise
            except asyncio.TimeoutError as exc:  # pragma: no cover - timeout path
                # Timeout doesn't trigger retry - propagate immediately. Timeouts usually
                # indicate provider outages or network issues that won't resolve with retry.
                print(
                    f"LLM call timed out after 120s for {response_model.__name__}."
                )
                raise exc

    # AsyncRetrying with reraise=True will always exit via return or raise. This line
    # is unreachable but satisfies type checker (function must return ModelT or raise).
    raise RuntimeError("LLM retry mechanism exited unexpectedly")
