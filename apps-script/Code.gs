const MENU_NAME = 'Territory Optimisation';

function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu(MENU_NAME)
    .addItem('Run now', 'runTerritoryOptimisation')
    .addItem('Refresh status', 'refreshTerritoryStatus')
    .addSeparator()
    .addItem('Create next-cycle tabs', 'createNextCycleTabs')
    .addToUi();
}

function runTerritoryOptimisation() {
  const response = signedRequest_('POST', '/v1/runs', {force: true});
  showResult_('Run submitted', response);
}

function refreshTerritoryStatus() {
  const response = signedRequest_('GET', '/v1/runs/latest', null);
  const latest = response.latest_check;
  if (!latest) {
    SpreadsheetApp.getActive().toast('No runs found.', MENU_NAME, 5);
    return;
  }
  SpreadsheetApp.getActive().toast(`Run ${latest.run_id}: ${latest.status}`, MENU_NAME, 8);
}

function createNextCycleTabs() {
  const ui = SpreadsheetApp.getUi();
  const answer = ui.alert(
    'Create next-cycle tabs?',
    'Every voluntary move must be explicitly approved or rejected. Current input tabs will not be changed.',
    ui.ButtonSet.OK_CANCEL
  );
  if (answer !== ui.Button.OK) return;
  const response = signedRequest_('POST', '/v1/next-cycle', {});
  showResult_('Next-cycle job submitted', response);
}

function signedRequest_(method, path, payload) {
  const properties = PropertiesService.getScriptProperties();
  const baseUrl = requiredProperty_(properties, 'API_BASE_URL').replace(/\/$/, '');
  const apiKey = requiredProperty_(properties, 'API_KEY');
  const secret = requiredProperty_(properties, 'HMAC_SECRET');
  const body = payload === null ? '' : JSON.stringify(payload);
  const timestamp = Math.floor(Date.now() / 1000).toString();
  const requestId = Utilities.getUuid().replace(/-/g, '');
  const idempotencyKey = Utilities.getUuid();
  const bodyHash = hex_(Utilities.computeDigest(
    Utilities.DigestAlgorithm.SHA_256,
    body,
    Utilities.Charset.UTF_8
  ));
  const canonical = [method, path, timestamp, requestId, bodyHash].join('\n');
  const signature = hex_(Utilities.computeHmacSha256Signature(
    canonical,
    secret,
    Utilities.Charset.UTF_8
  ));
  const options = {
    method: method.toLowerCase(),
    muteHttpExceptions: true,
    headers: {
      'x-api-key': apiKey,
      'X-Timestamp': timestamp,
      'X-Request-ID': requestId,
      'X-Signature': signature,
      'Idempotency-Key': idempotencyKey,
    },
  };
  if (payload !== null) {
    options.contentType = 'application/json';
    options.payload = body;
  }
  const response = UrlFetchApp.fetch(baseUrl + path, options);
  const text = response.getContentText();
  let parsed;
  try {
    parsed = JSON.parse(text || '{}');
  } catch (error) {
    throw new Error(`API returned invalid JSON (${response.getResponseCode()}).`);
  }
  if (response.getResponseCode() < 200 || response.getResponseCode() >= 300) {
    throw new Error(parsed.detail || `API request failed (${response.getResponseCode()}).`);
  }
  return parsed;
}

function onEdit(event) {
  if (!event || event.range.getSheet().getName() !== 'Manager Reviews') return;
  const sheet = event.range.getSheet();
  const headers = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0];
  const decisionColumn = headers.indexOf('Manager Decision') + 1;
  const lockColumn = headers.indexOf('Lock next cycle') + 1;
  if (![decisionColumn, lockColumn].includes(event.range.getColumn())) return;
  if (event.range.getRow() < 2) return;
  const actionColumn = headers.indexOf('Action') + 1;
  if (
    event.range.getColumn() === decisionColumn &&
    actionColumn > 0 &&
    String(event.value || '').toLowerCase() === 'reject' &&
    sheet.getRange(event.range.getRow(), actionColumn).getValue() !== 'Move'
  ) {
    event.range.clearContent();
    SpreadsheetApp.getActive().toast(
      'Mandatory assignments cannot be rejected.',
      MENU_NAME,
      6
    );
    return;
  }
  const reviewedByColumn = headers.indexOf('Reviewed By') + 1;
  const reviewedAtColumn = headers.indexOf('Reviewed At') + 1;
  if (reviewedAtColumn > 0) {
    sheet.getRange(event.range.getRow(), reviewedAtColumn).setValue(new Date());
  }
  if (reviewedByColumn > 0) {
    const email = Session.getActiveUser().getEmail();
    if (email) sheet.getRange(event.range.getRow(), reviewedByColumn).setValue(email);
  }
}

function requiredProperty_(properties, name) {
  const value = properties.getProperty(name);
  if (!value) throw new Error(`Missing Apps Script property: ${name}`);
  return value;
}

function hex_(bytes) {
  return bytes.map(byte => ((byte + 256) % 256).toString(16).padStart(2, '0')).join('');
}

function showResult_(message, response) {
  SpreadsheetApp.getActive().toast(`${message}: ${response.run_id}`, MENU_NAME, 8);
}
