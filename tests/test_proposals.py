import pandas as pd

from territory_api.proposals import merge_reviews


def test_review_is_reused_only_for_exact_proposal():
    moves = pd.DataFrame(
        [{"Account ID": "A", "Current AE": "Old", "Proposed AE": "New", "Action": "Move"}]
    )
    merged, new = merge_reviews(moves, pd.DataFrame())
    review = new.copy()
    review.loc[0, "Manager Decision"] = "Approve"
    merged, unseen = merge_reviews(moves, review)
    assert merged.loc[0, "Manager Decision"] == "Approve"
    assert unseen.empty

    changed = moves.copy()
    changed.loc[0, "Proposed AE"] = "Different"
    changed_merged, changed_new = merge_reviews(changed, review)
    assert changed_merged.loc[0, "Manager Decision"] == ""
    assert len(changed_new) == 1
