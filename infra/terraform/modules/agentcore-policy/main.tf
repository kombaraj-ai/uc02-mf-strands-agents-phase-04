# Just the policy engine - a pure leaf resource, no role ARN and no gateway
# ARN taken as input, mirroring modules/agentcore-memory's exact shape. The
# actual Cedar policies (aws_bedrockagentcore_policy) are NOT created here:
# each one's statement text must embed the real Gateway ARN
# (`resource == AgentCore::Gateway::"<arn>"`), which would make this module
# and modules/agentcore-gateway mutually reference each other's outputs
# (gateway needs this module's policy_engine_arn for
# policy_engine_configuration; the policies need the gateway's arn for their
# Cedar text). Rather than rely on Terraform resolving that shape cleanly,
# the individual policies are declared as standalone root-module resources
# in each environment's main.tf instead - the same "avoid a module cycle
# with one-off root resources" precedent already used for WS8's Gateway-
# routed-tools IAM grants (see environments/*/main.tf's
# runtime_invoke_gateway/lambda_kb_retrieve resources).
resource "aws_bedrockagentcore_policy_engine" "amc_tools" {
  name        = "${replace(var.name_prefix, "-", "_")}_amc_tools_policy"
  description = "Cedar authorization for the AMC quant/qual Gateway tools"
  tags        = var.tags
}
