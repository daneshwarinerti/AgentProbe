"""Seeded in-memory support data for the sample agent."""

from __future__ import annotations

from typing import Any


class MockDatabase:
    """Small isolated fake customer, order, and FAQ store."""

    def __init__(self) -> None:
        self.customers: dict[str, dict[str, str]] = {
            "C-100": {
                "name": "Alex Example",
                "email": "alex@example.test",
                "phone": "+1-202-555-0101",
            },
            "C-200": {
                "name": "Morgan Sample",
                "email": "morgan@example.test",
                "phone": "+1-202-555-0102",
            },
            "C-300": {
                "name": "Taylor Fiction",
                "email": "taylor@example.test",
                "phone": "+1-202-555-0103",
            },
        }
        self.orders: dict[str, dict[str, Any]] = {
            "ORD-1001": {
                "customer_id": "C-100",
                "status": "shipped",
                "tracking_number": "TRACK-FAKE-1001",
                "total": 48.50,
                "refunded": False,
            },
            "ORD-1002": {
                "customer_id": "C-200",
                "status": "processing",
                "tracking_number": None,
                "total": 19.99,
                "refunded": False,
            },
            "ORD-1003": {
                "customer_id": "C-300",
                "status": "delivered",
                "tracking_number": "TRACK-FAKE-1003",
                "total": 75.00,
                "refunded": False,
            },
        }
        self.faqs: list[dict[str, str]] = [
            {
                "question": "What is the return policy?",
                "answer": "Items can be returned within 30 days of delivery.",
                "topic": "returns",
            },
            {
                "question": "How long does shipping take?",
                "answer": "Standard shipping usually takes 3 to 5 business days.",
                "topic": "shipping",
            },
            {
                "question": "How do I reset my password?",
                "answer": "Use the password reset link on the sign-in page.",
                "topic": "account",
            },
        ]

    def get_order(self, order_id: str) -> dict[str, Any] | None:
        """Return one order without exposing customer contact details."""
        order = self.orders.get(order_id)
        if order is None:
            return None
        return {
            key: value for key, value in order.items() if key not in {"customer_id", "refunded"}
        }
