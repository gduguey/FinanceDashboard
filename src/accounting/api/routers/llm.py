"""LLM categorization endpoints — mirrors `accounting.llm.*`: provider selection, usage tracking, key settings."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

import polars as pl
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from accounting.api.api_models import (
    BulkSuggestResult,
    CategorySuggestionResult,
    LlmProviderUsage,
    LlmSettings,
    LLMSettingsUpdate,
    PatternSuggestBulkRequest,
    VerifyResult,
)
from accounting.api.dependencies import _resolved_postings_and_store, state
from accounting.ledger.patterns import match_patterns_bulk, matching_pattern
from accounting.ledger.pending import stage_pending_suggestion
from accounting.llm import categorize
from accounting.llm.gemini import GeminiProvider, verify_gemini_key
from accounting.llm.mistral import MistralProvider, verify_mistral_key
from accounting.llm.provider import LLMProvider, LLMProviderError, complete_with_fallback
from accounting.llm.settings import (
    clear_llm_api_key,
    load_llm_api_key,
    resolve_llm_credentials,
    save_llm_api_key,
)
from accounting.llm.usage import RESET_PERIOD, TrackedProvider, load_usage
from accounting.models import CategoryClassification, PendingSuggestionSource
from accounting.store import load_overrides, save_overrides
from accounting.utils.io_utils import collect_if_lazy
from db.current_user import get_current_user_id
from db.session import get_db

router = APIRouter()

_MAX_FEW_SHOT_EXAMPLES = 20


def _llm_providers(session: Session, user_id: uuid.UUID) -> list[LLMProvider]:
    """Build the default-Gemini-then-Mistral fallback chain from whichever API keys this user has saved.

    Every provider is wrapped in `TrackedProvider` so each call's outcome
    is recorded to the `llm_usage` table regardless of which provider in the
    chain ends up being tried (see `get_llm_usage`).

    Returns
    -------
    list[LLMProvider]
        Gemini first (if a Gemini key is saved), then Mistral (if a Mistral key is saved) — empty if neither is.
    """
    credentials = resolve_llm_credentials(session, user_id)
    providers: list[LLMProvider] = []
    if credentials.gemini_api_key is not None:
        providers.append(
            TrackedProvider(GeminiProvider(credentials.gemini_api_key.get_secret_value()), "gemini", session, user_id)
        )
    if credentials.mistral_api_key is not None:
        providers.append(
            TrackedProvider(
                MistralProvider(credentials.mistral_api_key.get_secret_value()), "mistral", session, user_id
            )
        )
    return providers


@router.get("/llm-usage")
def get_llm_usage(
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> dict[str, LlmProviderUsage]:
    """Return each LLM provider's self-tracked call count this period, and whether it's currently rate-limited.

    Returns
    -------
    dict[str, LlmProviderUsage]
        Keyed by provider name (`"gemini"`, `"mistral"`). `last_error` is
        the provider's own error text from the last refused call, `None`
        if it hasn't been refused since its count last reset.
    """
    credentials = resolve_llm_credentials(session, user_id)
    configured = {
        "gemini": credentials.gemini_api_key is not None,
        "mistral": credentials.mistral_api_key is not None,
    }
    usage = load_usage(session, user_id)
    return {
        provider: LlmProviderUsage(
            configured=configured[provider],
            used_count=entry.used_count,
            period=RESET_PERIOD[provider],
            is_limited=entry.is_limited,
            last_error=entry.last_error,
        )
        for provider, entry in usage.items()
    }


@router.post("/settings/llm/verify")
def verify_llm_settings(
    provider: str,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> VerifyResult:
    """Actually attempt to authenticate with a provider, not just check that a key is typed in.

    `verify_gemini_key`/`verify_mistral_key` make a single free, read-only
    call (`models.list()`) — cheap enough for the Settings page to call
    whenever it wants a real "does this work" answer instead of "is this set".

    Parameters
    ----------
    provider
        `"gemini"` or `"mistral"`.

    Returns
    -------
    VerifyResult
        `error` is only ever set when `ok` is false.

    Raises
    ------
    HTTPException
        400 if `provider` isn't `"gemini"` or `"mistral"`.
    """
    credentials = resolve_llm_credentials(session, user_id)
    if provider == "gemini":
        key = credentials.gemini_api_key
        verify = verify_gemini_key
    elif provider == "mistral":
        key = credentials.mistral_api_key
        verify = verify_mistral_key
    else:
        raise HTTPException(status_code=400, detail="provider must be 'gemini' or 'mistral'")

    if key is None:
        return VerifyResult(ok=False, error="No key configured")
    try:
        verify(key.get_secret_value())
    except LLMProviderError as error:
        return VerifyResult(ok=False, error=str(error))
    return VerifyResult(ok=True, error=None)


@router.get("/settings/llm")
def get_llm_settings(
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> LlmSettings:
    """Report whether each LLM provider's API key is saved, without ever exposing its value.

    Returns
    -------
    LlmSettings
        Whether this user has a key saved for each provider — there is no
        `.env` fallback left to also reflect (see `GET /llm-usage`'s own
        `configured`, which now means exactly the same thing).
    """
    return LlmSettings(
        gemini_key_set=load_llm_api_key(session, user_id, "gemini") is not None,
        mistral_key_set=load_llm_api_key(session, user_id, "mistral") is not None,
    )


@router.put("/settings/llm")
def put_llm_settings(
    update: LLMSettingsUpdate,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> LlmSettings:
    """Persist an LLM API key update (merges into whatever's already saved).

    Returns
    -------
    LlmSettings
        Same shape as `GET /settings/llm`, reflecting what was just persisted.
    """
    if update.gemini_api_key is not None:
        save_llm_api_key(session, user_id, "gemini", update.gemini_api_key)
    if update.mistral_api_key is not None:
        save_llm_api_key(session, user_id, "mistral", update.mistral_api_key)
    return LlmSettings(
        gemini_key_set=load_llm_api_key(session, user_id, "gemini") is not None,
        mistral_key_set=load_llm_api_key(session, user_id, "mistral") is not None,
    )


@router.delete("/settings/llm")
def delete_llm_settings(
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> LlmSettings:
    """Clear this user's saved API key for every provider.

    Returns
    -------
    LlmSettings
        Same shape as `GET /settings/llm`.
    """
    clear_llm_api_key(session, user_id, "gemini")
    clear_llm_api_key(session, user_id, "mistral")
    return LlmSettings(gemini_key_set=False, mistral_key_set=False)


Example = tuple[str, str, str | None]


def _few_shot_examples(postings: pl.DataFrame, classification: CategoryClassification) -> list[Example]:
    """Return already-categorized postings on `classification`'s side, as `(description, category_id, subcategory_id)`.

    Returns
    -------
    list[tuple[str, str, str | None]]
        Up to `_MAX_FEW_SHOT_EXAMPLES` examples.
    """
    categorized = postings.filter(pl.col("category_id").is_not_null()).select(
        "description", "amount", "category_id", "subcategory_id"
    )
    return [
        (row["description"], row["category_id"], row["subcategory_id"])
        for row in categorized.to_dicts()
        if (row["amount"] >= 0) == (classification == "income")
    ][:_MAX_FEW_SHOT_EXAMPLES]


def _stage_and_save_pending_suggestion(
    posting_id: str,
    target_row: dict[str, Any],
    category_id: str,
    subcategory_id: str | None,
    source: PendingSuggestionSource,
    session: Session,
) -> CategorySuggestionResult:
    """Stage a not-yet-confirmed suggestion as a pending override, snapshotting the posting's current category.

    Shared by `post_ai_suggest_category` and `post_pattern_suggest_category`
    — the only difference between the two is how `category_id`/
    `subcategory_id` were arrived at.

    Returns
    -------
    CategorySuggestionResult
        `applied` is always `True`.
    """
    overrides = load_overrides(session)
    staged = stage_pending_suggestion(
        existing=overrides.get(posting_id),
        category_id=category_id,
        subcategory_id=subcategory_id,
        source=source,
        previous_category_id=target_row["category_id"],
        previous_subcategory_id=target_row["subcategory_id"],
    )
    overrides[posting_id] = staged
    save_overrides(overrides, session)
    return CategorySuggestionResult(category_id=category_id, subcategory_id=subcategory_id, applied=True)


@router.post("/postings/{posting_id}/ai-suggest-category")
def post_ai_suggest_category(
    posting_id: str,
    *,
    lock_category_id: str | None = None,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> CategorySuggestionResult:
    """Ask an LLM to suggest a category for one posting, from already-categorized examples — never automatic.

    Only ever called when the user clicks the "AI suggestion" button, or
    by the bulk-suggest action over a filtered set of postings — nothing
    in this module calls it on its own. The suggestion is validated
    against real category/subcategory ids before being applied (see
    `llm.categorize.parse_and_validate_suggestion`) and, if valid,
    persisted as a manual override exactly as if the user had picked it
    from the dropdown themselves.

    Parameters
    ----------
    posting_id
        The posting to suggest a category for.
    lock_category_id
        If given, the posting already has this category and only its
        subcategory is missing — the suggestion is discarded (treated as
        unapplied) unless the LLM's own top-level guess agrees with it, so
        this call can never change a category the user (or an earlier
        rule) already assigned.

    Returns
    -------
    CategorySuggestionResult
        `applied` is `False` if no provider returned a usable suggestion,
        in which case nothing about the posting is changed.

    Raises
    ------
    HTTPException
        404 if the posting doesn't exist; 503 if no LLM provider is configured or every configured one failed.
    """
    postings, store = _resolved_postings_and_store(state.config, session)
    target = postings.filter(pl.col("posting_id") == posting_id)
    if target.is_empty():
        raise HTTPException(status_code=404, detail=f"Posting {posting_id!r} not found")
    target_row = target.row(0, named=True)
    classification: CategoryClassification = "income" if target_row["amount"] >= 0 else "expense"

    system_prompt, user_prompt = categorize.build_prompt(
        target_row["description"], classification, store.categories, _few_shot_examples(postings, classification)
    )
    try:
        raw_response = complete_with_fallback(_llm_providers(session, user_id), system_prompt, user_prompt)
    except LLMProviderError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error

    category_id, subcategory_id = categorize.parse_and_validate_suggestion(
        raw_response, store.categories, classification
    )
    if category_id is None:
        return CategorySuggestionResult(category_id=None, subcategory_id=None, applied=False)
    if lock_category_id is not None and category_id != lock_category_id:
        return CategorySuggestionResult(category_id=None, subcategory_id=None, applied=False)

    return _stage_and_save_pending_suggestion(posting_id, target_row, category_id, subcategory_id, "ai", session)


@router.post("/postings/{posting_id}/pattern-suggest-category")
def post_pattern_suggest_category(
    posting_id: str, *, lock_category_id: str | None = None, session: Annotated[Session, Depends(get_db)]
) -> CategorySuggestionResult:
    """Suggest a category for one posting from a user-maintained `CategoryPattern` description match.

    The description-match/suggest-don't-apply counterpart to
    `post_ai_suggest_category` — same staged-pending flow (see
    `ledger.pending`), same `lock_category_id` guarantee, just matched
    against `store.category_patterns` instead of calling an LLM.

    Parameters
    ----------
    posting_id
        The posting to suggest a category for.
    lock_category_id
        If given, the posting already has this category and only its
        subcategory is missing — a pattern match is discarded unless its
        own `category_id` agrees, so this call can never change a
        category the user (or an earlier rule) already assigned.

    Returns
    -------
    CategorySuggestionResult
        `applied` is `False` if no pattern matched.

    Raises
    ------
    HTTPException
        404 if the posting doesn't exist.
    """
    postings, store = _resolved_postings_and_store(state.config, session)
    target = postings.filter(pl.col("posting_id") == posting_id)
    if target.is_empty():
        raise HTTPException(status_code=404, detail=f"Posting {posting_id!r} not found")
    target_row = target.row(0, named=True)

    pattern = matching_pattern(store.category_patterns, str(target_row["description"]))
    if pattern is None:
        return CategorySuggestionResult(category_id=None, subcategory_id=None, applied=False)
    if lock_category_id is not None and pattern.category_id != lock_category_id:
        return CategorySuggestionResult(category_id=None, subcategory_id=None, applied=False)

    return _stage_and_save_pending_suggestion(
        posting_id, target_row, pattern.category_id, pattern.subcategory_id, "pattern", session
    )


@router.post("/postings/pattern-suggest-category/bulk")
def post_pattern_suggest_category_bulk(
    payload: PatternSuggestBulkRequest, session: Annotated[Session, Depends(get_db)]
) -> BulkSuggestResult:
    """Suggest categories for many postings at once from category-pattern matches, in one ledger load.

    The bulk counterpart to `post_pattern_suggest_category` — that
    endpoint reloads and re-resolves the entire ledger on every single
    call, which is fine for one posting but made the "run pattern
    suggestions" bulk action take minutes over a few hundred rows (each
    one its own full reload). This loads everything exactly once and
    matches every posting in a single vectorized pass (see
    `ledger.patterns.match_patterns_bulk`) instead of looping over
    postings to match them one at a time.

    A posting that already has a category only gets a pattern match if
    the pattern's own category agrees with it — the same guarantee
    `post_pattern_suggest_category`'s `lock_category_id` gives, using each
    posting's own current category as its lock.

    Parameters
    ----------
    payload
        The postings to suggest categories for.

    Returns
    -------
    BulkSuggestResult
        How many postings got a staged suggestion.
    """
    postings, store = _resolved_postings_and_store(state.config, session)
    targets = postings.filter(pl.col("posting_id").is_in(payload.posting_ids))
    if targets.is_empty():
        return BulkSuggestResult(applied=0)

    matches = collect_if_lazy(match_patterns_bulk(store.category_patterns, targets.select("posting_id", "description")))
    if matches.is_empty():
        return BulkSuggestResult(applied=0)

    target_rows = {row["posting_id"]: row for row in targets.to_dicts()}
    overrides = load_overrides(session)
    applied = 0
    for match in matches.iter_rows(named=True):
        target_row = target_rows[match["posting_id"]]
        if target_row["category_id"] is not None and target_row["category_id"] != match["category_id"]:
            continue
        staged = stage_pending_suggestion(
            existing=overrides.get(match["posting_id"]),
            category_id=match["category_id"],
            subcategory_id=match["subcategory_id"],
            source="pattern",
            previous_category_id=target_row["category_id"],
            previous_subcategory_id=target_row["subcategory_id"],
        )
        overrides[match["posting_id"]] = staged
        applied += 1
    save_overrides(overrides, session)
    return BulkSuggestResult(applied=applied)
