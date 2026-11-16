from collections.abc import Callable
from copy import deepcopy
from typing import Any

import pandas as pd
import pytest

from territory_api.sheets import GoogleSheetRepository


class Call:
    def __init__(self, execute: Callable[[], dict[str, Any]]) -> None:
        self._execute = execute

    def execute(self) -> dict[str, Any]:
        return self._execute()


class FakeValues:
    def __init__(self, parent: "FakeSpreadsheets") -> None:
        self.parent = parent

    def clear(self, **kwargs: Any) -> Call:
        return Call(lambda: self.parent.value_calls.append(("clear", kwargs)) or {})

    def batchUpdate(self, **kwargs: Any) -> Call:
        return Call(lambda: self.parent.value_calls.append(("batchUpdate", kwargs)) or {})

    def get(self, **kwargs: Any) -> Call:
        return Call(lambda: {"values": self.parent.range_values.get(kwargs["range"], [])})


class FakeSpreadsheets:
    def __init__(self, *, fail_swap: bool = False) -> None:
        self.fail_swap = fail_swap
        self.sheets = {
            1: {"sheetId": 1, "title": "Assignments", "hidden": False},
            2: {"sheetId": 2, "title": "Moves", "hidden": False},
        }
        self.next_id = 10
        self.value_calls: list[tuple[str, dict[str, Any]]] = []
        self.range_values: dict[str, list[list[Any]]] = {}
        self.values_api = FakeValues(self)

    def values(self) -> FakeValues:
        return self.values_api

    def get(self, **kwargs: Any) -> Call:
        return Call(
            lambda: {
                "sheets": [
                    {"properties": deepcopy(properties)} for properties in self.sheets.values()
                ]
            }
        )

    def batchUpdate(self, **kwargs: Any) -> Call:
        requests = kwargs["body"]["requests"]

        def execute() -> dict[str, Any]:
            if self.fail_swap and any("updateSheetProperties" in item for item in requests):
                raise RuntimeError("simulated atomic swap failure")
            candidate = deepcopy(self.sheets)
            replies: list[dict[str, Any]] = []
            for item in requests:
                if "addSheet" in item:
                    properties = deepcopy(item["addSheet"]["properties"])
                    properties["sheetId"] = self.next_id
                    candidate[self.next_id] = properties
                    replies.append({"addSheet": {"properties": deepcopy(properties)}})
                    self.next_id += 1
                elif "updateSheetProperties" in item:
                    properties = item["updateSheetProperties"]["properties"]
                    candidate[properties["sheetId"]].update(properties)
                    replies.append({})
                elif "deleteSheet" in item:
                    del candidate[item["deleteSheet"]["sheetId"]]
                    replies.append({})
                else:
                    replies.append({})
            self.sheets = candidate
            return {"replies": replies}

        return Call(execute)


class FakeService:
    def __init__(self, *, fail_swap: bool = False) -> None:
        self.api = FakeSpreadsheets(fail_swap=fail_swap)

    def spreadsheets(self) -> FakeSpreadsheets:
        return self.api


def test_atomic_swap_replaces_existing_tabs() -> None:
    service = FakeService()
    repository = GoogleSheetRepository("sheet", service=service)
    repository.publish(
        "run-1",
        {
            "Assignments": pd.DataFrame([{"Account ID": "A-1"}]),
            "Moves": pd.DataFrame([{"Account ID": "A-1"}]),
        },
    )
    titles = {properties["title"] for properties in service.api.sheets.values()}
    assert titles == {"Assignments", "Moves"}
    assert all(properties["sheetId"] >= 10 for properties in service.api.sheets.values())
    assert sum(name == "clear" for name, _ in service.api.value_calls) == 2


def test_failed_atomic_swap_keeps_previous_visible_tabs() -> None:
    service = FakeService(fail_swap=True)
    repository = GoogleSheetRepository("sheet", service=service)
    with pytest.raises(RuntimeError, match="atomic swap failure"):
        repository.publish(
            "run-2",
            {"Assignments": pd.DataFrame([{"Account ID": "A-2"}])},
        )
    visible = {
        properties["title"]
        for properties in service.api.sheets.values()
        if not properties.get("hidden", False)
    }
    assert visible == {"Assignments", "Moves"}


def test_missing_review_tab_is_an_empty_review_register() -> None:
    service = FakeService()
    repository = GoogleSheetRepository("sheet", service=service)
    reviews = repository.read_reviews()
    assert reviews.empty
    assert "Proposal ID" in reviews.columns
