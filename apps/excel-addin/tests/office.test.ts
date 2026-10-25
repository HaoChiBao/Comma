import { describe, expect, it } from "vitest";
import { REVIEW_FILL, UNKNOWN_FILL, clearHighlights, markCells, memoryStore } from "../src/taskpane/office/formatting";
import { SelectionError, expandAddress, readPrecedents, readSelectedCell, readSelectedRange, readSelection, readUsedRange, splitAddress } from "../src/taskpane/office/selection";
import { FakeExcel } from "./fakeExcel";

function demo() {
  return new FakeExcel({
    "Accruals!A1": { value: "Vendor" },
    "Accruals!B3": { value: 100 },
    "Accruals!C3": { value: 50 },
    "Accruals!D3": { value: 150, formula: "=B3+C3", text: "$150.00" },
    "Accruals!D5": { value: 980, text: "$980.00", fill: "#CCE5FF" },
  });
}

describe("addresses", () => {
  it("splits sheet-qualified addresses", () => {
    expect(splitAddress("Accruals!$D$3")).toEqual({ sheet: "Accruals", address: "D3" });
    expect(splitAddress("'Q3 Accruals'!D3")).toEqual({ sheet: "Q3 Accruals", address: "D3" });
    expect(splitAddress("[Book1]Accruals!D3")).toEqual({ sheet: "Accruals", address: "D3" });
  });

  it("expands ranges and lists into single cells", () => {
    expect(expandAddress("Accruals!B3:C3")).toEqual(["Accruals!B3", "Accruals!C3"]);
    expect(expandAddress("B3:B4,Accruals!E1", "Accruals")).toEqual(["Accruals!B3", "Accruals!B4", "Accruals!E1"]);
    expect(expandAddress("Accruals!Z1:AA1")).toEqual(["Accruals!Z1", "Accruals!AA1"]);
  });
});

describe("selection adapter", () => {
  it("reads the active cell", async () => {
    const cell = await readSelectedCell(demo().context());
    expect(cell).toEqual({ sheetName: "Accruals", address: "D3", key: "Accruals!D3", rawValue: 150, displayText: "$150.00", formula: "=B3+C3" });
  });

  it("rejects a multi-cell selection", async () => {
    const excel = demo();
    excel.selection = "Accruals!B3:D3";
    await expect(readSelectedCell(excel.context())).rejects.toBeInstanceOf(SelectionError);
  });

  it("reads direct precedents with their values", async () => {
    const excel = demo();
    excel.precedents["Accruals!D3"] = ["Accruals!B3:C3"];
    const cell = await readSelectedCell(excel.context());
    expect(await readPrecedents(excel.context(), cell, true)).toEqual({
      supported: true,
      addresses: ["Accruals!B3", "Accruals!C3"],
      values: { "Accruals!B3": 100, "Accruals!C3": 50 },
    });
  });

  it("treats ItemNotFound as no precedents", async () => {
    const excel = demo();
    const cell = await readSelectedCell(excel.context());
    expect(await readPrecedents(excel.context(), cell, true)).toEqual({ supported: true, addresses: [], values: {} });
  });

  it("does not invent precedents for a manual value or an unsupported host", async () => {
    const excel = demo();
    excel.selection = "Accruals!D5";
    const manual = await readSelectedCell(excel.context());
    expect(manual.formula).toBe("");
    expect((await readPrecedents(excel.context(), manual, true)).addresses).toEqual([]);
    excel.selection = "Accruals!D3";
    const formula = await readSelectedCell(excel.context());
    expect(await readPrecedents(excel.context(), formula, false)).toEqual({ supported: false, addresses: [], values: {} });
  });

  it("reads a bounded range and skips blanks", async () => {
    const excel = demo();
    excel.selection = "Accruals!A1:D3";
    const range = await readSelectedRange(excel.context());
    expect(range.sheetName).toBe("Accruals");
    expect(range.cells.map((cell) => cell.address)).toEqual(["A1", "B3", "C3", "D3"]);
    expect(range.cells[3]).toEqual({ address: "D3", raw_value: 150, formula: "=B3+C3" });
  });

  it("refuses ranges over 200 cells", async () => {
    const excel = demo();
    excel.selection = "Accruals!A1:J21";
    await expect(readSelectedRange(excel.context())).rejects.toThrow(/at most 200/);
  });

  it("reads the Decagon vendor from column B and reports the current cell", async () => {
    const excel = new FakeExcel({
      "Accruals!B2": { value: "Cloud Services Inc." },
      "Accruals!F2": { value: 150, text: "$150.00" },
    });
    excel.selection = "Accruals!F2";
    const selection = await readSelection(excel.context());
    expect(selection).toMatchObject({ single: true, vendor: "Cloud Services Inc.", cell: { key: "Accruals!F2", rawValue: 150 } });
  });

  it("reads a bounded active used range for the S2c flagged list", async () => {
    const excel = new FakeExcel({
      "Accruals!A1": { value: "Case" },
      "Accruals!B2": { value: "Cloud Services Inc." },
      "Accruals!F2": { value: 150 },
    });
    excel.usedRange = "A1:H2";
    const range = await readUsedRange(excel.context());
    expect(range?.rangeAddress).toBe("Accruals!A1:H2");
    expect(range?.cells.map((cell) => cell.address)).toEqual(["A1", "B2", "F2"]);
  });
});

describe("highlight ledger", () => {
  it("marks review and unknown cells and restores the original fill", async () => {
    const excel = demo();
    const store = memoryStore();
    const marked = await markCells(
      excel.context(),
      [
        { key: "Accruals!D3", status: "review" },
        { key: "Accruals!D5", status: "unknown" },
        { key: "Accruals!D2", status: "supported" },
      ],
      store,
    );
    expect(marked).toBe(2);
    expect(excel.cell("Accruals!D3").fill).toBe(REVIEW_FILL);
    expect(excel.cell("Accruals!D5").fill).toBe(UNKNOWN_FILL);
    expect(store.entries).toEqual([
      { key: "Accruals!D3", previousColor: null },
      { key: "Accruals!D5", previousColor: "#CCE5FF" },
    ]);

    // A second scan must not overwrite the recorded original with WorldState's own color.
    await markCells(excel.context(), [{ key: "Accruals!D5", status: "review" }], store);
    expect(store.entries).toHaveLength(2);
    expect(store.entries[1].previousColor).toBe("#CCE5FF");

    expect(await clearHighlights(excel.context(), store)).toBe(2);
    expect(excel.cell("Accruals!D3").fill).toBeNull();
    expect(excel.cell("Accruals!D5").fill).toBe("#CCE5FF");
    expect(store.entries).toEqual([]);
  });

  it("never writes values or formulas", async () => {
    const excel = demo();
    const store = memoryStore();
    await markCells(excel.context(), [{ key: "Accruals!D3", status: "review" }], store);
    await clearHighlights(excel.context(), store);
    expect(excel.writes.filter((write) => write.startsWith("values"))).toEqual([]);
    expect(excel.cell("Accruals!D3").value).toBe(150);
    expect(excel.cell("Accruals!D3").formula).toBe("=B3+C3");
  });
});
