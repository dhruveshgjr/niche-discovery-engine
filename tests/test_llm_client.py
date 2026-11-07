from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from openai import BadRequestError

import heuristics
from llm_client import LLMClient, LLMError


def make_fake_client(content: str):
    response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])
    create = Mock(return_value=response)
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


@pytest.fixture
def live_settings(settings):
    settings.demo_mode = False
    return settings


def test_extraction_routes_to_small_model(live_settings):
    fake = make_fake_client('{"pain_points": []}')
    llm = LLMClient(live_settings, demo=False, client=fake)
    llm.extract_pain_points({"title": "t", "body": "b", "source": "hn"})
    assert fake.chat.completions.create.call_args.kwargs["model"] == live_settings.small_model


def test_strategy_tasks_route_to_primary_model(live_settings):
    fake = make_fake_client('{"queries": ["q1"]}')
    llm = LLMClient(live_settings, demo=False, client=fake)
    llm.generate_queries(["data extraction"], 20)
    assert fake.chat.completions.create.call_args.kwargs["model"] == live_settings.primary_model

    fake.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(
            content='{"clusters": [{"cluster_name": "c", "pain_ids": [1]}]}'))])
    llm.cluster_pains([{"id": 1, "pain_statement": "no api", "persona": "dev"}])
    assert fake.chat.completions.create.call_args.kwargs["model"] == live_settings.primary_model

    fake.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(
            content='{"product_idea": "x", "validation_plan": "y", "competition": 5, "legal_risk": 3, "reachability": 7}'))])
    llm.strategize("c", [{"pain_statement": "no api", "persona": "dev"}])
    assert fake.chat.completions.create.call_args.kwargs["model"] == live_settings.primary_model


def test_fenced_json_is_parsed(live_settings):
    fake = make_fake_client('```json\n{"queries": ["q1"]}\n```')
    llm = LLMClient(live_settings, demo=False, client=fake)
    assert llm.generate_queries(["seed"], 20) == ["q1"]


def test_invalid_json_retries_then_raises(live_settings):
    fake = make_fake_client("absolute nonsense")
    llm = LLMClient(live_settings, demo=False, client=fake)
    with pytest.raises(LLMError):
        llm.generate_queries(["seed"], 20)
    assert fake.chat.completions.create.call_count == 2


def test_context_overflow_shrinks_and_retries(live_settings):
    fake = make_fake_client('{"queries": ["q1"]}')
    error = BadRequestError(
        "context length exceeded",
        response=httpx.Response(400, request=httpx.Request("POST", "http://localhost")),
        body=None,
    )
    fake.chat.completions.create.side_effect = [error, fake.chat.completions.create.return_value]
    llm = LLMClient(live_settings, demo=False, client=fake)
    assert llm.generate_queries(["seed"], 20) == ["q1"]
    assert fake.chat.completions.create.call_count == 2
    first_len = len(fake.chat.completions.create.call_args_list[0].kwargs["messages"][1]["content"])
    second_len = len(fake.chat.completions.create.call_args_list[1].kwargs["messages"][1]["content"])
    assert second_len <= first_len


def test_demo_mode_uses_heuristics_without_client(settings):
    llm = LLMClient(settings, demo=True)
    seeds = ["data extraction", "missing API"]
    assert llm.generate_queries(seeds, 20) == heuristics.generate_queries(seeds, 20)
    post = {"title": "No API", "body": "I spend hours copying data into spreadsheets manually."}
    pains = llm.extract_pain_points(post)
    assert len(pains) == 1
    assert pains[0]["extraction_fit"] is True
    assert pains[0]["severity"] > 0


def test_demo_noise_post_yields_nothing(settings):
    llm = LLMClient(settings, demo=True)
    assert llm.extract_pain_points({"title": "Library released", "body": "Version 2.0 is out."}) == []


def test_cluster_payload_is_capped_to_context_budget(live_settings):
    live_settings.primary_max_input_chars = 400
    fake = make_fake_client('{"clusters": [{"cluster_name": "c", "pain_ids": [0]}]}')
    llm = LLMClient(live_settings, demo=False, client=fake)
    pains = [{"id": i, "pain_statement": f"pain statement number {i} with some padding text",
              "persona": "Developer"} for i in range(50)]
    llm.cluster_pains(pains)
    user_content = fake.chat.completions.create.call_args.kwargs["messages"][1]["content"]
    assert len(user_content) <= 400
    assert user_content.count("pain statement number") < 50  # tail was dropped, JSON kept whole