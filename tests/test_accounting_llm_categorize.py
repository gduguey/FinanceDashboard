from accounting.llm.categorize import build_prompt, parse_and_validate_suggestion
from accounting.models import Category

FOOD = Category(category_id="expense:food", name="Food & Drink", classification="expense", color="#abc")
GROCERIES = Category(
    category_id="expense:food:groceries",
    name="Groceries",
    classification="expense",
    parent_category_id="expense:food",
    color="#abc",
)
FOOD_OTHER = Category(
    category_id="expense:food:other",
    name="Other",
    classification="expense",
    parent_category_id="expense:food",
    color="#abc",
)
SALARY = Category(category_id="income:salary", name="Salary", classification="income", color="#def")
CATEGORIES = {
    FOOD.category_id: FOOD,
    GROCERIES.category_id: GROCERIES,
    FOOD_OTHER.category_id: FOOD_OTHER,
    SALARY.category_id: SALARY,
}


def test_build_prompt_includes_the_target_description_and_taxonomy() -> None:
    system_prompt, user_prompt = build_prompt("TRADER JOES 123", "expense", CATEGORIES, [])
    assert "expense:food" in system_prompt
    assert "Groceries" in system_prompt
    assert "income:salary" not in system_prompt  # wrong classification, not offered
    assert "TRADER JOES 123" in user_prompt


def test_build_prompt_includes_few_shot_examples() -> None:
    _system_prompt, user_prompt = build_prompt(
        "WHOLE FOODS", "expense", CATEGORIES, [("TRADER JOES", "expense:food", "expense:food:groceries")]
    )
    assert "TRADER JOES" in user_prompt
    assert "expense:food:groceries" in user_prompt


def test_parse_and_validate_suggestion_accepts_a_valid_response() -> None:
    response = '{"category_id": "expense:food", "subcategory_id": "expense:food:groceries"}'
    category_id, subcategory_id = parse_and_validate_suggestion(response, CATEGORIES, "expense")
    assert category_id == "expense:food"
    assert subcategory_id == "expense:food:groceries"


def test_parse_and_validate_suggestion_strips_markdown_code_fences() -> None:
    response = '```json\n{"category_id": "expense:food", "subcategory_id": null}\n```'
    category_id, subcategory_id = parse_and_validate_suggestion(response, CATEGORIES, "expense")
    assert category_id == "expense:food"
    assert subcategory_id == "expense:food:other"


def test_parse_and_validate_suggestion_rejects_unparseable_text() -> None:
    assert parse_and_validate_suggestion("not json at all", CATEGORIES, "expense") == (None, None)


def test_parse_and_validate_suggestion_rejects_a_hallucinated_category_id() -> None:
    response = '{"category_id": "expense:made-up", "subcategory_id": null}'
    assert parse_and_validate_suggestion(response, CATEGORIES, "expense") == (None, None)


def test_parse_and_validate_suggestion_rejects_a_category_on_the_wrong_side() -> None:
    response = '{"category_id": "income:salary", "subcategory_id": null}'
    assert parse_and_validate_suggestion(response, CATEGORIES, "expense") == (None, None)


def test_parse_and_validate_suggestion_drops_a_subcategory_that_belongs_to_a_different_parent() -> None:
    other_parent = Category(
        category_id="expense:other:sub",
        name="Sub",
        classification="expense",
        parent_category_id="expense:other",
        color="#abc",
    )
    categories = {**CATEGORIES, other_parent.category_id: other_parent}
    response = f'{{"category_id": "expense:food", "subcategory_id": "{other_parent.category_id}"}}'
    category_id, subcategory_id = parse_and_validate_suggestion(response, categories, "expense")
    assert category_id == "expense:food"
    # Dropped for belonging to the wrong parent, but "expense:food" still has
    # subcategories of its own, so it falls back to its "Other" catch-all
    # rather than leaving the posting without a subcategory at all.
    assert subcategory_id == "expense:food:other"


def test_parse_and_validate_suggestion_defaults_to_other_when_llm_omits_a_subcategory() -> None:
    response = '{"category_id": "expense:food", "subcategory_id": null}'
    category_id, subcategory_id = parse_and_validate_suggestion(response, CATEGORIES, "expense")
    assert category_id == "expense:food"
    assert subcategory_id == "expense:food:other"


def test_parse_and_validate_suggestion_leaves_subcategory_none_when_category_has_no_subcategories() -> None:
    response = '{"category_id": "income:salary", "subcategory_id": null}'
    category_id, subcategory_id = parse_and_validate_suggestion(response, CATEGORIES, "income")
    assert category_id == "income:salary"
    assert subcategory_id is None


def test_parse_and_validate_suggestion_leaves_subcategory_none_when_other_catch_all_is_missing() -> None:
    # Defensive case: in production `store.normalize_categories` guarantees
    # an "Other" catch-all whenever a category has any real subcategory,
    # but this shouldn't ever raise if that invariant were somehow violated.
    categories_without_other = {FOOD.category_id: FOOD, GROCERIES.category_id: GROCERIES, SALARY.category_id: SALARY}
    response = '{"category_id": "expense:food", "subcategory_id": null}'
    category_id, subcategory_id = parse_and_validate_suggestion(response, categories_without_other, "expense")
    assert category_id == "expense:food"
    assert subcategory_id is None
