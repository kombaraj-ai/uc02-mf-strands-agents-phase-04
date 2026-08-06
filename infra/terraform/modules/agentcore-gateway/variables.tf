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

variable "tags" {
  type    = map(string)
  default = {}
}
