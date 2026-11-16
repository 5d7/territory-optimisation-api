import pandas as pd
import pytest

from territory_api.hashing import input_hash, normalize_frames, parse_config


def test_row_order_does_not_change_hash():
    accounts = pd.DataFrame([{"Account ID": "B"}, {"Account ID": "A"}])
    roles = pd.DataFrame([{"AE Name": "Y"}, {"AE Name": "X"}])
    left = normalize_frames(accounts, roles)
    right = normalize_frames(accounts.iloc[::-1], roles.iloc[::-1])
    config = parse_config([])
    assert input_hash(*left, config, "v1") == input_hash(*right, config, "v1")
    assert input_hash(*left, config, "v1") != input_hash(*left, config, "v2")


def test_settings_reject_unknown_and_protected_fields():
    with pytest.raises(ValueError, match="unknown or protected"):
        parse_config([["Setting", "Value"], ["input_file", "secret.xlsx"]])
    with pytest.raises(ValueError, match="unknown or protected"):
        parse_config([["Setting", "Value"], ["not_real", "1"]])
