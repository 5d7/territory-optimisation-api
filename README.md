# Territory Optimisation API

A change-aware FastAPI and Cloud Run Job wrapper for the [Territory Optimisation Engine](https://github.com/5d7/territory-optimisation-engine). It reads one allowlisted Google Spreadsheet, runs the pinned OR-Tools model in the background, and publishes reviewable results without changing live input tabs.

This repository is an implementation starter for a trusted, single-spreadsheet deployment. Keep the scheduler disabled until the synthetic-sheet release gates have passed.

## Data flow

```text
Cloud Scheduler ─OIDC─┐
                     ├─> Cloud Run API ─> Firestore run + lease ─> Cloud Run Job
Apps Script ─Gateway─┘                                             │
                                                                  v
              Manager Reviews <─ proposals ─ Google Sheet ─ inputs/results
```

The API returns run metadata and headline KPIs only. Account assignments, moves, rule detail, and review decisions remain in the authorized Sheet. Firestore and logs contain no account names, revenue rows, assignments, or review content.

## Spreadsheet contract

Create these user-controlled tabs before the first run:

- `Accounts` — engine account columns, including a unique `Account ID`.
- `AE_Roles` — engine AE capacity and availability columns, including `AE Name`.
- `Settings` — exactly two columns, `Setting` and `Value`. Only public `Config` fields are accepted; file, sheet-name, demo-data, and output-path fields are blocked.
- `Manager Reviews` — optional initially. The service creates it and appends proposals; people own the decision fields.
- `Control` — service status and safe error summary.

The service replaces only these result tabs through hidden staging sheets: `Run Summary`, `Region Status`, `Moves`, `Rule Scorecard`, `AE Summary`, `Assignments`, `Unassigned`, `Rule Detail`, `Pre-flight`, and `Data Quality`. It also maintains a compact `Run History` capped at 500 rows.

Each move has a deterministic proposal ID derived from account ID, current owner, proposed owner, and action. An exact proposal keeps its previous decision. A different destination creates a new proposal. Voluntary moves must say `Approve` or `Reject` before next-cycle tabs can be created; mandatory reassignment actions are not rejectable.

`Next_Cycle_Accounts` and `Next_Cycle_AE_Roles` are generated as separate tabs. The service never replaces `Accounts`, `AE_Roles`, or `Settings`.

## HTTP interface

Public routes pass through API Gateway and require its `x-api-key` plus these HMAC headers:

```text
X-Timestamp: Unix seconds
X-Request-ID: unique random value
X-Signature: hex(HMAC-SHA256(secret, canonical_request))
Idempotency-Key: stable operation ID for POST requests
```

The canonical request is `METHOD`, path, timestamp, request ID, and the hex SHA-256 body digest joined with newline characters. Signatures older than five minutes and reused request IDs are rejected.

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/v1/runs` | Queue a check or forced optimization with `{"force": false}` |
| `GET` | `/v1/runs/{run_id}` | Read status, solver statuses, safe error, and KPIs |
| `GET` | `/v1/runs/latest` | Read the latest check and last successful optimization |
| `POST` | `/v1/next-cycle` | Queue review validation and next-cycle generation |
| `GET` | `/healthz` | Check API and engine import without reading customer data |

`POST /internal/scheduled-runs` is not exposed by API Gateway. Cloud Run IAM restricts it to the Scheduler service account using an OIDC token. A live spreadsheet lease prevents overlaps; duplicate idempotency keys return the original run.

## Local development

The API requires Python 3.12. The engine dependency is pinned to commit `91aacf817a8c94f338c1bd508079c6e83581fb6a`; push that engine commit before installing this repository from a clean machine.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --requirement requirements-dev.lock
python -m pip install --no-deps --editable .
pytest
ruff check .
mypy src
territory-api
```

Local defaults use in-memory state, jobs, and Sheets. Production must set the variables shown in `.env.example`. Run a worker locally only after a run record exists:

```bash
TERRITORY_RUN_ID=<run-id> territory-worker
```

## Container and deployment

Build and publish an immutable image to the Terraform-created Artifact Registry repository:

```bash
PROJECT_ID=your-project
REGION=europe-west2
IMAGE="$REGION-docker.pkg.dev/$PROJECT_ID/territory-optimisation/api:1.0.0"
gcloud builds submit --tag "$IMAGE"
```

Provision with Scheduler disabled first:

```bash
cd terraform
cp terraform.tfvars.example terraform.tfvars
terraform init
terraform plan
terraform apply
```

If the project already has the Firestore `(default)` database, import it into Terraform state before applying rather than creating another one. Share the target Sheet as Editor with the `worker_service_account` output. The API service account does not need Sheet access.

Install the bound script using the instructions in [`apps-script/README.md`](apps-script/README.md). Put the gateway URL, API key, and HMAC secret in Apps Script Properties, never cells or source control.

## Rollout gates

1. Keep `scheduler_enabled = false` and use a copied synthetic Sheet.
2. Confirm manual forced runs, unchanged-input skips, proposal preservation, and last-good-output retention.
3. Approve/reject all voluntary proposals and verify next-cycle tabs without changing live inputs.
4. Benchmark the 2,550-account, 30-AE, three-region case. Target under 12 minutes; the job deadline is 40 minutes.
5. Enable the schedule for 24 hours on the synthetic Sheet and inspect alerts and run history.
6. Point the allowlist at the production Sheet only after those gates pass.

The job has one task, no platform retry, and a 40-minute timeout. Sheets writes use hidden staging tabs and one atomic structural `batchUpdate`, so a failed swap leaves the prior successful result tabs visible. Read/write retries should remain limited to operations that are safe to repeat.

## Security and operational notes

- Workload identity and Application Default Credentials are used; do not download service-account keys.
- The spreadsheet ID is an environment-level allowlist, not a request parameter.
- Secret Manager holds the HMAC key. Apps Script has a matching copy in Script Properties.
- The schedule runs every 15 minutes in `Europe/London`; unchanged normalized inputs create a cheap `skipped` run.
- Input hashes include normalized accounts, AE roles, accepted settings, and the immutable engine commit.
- Validation and execution failures update `Control` and `Run History` but never clear prior outputs.
- Treat Terraform state as sensitive because it contains generated secret material and API-key metadata. Use an encrypted remote backend with restricted access before production.

## Deferred work

Region-only re-optimization, scenario comparison, Cloud Storage snapshots, multiple spreadsheet registration, Microsoft Graph/Excel, CRM write-back, approval-gated Salesforce changes, and deterministic single-worker audit mode are intentionally outside v1.

## License

MIT. See [`LICENSE`](LICENSE).
