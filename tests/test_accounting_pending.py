from accounting.ledger.pending import resolve_pending_suggestion, stage_pending_suggestion
from accounting.models import ManualOverride


def test_stage_pending_suggestion_with_no_prior_override_snapshots_the_previous_state() -> None:
    staged = stage_pending_suggestion(
        existing=None,
        category_id="expense:food-drink",
        subcategory_id="expense:food-drink:groceries",
        source="ai",
        previous_category_id=None,
        previous_subcategory_id=None,
    )
    assert staged.category_id == "expense:food-drink"
    assert staged.subcategory_id == "expense:food-drink:groceries"
    assert staged.pending_source == "ai"
    assert staged.pending_selected is True
    assert staged.pending_previous_category_id is None
    assert staged.pending_previous_subcategory_id is None


def test_stage_pending_suggestion_preserves_other_fields_of_an_existing_override() -> None:
    existing = ManualOverride(tag_ids=["trip:japan"], account_id="chase:checking:9579")
    staged = stage_pending_suggestion(
        existing=existing,
        category_id="expense:travel",
        subcategory_id=None,
        source="pattern",
        previous_category_id="expense:shopping",
        previous_subcategory_id="expense:shopping:clothing",
    )
    assert staged.tag_ids == ["trip:japan"]
    assert staged.account_id == "chase:checking:9579"
    assert staged.category_id == "expense:travel"
    assert staged.pending_source == "pattern"
    assert staged.pending_previous_category_id == "expense:shopping"
    assert staged.pending_previous_subcategory_id == "expense:shopping:clothing"


def test_resolve_pending_suggestion_accepts_when_selected() -> None:
    staged = ManualOverride(
        category_id="expense:travel",
        subcategory_id=None,
        pending_source="ai",
        pending_selected=True,
        pending_previous_category_id=None,
        pending_previous_subcategory_id=None,
    )
    resolved = resolve_pending_suggestion(staged)
    assert resolved is not None
    assert resolved.category_id == "expense:travel"
    assert resolved.pending_source is None
    assert resolved.pending_previous_category_id is None


def test_resolve_pending_suggestion_reverts_to_the_previous_category_when_unselected() -> None:
    staged = ManualOverride(
        category_id="expense:travel",
        subcategory_id="expense:travel:flights",
        pending_source="ai",
        pending_selected=False,
        pending_previous_category_id="expense:shopping",
        pending_previous_subcategory_id="expense:shopping:clothing",
    )
    resolved = resolve_pending_suggestion(staged)
    assert resolved is not None
    assert resolved.category_id == "expense:shopping"
    assert resolved.subcategory_id == "expense:shopping:clothing"
    assert resolved.pending_source is None


def test_resolve_pending_suggestion_reverting_to_a_fully_empty_override_returns_none() -> None:
    staged = ManualOverride(
        category_id="expense:travel",
        subcategory_id=None,
        pending_source="ai",
        pending_selected=False,
        pending_previous_category_id=None,
        pending_previous_subcategory_id=None,
    )
    assert resolve_pending_suggestion(staged) is None
