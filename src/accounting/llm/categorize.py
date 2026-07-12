"""Turn already-categorized transaction descriptions + the category taxonomy into an LLM category suggestion.

Every suggestion is forced onto a real, existing category/subcategory id
before it's ever returned to a caller (see `parse_and_validate_suggestion`)
— the LLM's raw output is never trusted directly, only used to pick from
a closed set. A hallucinated or malformed response degrades to "no
suggestion" rather than corrupting a posting with a category that doesn't exist.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from accounting.models import Category, CategoryClassification

_SYSTEM_PROMPT_TEMPLATE = (
    "You are a personal-finance transaction categorization assistant. Given a bank transaction "
    'description, reply with ONLY a JSON object of the exact form {{"category_id": "...", '
    '"subcategory_id": "..." or null}}, using one of the category ids listed below. Never invent an id '
    "that isn't listed, and never explain your answer outside the JSON object.\n\n"
    "Available categories:\n{taxonomy}"
)


def _taxonomy_text(categories: dict[str, Category], classification: CategoryClassification) -> str:
    """List every top-level category (and its subcategories) for one classification, as prompt context.

    Returns
    -------
    str
        One `- category_id (Name): sub1, sub2` line per top-level category, sorted by name.
    """
    top_level = sorted(
        (c for c in categories.values() if c.classification == classification and c.parent_category_id is None),
        key=lambda c: c.name,
    )
    lines = []
    for category in top_level:
        subcategory_names = sorted(c.name for c in categories.values() if c.parent_category_id == category.category_id)
        subcategories = ", ".join(subcategory_names) if subcategory_names else "no subcategories"
        lines.append(f"- {category.category_id} ({category.name}): {subcategories}")
    return "\n".join(lines)


def build_prompt(
    target_description: str,
    classification: CategoryClassification,
    categories: dict[str, Category],
    examples: list[tuple[str, str, str | None]],
) -> tuple[str, str]:
    """Build the system/user prompt pair for suggesting a category for one posting.

    Parameters
    ----------
    target_description
        The posting's own description — the thing being categorized.
    classification
        Restricts the offered taxonomy to one side (income postings only ever get income categories).
    categories
        Every known category, keyed by `category_id`.
    examples
        Already-categorized `(description, category_id, subcategory_id)` few-shot examples.

    Returns
    -------
    tuple[str, str]
        `(system_prompt, user_prompt)`.
    """
    system_prompt = _SYSTEM_PROMPT_TEMPLATE.format(taxonomy=_taxonomy_text(categories, classification))
    example_lines = "\n".join(
        f'- "{description}" -> {{"category_id": "{category_id}", "subcategory_id": {subcategory_id!r}}}'
        for description, category_id, subcategory_id in examples
    )
    user_prompt = (
        f"Previously categorized examples:\n{example_lines or '(none yet)'}\n\n"
        f'Now categorize this transaction: "{target_description}"'
    )
    return system_prompt, user_prompt


def parse_and_validate_suggestion(
    raw_response: str, categories: dict[str, Category], classification: CategoryClassification
) -> tuple[str | None, str | None]:
    """Parse the LLM's raw text and force it onto a real category/subcategory id.

    Parameters
    ----------
    raw_response
        The LLM's raw text response to `build_prompt`'s prompt pair.
    categories
        Every known category, keyed by `category_id` — the closed set any suggestion must belong to.
    classification
        The suggested category must belong to this side, matching the posting's own sign.

    Returns
    -------
    tuple[str | None, str | None]
        `(category_id, subcategory_id)`, both `None` if the response can't be trusted: unparseable, names a
        category id that doesn't exist, or names one on the wrong side of `classification`.
    """
    cleaned = raw_response.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        return None, None
    if not isinstance(data, dict):
        return None, None

    category_id = data.get("category_id")
    category = categories.get(category_id) if isinstance(category_id, str) else None
    if category is None or category.classification != classification or category.parent_category_id is not None:
        return None, None

    subcategory_id = data.get("subcategory_id")
    subcategory = categories.get(subcategory_id) if isinstance(subcategory_id, str) else None
    if subcategory is not None and subcategory.parent_category_id != category.category_id:
        subcategory = None

    return category.category_id, (subcategory.category_id if subcategory is not None else None)
