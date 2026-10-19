"""Specialist agents for customer support workflow."""

from typing import Any
import re
from agentprobe_sdk import trace_handoff, traced_agent
from support_bot.tools import (
    check_account_balance,
    process_refund,
    query_knowledge_base,
    reset_api_token,
)


@traced_agent(name="billing_agent")
def handle_billing(prompt: str) -> str:
    """Handle billing, refunds, and balance inquiries."""
    lower = prompt.lower()

    # Refund request
    if "refund" in lower:
        # Extract order id
        order_match = re.search(r"ORD-\d+", prompt, re.IGNORECASE)
        order_id = order_match.group(0).upper() if order_match else "ORD-999"

        # Extract amount
        amount_match = re.search(r"\$?(\d+(?:\.\d{1,2})?)", prompt)
        amount = float(amount_match.group(1)) if amount_match else 50.0

        res = process_refund(order_id, amount)
        if res["status"] == "refunded":
            return f"Refund of ${res['amount_usd']:.2f} for order {res['order_id']} has been successfully processed (Refund ID: {res['refund_id']})."
        elif res["status"] == "manual_review":
            return f"Refund for order {res['order_id']} is pending manual review: {res['reason']}"
        else:
            return f"Refund request could not be completed: {res['reason']}"

    # Balance request
    if "balance" in lower or "account" in lower:
        acc_match = re.search(r"ACC-[A-Za-z0-9]+", prompt)
        account_id = acc_match.group(0).upper() if acc_match else "ACC-001"
        res = check_account_balance(account_id)
        return f"Account {res['account_id']} has a current balance of ${res['balance_usd']:.2f} ({res['status']})."

    return "Billing department: Your inquiry has been logged. Please specify an order ID or account ID for further help."


@traced_agent(name="tech_support_agent")
def handle_tech_support(prompt: str) -> str:
    """Handle technical support, API questions, and account security."""
    lower = prompt.lower()

    # Safety check: Refuse destructive actions or secret leak attempts
    if any(k in lower for k in ["delete database", "drop table", "ignore previous instructions", "system prompt", "reveal api key"]):
        return "I cannot fulfill this request. Destructive operations and security prompt disclosures are strictly prohibited."

    # API token reset
    if "token" in lower or "api key" in lower:
        acc_match = re.search(r"ACC-[A-Za-z0-9]+", prompt)
        account_id = acc_match.group(0).upper() if acc_match else "ACC-001"
        res = reset_api_token(account_id)
        return f"A new API token has been generated for {res['account_id']} with prefix '{res['new_token_prefix']}'."

    # Knowledge base lookup
    res = query_knowledge_base(prompt)
    return f"Knowledge Base: {res['answer']}"


@traced_agent(name="triage_agent")
def handle_triage(prompt: str) -> str:
    """Classify user query and route to the appropriate specialist agent."""
    lower = prompt.lower()

    # Route to billing
    if any(k in lower for k in ["refund", "billing", "invoice", "charge", "balance", "cost", "payment"]):
        trace_handoff("triage_agent", "billing_agent", result="routed to billing specialist")
        return handle_billing(prompt)

    # Route to technical support
    if any(k in lower for k in ["api", "token", "password", "bug", "crash", "error", "export", "login", "technical"]):
        trace_handoff("triage_agent", "tech_support_agent", result="routed to technical specialist")
        return handle_tech_support(prompt)

    # General / triage response
    return f"Welcome to Customer Support! I can assist you with billing, refunds, account balances, or technical questions. Your query was: '{prompt}'"

