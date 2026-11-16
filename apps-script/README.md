# Bound Apps Script setup

1. Open the target Google Sheet and select **Extensions → Apps Script**.
2. Copy `Code.gs` and `appsscript.json` into the bound project, or deploy with
   `clasp` after authenticating locally.
3. In **Project Settings → Script Properties**, add:
   - `API_BASE_URL`: the Terraform `api_gateway_url` output
   - `API_KEY`: the generated API Gateway key
   - `HMAC_SECRET`: the value stored in Secret Manager
4. Reload the Sheet and authorize the requested scopes.

Only trusted Sheet editors should be given edit access to the bound script.
