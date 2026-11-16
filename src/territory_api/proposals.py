"""Stable proposal identifiers and review merging."""

from __future__ import annotations

import hashlib

import pandas as pd

REVIEW_COLUMNS = [
    "Proposal ID",
    "Account ID",
    "Current AE",
    "Proposed AE",
    "Action",
    "Manager Decision",
    "Lock next cycle",
    "Reviewed By",
    "Reviewed At",
]


def proposal_id(row: pd.Series) -> str:
    source = "\x1f".join(
        str(row.get(name, "")).strip()
        for name in ["Account ID", "Current AE", "Proposed AE", "Action"]
    )
    return "P-" + hashlib.sha256(source.encode("utf-8")).hexdigest()[:20]


def proposals_for_moves(moves: pd.DataFrame) -> pd.DataFrame:
    proposals = pd.DataFrame(columns=REVIEW_COLUMNS)
    if moves.empty:
        return proposals
    proposals = moves[["Account ID", "Current AE", "Proposed AE", "Action"]].copy()
    proposals.insert(0, "Proposal ID", moves.apply(proposal_id, axis=1))
    for column in REVIEW_COLUMNS[5:]:
        proposals[column] = ""
    return proposals[REVIEW_COLUMNS]


def merge_reviews(moves: pd.DataFrame, reviews: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    proposals = proposals_for_moves(moves)
    if reviews.empty:
        existing_ids: set[str] = set()
        review_lookup = reviews
    else:
        existing_ids = set(reviews["Proposal ID"].astype(str))
        review_lookup = reviews.drop_duplicates("Proposal ID", keep="last")
    new = proposals[~proposals["Proposal ID"].isin(existing_ids)].copy()
    merged = moves.copy()
    merged.insert(0, "Proposal ID", moves.apply(proposal_id, axis=1))
    if review_lookup.empty:
        merged["Manager Decision"] = ""
        merged["Lock next cycle"] = ""
    else:
        lookup = review_lookup.set_index("Proposal ID")
        merged["Manager Decision"] = (
            merged["Proposal ID"].map(lookup["Manager Decision"]).fillna("")
        )
        merged["Lock next cycle"] = merged["Proposal ID"].map(lookup["Lock next cycle"]).fillna("")
    return merged, new
