import pytest

from accounting.models import (
    Budget,
    Category,
    CategoryPattern,
    PostingSplit,
    PostingSplitLeg,
    Tag,
)
from accounting.taxonomy import (
    CATEGORY_COLOR_PALETTE,
    CategoryReferences,
    category_ids_to_delete,
    default_categories,
    next_available_color,
    normalize_categories,
    plan_category_rename,
    plan_tag_rename,
    remap_category_ids,
    slugify,
    uncategorize_category_ids,
)


def test_slugify_lowercases_and_hyphenates() -> None:
    assert slugify("Food & Drink") == "food-drink"
    assert slugify("  Bank Fees  ") == "bank-fees"


def test_default_categories_builds_a_two_level_tree() -> None:
    categories = default_categories()
    food = categories["expense:food-drink"]
    assert food.parent_category_id is None
    assert food.classification == "expense"
    groceries = categories["expense:food-drink:groceries"]
    assert groceries.parent_category_id == "expense:food-drink"
    assert groceries.color != food.color

    salary = categories["income:salary"]
    assert salary.classification == "income"


def test_default_categories_never_repeats_a_color() -> None:
    categories = default_categories()
    colors = [category.color.lower() for category in categories.values()]
    assert len(colors) == len(set(colors))
    assert "#ffffff" not in colors


def test_category_color_palette_has_hundreds_of_distinct_non_white_colors() -> None:
    lowered = [color.lower() for color in CATEGORY_COLOR_PALETTE]
    assert len(CATEGORY_COLOR_PALETTE) >= 200
    assert len(lowered) == len(set(lowered))
    assert "#ffffff" not in lowered


def test_next_available_color_skips_colors_already_in_use() -> None:
    used = {CATEGORY_COLOR_PALETTE[0], CATEGORY_COLOR_PALETTE[1]}
    assert next_available_color(used) == CATEGORY_COLOR_PALETTE[2]


def test_next_available_color_is_case_insensitive() -> None:
    used = {CATEGORY_COLOR_PALETTE[0].upper()}
    assert next_available_color(used) != CATEGORY_COLOR_PALETTE[0]


def _cat(
    category_id: str, name: str, parent_category_id: str | None = None, classification: str = "expense"
) -> Category:
    return Category(
        category_id=category_id,
        name=name,
        classification=classification,
        parent_category_id=parent_category_id,
        color="#123456",
    )


def test_plan_category_rename_with_a_new_unique_name_just_renames() -> None:
    categories = {"expense:food": _cat("expense:food", "Food")}
    updated, id_remap = plan_category_rename(categories, "expense:food", "Groceries & Dining")
    assert updated["expense:food"].name == "Groceries & Dining"
    assert id_remap == {}


def test_plan_category_rename_merges_two_top_level_categories_with_the_same_name() -> None:
    categories = {
        "expense:food": _cat("expense:food", "Food"),
        "expense:nourriture": _cat("expense:nourriture", "Nourriture"),
    }
    updated, id_remap = plan_category_rename(categories, "expense:nourriture", "Food")
    assert "expense:nourriture" not in updated
    assert "expense:food" in updated
    assert id_remap == {"expense:nourriture": "expense:food"}


def test_plan_category_rename_never_merges_across_different_classifications() -> None:
    categories = {
        "expense:gifts": _cat("expense:gifts", "Gifts", classification="expense"),
        "income:gifts": _cat("income:gifts", "Gifts", classification="income"),
    }
    updated, id_remap = plan_category_rename(categories, "expense:gifts", "Gifts")
    assert id_remap == {}
    assert "expense:gifts" in updated
    assert "income:gifts" in updated


def test_plan_category_rename_reparents_a_non_matching_subcategory_and_merges_a_matching_one() -> None:
    categories = {
        "expense:food": _cat("expense:food", "Food"),
        "expense:food:snacks": _cat("expense:food:snacks", "Snacks", parent_category_id="expense:food"),
        "expense:nourriture": _cat("expense:nourriture", "Nourriture"),
        "expense:nourriture:snacks": _cat(
            "expense:nourriture:snacks", "Snacks", parent_category_id="expense:nourriture"
        ),
        "expense:nourriture:vegetables": _cat(
            "expense:nourriture:vegetables", "Vegetables", parent_category_id="expense:nourriture"
        ),
    }
    updated, id_remap = plan_category_rename(categories, "expense:nourriture", "Food")
    assert id_remap["expense:nourriture"] == "expense:food"
    assert id_remap["expense:nourriture:snacks"] == "expense:food:snacks"
    assert "expense:nourriture:vegetables" not in id_remap
    assert updated["expense:nourriture:vegetables"].parent_category_id == "expense:food"
    assert "expense:nourriture:snacks" not in updated


def test_plan_category_rename_merges_the_other_catch_all_subcategory_too() -> None:
    categories = normalize_categories({
        "expense:food": _cat("expense:food", "Food"),
        "expense:food:snacks": _cat("expense:food:snacks", "Snacks", parent_category_id="expense:food"),
        "expense:nourriture": _cat("expense:nourriture", "Nourriture"),
        "expense:nourriture:vegetables": _cat(
            "expense:nourriture:vegetables", "Vegetables", parent_category_id="expense:nourriture"
        ),
    })
    assert "expense:food:other" in categories
    assert "expense:nourriture:other" in categories
    updated, id_remap = plan_category_rename(categories, "expense:nourriture", "Food")
    assert id_remap["expense:nourriture:other"] == "expense:food:other"
    assert "expense:nourriture:other" not in updated
    assert "expense:food:other" in updated


def test_plan_category_rename_merges_two_subcategories_under_the_same_parent() -> None:
    categories = {
        "expense:food": _cat("expense:food", "Food"),
        "expense:food:snacks": _cat("expense:food:snacks", "Snacks", parent_category_id="expense:food"),
        "expense:food:chips": _cat("expense:food:chips", "Chips", parent_category_id="expense:food"),
    }
    updated, id_remap = plan_category_rename(categories, "expense:food:chips", "Snacks")
    assert id_remap == {"expense:food:chips": "expense:food:snacks"}
    assert "expense:food:chips" not in updated


def test_plan_category_rename_does_not_merge_a_subcategory_with_the_same_name_under_a_different_parent() -> None:
    categories = {
        "expense:food": _cat("expense:food", "Food"),
        "expense:food:snacks": _cat("expense:food:snacks", "Snacks", parent_category_id="expense:food"),
        "expense:travel": _cat("expense:travel", "Travel"),
        "expense:travel:chips": _cat("expense:travel:chips", "Chips", parent_category_id="expense:travel"),
    }
    updated, id_remap = plan_category_rename(categories, "expense:travel:chips", "Snacks")
    assert id_remap == {}
    assert "expense:food:snacks" in updated
    assert "expense:travel:chips" in updated
    assert updated["expense:travel:chips"].name == "Snacks"


def test_remap_category_ids_updates_every_reference() -> None:
    references = CategoryReferences(
        category_patterns={
            "p1": CategoryPattern(
                pattern_id="p1",
                description_contains="y",
                category_id="expense:nourriture",
                subcategory_id=None,
            )
        },
        budgets=[
            Budget(
                budget_id="2026-06:expense:nourriture",
                month="2026-06",
                category_id="expense:nourriture",
                subcategory_id=None,
                amount=100.0,
                currency="USD",
            ),
            Budget(budget_id=":expense:nourriture", month=None, category_id="expense:nourriture", amount=50.0),
        ],
    )
    id_remap = {"expense:nourriture": "expense:food", "expense:nourriture:snacks": "expense:food:snacks"}
    updated = remap_category_ids(references, id_remap)
    assert updated.category_patterns["p1"].category_id == "expense:food"
    assert {budget.category_id for budget in updated.budgets} == {"expense:food"}
    # The natural key is the identity triple, so a repointed budget's id follows
    # its new category rather than still naming the merged-away one.
    assert {budget.budget_id for budget in updated.budgets} == {"2026-06:expense:food", ":expense:food"}


def test_remap_category_ids_drops_the_merged_away_categorys_colliding_budget() -> None:
    references = CategoryReferences(
        budgets=[
            Budget(budget_id="b1", month="2026-06", category_id="expense:nourriture", amount=100.0),
            Budget(budget_id="b2", month="2026-06", category_id="expense:food", amount=200.0),
        ]
    )
    id_remap = {"expense:nourriture": "expense:food"}
    updated = remap_category_ids(references, id_remap)
    assert len(updated.budgets) == 1
    assert updated.budgets[0].amount == pytest.approx(200.0)


def test_remap_category_ids_drops_the_merged_away_categorys_colliding_general_budget() -> None:
    references = CategoryReferences(
        budgets=[
            Budget(budget_id=":expense:nourriture", month=None, category_id="expense:nourriture", amount=50.0),
            Budget(budget_id=":expense:food", month=None, category_id="expense:food", amount=75.0),
        ]
    )
    id_remap = {"expense:nourriture": "expense:food"}
    updated = remap_category_ids(references, id_remap)
    assert [budget.budget_id for budget in updated.budgets] == [":expense:food"]
    assert updated.budgets[0].amount == pytest.approx(75.0)


def test_remap_category_ids_keeps_a_general_budget_alongside_a_month_one_for_the_same_category() -> None:
    # `month=None` is its own key, so merging never collapses the standing
    # target into the month one (or the other way round).
    references = CategoryReferences(
        budgets=[
            Budget(budget_id=":expense:nourriture", month=None, category_id="expense:nourriture", amount=50.0),
            Budget(budget_id="2026-06:expense:food", month="2026-06", category_id="expense:food", amount=200.0),
        ]
    )
    updated = remap_category_ids(references, {"expense:nourriture": "expense:food"})
    assert {(budget.month, budget.amount) for budget in updated.budgets} == {(None, 50.0), ("2026-06", 200.0)}


def test_remap_category_ids_with_no_collision_keeps_both_budgets() -> None:
    references = CategoryReferences(
        budgets=[
            Budget(budget_id="b1", month="2026-06", category_id="expense:nourriture", amount=100.0),
            Budget(budget_id="b2", month="2026-07", category_id="expense:food", amount=200.0),
        ]
    )
    id_remap = {"expense:nourriture": "expense:food"}
    updated = remap_category_ids(references, id_remap)
    assert {budget.month for budget in updated.budgets} == {"2026-06", "2026-07"}


def test_category_ids_to_delete_for_a_subcategory_is_just_itself() -> None:
    categories = {
        "expense:food": _cat("expense:food", "Food"),
        "expense:food:snacks": _cat("expense:food:snacks", "Snacks", parent_category_id="expense:food"),
    }
    assert category_ids_to_delete(categories, "expense:food:snacks") == {"expense:food:snacks"}


def test_category_ids_to_delete_for_a_top_level_category_includes_every_subcategory() -> None:
    categories = {
        "expense:food": _cat("expense:food", "Food"),
        "expense:food:snacks": _cat("expense:food:snacks", "Snacks", parent_category_id="expense:food"),
        "expense:food:chips": _cat("expense:food:chips", "Chips", parent_category_id="expense:food"),
        "expense:travel": _cat("expense:travel", "Travel"),
    }
    assert category_ids_to_delete(categories, "expense:food") == {
        "expense:food",
        "expense:food:snacks",
        "expense:food:chips",
    }


def test_uncategorize_category_ids_clears_nullable_references() -> None:
    references = CategoryReferences(
        posting_splits={
            "p1": PostingSplit(
                posting_id="p1",
                legs=[
                    PostingSplitLeg(amount=10.0, category_id="expense:food", subcategory_id="expense:food:snacks"),
                    PostingSplitLeg(amount=20.0, category_id="expense:travel"),
                ],
            )
        },
    )
    updated = uncategorize_category_ids(references, {"expense:food", "expense:food:snacks"})
    assert updated.posting_splits["p1"].legs[0].category_id is None
    assert updated.posting_splits["p1"].legs[0].subcategory_id is None
    assert updated.posting_splits["p1"].legs[1].category_id == "expense:travel"


def test_uncategorize_category_ids_deletes_a_budget_whose_own_category_is_deleted() -> None:
    references = CategoryReferences(
        budgets=[
            Budget(budget_id="b1", month="2026-06", category_id="expense:food", amount=100.0),
            Budget(budget_id="b2", month="2026-06", category_id="expense:travel", amount=200.0),
        ]
    )
    updated = uncategorize_category_ids(references, {"expense:food"})
    assert [budget.category_id for budget in updated.budgets] == ["expense:travel"]


def test_uncategorize_category_ids_only_clears_subcategory_when_just_the_subcategory_is_deleted() -> None:
    references = CategoryReferences(
        budgets=[
            Budget(
                budget_id="b1",
                month="2026-06",
                category_id="expense:food",
                subcategory_id="expense:food:snacks",
                amount=100.0,
            ),
        ]
    )
    updated = uncategorize_category_ids(references, {"expense:food:snacks"})
    assert len(updated.budgets) == 1
    assert updated.budgets[0].category_id == "expense:food"
    assert updated.budgets[0].subcategory_id is None
    assert updated.budgets[0].budget_id == "2026-06:expense:food"


def test_uncategorize_category_ids_clears_a_general_budgets_deleted_subcategory() -> None:
    references = CategoryReferences(
        budgets=[
            Budget(
                budget_id=":expense:food:expense:food:snacks",
                month=None,
                category_id="expense:food",
                subcategory_id="expense:food:snacks",
                amount=50.0,
            ),
            Budget(budget_id=":expense:travel", month=None, category_id="expense:travel", amount=75.0),
        ]
    )
    updated = uncategorize_category_ids(references, {"expense:food:snacks"})
    assert {(budget.budget_id, budget.subcategory_id) for budget in updated.budgets} == {
        (":expense:food", None),
        (":expense:travel", None),
    }


def test_uncategorize_category_ids_keeps_the_untouched_budget_when_clearing_a_subcategory_collides() -> None:
    # Clearing the deleted subcategory would give this budget the same
    # (month, category, subcategory) identity as the whole-category one — the
    # untouched budget wins and the cleared one is dropped, since two rows
    # cannot share that identity in the table.
    references = CategoryReferences(
        budgets=[
            Budget(
                budget_id="2026-06:expense:food:expense:food:snacks",
                month="2026-06",
                category_id="expense:food",
                subcategory_id="expense:food:snacks",
                amount=50.0,
            ),
            Budget(budget_id="2026-06:expense:food", month="2026-06", category_id="expense:food", amount=300.0),
        ]
    )
    updated = uncategorize_category_ids(references, {"expense:food:snacks"})
    assert [(budget.budget_id, budget.amount) for budget in updated.budgets] == [
        ("2026-06:expense:food", pytest.approx(300.0))
    ]


def test_uncategorize_category_ids_deletes_a_category_pattern_for_the_deleted_category() -> None:
    references = CategoryReferences(
        category_patterns={
            "p1": CategoryPattern(pattern_id="p1", description_contains="x", category_id="expense:food"),
            "p2": CategoryPattern(pattern_id="p2", description_contains="y", category_id="expense:travel"),
        }
    )
    updated = uncategorize_category_ids(references, {"expense:food"})
    assert list(updated.category_patterns) == ["p2"]


def test_normalize_categories_adds_other_when_a_first_real_subcategory_appears() -> None:
    parent = Category(category_id="expense:shopping", name="Shopping", classification="expense", color="#111111")
    clothing = Category(
        category_id="expense:shopping:clothing",
        name="Clothing",
        classification="expense",
        parent_category_id="expense:shopping",
        color="#111111",
    )
    result = normalize_categories({parent.category_id: parent, clothing.category_id: clothing})
    other = result["expense:shopping:other"]
    assert other.name == "Other"
    assert other.parent_category_id == "expense:shopping"
    assert other.color not in {parent.color, clothing.color}


def test_normalize_categories_removes_other_once_it_is_the_sole_subcategory() -> None:
    parent = Category(category_id="expense:shopping", name="Shopping", classification="expense", color="#111111")
    other = Category(
        category_id="expense:shopping:other",
        name="Other",
        classification="expense",
        parent_category_id="expense:shopping",
        color="#111111",
    )
    result = normalize_categories({parent.category_id: parent, other.category_id: other})
    assert "expense:shopping:other" not in result


def test_normalize_categories_leaves_a_category_with_no_subcategories_alone() -> None:
    parent = Category(category_id="income:salary", name="Salary", classification="income", color="#222222")
    result = normalize_categories({parent.category_id: parent})
    assert result == {parent.category_id: parent}


def _tag(tag_id: str, name: str) -> Tag:
    return Tag(tag_id=tag_id, name=name)


def test_plan_tag_rename_with_a_new_unique_name_just_renames() -> None:
    tags = {"tag:trip": _tag("tag:trip", "Trip")}
    updated, id_remap = plan_tag_rename(tags, "tag:trip", "Vacation")
    assert updated["tag:trip"].name == "Vacation"
    assert id_remap == {}


def test_plan_tag_rename_merges_two_tags_with_the_same_name() -> None:
    tags = {
        "tag:trip": _tag("tag:trip", "Trip"),
        "tag:vacation": _tag("tag:vacation", "Vacation"),
    }
    updated, id_remap = plan_tag_rename(tags, "tag:trip", "Vacation")
    assert "tag:trip" not in updated
    assert "tag:vacation" in updated
    assert id_remap == {"tag:trip": "tag:vacation"}


def test_plan_tag_rename_matches_names_case_insensitively() -> None:
    tags = {
        "tag:trip": _tag("tag:trip", "Trip"),
        "tag:vacation": _tag("tag:vacation", "Vacation"),
    }
    _updated, id_remap = plan_tag_rename(tags, "tag:trip", "vacation")
    assert id_remap == {"tag:trip": "tag:vacation"}


def test_plan_tag_rename_never_matches_itself() -> None:
    tags = {"tag:trip": _tag("tag:trip", "Trip")}
    updated, id_remap = plan_tag_rename(tags, "tag:trip", "Trip")
    assert id_remap == {}
    assert updated["tag:trip"].name == "Trip"
