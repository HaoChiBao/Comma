// Office.js adapter. Every function takes the request context so it can run against a fake in tests.

export const DEFAULT_WORKBOOK_ID = "demo-accruals-v1";
export const WORKBOOK_ID_PROPERTY = "WorldStateWorkbookId";
export const MAX_SCAN_CELLS = 200;

export class SelectionError extends Error {}

export type SelectedCell = {
  sheetName: string;
  address: string;
  key: string;
  rawValue: unknown;
  displayText: string;
  formula: string;
};

export type Precedents = {
  supported: boolean;
  addresses: string[];
  values: Record<string, unknown>;
};

export type RangeCells = {
  sheetName: string;
  rangeAddress: string;
  cells: { address: string; raw_value: unknown; formula: string }[];
};

/** Splits `Accruals!D3`, `'My Sheet'!$D$3`, or `[Book1]Accruals!D3` into sheet and plain address. */
export function splitAddress(full: string): { sheet: string; address: string } {
  const bang = full.lastIndexOf("!");
  if (bang < 0) return { sheet: "", address: full.replace(/\$/g, "") };
  const sheet = full
    .slice(0, bang)
    .replace(/^\[[^\]]*\]/, "")
    .replace(/^'(.*)'$/, "$1")
    .replace(/''/g, "'");
  return { sheet, address: full.slice(bang + 1).replace(/\$/g, "") };
}

export function columnName(index: number): string {
  let name = "";
  for (let n = index + 1; n > 0; n = Math.floor((n - 1) / 26)) {
    name = String.fromCharCode(65 + ((n - 1) % 26)) + name;
  }
  return name;
}

export function columnIndex(name: string): number {
  return name.split("").reduce((total, char) => total * 26 + (char.charCodeAt(0) - 64), 0) - 1;
}

/** Expands `Accruals!B3:C4,Accruals!E1` into single-cell keys like `Accruals!B3`. */
export function expandAddress(range: string, defaultSheet = ""): string[] {
  const keys: string[] = [];
  for (const part of range.split(",")) {
    const { sheet, address } = splitAddress(part.trim());
    const sheetName = sheet || defaultSheet;
    const [start, end = start] = address.split(":");
    const a = /^([A-Z]+)(\d+)$/.exec(start);
    const b = /^([A-Z]+)(\d+)$/.exec(end);
    if (!a || !b) continue;
    for (let row = Number(a[2]); row <= Number(b[2]); row++) {
      for (let col = columnIndex(a[1]); col <= columnIndex(b[1]); col++) {
        keys.push(`${sheetName}!${columnName(col)}${row}`);
      }
    }
  }
  return keys;
}

export async function readWorkbookId(context: Excel.RequestContext): Promise<string> {
  try {
    const property = context.workbook.properties.custom.getItemOrNullObject(WORKBOOK_ID_PROPERTY);
    property.load("value");
    await context.sync();
    if (!property.isNullObject && property.value) return String(property.value);
  } catch {
    // Custom properties need ExcelApi 1.7; fall back to the demo workbook.
  }
  return DEFAULT_WORKBOOK_ID;
}

export async function readSelectedCell(context: Excel.RequestContext): Promise<SelectedCell> {
  const range = context.workbook.getSelectedRange();
  range.load("address,values,text,formulas,rowCount,columnCount");
  await context.sync();
  if (range.rowCount !== 1 || range.columnCount !== 1) {
    throw new SelectionError("Select a single cell to inspect. Use Scan range for several cells.");
  }
  const { sheet, address } = splitAddress(range.address);
  const formula = String(range.formulas[0][0] ?? "");
  return {
    sheetName: sheet,
    address,
    key: `${sheet}!${address}`,
    rawValue: range.values[0][0],
    displayText: String(range.text[0][0] ?? ""),
    formula: formula.startsWith("=") ? formula : "",
  };
}

export function supportsPrecedents(isSetSupported: (name: string, version: string) => boolean = defaultIsSetSupported): boolean {
  return isSetSupported("ExcelApi", "1.12");
}

function defaultIsSetSupported(name: string, version: string): boolean {
  return typeof Office !== "undefined" && Office.context.requirements.isSetSupported(name, version);
}

/** Direct precedents, gated on ExcelApi 1.12. A cell without precedents throws ItemNotFound; that is an empty list. */
export async function readPrecedents(context: Excel.RequestContext, cell: SelectedCell, supported = supportsPrecedents()): Promise<Precedents> {
  if (!supported || !cell.formula) return { supported, addresses: [], values: {} };
  const range = context.workbook.worksheets.getItem(cell.sheetName).getRange(cell.address);
  const precedents = range.getDirectPrecedents();
  precedents.load("addresses");
  try {
    await context.sync();
  } catch (error) {
    if ((error as { code?: string }).code === "ItemNotFound") return { supported, addresses: [], values: {} };
    throw error;
  }
  const addresses = precedents.addresses.flatMap((address) => expandAddress(address, cell.sheetName)).slice(0, MAX_SCAN_CELLS);
  const loaded = addresses.map((key) => {
    const { sheet, address } = splitAddress(key);
    const target = context.workbook.worksheets.getItem(sheet).getRange(address);
    target.load("values");
    return { key, target };
  });
  await context.sync();
  const values: Record<string, unknown> = {};
  for (const { key, target } of loaded) values[key] = target.values[0][0];
  return { supported, addresses, values };
}

/** Reads a bounded selection for Scan range. Never reads the whole used range. */
export async function readSelectedRange(context: Excel.RequestContext): Promise<RangeCells> {
  const range = context.workbook.getSelectedRange();
  range.load("address,values,formulas,rowCount,columnCount,rowIndex,columnIndex");
  await context.sync();
  if (range.rowCount * range.columnCount > MAX_SCAN_CELLS) {
    throw new SelectionError(`Select at most ${MAX_SCAN_CELLS} cells to scan (this range has ${range.rowCount * range.columnCount}).`);
  }
  const { sheet } = splitAddress(range.address);
  const cells: RangeCells["cells"] = [];
  for (let r = 0; r < range.rowCount; r++) {
    for (let c = 0; c < range.columnCount; c++) {
      const value = range.values[r][c];
      if (value === "" || value === null) continue;
      const formula = String(range.formulas[r][c] ?? "");
      cells.push({
        address: `${columnName(range.columnIndex + c)}${range.rowIndex + r + 1}`,
        raw_value: value,
        formula: formula.startsWith("=") ? formula : "",
      });
    }
  }
  return { sheetName: sheet, rangeAddress: range.address, cells };
}

export async function selectCell(context: Excel.RequestContext, key: string): Promise<void> {
  const { sheet, address } = splitAddress(key);
  const worksheet = context.workbook.worksheets.getItem(sheet);
  worksheet.activate();
  worksheet.getRange(address).select();
  await context.sync();
}
