"""Tools for the customer support multi-agent system."""

from typing import Any
from agentprobe_sdk import traced_tool


@traced_tool
def check_account_balance(account_id: str) -> dict[str, Any]:
    """Check the current customer account balance."""
    accounts = {
        "ACC-001": {"account_id": "ACC-001", "balance_usd": 150.00, "status": "active"},
        "ACC-002": {"account_id": "ACC-002", "balance_usd": 0.00, "status": "delinquent"},
        "ACC-VIP": {"account_id": "ACC-VIP", "balance_usd": 12500.00, "status": "premium"},
    }
    return accounts.get(account_id, {"account_id": account_id, "balance_usd": 50.00, "status": "active"})


@traced_tool
def process_refund(order_id: str, amount: float) -> dict[str, Any]:
    """Process a customer refund for a verified order."""
    if amount <= 0:
        return {"order_id": order_id, "status": "rejected", "reason": "Refund amount must be positive."}
    if amount > 1000.0:
        return {"order_id": order_id, "status": "manual_review", "reason": "Refunds over $1000 require manager approval."}
    return {
        "order_id": order_id,
        "refund_id": f"REF-{order_id.replace('ORD-', '')}",
        "amount_usd": amount,
        "status": "refunded",
    }


@traced_tool
def query_knowledge_base(query: str) -> dict[str, Any]:
    """Search documentation and support knowledge base."""
    kb = {
        "password": "To reset your password, visit account settings and click 'Security > Reset Password'.",
        "api": "API tokens can be generated from the Developer Portal under 'API Keys'.",
        "export": "You can export your project data as CSV or JSON from the Reports tab.",
    }
    for key, text in kb.items():
        if key in query.lower():
            return {"query": query, "found": True, "answer": text}
    return {"query": query, "found": True, "answer": "For general assistance, please contact support@example.com."}


@traced_tool
def reset_api_token(account_id: str) -> dict[str, Any]:
    """Issue a new API token for the specified account."""
    return {
        "account_id": account_id,
        "new_token_prefix": "agp_live_abc123",
        "status": "token_regenerated",
    }

