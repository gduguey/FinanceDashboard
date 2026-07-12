from accounting.config import AccountingConfig
from accounting.models import Category, ManualOverride
from accounting.store import (
    UNCATEGORIZED_EXPENSE_ACCOUNT_ID,
    UNCATEGORIZED_INCOME_ACCOUNT_ID,
    default_categories,
    load_overrides,
    load_store,
    normalize_categories,
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
    assert groceries.color == food.color

    salary = categories["income:salary"]
    assert salary.classification == "income"


def test_load_store_with_no_file_yet_seeds_defaults(tmp_path) -> None:
    store = load_store(_config(tmp_path))
    assert UNCATEGORIZED_EXPENSE_ACCOUNT_ID in store.accounts
    assert UNCATEGORIZED_INCOME_ACCOUNT_ID in store.accounts
    assert "expense:food-drink" in store.categories
    assert any(rule.rule_id == "eqore-payroll" for rule in store.rules)


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
    assert other.color == parent.color


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
