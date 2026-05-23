/**
 * kids-sports-sync telemetry receiver
 * Deploy as a Google Apps Script web app (Execute as: Me, Who has access: Anyone)
 * Paste the web app URL into telemetry.py → TELEMETRY_URL
 */

const SHEET_NAME = "pings";

function doPost(e) {
  try {
    const data = JSON.parse(e.postData.contents);
    const ss    = SpreadsheetApp.getActiveSpreadsheet();
    let sheet   = ss.getSheetByName(SHEET_NAME);

    if (!sheet) {
      sheet = ss.insertSheet(SHEET_NAME);
      sheet.appendRow([
        "timestamp", "instance_id", "script", "v",
        "teams", "kids", "platforms", "personalizations", "email_enabled",
        "events_copied", "events_updated", "events_deleted",
        "rsvps_set", "cal_changes",
      ]);
      sheet.setFrozenRows(1);
    }

    sheet.appendRow([
      new Date(data.ts || new Date()),
      data.instance_id   || "",
      data.script        || "",
      data.v             || "",
      data.teams         ?? "",
      data.kids          ?? "",
      (data.platforms    || []).join(", "),
      data.personalizations ?? "",
      data.email_enabled === false ? "false" : "true",
      data.events_copied  ?? "",
      data.events_updated ?? "",
      data.events_deleted ?? "",
      data.rsvps_set      ?? "",
      data.cal_changes    ?? "",
    ]);

    return ContentService
      .createTextOutput("ok")
      .setMimeType(ContentService.MimeType.TEXT);
  } catch (err) {
    return ContentService
      .createTextOutput("error: " + err.message)
      .setMimeType(ContentService.MimeType.TEXT);
  }
}

/** Test: run this manually in the Apps Script editor to verify the sheet works */
function testPost() {
  doPost({ postData: { contents: JSON.stringify({
    instance_id: "test-123", ts: new Date().toISOString(),
    script: "test", v: "1", teams: 3, kids: 2,
    platforms: ["teamsnap"], personalizations: 2, email_enabled: true,
    events_copied: 1, events_updated: 0, events_deleted: 0,
    rsvps_set: 4, cal_changes: 1,
  })}});
  Logger.log("done — check the pings sheet");
}
