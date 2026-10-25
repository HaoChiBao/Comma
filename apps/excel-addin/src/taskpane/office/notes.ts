// Native Excel cell notes (the hover popup). Notes need ExcelApi 1.18; older hosts get a clear message instead.

import { markCells, type LedgerStore } from "./formatting";

export const NOTES_API = { name: "ExcelApi", version: "1.18" };
export const TEST_NOTE_CELL = "B3";
export const TEST_NOTE_TEXT = [
  "⚠️ WorldState — Potential Discrepancy",
  "",
  "Recorded Amount: $150",
  "Vendor Invoice: $160",
  "Difference: $10",
  "",
  "This cell may contain an inconsistency. Review the supporting evidence.",
].join("\n");

export class NotesUnsupportedError extends Error {
  constructor() {
    super(
      "This version of Excel does not support cell notes from add-ins (needs ExcelApi 1.18). " +
        "Update to a current Microsoft 365 build of Excel, or use Excel on the web.",
    );
  }
}

export function supportsNotes(isSetSupported: (name: string, version: string) => boolean = defaultIsSetSupported): boolean {
  return isSetSupported(NOTES_API.name, NOTES_API.version);
}

function defaultIsSetSupported(name: string, version: string): boolean {
  return typeof Office !== "undefined" && Office.context.requirements.isSetSupported(name, version);
}

/**
 * Writes the note on the active sheet's cell, updating an existing note rather than adding a second one,
 * and highlights the cell through the reversible ledger. Values and formulas are never written.
 */
export async function upsertNote(
  context: Excel.RequestContext,
  store: LedgerStore,
  { address = TEST_NOTE_CELL, content = TEST_NOTE_TEXT, supported = supportsNotes() } = {},
): Promise<{ sheet: string; address: string; created: boolean }> {
  if (!supported) throw new NotesUnsupportedError();
  const sheet = context.workbook.worksheets.getActiveWorksheet();
  sheet.load("name");
  const existing = sheet.notes.getItemOrNullObject(address);
  existing.load("content");
  await context.sync();
  const created = existing.isNullObject;
  if (created) sheet.notes.add(address, content);
  else existing.content = content;
  await context.sync();
  await markCells(context, [{ key: `${sheet.name}!${address}`, status: "review" }], store);
  return { sheet: sheet.name, address, created };
}
