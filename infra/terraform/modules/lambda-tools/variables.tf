variable "name_prefix" {
  type = string
}

variable "tool_names" {
  description = "Short names (without prefix) of the tool Lambdas to create, e.g. [\"quant-tools\", \"qual-tools\"]. Must match modules/iam's var.lambda_tool_names exactly - see that module's gateway_role.tf for why."
  type        = list(string)
  default     = ["quant-tools", "qual-tools"]
}

variable "lambda_execution_role_arn" {
  type = string
}

variable "dynamodb_table_name" {
  description = "DynamoDB table the quant-tools Lambda reads from."
  type        = string
}

variable "bedrock_knowledge_base_id" {
  description = "Bedrock Knowledge Base ID the qual-tools Lambda calls Retrieve against. Empty string when enable_knowledge_base = false."
  type        = string
}

variable "memory_size" {
  type    = number
  default = 256
}

variable "timeout_seconds" {
  type    = number
  default = 30
}

variable "log_retention_days" {
  type    = number
  default = 30
}

variable "tags" {
  type    = map(string)
  default = {}
}
