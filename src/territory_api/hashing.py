"""Canonical input normalization and change detection."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, fields
from typing import Any, cast, get_type_hints

import pandas as pd
from territory_optimisation import Config

DISALLOWED_SETTINGS = {
    "use_demo_data",
    "input_file",
    "accounts_sheet",
    "aes_sheet",
    "output_prefix",
}


def rows_to_frame(rows: list[list[Any]], name: str) -> pd.DataFrame:
    if not rows:
        raise ValueError(f"{name} is empty")
    headers = [str(value).strip() for value in rows[0]]
    if not headers or any(not header for header in headers):
        raise ValueError(f"{name} contains a blank header")
    width = len(headers)
    values = [list(row) + [""] * (width - len(row)) for row in rows[1:]]
    return pd.DataFrame([row[:width] for row in values], columns=headers)


def _parse_bool(value: Any) -> bool:
    normalized = str(value).strip().lower()
    if normalized in {"true", "yes", "1", "y"}:
        return True
    if normalized in {"false", "no", "0", "n"}:
        return False
    raise ValueError(f"expected a boolean, found {value!r}")


def parse_config(rows: list[list[Any]]) -> Config:
    if not rows:
        return Config(use_demo_data=False)
    frame = rows_to_frame(rows, "Settings")
    if not {"Setting", "Value"}.issubset(frame.columns):
        raise ValueError("Settings must contain Setting and Value columns")
    if frame["Setting"].astype(str).duplicated().any():
        raise ValueError("Settings contains duplicate setting names")

    allowed = {field.name for field in fields(Config)} - DISALLOWED_SETTINGS
    hints = get_type_hints(Config)
    overrides: dict[str, Any] = {"use_demo_data": False}
    for _, row in frame.iterrows():
        name = str(row["Setting"]).strip()
        if not name:
            continue
        if name not in allowed:
            raise ValueError(f"unknown or protected setting: {name}")
        raw = row["Value"]
        target = hints[name]
        try:
            if target is bool:
                value: Any = _parse_bool(raw)
            elif target is int:
                value = int(raw)
            elif target is float:
                value = float(raw)
            else:
                value = str(raw).strip()
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid value for {name}: {raw!r}") from exc
        overrides[name] = value
    return Config(**overrides).validate()


def normalize_frames(
    accounts: pd.DataFrame, ae_roles: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame]:
    accounts = accounts.copy()
    ae_roles = ae_roles.copy()
    if "Account ID" in accounts:
        accounts = accounts.sort_values("Account ID", kind="stable")
    if "AE Name" in ae_roles:
        ae_roles = ae_roles.sort_values("AE Name", kind="stable")
    return accounts.reset_index(drop=True), ae_roles.reset_index(drop=True)


def _records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    clean = frame.astype(object).where(pd.notna(frame), None)
    return cast(list[dict[str, Any]], clean.to_dict(orient="records"))


def input_hash(
    accounts: pd.DataFrame,
    ae_roles: pd.DataFrame,
    config: Config,
    engine_version: str,
) -> str:
    payload = {
        "accounts": _records(accounts),
        "ae_roles": _records(ae_roles),
        "settings": asdict(config),
        "engine_version": engine_version,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()
