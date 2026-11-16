"""Cloud Run Job entry point."""

import logging
import os

from .runtime import get_config, get_sheets, get_state
from .worker_service import WorkerService


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    run_id = os.environ.get("TERRITORY_RUN_ID")
    if not run_id:
        raise SystemExit("TERRITORY_RUN_ID is required")
    WorkerService(get_config(), get_state(), get_sheets()).run(run_id)


if __name__ == "__main__":
    main()
