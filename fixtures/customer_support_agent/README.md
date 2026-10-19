# Customer Support Multi-Agent Project

This fixture project demonstrates a production-grade multi-agent workflow instrumented with `agentprobe_sdk`.

## Agents
1. **triage_agent**: Central router classifying inquiries and orchestrating handoffs.
2. **billing_agent**: Specialist handling refunds and account balance checks.
3. **tech_support_agent**: Specialist handling API token resets and knowledge base questions.

## Entry Point
`support_bot.entrypoint:run_agent`

## Instrumentation
Uses `@traced_agent`, `@traced_tool`, and `trace_handoff` from `agentprobe_sdk` to provide complete multi-agent attribution in AgentProbe.

