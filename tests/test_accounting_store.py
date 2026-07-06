from accounting.config import AccountingConfig
from accounting.models import Budget, Category, CategoryPattern, GeneralBudget, ManualOverride, TransferRule
from accounting.store import (
    CATEGORY_COLOR_PALETTE,
    UNCATEGORIZED_EXPENSE_ACCOUNT_ID,
    UNCATEGORIZED_INCOME_ACCOUNT_ID,
    AccountingStore,
    default_categories,
    load_overrides,
    load_store,
    next_available_color,
    normalize_categories,
    plan_category_rename,
    remap_category_ids,
    save_overrides,
    save_store,
    slugify,
)


def _config(tmp_path) -> AccountingConfig:
    return AccountingConfig(data_dir=tmp_path)


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
    store = AccountingStore(
        rules=[
            TransferRule(
                rule_id="r1",
                description_contains="x",
                category_id="expense:nourriture",
                subcategory_id="expense:nourriture:snacks",
            )
        ],
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
                budget_id="b1",
                month="2026-06",
                category_id="expense:nourriture",
                subcategory_id=None,
                amount=100.0,
                currency="USD",
            )
        ],
        general_budgets={
            "expense:nourriture": GeneralBudget(category_id="expense:nourriture", subcategory_id=None, amount=50.0)
        },
    )
    id_remap = {"expense:nourriture": "expense:food", "expense:nourriture:snacks": "expense:food:snacks"}
    updated = remap_category_ids(store, id_remap)
    assert updated.rules[0].category_id == "expense:food"
    assert updated.rules[0].subcategory_id == "expense:food:snacks"
    assert updated.category_patterns["p1"].category_id == "expense:food"
    assert updated.budgets[0].category_id == "expense:food"
    assert "expense:food" in updated.general_budgets
    assert "expense:nourriture" not in updated.general_budgets
    assert updated.general_budgets["expense:food"].category_id == "expense:food"


def test_load_store_with_no_file_yet_seeds_defaults(tmp_path) -> None:
    store = load_store(_config(tmp_path))
    assert UNCATEGORIZED_EXPENSE_ACCOUNT_ID in store.accounts
    assert UNCATEGORIZED_INCOME_ACCOUNT_ID in store.accounts
    assert "expense:food-drink" in store.categories
    assert store.rules == []


def test_load_store_seeds_only_once_and_persists(tmp_path) -> None:
    config = _config(tmp_path)
    load_store(config)
    assert config.store_path.exists()


def test_save_then_load_store_round_trips_a_custom_category(tmp_path) -> None:
    config = _config(tmp_path)
    store = load_store(config)
    updated = store.model_copy(update={"categories": {}})
    save_store(updated, config)
    reloaded = load_store(config)
    assert reloaded.categories == {}


def test_load_overrides_with_no_file_yet_is_empty(tmp_path) -> None:
    assert load_overrides(_config(tmp_path)) == {}


def test_save_then_load_overrides_round_trips(tmp_path) -> None:
    config = _config(tmp_path)
    save_overrides({"p1": ManualOverride(category_id="expense:food-drink")}, config)
    reloaded = load_overrides(config)
    assert reloaded["p1"].category_id == "expense:food-drink"


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


def test_load_store_backfills_a_missing_placeholder_account(tmp_path) -> None:
    config = _config(tmp_path)
    store = load_store(config)
    stripped = store.model_copy(update={"accounts": {}})
    save_store(stripped, config)

    reloaded = load_store(config)
    assert UNCATEGORIZED_EXPENSE_ACCOUNT_ID in reloaded.accounts
    assert UNCATEGORIZED_INCOME_ACCOUNT_ID in reloaded.accounts
