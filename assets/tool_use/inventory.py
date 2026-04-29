"""Mini inventory accounting module — used as fixture for diff-edit benchmark.

The function calculate_total() is called from 3 different places. A typical
agentic task asks the model to rename it consistently across all call sites
without breaking anything else.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass
class LineItem:
    sku: str
    quantity: int
    unit_price_cents: int
    discount_pct: float = 0.0


def calculate_total(items: Iterable[LineItem]) -> int:
    """Return total in cents, applying per-item discount."""
    total = 0
    for item in items:
        gross = item.quantity * item.unit_price_cents
        net = int(gross * (1.0 - item.discount_pct))
        total += net
    return total


def format_receipt(items: list[LineItem], currency: str = "EUR") -> str:
    """Render a receipt showing each line and the grand total."""
    lines = []
    for it in items:
        lines.append(
            f"{it.sku:<10} x{it.quantity:>3}  {it.unit_price_cents / 100:>7.2f} {currency}"
            + (f"  (-{int(it.discount_pct * 100)}%)" if it.discount_pct else "")
        )
    grand_total = calculate_total(items)
    lines.append("-" * 40)
    lines.append(f"{'TOTAL':<10}        {grand_total / 100:>7.2f} {currency}")
    return "\n".join(lines)


def average_basket(baskets: list[list[LineItem]]) -> float:
    """Mean basket value across multiple baskets, in EUR."""
    if not baskets:
        return 0.0
    totals = [calculate_total(b) for b in baskets]
    return sum(totals) / len(totals) / 100.0


def basket_above_threshold(items: list[LineItem], threshold_eur: float) -> bool:
    """True if the basket total exceeds the given threshold in EUR."""
    return calculate_total(items) > threshold_eur * 100


# ---- demo data --------------------------------------------------------------


def _demo_basket() -> list[LineItem]:
    return [
        LineItem("BOOK-DE-001", 2, 1499, discount_pct=0.0),
        LineItem("MUG-CER-RED", 1, 1290, discount_pct=0.10),
        LineItem("SHIRT-L-NVY", 3, 2499, discount_pct=0.05),
        LineItem("STICKER-PK1", 5, 199, discount_pct=0.0),
    ]


def _demo_baskets() -> list[list[LineItem]]:
    return [
        _demo_basket(),
        [LineItem("LAPTOP-PRO", 1, 184900, discount_pct=0.0)],
        [
            LineItem("HEADPHONE-X", 1, 14990, discount_pct=0.15),
            LineItem("CABLE-USBC", 2, 1299, discount_pct=0.0),
        ],
    ]


if __name__ == "__main__":
    basket = _demo_basket()
    print(format_receipt(basket))
    print()
    print(f"Average basket: {average_basket(_demo_baskets()):.2f} EUR")
    print(
        "Above 50 EUR? " + ("yes" if basket_above_threshold(basket, 50.0) else "no")
    )
