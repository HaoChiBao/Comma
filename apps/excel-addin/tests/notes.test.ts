import { describe, expect, it } from "vitest";
import { NOTE_MARK, NotesUnsupportedError, clearNotes, memoryNoteStore, noteText, supportsNotes, writeFindingNotes, type NoteFinding } from "../src/taskpane/office/notes";
import { FakeExcel } from "./fakeExcel";

const CONTEXT = { asOfLabel: "Sep 30, 2025", actorLabel: "Avery Lin (accountant)" };

const review: NoteFinding = {
  key: "Accruals!F2",
  status: "review",
  title: "Potential estimate discrepancy",
  summary: "MSG-0001 (email, received Sep 25) shows $160.00; this cell is $150.00 (difference $10.00). This may be reasonable; review.",
  difference: { amount: "10.00", currency: "USD" },
};
const unknown: NoteFinding = { key: "Accruals!F5", status: "unknown", title: "Only candidate evidence", summary: "No verified source.", difference: null };
const supported: NoteFinding = { key: "Accruals!F7", status: "supported", title: "Supported by PO-C06-1123", summary: "", difference: null };

function demo() {
  return new FakeExcel({ "Accruals!F2": { value: 150 }, "Accruals!F5": { value: 2250 }, "Accruals!F7": { value: 3500 } });
}

describe("WorldState finding notes", () => {
  it("writes a native note on each flagged cell and none on supported cells", async () => {
    const excel = demo();
    const store = memoryNoteStore();
    const result = await writeFindingNotes(excel.context(), [review, unknown, supported], store, CONTEXT, true);
    expect(result).toEqual({ written: 2, updated: 0, skipped: [] });
    const note = excel.notes.get("Accruals!F2") as string;
    expect(note.startsWith(`${NOTE_MARK} Review`)).toBe(true);
    expect(note).toContain("Potential estimate discrepancy");
    expect(note).toContain("Difference: $10.00");
    expect(note).toContain("As of Sep 30, 2025 · viewed as Avery Lin (accountant)");
    expect(excel.notes.has("Accruals!F7")).toBe(false);
    expect(store.keys).toEqual(["Accruals!F2", "Accruals!F5"]);
    expect(excel.cell("Accruals!F2").value).toBe(150);
    expect(excel.writes.filter((write) => write.startsWith("values"))).toEqual([]);
  });

  it("updates its own note instead of adding a duplicate", async () => {
    const excel = demo();
    const store = memoryNoteStore();
    await writeFindingNotes(excel.context(), [review], store, CONTEXT, true);
    const later = await writeFindingNotes(excel.context(), [{ ...review, title: "Amount differs from the invoice" }], store, { ...CONTEXT, asOfLabel: "Dec 15, 2025" }, true);
    expect(later).toEqual({ written: 0, updated: 1, skipped: [] });
    expect(excel.notes.size).toBe(1);
    expect(excel.notes.get("Accruals!F2")).toContain("Amount differs from the invoice");
    expect(store.keys).toEqual(["Accruals!F2"]);
  });

  it("leaves a note someone else wrote alone", async () => {
    const excel = demo();
    excel.notes.set("Accruals!F2", "Checked with the vendor on Oct 1. — Avery");
    const store = memoryNoteStore();
    const result = await writeFindingNotes(excel.context(), [review], store, CONTEXT, true);
    expect(result).toEqual({ written: 0, updated: 0, skipped: ["Accruals!F2"] });
    expect(excel.notes.get("Accruals!F2")).toBe("Checked with the vendor on Oct 1. — Avery");
    expect(store.keys).toEqual([]);
  });

  it("clears only WorldState notes", async () => {
    const excel = demo();
    const store = memoryNoteStore();
    await writeFindingNotes(excel.context(), [review, unknown], store, CONTEXT, true);
    // Someone replaced the F5 note with their own text after the scan.
    excel.notes.set("Accruals!F5", "Keep this.");
    expect(await clearNotes(excel.context(), store, true)).toBe(1);
    expect(excel.notes.has("Accruals!F2")).toBe(false);
    expect(excel.notes.get("Accruals!F5")).toBe("Keep this.");
    expect(store.keys).toEqual([]);
  });

  it("refuses cleanly on hosts without ExcelApi 1.18", async () => {
    const excel = demo();
    await expect(writeFindingNotes(excel.context(), [review], memoryNoteStore(), CONTEXT, false)).rejects.toBeInstanceOf(NotesUnsupportedError);
    expect(excel.notes.size).toBe(0);
    expect(await clearNotes(excel.context(), memoryNoteStore(["Accruals!F2"]), false)).toBe(0);
    expect(supportsNotes((name, version) => name === "ExcelApi" && version === "1.18")).toBe(true);
    expect(supportsNotes(() => false)).toBe(false);
  });

  it("formats the note text", () => {
    expect(noteText(unknown, CONTEXT).split("\n")).toEqual([`${NOTE_MARK} Unknown`, "", "Only candidate evidence", "No verified source.", "", "As of Sep 30, 2025 · viewed as Avery Lin (accountant)"]);
  });
});
