import { describe, expect, it } from "vitest";
import { REVIEW_FILL, memoryStore } from "../src/taskpane/office/formatting";
import { NotesUnsupportedError, TEST_NOTE_TEXT, supportsNotes, upsertNote } from "../src/taskpane/office/notes";
import { FakeExcel } from "./fakeExcel";

function demo() {
  return new FakeExcel({ "Accruals!B3": { value: 100, formula: "", fill: null } });
}

describe("WorldState tooltip note", () => {
  it("adds a native note to B3 and highlights it without touching the value", async () => {
    const excel = demo();
    const store = memoryStore();
    const result = await upsertNote(excel.context(), store, { supported: true });
    expect(result).toEqual({ sheet: "Accruals", address: "B3", created: true });
    expect(excel.notes.get("Accruals!B3")).toBe(TEST_NOTE_TEXT);
    expect(TEST_NOTE_TEXT).toContain("Difference: $10");
    expect(excel.cell("Accruals!B3").fill).toBe(REVIEW_FILL);
    expect(excel.cell("Accruals!B3").value).toBe(100);
    expect(excel.writes.filter((write) => write.startsWith("values"))).toEqual([]);
  });

  it("updates an existing note instead of adding a duplicate", async () => {
    const excel = demo();
    excel.notes.set("Accruals!B3", "old note");
    const store = memoryStore();
    const result = await upsertNote(excel.context(), store, { supported: true });
    expect(result.created).toBe(false);
    expect(excel.notes.size).toBe(1);
    expect(excel.notes.get("Accruals!B3")).toBe(TEST_NOTE_TEXT);
    await upsertNote(excel.context(), store, { supported: true });
    expect(excel.writes.filter((write) => write.startsWith("note add"))).toEqual([]);
    expect(store.entries).toHaveLength(1);
  });

  it("refuses cleanly on hosts without ExcelApi 1.18", async () => {
    const excel = demo();
    await expect(upsertNote(excel.context(), memoryStore(), { supported: false })).rejects.toBeInstanceOf(NotesUnsupportedError);
    expect(excel.notes.size).toBe(0);
    expect(excel.cell("Accruals!B3").fill).toBeNull();
    expect(supportsNotes((name, version) => name === "ExcelApi" && version === "1.18")).toBe(true);
    expect(supportsNotes(() => false)).toBe(false);
  });
});
