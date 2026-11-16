"""Google Sheets and in-memory spreadsheet repositories."""

from __future__ import annotations

import re
import uuid
from copy import deepcopy
from typing import Any, Protocol, cast

import google.auth
import pandas as pd
from googleapiclient.discovery import build

from .models import SheetInputs
from .proposals import REVIEW_COLUMNS

OUTPUT_TABS = [
    "Run Summary",
    "Region Status",
    "Moves",
    "Rule Scorecard",
    "AE Summary",
    "Assignments",
    "Unassigned",
    "Rule Detail",
    "Pre-flight",
    "Data Quality",
]


def frame_values(frame: pd.DataFrame) -> list[list[Any]]:
    clean = frame.astype(object).where(pd.notna(frame), "")
    return [list(frame.columns)] + cast(list[list[Any]], clean.values.tolist())


class SheetRepository(Protocol):
    def read_inputs(self) -> SheetInputs: ...
    def read_reviews(self) -> pd.DataFrame: ...
    def read_output(self, title: str) -> pd.DataFrame: ...
    def append_reviews(self, rows: pd.DataFrame) -> None: ...
    def publish(self, run_id: str, tables: dict[str, pd.DataFrame]) -> None: ...
    def write_control(self, values: dict[str, Any]) -> None: ...
    def append_history(self, values: dict[str, Any], limit: int) -> None: ...
    def write_next_cycle(self, accounts: pd.DataFrame, roles: pd.DataFrame) -> None: ...


class InMemorySheetRepository:
    def __init__(
        self,
        *,
        accounts: list[list[Any]] | None = None,
        ae_roles: list[list[Any]] | None = None,
        settings: list[list[Any]] | None = None,
    ) -> None:
        self.inputs = SheetInputs(
            accounts=accounts or [], ae_roles=ae_roles or [], settings=settings or []
        )
        self.reviews = pd.DataFrame(columns=REVIEW_COLUMNS)
        self.outputs: dict[str, pd.DataFrame] = {}
        self.control: dict[str, Any] = {}
        self.history: list[dict[str, Any]] = []
        self.next_cycle: tuple[pd.DataFrame, pd.DataFrame] | None = None
        self.fail_publish = False

    def read_inputs(self) -> SheetInputs:
        return self.inputs.model_copy(deep=True)

    def read_reviews(self) -> pd.DataFrame:
        return self.reviews.copy()

    def read_output(self, title: str) -> pd.DataFrame:
        return self.outputs.get(title, pd.DataFrame()).copy()

    def append_reviews(self, rows: pd.DataFrame) -> None:
        if not rows.empty:
            self.reviews = pd.concat([self.reviews, rows], ignore_index=True)

    def publish(self, run_id: str, tables: dict[str, pd.DataFrame]) -> None:
        staged = {name: frame.copy() for name, frame in tables.items()}
        if self.fail_publish:
            raise RuntimeError("simulated publication failure")
        self.outputs = staged

    def write_control(self, values: dict[str, Any]) -> None:
        self.control.update(deepcopy(values))

    def append_history(self, values: dict[str, Any], limit: int) -> None:
        self.history.append(deepcopy(values))
        self.history = self.history[-limit:]

    def write_next_cycle(self, accounts: pd.DataFrame, roles: pd.DataFrame) -> None:
        self.next_cycle = (accounts.copy(), roles.copy())


class GoogleSheetRepository:
    """Single-spreadsheet adapter using ADC and hidden staging sheets."""

    def __init__(self, spreadsheet_id: str, service: Any | None = None) -> None:
        self.spreadsheet_id = spreadsheet_id
        if service is None:
            credentials, _ = google.auth.default(
                scopes=["https://www.googleapis.com/auth/spreadsheets"]
            )
            service = build("sheets", "v4", credentials=credentials, cache_discovery=False)
        self.service = service

    def _get_values(self, range_name: str) -> list[list[Any]]:
        result = (
            self.service.spreadsheets()
            .values()
            .get(spreadsheetId=self.spreadsheet_id, range=range_name)
            .execute()
        )
        return cast(list[list[Any]], result.get("values", []))

    def _frame(self, title: str) -> pd.DataFrame:
        values = self._get_values(f"'{title}'")
        if not values:
            return pd.DataFrame()
        width = len(values[0])
        rows = [row + [""] * (width - len(row)) for row in values[1:]]
        return pd.DataFrame([row[:width] for row in rows], columns=values[0])

    def read_inputs(self) -> SheetInputs:
        batch = (
            self.service.spreadsheets()
            .values()
            .batchGet(
                spreadsheetId=self.spreadsheet_id,
                ranges=["'Accounts'", "'AE_Roles'", "'Settings'"],
            )
            .execute()
        )
        ranges = batch.get("valueRanges", [])
        values = [item.get("values", []) for item in ranges]
        while len(values) < 3:
            values.append([])
        return SheetInputs(accounts=values[0], ae_roles=values[1], settings=values[2])

    def read_reviews(self) -> pd.DataFrame:
        titles = {sheet["properties"]["title"] for sheet in self._metadata().get("sheets", [])}
        if "Manager Reviews" not in titles:
            return pd.DataFrame(columns=REVIEW_COLUMNS)
        frame = self._frame("Manager Reviews")
        if frame.empty:
            return pd.DataFrame(columns=REVIEW_COLUMNS)
        missing = [column for column in REVIEW_COLUMNS if column not in frame]
        if missing:
            raise ValueError(f"Manager Reviews is missing columns: {', '.join(missing)}")
        return frame[REVIEW_COLUMNS]

    def read_output(self, title: str) -> pd.DataFrame:
        titles = {sheet["properties"]["title"] for sheet in self._metadata().get("sheets", [])}
        if title not in titles:
            return pd.DataFrame()
        return self._frame(title)

    def _metadata(self) -> dict[str, Any]:
        return cast(
            dict[str, Any],
            self.service.spreadsheets()
            .get(
                spreadsheetId=self.spreadsheet_id,
                fields="sheets.properties(sheetId,title,hidden)",
            )
            .execute(),
        )

    def _ensure_sheet(self, title: str, hidden: bool = False) -> int:
        for sheet in self._metadata().get("sheets", []):
            if sheet["properties"]["title"] == title:
                return int(sheet["properties"]["sheetId"])
        response = (
            self.service.spreadsheets()
            .batchUpdate(
                spreadsheetId=self.spreadsheet_id,
                body={
                    "requests": [{"addSheet": {"properties": {"title": title, "hidden": hidden}}}]
                },
            )
            .execute()
        )
        return int(response["replies"][0]["addSheet"]["properties"]["sheetId"])

    def append_reviews(self, rows: pd.DataFrame) -> None:
        if rows.empty:
            return
        sheet_id = self._ensure_sheet("Manager Reviews")
        current = self._get_values("'Manager Reviews'")
        if not current:
            self.service.spreadsheets().values().update(
                spreadsheetId=self.spreadsheet_id,
                range="'Manager Reviews'!A1",
                valueInputOption="RAW",
                body={"values": [REVIEW_COLUMNS]},
            ).execute()
        self.service.spreadsheets().values().append(
            spreadsheetId=self.spreadsheet_id,
            range="'Manager Reviews'!A:I",
            valueInputOption="RAW",
            insertDataOption="INSERT_ROWS",
            body={"values": rows[REVIEW_COLUMNS].values.tolist()},
        ).execute()
        self.service.spreadsheets().batchUpdate(
            spreadsheetId=self.spreadsheet_id,
            body={
                "requests": [
                    {
                        "setDataValidation": {
                            "range": {
                                "sheetId": sheet_id,
                                "startRowIndex": 1,
                                "startColumnIndex": 5,
                                "endColumnIndex": 6,
                            },
                            "rule": {
                                "condition": {
                                    "type": "ONE_OF_LIST",
                                    "values": [
                                        {"userEnteredValue": "Approve"},
                                        {"userEnteredValue": "Reject"},
                                    ],
                                },
                                "strict": True,
                                "showCustomUi": True,
                            },
                        }
                    }
                ]
            },
        ).execute()

    def publish(self, run_id: str, tables: dict[str, pd.DataFrame]) -> None:
        safe_id = re.sub(r"[^a-zA-Z0-9]", "", run_id)[:16]
        existing = {
            sheet["properties"]["title"]: sheet["properties"]
            for sheet in self._metadata().get("sheets", [])
        }
        stage_ids: dict[str, int] = {}
        for index, title in enumerate(tables):
            stage_title = f"__stage_{safe_id}_{index}"
            stage_ids[title] = self._ensure_sheet(stage_title, hidden=True)

        for index in range(len(tables)):
            stage_title = f"__stage_{safe_id}_{index}"
            self.service.spreadsheets().values().clear(
                spreadsheetId=self.spreadsheet_id,
                range=f"'{stage_title}'",
                body={},
            ).execute()

        data = []
        for index, (title, frame) in enumerate(tables.items()):
            stage_title = f"__stage_{safe_id}_{index}"
            data.append({"range": f"'{stage_title}'!A1", "values": frame_values(frame)})
        self.service.spreadsheets().values().batchUpdate(
            spreadsheetId=self.spreadsheet_id,
            body={"valueInputOption": "RAW", "data": data},
        ).execute()

        requests: list[dict[str, Any]] = []
        old_ids: list[int] = []
        for title, stage_id in stage_ids.items():
            old = existing.get(title)
            if old:
                old_id = int(old["sheetId"])
                old_ids.append(old_id)
                requests.append(
                    {
                        "updateSheetProperties": {
                            "properties": {
                                "sheetId": old_id,
                                "title": f"__old_{safe_id}_{old_id}",
                                "hidden": True,
                            },
                            "fields": "title,hidden",
                        }
                    }
                )
            requests.extend(
                [
                    {
                        "updateSheetProperties": {
                            "properties": {
                                "sheetId": stage_id,
                                "title": title,
                                "hidden": False,
                                "gridProperties": {"frozenRowCount": 1},
                            },
                            "fields": "title,hidden,gridProperties.frozenRowCount",
                        }
                    },
                    {
                        "repeatCell": {
                            "range": {
                                "sheetId": stage_id,
                                "startRowIndex": 0,
                                "endRowIndex": 1,
                            },
                            "cell": {
                                "userEnteredFormat": {
                                    "backgroundColor": {"red": 0.12, "green": 0.31, "blue": 0.47},
                                    "textFormat": {
                                        "bold": True,
                                        "foregroundColor": {"red": 1, "green": 1, "blue": 1},
                                    },
                                }
                            },
                            "fields": "userEnteredFormat(backgroundColor,textFormat)",
                        }
                    },
                ]
            )
        requests.extend({"deleteSheet": {"sheetId": old_id}} for old_id in old_ids)
        self.service.spreadsheets().batchUpdate(
            spreadsheetId=self.spreadsheet_id, body={"requests": requests}
        ).execute()

    def _replace_small_table(self, title: str, values: list[list[Any]]) -> None:
        self._ensure_sheet(title)
        self.service.spreadsheets().values().clear(
            spreadsheetId=self.spreadsheet_id, range=f"'{title}'", body={}
        ).execute()
        self.service.spreadsheets().values().update(
            spreadsheetId=self.spreadsheet_id,
            range=f"'{title}'!A1",
            valueInputOption="RAW",
            body={"values": values},
        ).execute()

    def write_control(self, values: dict[str, Any]) -> None:
        self._replace_small_table(
            "Control", [["Field", "Value"]] + [[key, value] for key, value in values.items()]
        )

    def append_history(self, values: dict[str, Any], limit: int) -> None:
        current = self.read_output("Run History")
        row = pd.DataFrame([values])
        combined = pd.concat([current, row], ignore_index=True).tail(limit)
        self._replace_small_table("Run History", frame_values(combined))

    def write_next_cycle(self, accounts: pd.DataFrame, roles: pd.DataFrame) -> None:
        self.publish(
            f"nextcycle{uuid.uuid4().hex}",
            {
                "Next_Cycle_Accounts": accounts,
                "Next_Cycle_AE_Roles": roles,
            },
        )
