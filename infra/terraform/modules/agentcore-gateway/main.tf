# AWS_IAM auth (SigV4) per the auth-model decision - internal tool, no
# external end users calling the Gateway directly, so no Cognito/JWT infra.
resource "aws_bedrockagentcore_gateway" "amc_tools" {
  name            = "${var.name_prefix}-amc-gateway"
  role_arn        = var.gateway_role_arn
  authorizer_type = "AWS_IAM"
  protocol_type   = "MCP"
  kms_key_arn     = var.kms_key_arn
  description     = "Exposes AMC quant/qual tools as MCP tools for the agent runtime"

  tags = var.tags
}

resource "aws_bedrockagentcore_gateway_target" "lambda_tool" {
  for_each = var.lambda_tools

  name               = "${var.name_prefix}-${each.key}"
  gateway_identifier = aws_bedrockagentcore_gateway.amc_tools.gateway_id
  description        = each.value.description

  credential_provider_configuration {
    gateway_iam_role {}
  }

  target_configuration {
    mcp {
      lambda {
        lambda_arn = each.value.lambda_arn

        tool_schema {
          inline_payload {
            # The MCP-advertised tool name - must match what the calling
            # agent's system prompt (and, for qual, QualGroundingHookProvider)
            # expects. Deliberately not each.key - see variables.tf.
            name        = each.value.mcp_tool_name
            description = each.value.description

            input_schema {
              type        = "object"
              description = "Real per-tool input, matching ${each.value.mcp_tool_name}'s actual signature."

              dynamic "property" {
                for_each = each.value.input_properties
                content {
                  name        = property.value.name
                  type        = property.value.type
                  description = property.value.description
                  required    = property.value.required
                }
              }
            }

            output_schema {
              type = "object"

              dynamic "property" {
                for_each = each.value.output_properties
                content {
                  name     = property.value.name
                  type     = property.value.type
                  required = property.value.required
                }
              }
            }
          }
        }
      }
    }
  }
}
