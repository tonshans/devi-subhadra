"""Portable SQLAlchemy Core schema shared by SQLite and PostgreSQL.

Timestamps are stored as fixed-width UTC strings (see db.timestamps) so ordering
and restoring are identical on both engines. `is_backup` tracks rows that still
need to be copied to the SQLite snapshot.
"""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Float,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    false,
)

from core.db.timestamps import utc_now_text

metadata = MetaData()

core_directives = Table(
    "core_directives",
    metadata,
    Column("directive_id", String(36), primary_key=True),
    Column("directive_type", String(32), nullable=False),
    Column("content", Text, nullable=False),
    Column("updated_at", String(32), nullable=False, default=utc_now_text),
    Column("is_backup", Boolean, nullable=False, default=False, server_default=false()),
    CheckConstraint(
        "directive_type IN ('VISION', 'IMMUTABLE_PERSONA', 'SYSTEM_ANATOMY')",
        name="ck_core_directives_type",
    ),
)

semantic_memory = Table(
    "semantic_memory",
    metadata,
    Column("rule_id", String(36), primary_key=True),
    Column("category", String(32), nullable=False),
    Column("heuristic_text", Text, nullable=False),
    Column("confidence_score", Integer, nullable=False, default=50),
    Column("last_used_at", String(32), nullable=False, default=utc_now_text),
    Column("is_archived", Integer, nullable=False, default=0),
    Column("is_backup", Boolean, nullable=False, default=False, server_default=false()),
    CheckConstraint(
        "category IN ('KOMUNIKASI', 'TEKNIS', 'PREFERENSI_USER')",
        name="ck_semantic_memory_category",
    ),
    CheckConstraint(
        "confidence_score >= 0 AND confidence_score <= 100",
        name="ck_semantic_memory_confidence",
    ),
)

plugin_registry = Table(
    "plugin_registry",
    metadata,
    Column("plugin_name", String(64), primary_key=True),
    Column("tier_type", String(8), nullable=False),
    Column("description_summary", Text, nullable=False),
    Column("last_used_at", String(32), nullable=False, default=utc_now_text),
    Column("is_archived", Integer, nullable=False, default=0),
    Column("is_backup", Boolean, nullable=False, default=False, server_default=false()),
    CheckConstraint(
        "tier_type IN ('CORE', 'ATELIER')", name="ck_plugin_registry_tier"
    ),
)

ledger_threads = Table(
    "ledger_threads",
    metadata,
    Column("thread_id", String(36), primary_key=True),
    Column("title", Text, nullable=False),
    Column("status", String(16), nullable=False),
    Column("origin", String(8), nullable=False),
    Column("priority_score", Integer, nullable=False),
    Column("is_human_endorsed", Integer, nullable=False, default=0),
    Column("expected_output", Text, nullable=True),
    Column("created_at", String(32), nullable=False, default=utc_now_text),
    Column("is_backup", Boolean, nullable=False, default=False, server_default=false()),
    CheckConstraint(
        "status IN ('Active', 'Paused', 'Completed', 'Archived')",
        name="ck_ledger_threads_status",
    ),
    CheckConstraint("origin IN ('HUMAN', 'AGENT')", name="ck_ledger_threads_origin"),
)

ledger_entries = Table(
    "ledger_entries",
    metadata,
    Column("entry_id", String(36), primary_key=True),
    Column(
        "thread_id",
        String(36),
        ForeignKey("ledger_threads.thread_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("tag", String(16), nullable=False),
    Column("content", Text, nullable=False),
    Column("token_count", Integer, nullable=False, default=0),
    Column("is_archived", Integer, nullable=False, default=0),
    Column("created_at", String(32), nullable=False, default=utc_now_text),
    Column("is_backup", Boolean, nullable=False, default=False, server_default=false()),
    Column(
        "is_vectorized", Boolean, nullable=False, default=False, server_default=false()
    ),
    CheckConstraint(
        "tag IN ('FAKTA', 'TINDAKAN', 'PEMIKIRAN')", name="ck_ledger_entries_tag"
    ),
)

atelier_discoveries = Table(
    "atelier_discoveries",
    metadata,
    Column("discovery_id", String(36), primary_key=True),
    Column("topic_category", Text, nullable=False),
    Column("content_summary", Text, nullable=False),
    Column("source_url", Text, nullable=False),
    Column("relevance_to_user", Text, nullable=False),
    Column("extracted_at", String(32), nullable=False, default=utc_now_text),
    Column("is_backup", Boolean, nullable=False, default=False, server_default=false()),
    Column(
        "is_vectorized", Boolean, nullable=False, default=False, server_default=false()
    ),
)

activity_logs = Table(
    "activity_logs",
    metadata,
    Column("log_id", String(36), primary_key=True),
    Column(
        "thread_id",
        String(36),
        ForeignKey("ledger_threads.thread_id"),
        nullable=True,
    ),
    Column("source", String(8), nullable=False),
    Column("initiator", String(32), nullable=False),
    Column("reasoning", Text, nullable=False),
    Column("budget_bucket", String(16), nullable=False),
    Column("tokens_used", Integer, nullable=False, default=0),
    Column("cost_usd", Float, nullable=False, default=0.0),
    Column("created_at", String(32), nullable=False, default=utc_now_text),
    Column("is_backup", Boolean, nullable=False, default=False, server_default=false()),
    CheckConstraint("source IN ('HUMAN', 'AGENT')", name="ck_activity_logs_source"),
    CheckConstraint(
        "initiator IN ('Telegram', 'Inner_Monologue', 'Healer_Node', 'Sleep_Mode', 'Webhook')",
        name="ck_activity_logs_initiator",
    ),
    CheckConstraint(
        "budget_bucket IN ('CORE_70', 'ATELIER_30', 'HUMAN_EXEMPT')",
        name="ck_activity_logs_bucket",
    ),
)

api_usage_metrics = Table(
    "api_usage_metrics",
    metadata,
    Column("metric_id", String(36), primary_key=True),
    Column("provider", Text, nullable=False),
    Column("model_name", Text, nullable=False),
    Column("window_minute", Text, nullable=False),
    Column("requests_count", Integer, nullable=False, default=0),
    Column("tokens_count", Integer, nullable=False, default=0),
    Column("is_backup", Boolean, nullable=False, default=False, server_default=false()),
)

system_state = Table(
    "system_state",
    metadata,
    Column("key", Text, primary_key=True),
    Column("value", Text, nullable=False),
    Column("updated_at", String(32), nullable=False, default=utc_now_text),
    Column("is_backup", Boolean, nullable=False, default=False, server_default=false()),
)

processed_updates = Table(
    "processed_updates",
    metadata,
    Column("update_id", Integer, primary_key=True),
    Column("processed_at", String(32), nullable=False, default=utc_now_text),
    Column("is_backup", Boolean, nullable=False, default=False, server_default=false()),
)

budget_usage = Table(
    "budget_usage",
    metadata,
    Column("window_date", String(10), primary_key=True),
    Column("bucket", String(16), primary_key=True),
    Column("spent_usd", Float, nullable=False, default=0.0),
    Column("is_backup", Boolean, nullable=False, default=False, server_default=false()),
)

# One row per provider window (minute or day) and request side.
rate_limit_usage = Table(
    "rate_limit_usage",
    metadata,
    Column("window_key", String(32), primary_key=True),
    Column("side", String(8), primary_key=True),
    Column("requests_count", Integer, nullable=False, default=0),
    Column("tokens_count", Integer, nullable=False, default=0),
    Column("is_backup", Boolean, nullable=False, default=False, server_default=false()),
    CheckConstraint("side IN ('HUMAN', 'AGENT')", name="ck_rate_limit_usage_side"),
)

# Per provider-key usage, separate from the human/agent split above.
provider_key_usage = Table(
    "provider_key_usage",
    metadata,
    Column("window_key", String(32), primary_key=True),
    Column("key_id", String(32), primary_key=True),
    Column("requests_count", Integer, nullable=False, default=0),
    Column("tokens_count", Integer, nullable=False, default=0),
    Column("is_backup", Boolean, nullable=False, default=False, server_default=false()),
)

# Parent-first order for inserts/restores; deletes walk it backwards.
TABLE_ORDER = (
    core_directives,
    semantic_memory,
    plugin_registry,
    ledger_threads,
    ledger_entries,
    atelier_discoveries,
    activity_logs,
    api_usage_metrics,
    system_state,
    processed_updates,
    budget_usage,
    rate_limit_usage,
    provider_key_usage,
)
