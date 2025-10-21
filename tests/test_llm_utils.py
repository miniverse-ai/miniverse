"""Unit tests for the LLM retry helper."""

import pytest
from pydantic import BaseModel, ValidationError

from miniverse.llm_utils import call_llm_with_retries


class DummyModel(BaseModel):
    content: str


@pytest.mark.asyncio
async def test_call_llm_with_retries_success(monkeypatch):
    recorded_prompts: list[str] = []

    async def fake_caller(prompt: str) -> DummyModel:
        recorded_prompts.append(prompt)
        return DummyModel(content="ok")

    def fake_decorator(*, provider, model, response_model):
        assert response_model is DummyModel

        def wrapper(fn):
            async def inner(prompt: str):
                return await fake_caller(prompt)

            return inner

        return wrapper

    monkeypatch.setattr("miniverse.llm_utils.llm.call", fake_decorator)

    result = await call_llm_with_retries(
        system_prompt="System context",
        user_prompt="What now?",
        llm_provider="openai",
        llm_model="gpt-5-nano",
        response_model=DummyModel,
    )

    assert result.content == "ok"
    assert recorded_prompts == ["System context\n\nWhat now?"]


@pytest.mark.asyncio
async def test_call_llm_with_retries_injects_feedback(monkeypatch):
    attempts: list[str] = []

    try:
        DummyModel.model_validate({})
    except ValidationError as exc:
        validation_error = exc

    async def fake_caller(prompt: str) -> DummyModel:
        attempts.append(prompt)
        if len(attempts) == 1:
            raise validation_error
        return DummyModel(content="fixed")

    def fake_decorator(*, provider, model, response_model):
        def wrapper(fn):
            async def inner(prompt: str):
                return await fake_caller(prompt)

            return inner

        return wrapper

    monkeypatch.setattr("miniverse.llm_utils.llm.call", fake_decorator)

    result = await call_llm_with_retries(
        system_prompt="System",
        user_prompt="User",
        llm_provider="openai",
        llm_model="gpt-5-nano",
        response_model=DummyModel,
    )

    assert result.content == "fixed"
    assert len(attempts) == 2
    assert (
        "Your previous JSON response failed to validate against the required schema."
        in attempts[1]
    )
    assert "- content: Field required" in attempts[1]
