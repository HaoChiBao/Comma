// A small in-memory stand-in for the parts of Excel.RequestContext the adapters use.

type Cell = { value: unknown; formula?: string; text?: string; fill: string | null };

export class FakeExcel {
  cells = new Map<string, Cell>();
  selection = "Accruals!D3";
  precedents: Record<string, string[]> = {};
  syncs = 0;
  writes: string[] = [];
  private pendingError: Error | null = null;

  constructor(cells: Record<string, Partial<Cell>>) {
    for (const [key, cell] of Object.entries(cells)) this.cells.set(key, { value: "", fill: null, ...cell });
  }

  cell(key: string): Cell {
    if (!this.cells.has(key)) this.cells.set(key, { value: "", fill: null });
    return this.cells.get(key) as Cell;
  }

  notes = new Map<string, string>();
  activeSheet = "Accruals";
  usedRange: string | null = null;

  context(): Excel.RequestContext {
    const fake = this;
    const notes = (sheet: string) => ({
      add: (address: string, content: string) => {
        if (fake.notes.has(`${sheet}!${address}`)) throw new Error("Note already exists");
        fake.notes.set(`${sheet}!${address}`, content);
        fake.writes.push(`note add ${sheet}!${address}`);
      },
      getItemOrNullObject: (address: string) => {
        const key = `${sheet}!${address}`;
        return {
          load: () => undefined,
          get isNullObject() {
            return !fake.notes.has(key);
          },
          get content() {
            return fake.notes.get(key) ?? "";
          },
          set content(value: string) {
            fake.notes.set(key, value);
            fake.writes.push(`note update ${key}`);
          },
          delete: () => {
            fake.notes.delete(key);
            fake.writes.push(`note delete ${key}`);
          },
        };
      },
    });
    const worksheet = (sheet: string) => ({
      name: sheet,
      load: () => undefined,
      notes: notes(sheet),
      activate: () => fake.writes.push(`activate ${sheet}`),
      getRange: (address: string) => fake.range(`${sheet}!${address}`),
      getUsedRangeOrNullObject: () => fake.usedRange ? fake.range(`${sheet}!${fake.usedRange}`) : { load: () => undefined, isNullObject: true },
    });
    return {
      sync: async () => {
        fake.syncs += 1;
        if (fake.pendingError) {
          const error = fake.pendingError;
          fake.pendingError = null;
          throw error;
        }
      },
      workbook: {
        getSelectedRange: () => fake.range(fake.selection),
        worksheets: { getItem: worksheet, getActiveWorksheet: () => worksheet(fake.activeSheet) },
        properties: { custom: { getItemOrNullObject: () => ({ load: () => undefined, isNullObject: true, value: "" }) } },
      },
    } as unknown as Excel.RequestContext;
  }

  range(key: string) {
    const fake = this;
    const [sheet, address] = key.split("!");
    const [start, end = start] = address.split(":");
    const parse = (a: string) => {
      const match = /^([A-Z]+)(\d+)$/.exec(a) as RegExpExecArray;
      return { col: match[1].charCodeAt(0) - 65, row: Number(match[2]) - 1 };
    };
    const a = parse(start);
    const b = parse(end);
    const keys: string[][] = [];
    for (let r = a.row; r <= b.row; r++) {
      const row: string[] = [];
      for (let c = a.col; c <= b.col; c++) row.push(`${sheet}!${String.fromCharCode(65 + c)}${r + 1}`);
      keys.push(row);
    }
    const first = keys[0][0];
    return {
      load: () => undefined,
      address: key,
      rowCount: keys.length,
      columnCount: keys[0].length,
      rowIndex: a.row,
      columnIndex: a.col,
      get values() {
        return keys.map((row) => row.map((k) => fake.cell(k).value));
      },
      set values(_: unknown) {
        fake.writes.push(`values ${key}`);
      },
      get formulas() {
        return keys.map((row) => row.map((k) => fake.cell(k).formula ?? fake.cell(k).value));
      },
      get text() {
        return keys.map((row) => row.map((k) => fake.cell(k).text ?? String(fake.cell(k).value)));
      },
      format: {
        fill: {
          load: () => undefined,
          get color() {
            return fake.cell(first).fill ?? "#FFFFFF";
          },
          set color(value: string) {
            fake.cell(first).fill = value;
            fake.writes.push(`fill ${first} ${value}`);
          },
          clear: () => {
            fake.cell(first).fill = null;
            fake.writes.push(`clear ${first}`);
          },
        },
      },
      select: () => fake.writes.push(`select ${key}`),
      getDirectPrecedents: () => {
        const addresses = fake.precedents[key];
        if (!addresses) fake.pendingError = Object.assign(new Error("No precedents"), { code: "ItemNotFound" });
        return { load: () => undefined, addresses: addresses ?? [] };
      },
      getResizedRange: (rowDelta: number, columnDelta: number) => {
        const endColumn = b.col + columnDelta;
        const endRow = b.row + rowDelta;
        const columnLabel = (index: number) => {
          let label = "";
          for (let value = index + 1; value > 0; value = Math.floor((value - 1) / 26)) label = String.fromCharCode(65 + ((value - 1) % 26)) + label;
          return label;
        };
        return fake.range(`${sheet}!${columnLabel(a.col)}${a.row + 1}:${columnLabel(endColumn)}${endRow + 1}`);
      },
    };
  }
}
