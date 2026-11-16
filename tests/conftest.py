import pandas as pd
import pytest
from territory_optimisation import make_demo_data


def frame_rows(frame: pd.DataFrame):
    return [list(frame.columns)] + frame.astype(object).values.tolist()


@pytest.fixture
def sheet_rows():
    accounts, roles = make_demo_data(seed=4, regions=("TEST",), aes_per_region=3, accounts_per_ae=3)
    settings = [
        ["Setting", "Value"],
        ["ai_specialists_only", "false"],
        ["min_tier_3_per_ae", "0"],
        ["allowed_workload_variance_pct", "100"],
        ["require_full_coverage", "true"],
        ["max_time_per_region_seconds", "5"],
        ["num_workers", "1"],
    ]
    return frame_rows(accounts), frame_rows(roles), settings
