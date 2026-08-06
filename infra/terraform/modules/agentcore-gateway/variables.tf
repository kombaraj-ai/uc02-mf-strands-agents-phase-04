variable "name_prefix" {
  type = string
}

variable "gateway_role_arn" {
  type = string
}

variable "kms_key_arn" {
  description = "Optional CMK for gateway data encryption. Null uses AWS-managed encryption."
  type        = string
  default     = null
}

variable "lambda_tools" {
  description = <<-EOT
    Map of tool short-name (e.g. "quant-tools") -> real per-tool config.
    One gateway_target per entry. `mcp_tool_name` is the tool name actually
    advertised over MCP - this must match what the agents' system prompts
    reference (e.g. "get_fund_performance") and, for qual specifically, the
    exact tool name observability/hooks.py's QualGroundingHookProvider
    pattern-matches on ("search_fund_commentary") - it is deliberately a
    separate field from the map key, which stays the Terraform-side target
    resource name.
  EOT
  type = map(object({
    lambda_arn    = string
    description   = string
    mcp_tool_name = string
    input_properties = list(object({
      name        = string
      type        = string
      description = string
      required    = bool
    }))
    output_properties = list(object({
      name     = string
      type     = string
      required = bool
    }))
  }))
}

variable "policy_engine_arn" {
  description = "ARN of the AgentCore Policy engine (modules/agentcore-policy's policy_engine_arn output) to attach for Cedar-based tool authorization. Empty string (default) means no policy engine is attached - the gateway allows any tool call its own IAM role permits, exactly as before Policy existed."
  type        = string
  default     = ""
}

variable "policy_engine_mode" {
  description = "Enforcement mode when policy_engine_arn is set: \"LOG_ONLY\" evaluates and logs every request without blocking (the safe default for initial rollout - see infra/terraform/README.md), \"ENFORCE\" actually denies requests default-deny/forbid-wins. Ignored when policy_engine_arn is empty."
  type        = string
  default     = "LOG_ONLY"

  validation {
    condition     = contains(["LOG_ONLY", "ENFORCE"], var.policy_engine_mode)
    error_message = "policy_engine_mode must be \"LOG_ONLY\" or \"ENFORCE\"."
  }
}

variable "tags" {
  type    = map(string)
  default = {}
}
