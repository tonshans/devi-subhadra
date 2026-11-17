"""Unit tests for the locked PydanticAI schemas (extra=forbid, strict types)."""

import pytest
from pydantic import ValidationError

from core.schemas import (
    DiscoveryRecord,
    HealerPatchProposal,
    HeuristicCandidate,
    InterAgentMessage,
    PluginOutputSchema,
    SupervisorDecision,
)


def _inter_agent_message() -> dict:
    return {
        "task_id": "task-1",
        "thread_id": "thread-1",
        "project_context": {"mission": "audit"},
        "instruction": "Summarize the ledger.",
        "expected_schema": {"type": "object"},
    }


def _plugin_output() -> dict:
    return {"success": True, "data": {"rows": 3}}


def _supervisor_decision() -> dict:
    return {
        "action_type": "REPLY_USER",
        "model_tier_used": "TIER_1_LIGHT",
        "reasoning": "User asked a direct question.",
        "thought_entry": "Answer from the ledger.",
    }


def _healer_patch() -> dict:
    return {
        "attempt_number": 1,
        "root_cause_analysis": "Missing assert on output shape.",
        "patched_code": "assert result['ok']",
        "embedded_assertions": ["result['ok'] is True"],
    }


def _heuristic() -> dict:
    return {
        "category": "TEKNIS",
        "heuristic_text": "Gunakan Python untuk data.",
    }


def _discovery() -> dict:
    return {
        "topic_category": "PYTHON",
        "content_summary": "Python 3.13 ships a new REPL.",
        "source_url": "https://example.com/python",
        "relevance_to_user": "Helps local scripting.",
    }


def test_inter_agent_message_accepts_valid_payload_and_default_timeout():
    message = InterAgentMessage(**_inter_agent_message())

    assert message.task_id == "task-1"
    assert message.timeout_seconds == 300


@pytest.mark.parametrize("timeout", [9, 3601])
def test_inter_agent_message_rejects_timeout_out_of_range(timeout):
    with pytest.raises(ValidationError):
        InterAgentMessage(**_inter_agent_message(), timeout_seconds=timeout)


def test_inter_agent_message_rejects_extra_field():
    with pytest.raises(ValidationError):
        InterAgentMessage(**_inter_agent_message(), surprise="nope")


def test_inter_agent_message_rejects_wrong_strict_type():
    with pytest.raises(ValidationError):
        InterAgentMessage(**_inter_agent_message(), timeout_seconds="300")


def test_inter_agent_message_rejects_empty_required_text():
    payload = _inter_agent_message()
    payload["task_id"] = ""

    with pytest.raises(ValidationError):
        InterAgentMessage(**payload)


def test_plugin_output_defaults_are_locked():
    output = PluginOutputSchema(**_plugin_output())

    assert output.success is True
    assert output.error_message is None
    assert output.assertions_passed is True


def test_plugin_output_rejects_extra_field_and_non_bool_success():
    with pytest.raises(ValidationError):
        PluginOutputSchema(**_plugin_output(), surprise=True)

    with pytest.raises(ValidationError):
        PluginOutputSchema(success="true", data={})


def test_supervisor_decision_accepts_valid_payload_with_optional_defaults():
    decision = SupervisorDecision(**_supervisor_decision())

    assert decision.target_plugin is None
    assert decision.plugin_payload is None
    assert decision.reply_message is None


@pytest.mark.parametrize("field", ["action_type", "model_tier_used"])
def test_supervisor_decision_rejects_unknown_literal(field):
    payload = _supervisor_decision()
    payload[field] = "NOT_A_VALID_VALUE"

    with pytest.raises(ValidationError):
        SupervisorDecision(**payload)


@pytest.mark.parametrize("field", ["reasoning", "thought_entry"])
def test_supervisor_decision_rejects_short_text(field):
    payload = _supervisor_decision()
    payload[field] = "tiny"

    with pytest.raises(ValidationError):
        SupervisorDecision(**payload)


def test_supervisor_decision_rejects_extra_field():
    with pytest.raises(ValidationError):
        SupervisorDecision(**_supervisor_decision(), surprise=True)


@pytest.mark.parametrize("attempt", [0, 4])
def test_healer_patch_rejects_attempt_outside_one_to_three(attempt):
    payload = _healer_patch()
    payload["attempt_number"] = attempt

    with pytest.raises(ValidationError):
        HealerPatchProposal(**payload)


def test_healer_patch_requires_at_least_one_assertion():
    payload = _healer_patch()
    payload["embedded_assertions"] = []

    with pytest.raises(ValidationError):
        HealerPatchProposal(**payload)


def test_healer_patch_rejects_extra_field_and_wrong_type():
    with pytest.raises(ValidationError):
        HealerPatchProposal(**_healer_patch(), surprise=True)

    payload = _healer_patch()
    payload["attempt_number"] = "1"
    with pytest.raises(ValidationError):
        HealerPatchProposal(**payload)


def test_heuristic_candidate_defaults_and_valid_payload():
    candidate = HeuristicCandidate(**_heuristic())

    assert candidate.confidence_delta == 10


@pytest.mark.parametrize("delta", [0, 51])
def test_heuristic_candidate_rejects_delta_out_of_range(delta):
    payload = _heuristic()
    payload["confidence_delta"] = delta

    with pytest.raises(ValidationError):
        HeuristicCandidate(**payload)


def test_heuristic_candidate_rejects_unknown_category_and_extra_field():
    payload = _heuristic()
    payload["category"] = "LAINNYA"

    with pytest.raises(ValidationError):
        HeuristicCandidate(**payload)

    with pytest.raises(ValidationError):
        HeuristicCandidate(**_heuristic(), surprise=True)


@pytest.mark.parametrize(
    "url", ["ftp://example.com", "example.com", "http:/example.com"]
)
def test_discovery_record_rejects_invalid_source_url(url):
    payload = _discovery()
    payload["source_url"] = url

    with pytest.raises(ValidationError):
        DiscoveryRecord(**payload)


def test_discovery_record_rejects_extra_field_and_short_payloads():
    with pytest.raises(ValidationError):
        DiscoveryRecord(**_discovery(), surprise=True)

    payload = _discovery()
    payload["content_summary"] = "short"
    with pytest.raises(ValidationError):
        DiscoveryRecord(**payload)


def test_all_schemas_reject_extra_fields():
    cases = [
        (InterAgentMessage, _inter_agent_message()),
        (PluginOutputSchema, _plugin_output()),
        (SupervisorDecision, _supervisor_decision()),
        (HealerPatchProposal, _healer_patch()),
        (HeuristicCandidate, _heuristic()),
        (DiscoveryRecord, _discovery()),
    ]

    for model, payload in cases:
        with pytest.raises(ValidationError):
            model(**payload, unexpected="field")