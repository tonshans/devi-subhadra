"""Locked Pydantic contracts for all internal and LLM-driven I/O."""

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class StrictBaseModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class InterAgentMessage(StrictBaseModel):
    task_id: str = Field(..., min_length=1)
    thread_id: str = Field(..., min_length=1)
    project_context: Dict[str, Any]
    instruction: str = Field(..., min_length=1)
    expected_schema: Dict[str, Any]
    timeout_seconds: int = Field(default=300, ge=10, le=3600)


class PluginOutputSchema(StrictBaseModel):
    success: bool
    data: Dict[str, Any]
    error_message: Optional[str] = None
    assertions_passed: bool = True


class SupervisorDecision(StrictBaseModel):
    action_type: Literal[
        "REPLY_USER", "CALL_PLUGIN", "DELEGATE_WORKER", "CREATE_INITIATIVE", "SLEEP_NOOP"
    ]
    model_tier_used: Literal["TIER_1_LIGHT", "TIER_2_HEAVY"]
    reasoning: str = Field(..., min_length=5)
    thought_entry: str = Field(..., min_length=5)
    target_plugin: Optional[str] = None
    plugin_payload: Optional[Dict[str, Any]] = None
    reply_message: Optional[str] = None


class HealerPatchProposal(StrictBaseModel):
    attempt_number: int = Field(..., ge=1, le=3)
    root_cause_analysis: str = Field(..., min_length=10)
    patched_code: str = Field(..., min_length=1)
    embedded_assertions: List[str] = Field(..., min_length=1)


class HeuristicCandidate(StrictBaseModel):
    category: Literal["KOMUNIKASI", "TEKNIS", "PREFERENSI_USER"]
    heuristic_text: str = Field(..., min_length=10)
    confidence_delta: int = Field(default=10, ge=1, le=50)


class DiscoveryRecord(StrictBaseModel):
    topic_category: str = Field(..., min_length=2)
    content_summary: str = Field(..., min_length=10)
    source_url: str = Field(..., pattern=r"^https?://.+")
    relevance_to_user: str = Field(..., min_length=5)


class MissionReply(StrictBaseModel):
    reply: str = Field(..., min_length=1)
    files: List[str] = Field(default_factory=list, max_length=5)
    # Tool request: the runtime runs the plugin, then asks the model again.
    plugin: Optional[str] = None
    plugin_payload: Optional[Dict[str, Any]] = None
    # Why the model chose this action; stored as CoT in audit_logs.
    reasoning: str = ""