"""Centralized, environment-aware configuration.

Every other module reads configuration through `get_settings()` - never via
`os.getenv` directly - so DEV/STAGING/PROD only ever differ by which env file
is loaded, never by branching application code.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

# Repo root: .../Phase-01 (three parents up from this file: config -> amc_orchestrator -> src)
REPO_ROOT = Path(__file__).resolve().parents[3]


def _env_file_for(environment: str) -> Path:
    return REPO_ROOT / "environments" / f".env.{environment}"


class Settings(BaseSettings):
    """Application settings, one instance per process, loaded from the env
    file selected by the `ENVIRONMENT` variable (defaults to "dev")."""

    model_config = SettingsConfigDict(extra="ignore")

    environment: Literal["dev", "staging", "prod"] = "dev"

    # --- Model provider selection ---
    # Which LLM actually generates responses, independent of `environment`:
    # STAGING/PROD always use Bedrock regardless of this setting (see
    # `effective_model_provider`); DEV respects it, defaulting to Ollama but
    # allowing an opt-in to Bedrock for use cases where local CPU-only
    # generation is too slow. Needs AWS credentials configured to actually
    # work, and incurs real per-request cost even from DEV.
    model_provider: Literal["ollama", "bedrock"] = "ollama"

    # --- Model provider (Ollama; used when effective_model_provider == "ollama") ---
    ollama_host: str = "http://localhost:11434"
    ollama_model_id: str = "qwen2.5:7b-instruct"

    # --- Model provider (Bedrock; used when effective_model_provider == "bedrock") ---
    bedrock_model_id: str = "anthropic.claude-3-5-sonnet-20241022-v2:0"
    aws_region: str = "us-east-1"

    # --- Shared model tuning ---
    # model_temperature_judge was 0.15 through Phase 03; lowered to 0.0 in
    # Phase 04 after live testing on dev reproduced a genuine
    # escalation-after-3-attempts outcome (INC2, repeated identical
    # invocations) even with the qual-agent-fabrication bug already fixed.
    # Kept at 0.0 as a correct, no-downside change - a judge has no need for
    # creative variance - but a live A/B (5 runs before, 5 after) showed the
    # SAME ~1-in-5 escalation rate at 0.0 as before, so this alone does NOT
    # eliminate the anomaly: model_temperature_worker (0.2, below) still
    # makes the draft TEXT the judge evaluates vary run to run, so even a
    # fully deterministic judge can legitimately reject a genuinely
    # different draft. Root cause is not fully closed - see CLAUDE.md's
    # Phase 04 WS4 entry. Treated as the system's designed resilience
    # contract (graceful escalation, never fabrication) working as intended
    # under inherent LLM variance, not a defect being actively chased.
    model_temperature_judge: float = 0.0
    model_temperature_worker: float = 0.2
    model_temperature_synthesis: float = 0.4

    # --- Data backend selection ---
    # Which data stores the app actually reads/writes, independent of `environment` -
    # STAGING/PROD always use "aws" regardless of this setting (see
    # `effective_data_backend`); DEV respects it, defaulting to "local" but allowing
    # opt-in AWS testing against Phase 02's Terraform-provisioned DynamoDB table /
    # Bedrock Knowledge Base before a full staging cutover.
    data_backend: Literal["local", "aws"] = "local"

    # --- Quantitative data store ---
    sqlite_path: str = "local_dev.db"
    # Populated from Terraform's `dynamodb_table_name` output; used when
    # effective_data_backend == "aws".
    dynamodb_table_name: str = ""

    # --- Qualitative data store ---
    chroma_persist_dir: str = "data/chroma"
    chroma_collection_name: str = "fund_manager_commentary"
    # Populated from Terraform's `knowledge_base_id` output; used when
    # effective_data_backend == "aws".
    bedrock_knowledge_base_id: str = ""

    # --- Tool routing selection (WS8) ---
    # Which transport agents use to call get_fund_performance/search_fund_commentary:
    # in-process Python calls (default, everywhere) or the real AgentCore Gateway
    # (MCP over SigV4). Unlike model_provider/data_backend, this has NO
    # effective_* override forcing "gateway" in STAGING/PROD - Gateway-routing
    # has no correctness requirement the in-process path can't already satisfy
    # (the in-process tools already call the real AWS-backed stores everywhere
    # via effective_data_backend), so there's no reason to force a newer,
    # less-proven code path before it has real production mileage. Pure
    # opt-in everywhere, user-confirmed - see docs/architecture.md's WS8 notes.
    tool_backend: Literal["in_process", "gateway"] = "in_process"
    # Populated from Terraform's `gateway_url` output; used when
    # effective_tool_backend == "gateway".
    gateway_url: str = ""

    # --- Compliance self-correction loop ---
    max_compliance_attempts: int = 3
    # Retries within a single compliance_check node call when qwen2.5:7b-instruct
    # fails to invoke the structured-output tool (StructuredOutputException) -
    # separate from max_compliance_attempts, which governs REJECTED verdicts.
    compliance_structured_output_max_attempts: int = 3
    # Note: Strands GraphBuilder.set_execution_timeout() takes SECONDS, not ms.
    graph_execution_timeout_seconds: int = 300
    graph_max_node_executions: int = 12

    # --- API server ---
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    cors_origins: str = "*"

    # --- Observability ---
    log_level: str = "DEBUG"
    log_format: Literal["json", "console"] = "json"

    @property
    def sqlite_full_path(self) -> Path:
        path = Path(self.sqlite_path)
        return path if path.is_absolute() else REPO_ROOT / path

    @property
    def chroma_full_path(self) -> Path:
        path = Path(self.chroma_persist_dir)
        return path if path.is_absolute() else REPO_ROOT / path

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def effective_model_provider(self) -> Literal["ollama", "bedrock"]:
        """Resolve the model provider actually used, applying the staging/prod override.

        STAGING/PROD always use Bedrock regardless of `model_provider` - a
        compliance/production requirement, not a developer preference. DEV
        respects `model_provider` as configured, so a given run can opt into
        Bedrock without needing a separate environment.
        """
        if self.environment != "dev":
            return "bedrock"
        return self.model_provider

    @property
    def effective_data_backend(self) -> Literal["local", "aws"]:
        """Resolve the data backend actually used, applying the staging/prod override.

        STAGING/PROD always use the AWS-backed stores (DynamoDB, Bedrock Knowledge
        Base) regardless of `data_backend` - mirrors `effective_model_provider`'s
        reasoning exactly. DEV respects `data_backend` as configured, so a given run
        can opt into the real AWS resources without needing a separate environment.
        """
        if self.environment != "dev":
            return "aws"
        return self.data_backend

    @property
    def effective_tool_backend(self) -> Literal["in_process", "gateway"]:
        """Resolve the tool-routing backend actually used.

        Deliberately NOT environment-forced, unlike effective_model_provider/
        effective_data_backend above - see tool_backend's own comment for why.
        """
        return self.tool_backend


@lru_cache
def get_settings() -> Settings:
    """Return the cached process-wide Settings instance.

    The `ENVIRONMENT` variable (default "dev") selects which env file under
    `environments/` is loaded; explicit process environment variables always
    take precedence over the file's values.
    """
    environment = os.getenv("ENVIRONMENT", "dev").lower()
    env_file = _env_file_for(environment)
    return Settings(_env_file=env_file if env_file.exists() else None)  # type: ignore[call-arg]
