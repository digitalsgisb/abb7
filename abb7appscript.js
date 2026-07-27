var PDF_FOLDER_ID = "1nEckc0ZmX4j1UKjWZT_2pv1e7grfxcWr"; 

function doPost(e) {
  var lock = LockService.getScriptLock();
  try {
    lock.waitLock(10000);
    var payload = JSON.parse(e.postData.contents);
    var action = payload.action;
    
    if (action === "APPEND_ROW") {
      var sheet = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(payload.tab_name);
      sheet.appendRow(payload.row_data);
      return ContentService.createTextOutput(JSON.stringify({"status": "success"})).setMimeType(ContentService.MimeType.JSON);
    } 
    
    else if (action === "GENERATE_PDF") {
      var shiftId = payload.shift_id;
      var pdfUrl = generateShiftPDF(shiftId);
      return ContentService.createTextOutput(JSON.stringify({
        "status": "success", 
        "message": "PDF Generated!",
        "url": pdfUrl
      })).setMimeType(ContentService.MimeType.JSON);
    }

    // ==============================================================
    // NEW: Action to scan Google Drive and get a list of available shifts
    // ==============================================================
    else if (action === "GET_SHIFT_LIST") {
      var folder = DriveApp.getFolderById(PDF_FOLDER_ID);
      var files = folder.searchFiles("mimeType = 'application/pdf'");
      var shiftSet = {}; 
      
      // Loop through all PDFs in the folder
      while (files.hasNext()) {
        var file = files.next();
        var name = file.getName(); // Example: "PRS_20260313-Day-Line1_101405.pdf"
        
        // Chop up the name based on the underscores
        var parts = name.split("_");
        if (parts.length >= 2) {
          var shiftId = parts[1]; // This grabs just "20260313-Day-Line1"
          shiftSet[shiftId] = true; // Saves it to our list (automatically prevents duplicates!)
        }
      }
      
      // Convert our list to an array and sort it so the newest shifts are at the top
      var shiftList = Object.keys(shiftSet).sort().reverse(); 
      
      return ContentService.createTextOutput(JSON.stringify({
        "status": "success", 
        "shift_list": shiftList
      })).setMimeType(ContentService.MimeType.JSON);
    }
    
    // ==============================================================
    // NEW: Action to search Drive and return the LATEST iframe link
    // ==============================================================
    else if (action === "GET_PDF_URL") {
      var shiftId = payload.shift_id;
      var folder = DriveApp.getFolderById(PDF_FOLDER_ID);
      var files = folder.searchFiles("title contains '" + shiftId + "' and mimeType = 'application/pdf'");
      
      var latestFile = null;
      var latestDate = 0;
      
      // Loop through ALL files found for this shift
      while (files.hasNext()) {
        var currentFile = files.next();
        var fileDate = currentFile.getDateCreated().getTime(); // Get the exact millisecond it was created
        
        // If this file is newer than the last one we checked, save it!
        if (fileDate > latestDate) {
          latestDate = fileDate;
          latestFile = currentFile;
        }
      }
      
      // Now, if we actually found a newest file, generate the link!
      if (latestFile) {
        latestFile.setSharing(DriveApp.Access.ANYONE_WITH_LINK, DriveApp.Permission.VIEW);
        var iframeUrl = "https://drive.google.com/file/d/" + latestFile.getId() + "/preview";
        
        return ContentService.createTextOutput(JSON.stringify({
          "status": "success", 
          "url": iframeUrl
        })).setMimeType(ContentService.MimeType.JSON);
      } else {
        return ContentService.createTextOutput(JSON.stringify({
          "status": "error", 
          "message": "No PDF found for this shift yet."
        })).setMimeType(ContentService.MimeType.JSON);
      }
    }
    
  } catch (error) {
    return ContentService.createTextOutput(JSON.stringify({"status": "error", "message": error.message})).setMimeType(ContentService.MimeType.JSON);
  } finally {
    lock.releaseLock();
  }
}

function isBlank(value) {
  return value === "" || value === null || typeof value === "undefined";
}

function cleanText(value, fallback) {
  if (isBlank(value) || String(value).trim() === "") {
    return typeof fallback === "undefined" ? "" : fallback;
  }
  return String(value).trim();
}

function toNumber(value) {
  if (isBlank(value)) {
    return 0;
  }
  var numberValue = Number(String(value).replace(/,/g, ""));
  return isNaN(numberValue) ? 0 : numberValue;
}

function roundToTwo(value) {
  return Math.round((toNumber(value) + Number.EPSILON) * 100) / 100;
}

function sameShiftId(value, shiftId) {
  return cleanText(value, "") === cleanText(shiftId, "");
}

function parseHourSlotStart(hourSlot) {
  var match = cleanText(hourSlot, "").match(/^(\d{1,2})(?:[.:]\d{1,2})?/);
  if (!match) {
    return null;
  }

  var hour = parseInt(match[1], 10);
  if (hour === 24) {
    hour = 0;
  }
  return hour >= 0 && hour <= 23 ? hour : null;
}

function parseWorkingTimeStartHour(workingTime, shiftType) {
  var text = cleanText(workingTime, "");
  var twelveHourMatch = text.match(/(\d{1,2})(?::(\d{2}))?\s*(AM|PM)/i);

  if (twelveHourMatch) {
    var twelveHour = parseInt(twelveHourMatch[1], 10) % 12;
    if (twelveHourMatch[3].toUpperCase() === "PM") {
      twelveHour += 12;
    }
    return twelveHour;
  }

  var twentyFourHourMatch = text.match(/^\s*(\d{1,2})(?::\d{2})?/);
  if (twentyFourHourMatch) {
    var twentyFourHour = parseInt(twentyFourHourMatch[1], 10);
    if (twentyFourHour >= 0 && twentyFourHour <= 23) {
      return twentyFourHour;
    }
  }

  return cleanText(shiftType, "").toLowerCase().indexOf("night") !== -1 ? 20 : 8;
}

function buildShiftRowMap(startHour) {
  var rowMap = {};
  for (var index = 0; index < 12; index++) {
    rowMap[String((startHour + index) % 24)] = 10 + (index * 2);
  }
  return rowMap;
}

function displayHour(hour) {
  var twelveHour = hour % 12;
  return twelveHour === 0 ? 12 : twelveHour;
}

function configureTemplateHourLabels(sheet, startHour) {
  for (var index = 0; index < 12; index++) {
    var hour = (startHour + index) % 24;
    var nextHour = (hour + 1) % 24;
    var rowNumber = 10 + (index * 2);
    sheet.getRange("D" + rowNumber).setValue(
      displayHour(hour) + ".00 - " + displayHour(nextHour) + ".00"
    );
  }
}

function findLatestShiftRow(data, shiftId) {
  for (var index = data.length - 1; index >= 1; index--) {
    if (sameShiftId(data[index][0], shiftId)) {
      return data[index];
    }
  }
  return null;
}

function appendUnique(values, value) {
  var text = cleanText(value, "");
  if (text !== "" && values.indexOf(text) === -1) {
    values.push(text);
  }
}

function aggregateHourlyData(data, shiftId, rowMap) {
  var hours = {};
  var modelTotals = {};

  for (var index = 1; index < data.length; index++) {
    var row = data[index];
    if (!sameShiftId(row[0], shiftId)) {
      continue;
    }

    var startHour = parseHourSlotStart(row[3]);
    var hourKey = startHour === null ? "" : String(startHour);
    if (hourKey === "" || !Object.prototype.hasOwnProperty.call(rowMap, hourKey)) {
      continue;
    }

    if (!hours[hourKey]) {
      hours[hourKey] = {
        plan: 0,
        actual: 0,
        restMinutes: 0,
        segments: {},
        segmentOrder: []
      };
    }

    var model = cleanText(row[2], "-");
    var lotNumber = cleanText(row[6], "-");
    var plan = toNumber(row[4]);
    var actual = toNumber(row[5]);
    var restMinutes = toNumber(row[7]);
    var segmentKey = model + "\u001f" + lotNumber;
    var hourData = hours[hourKey];

    if (!hourData.segments[segmentKey]) {
      hourData.segments[segmentKey] = {
        model: model,
        lotNumber: lotNumber,
        plan: 0,
        actual: 0
      };
      hourData.segmentOrder.push(segmentKey);
    }

    hourData.plan += plan;
    hourData.actual += actual;
    hourData.restMinutes += restMinutes;
    hourData.segments[segmentKey].plan += plan;
    hourData.segments[segmentKey].actual += actual;

    if (model !== "-") {
      if (!Object.prototype.hasOwnProperty.call(modelTotals, model)) {
        modelTotals[model] = 0;
      }
      modelTotals[model] += actual;
    }
  }

  return {
    hours: hours,
    modelTotals: modelTotals
  };
}

function compactNumber(value) {
  return String(roundToTwo(value));
}

function getHourSegments(hourData) {
  var segments = [];
  for (var index = 0; index < hourData.segmentOrder.length; index++) {
    segments.push(hourData.segments[hourData.segmentOrder[index]]);
  }
  return segments;
}

function splitProductionHourRows(sheet, rowNumber) {
  sheet.getRange("B" + rowNumber + ":C" + (rowNumber + 1)).breakApart();
  sheet.getRange("B" + rowNumber + ":C" + rowNumber).merge();
  sheet.getRange("B" + (rowNumber + 1) + ":C" + (rowNumber + 1)).merge();
  sheet.getRange("E" + rowNumber + ":E" + (rowNumber + 1)).breakApart();
  sheet.getRange("F" + rowNumber + ":F" + (rowNumber + 1)).breakApart();
  sheet.getRange("G" + rowNumber + ":G" + (rowNumber + 1)).breakApart();
}

function writeProductionSegment(sheet, rowNumber, segments) {
  var modelValues = [];
  var planValues = [];
  var actualValues = [];
  var lotValues = [];

  for (var index = 0; index < segments.length; index++) {
    modelValues.push(segments[index].model);
    planValues.push(compactNumber(segments[index].plan));
    actualValues.push(compactNumber(segments[index].actual));
    lotValues.push(segments[index].lotNumber);
  }

  var modelCell = sheet.getRange("B" + rowNumber);
  var planCell = sheet.getRange("E" + rowNumber);
  var actualCell = sheet.getRange("F" + rowNumber);
  var lotCell = sheet.getRange("G" + rowNumber);

  modelCell.setValue(modelValues.join("\n")).setWrap(true);
  planCell.setValue(planValues.join("\n")).setWrap(true);
  actualCell.setValue(actualValues.join("\n")).setWrap(true);
  lotCell.setValue(lotValues.join("\n")).setWrap(true);

  var fontSize = segments.length > 1 ? 14 : 18;
  modelCell.setFontSize(fontSize);
  planCell.setFontSize(fontSize).setFontWeight("bold");
  actualCell.setFontSize(fontSize).setFontWeight("bold");
  lotCell.setFontSize(fontSize);
}

function writeHourlyData(sheet, data, shiftId, rowMap) {
  var aggregate = aggregateHourlyData(data, shiftId, rowMap);

  for (var hourKey in aggregate.hours) {
    if (!Object.prototype.hasOwnProperty.call(aggregate.hours, hourKey)) {
      continue;
    }

    var hourData = aggregate.hours[hourKey];
    var rowNumber = rowMap[hourKey];
    var segments = getHourSegments(hourData);

    if (segments.length === 1) {
      writeProductionSegment(sheet, rowNumber, segments);
    } else {
      splitProductionHourRows(sheet, rowNumber);
      writeProductionSegment(sheet, rowNumber, [segments[0]]);
      writeProductionSegment(sheet, rowNumber + 1, segments.slice(1));
    }

    sheet.getRange("D" + (rowNumber + 1)).setValue(
      hourData.restMinutes > 0 ? roundToTwo(hourData.restMinutes) : ""
    ).setFontSize(16);
  }

  return aggregate.modelTotals;
}

function aggregateRejectData(data, shiftId, rowMap) {
  var hours = {};

  for (var index = 1; index < data.length; index++) {
    var row = data[index];
    if (!sameShiftId(row[0], shiftId)) {
      continue;
    }

    var startHour = parseHourSlotStart(row[1]);
    var hourKey = startHour === null ? "" : String(startHour);
    if (hourKey === "" || !Object.prototype.hasOwnProperty.call(rowMap, hourKey)) {
      continue;
    }

    if (!hours[hourKey]) {
      hours[hourKey] = {
        slab: 0,
        slabCodes: [],
        returnRoll: 0,
        ohtNumbers: [],
        rejectNg: 0,
        ngCodes: [],
        loft: 0,
        loftCodes: []
      };
    }

    var hourData = hours[hourKey];
    hourData.slab += toNumber(row[2]);
    appendUnique(hourData.slabCodes, row[3]);
    hourData.returnRoll += toNumber(row[4]);
    appendUnique(hourData.ohtNumbers, row[5]);
    hourData.rejectNg += toNumber(row[6]);
    appendUnique(hourData.ngCodes, row[7]);
    hourData.loft += toNumber(row[8]);
    appendUnique(hourData.loftCodes, row[9]);
  }

  return hours;
}

function joinedOrDash(values) {
  return values.length > 0 ? values.join(", ") : "-";
}

function writeRejectData(sheet, data, shiftId, rowMap) {
  var hours = aggregateRejectData(data, shiftId, rowMap);

  for (var hourKey in hours) {
    if (!Object.prototype.hasOwnProperty.call(hours, hourKey)) {
      continue;
    }

    var rowNumber = rowMap[hourKey];
    var hourData = hours[hourKey];
    sheet.getRange("N" + rowNumber).setValue(roundToTwo(hourData.slab)).setFontSize(16);
    sheet.getRange("O" + rowNumber).setValue(joinedOrDash(hourData.slabCodes)).setWrap(true).setFontSize(16);
    sheet.getRange("P" + rowNumber).setValue(roundToTwo(hourData.returnRoll)).setFontSize(16);
    sheet.getRange("Q" + rowNumber).setValue(joinedOrDash(hourData.ohtNumbers)).setWrap(true).setFontSize(16);
    sheet.getRange("R" + rowNumber).setValue(roundToTwo(hourData.rejectNg)).setFontSize(16);
    sheet.getRange("S" + rowNumber).setValue(joinedOrDash(hourData.ngCodes)).setWrap(true).setFontSize(16);
    sheet.getRange("T" + rowNumber).setValue(roundToTwo(hourData.loft)).setFontSize(16);
    sheet.getRange("U" + rowNumber).setValue(joinedOrDash(hourData.loftCodes)).setWrap(true).setFontSize(16);
  }
}

function downtimeBucket(category) {
  var normalized = cleanText(category, "").toLowerCase();
  if (normalized.indexOf("schedule") !== -1 || normalized.indexOf("planned") !== -1) {
    return "schedule";
  }
  if (
    normalized.indexOf("machine") !== -1 ||
    normalized.indexOf("mechanical") !== -1 ||
    normalized.indexOf("electrical") !== -1 ||
    normalized.indexOf("facility") !== -1 ||
    normalized.indexOf("maintenance") !== -1
  ) {
    return "machine";
  }
  return "production";
}

function newDowntimeBucket() {
  return {
    codes: [],
    duration: 0,
    hasDuration: false
  };
}

function aggregateDowntimeData(data, shiftId, rowMap) {
  var hours = {};

  for (var index = 1; index < data.length; index++) {
    var row = data[index];
    if (!sameShiftId(row[0], shiftId)) {
      continue;
    }

    var startHour = parseHourSlotStart(row[1]);
    var hourKey = startHour === null ? "" : String(startHour);
    if (hourKey === "" || !Object.prototype.hasOwnProperty.call(rowMap, hourKey)) {
      continue;
    }

    if (!hours[hourKey]) {
      hours[hourKey] = {
        schedule: newDowntimeBucket(),
        production: newDowntimeBucket(),
        machine: newDowntimeBucket(),
        remarks: []
      };
    }

    var hourData = hours[hourKey];
    var bucketName = downtimeBucket(row[2]);
    var bucket = hourData[bucketName];
    var code = cleanText(row[3], "");
    var description = cleanText(row[5], "");
    var remarks = cleanText(row[6], "");

    appendUnique(bucket.codes, code);
    if (!isBlank(row[4]) && String(row[4]).trim() !== "") {
      bucket.duration += toNumber(row[4]);
      bucket.hasDuration = true;
    }

    var detailParts = [];
    if (code !== "") {
      detailParts.push(code);
    }
    if (description !== "") {
      detailParts.push(description);
    }
    var detail = detailParts.join(": ");
    if (remarks !== "") {
      detail += (detail === "" ? "" : " - ") + remarks;
    }
    appendUnique(hourData.remarks, detail);
  }

  return hours;
}

function writeDowntimeBucket(sheet, rowNumber, bucket, codeColumn, minuteColumn) {
  if (bucket.codes.length === 0 && !bucket.hasDuration) {
    return;
  }
  sheet.getRange(codeColumn + rowNumber)
    .setValue(joinedOrDash(bucket.codes))
    .setWrap(true)
    .setFontSize(16);
  sheet.getRange(minuteColumn + rowNumber).setValue(
    bucket.hasDuration ? roundToTwo(bucket.duration) : "-"
  ).setFontSize(16);
}

function writeDowntimeData(sheet, data, shiftId, rowMap) {
  var hours = aggregateDowntimeData(data, shiftId, rowMap);

  for (var hourKey in hours) {
    if (!Object.prototype.hasOwnProperty.call(hours, hourKey)) {
      continue;
    }

    var rowNumber = rowMap[hourKey];
    var hourData = hours[hourKey];
    writeDowntimeBucket(sheet, rowNumber, hourData.schedule, "AA", "AB");
    writeDowntimeBucket(sheet, rowNumber, hourData.production, "AC", "AD");
    writeDowntimeBucket(sheet, rowNumber, hourData.machine, "AE", "AF");

    if (hourData.remarks.length > 0) {
      sheet.getRange("AA" + (rowNumber + 1))
        .setValue(hourData.remarks.join(" | "))
        .setWrap(true)
        .setFontSize(14);
    }
  }
}

function writeSummaryData(sheet, modelTotals) {
  var summaryColumns = ["AD", "AE", "AF", "AG"];
  var models = Object.keys(modelTotals);
  var normalCount = models.length > 4 ? 3 : models.length;

  for (var index = 0; index < normalCount; index++) {
    var column = summaryColumns[index];
    sheet.getRange(column + "54").setValue(models[index]).setFontSize(16).setFontWeight("bold");
    sheet.getRange(column + "56").setValue(roundToTwo(modelTotals[models[index]])).setFontSize(18).setFontWeight("bold");
  }

  if (models.length > 4) {
    var overflowModels = models.slice(3);
    var overflowTotal = 0;
    for (var overflowIndex = 0; overflowIndex < overflowModels.length; overflowIndex++) {
      overflowTotal += modelTotals[overflowModels[overflowIndex]];
    }
    sheet.getRange("AG54").setValue(overflowModels.join("\n")).setWrap(true).setFontSize(14).setFontWeight("bold");
    sheet.getRange("AG56").setValue(roundToTwo(overflowTotal)).setFontSize(18).setFontWeight("bold");
  } else if (models.length === 4) {
    sheet.getRange("AG54").setValue(models[3]).setFontSize(16).setFontWeight("bold");
    sheet.getRange("AG56").setValue(roundToTwo(modelTotals[models[3]])).setFontSize(18).setFontWeight("bold");
  }
}

function findLatestParameterRows(data, shiftId) {
  var rowsByModel = {};
  var modelOrder = [];

  for (var index = 1; index < data.length; index++) {
    var row = data[index];
    if (!sameShiftId(row[0], shiftId)) {
      continue;
    }

    var model = cleanText(row[1], "-");
    if (!Object.prototype.hasOwnProperty.call(rowsByModel, model)) {
      modelOrder.push(model);
    }
    rowsByModel[model] = row;
  }

  return {
    rowsByModel: rowsByModel,
    modelOrder: modelOrder
  };
}

function writeParameterData(sheet, data, shiftId) {
  var latest = findLatestParameterRows(data, shiftId);
  var tableOneRows = [41, 43, 45, 47];
  var tableTwoRows = [55, 57, 59];
  var tableThreeRows = [41, 45];
  var glueCount = 0;

  for (var index = 0; index < latest.modelOrder.length; index++) {
    var model = latest.modelOrder[index];
    var row = latest.rowsByModel[model];

    if (index < tableOneRows.length) {
      var tableOneRow = tableOneRows[index];
      sheet.getRange("V" + tableOneRow).setValue(model).setFontSize(16).setFontWeight("bold");
      sheet.getRange("W" + tableOneRow).setValue(row[2]).setFontSize(16);
      sheet.getRange("X" + tableOneRow).setValue(row[3]).setFontSize(16);
      sheet.getRange("Y" + tableOneRow).setValue(row[4]).setFontSize(16);
      sheet.getRange("Z" + tableOneRow).setValue(row[5]).setFontSize(16);
    }

    if (index < tableTwoRows.length) {
      var tableTwoRow = tableTwoRows[index];
      sheet.getRange("V" + tableTwoRow).setValue(model).setFontSize(16).setFontWeight("bold");
      sheet.getRange("W" + tableTwoRow).setValue(row[6]).setFontSize(16);
      sheet.getRange("X" + tableTwoRow).setValue(row[7]).setFontSize(16);
      sheet.getRange("Y" + tableTwoRow).setValue(row[8]).setFontSize(16);
    }

    var glueStandard = row[9];
    var glueActual = row[10];
    var hasGlue = (
      (!isBlank(glueStandard) && cleanText(glueStandard, "") !== "-") ||
      (!isBlank(glueActual) && cleanText(glueActual, "") !== "-")
    );

    if (hasGlue && glueCount < tableThreeRows.length) {
      var tableThreeRow = tableThreeRows[glueCount];
      sheet.getRange("AD" + tableThreeRow).setValue(model).setFontSize(16).setFontWeight("bold");
      sheet.getRange("AE" + tableThreeRow).setValue(glueStandard).setFontSize(16);
      sheet.getRange("AF" + tableThreeRow).setValue(glueActual).setFontSize(16);
      glueCount++;
    }
  }
}

function generateShiftPDF(shiftId) {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  
  var templateSheet = ss.getSheetByName("PRS_Template");
  if (!templateSheet) {
    throw new Error("Could not find 'PRS_Template' tab.");
  }
  var shiftDataSheet = ss.getSheetByName("Shift_Data");
  if (!shiftDataSheet) {
    throw new Error("Could not find 'Shift_Data' tab.");
  }

  var shiftData = shiftDataSheet.getDataRange().getValues();
  var targetShift = findLatestShiftRow(shiftData, shiftId);
  if (!targetShift) {
    throw new Error("Shift ID '" + shiftId + "' was not found in Shift_Data.");
  }
  
  var uniqueTempName = "TEMP_" + shiftId + "_" + new Date().getTime();
  var tempSheet = templateSheet.copyTo(ss);
  tempSheet.setName(uniqueTempName);
  
  try {
    tempSheet.getRange("C3").setValue(targetShift[2]).setFontSize(18).setFontWeight("bold"); // Line
    tempSheet.getRange("G3").setValue(targetShift[3]).setFontSize(18).setFontWeight("bold"); // Shift
    tempSheet.getRange("C4").setValue(targetShift[1]).setFontSize(18).setFontWeight("bold"); // Production date
    tempSheet.getRange("H4").setValue(targetShift[5]).setFontSize(18).setFontWeight("bold"); // Working time
    tempSheet.getRange("N3").setValue(targetShift[6]).setFontSize(18).setFontWeight("bold"); // Supervisor
    tempSheet.getRange("N4").setValue(targetShift[7]).setFontSize(18).setFontWeight("bold"); // Leader
    tempSheet.getRange("V3").setValue(targetShift[8]).setFontSize(18).setFontWeight("bold"); // Forming operator
    tempSheet.getRange("V4").setValue(targetShift[9]).setFontSize(18).setFontWeight("bold"); // Waterjet operator
    tempSheet.getRange("AC3").setValue(targetShift[10]).setFontSize(18).setFontWeight("bold"); // Assembly operator
    tempSheet.getRange("AC4").setValue(targetShift[11]).setFontSize(18).setFontWeight("bold"); // Quality inspector

    var shiftType = cleanText(targetShift[3], "");
    var startHour = parseWorkingTimeStartHour(targetShift[5], shiftType);
    var rowMap = buildShiftRowMap(startHour);
    configureTemplateHourLabels(tempSheet, startHour);

    for (var rowIndex = 0; rowIndex < 12; rowIndex++) {
      var templateRow = 10 + (rowIndex * 2);
      tempSheet.getRange("N" + templateRow + ":U" + templateRow).setValue("-");
      tempSheet.getRange("AA" + templateRow + ":AF" + templateRow).setValue("-");
      tempSheet.getRange("AA" + (templateRow + 1)).setValue("-");
    }

    var modelTotals = {};
    var hourlySheet = ss.getSheetByName("Hourly_Data");
    if (hourlySheet) {
      modelTotals = writeHourlyData(
        tempSheet,
        hourlySheet.getDataRange().getValues(),
        shiftId,
        rowMap
      );
    }

    var rejectSheet = ss.getSheetByName("Reject_Data");
    if (rejectSheet) {
      writeRejectData(
        tempSheet,
        rejectSheet.getDataRange().getValues(),
        shiftId,
        rowMap
      );
    }

    var downtimeSheet = ss.getSheetByName("Downtime_Data");
    if (downtimeSheet) {
      writeDowntimeData(
        tempSheet,
        downtimeSheet.getDataRange().getValues(),
        shiftId,
        rowMap
      );
    }

    writeSummaryData(tempSheet, modelTotals);

    var parameterSheet = ss.getSheetByName("Parameter_Data");
    if (parameterSheet) {
      writeParameterData(
        tempSheet,
        parameterSheet.getDataRange().getValues(),
        shiftId
      );
    }
    
    SpreadsheetApp.flush();
    
    var folder = DriveApp.getFolderById(PDF_FOLDER_ID);
    var url = "https://docs.google.com/spreadsheets/d/" + ss.getId() +
      "/export?exportFormat=pdf&format=pdf&size=A3&portrait=false&scale=2" +
      "&sheetnames=false&printtitle=false&pagenumbers=false&gridlines=false&fzr=false" +
      "&horizontal_alignment=CENTER&vertical_alignment=TOP&range=A1%3AAJ62" +
      "&top_margin=0.10&bottom_margin=0.10&left_margin=0.10&right_margin=0.10" +
      "&gid=" + tempSheet.getSheetId();

    var token = ScriptApp.getOAuthToken();
    var response = UrlFetchApp.fetch(url, { headers: { 'Authorization': 'Bearer ' + token } });
    if (response.getResponseCode() !== 200) {
      throw new Error("Google Sheets PDF export failed with HTTP " + response.getResponseCode() + ".");
    }
    
    var pdfName = "PRS_" + shiftId + "_" + Utilities.formatDate(new Date(), "GMT+8", "HHmmss") + ".pdf";
    var blob = response.getBlob().setName(pdfName);
    var newFile = folder.createFile(blob);
    
    // ==============================================================
    // NEW: Update file permissions and return the iframe preview link!
    // ==============================================================
    newFile.setSharing(DriveApp.Access.ANYONE_WITH_LINK, DriveApp.Permission.VIEW);
    var iframeUrl = "https://drive.google.com/file/d/" + newFile.getId() + "/preview";
    
    return iframeUrl;
    
  } finally {
    ss.deleteSheet(tempSheet);
  }
}

function testRun() {
  var testShiftId = "20260313-Day-Line1"; 
  Logger.log("Starting test for Shift ID: " + testShiftId);
  var resultUrl = generateShiftPDF(testShiftId);
  Logger.log("Test Complete! PDF URL: " + resultUrl);
}
