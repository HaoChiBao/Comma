// Review highlights. WorldState only changes fill color, records what it replaced, and can put it back.
// It never writes values or formulas.

import { splitAddress } from "./selection";
import type { Status } from "../types";

export const REVIEW_FILL = "#FFF3CD";
export const UNKNOWN_FILL = "#E2E3E5";
export const LEDGER_SETTING = "worldstate.highlights";

/** `previousColor: null` means the cell had no fill. */
export type LedgerEntry = { key: string; previousColor: string | null };

export interface LedgerStore {
  load(): LedgerEntry[];
  save(entries: LedgerEntry[]): Promise<void>;
}

/** Stores the ledger in the workbook's add-in settings so Clear still works after the pane reloads. */
export function settingsStore(settings: Office.Settings = Office.context.document.settings): LedgerStore {
  return {
    load: () => {
      const value = settings.get(LEDGER_SETTING);
      return Array.isArray(value) ? (value as LedgerEntry[]) : [];
    },
    save: (entries) =>
      new Promise((resolve, reject) => {
        settings.set(LEDGER_SETTING, entries);
        settings.saveAsync((result) => (result.status === Office.AsyncResultStatus.Failed ? reject(result.error) : resolve()));
      }),
  };
}

export function memoryStore(initial: LedgerEntry[] = []): LedgerStore & { entries: LedgerEntry[] } {
  const store = {
    entries: [...initial],
    load: () => [...store.entries],
    save: async (entries: LedgerEntry[]) => {
      store.entries = [...entries];
    },
  };
  return store;
}

export function fillFor(status: Status): string | null {
  if (status === "review") return REVIEW_FILL;
  if (status === "unknown") return UNKNOWN_FILL;
  return null;
}

export async function markCells(context: Excel.RequestContext, marks: { key: string; status: Status }[], store: LedgerStore): Promise<number> {
  const targets = marks
    .map((mark) => ({ ...mark, color: fillFor(mark.status) }))
    .filter((mark): mark is { key: string; status: Status; color: string } => mark.color !== null);
  if (!targets.length) return 0;
  const ledger = store.load();
  const recorded = new Set(ledger.map((entry) => entry.key));
  const ranges = targets.map((mark) => {
    const { sheet, address } = splitAddress(mark.key);
    const range = context.workbook.worksheets.getItem(sheet).getRange(address);
    range.format.fill.load("color");
    return { mark, range };
  });
  await context.sync();
  for (const { mark, range } of ranges) {
    if (!recorded.has(mark.key)) {
      // Excel reports no fill as white; restoring a white fill as "no fill" looks the same.
      const color = range.format.fill.color;
      ledger.push({ key: mark.key, previousColor: !color || color.toUpperCase() === "#FFFFFF" ? null : color });
      recorded.add(mark.key);
    }
    range.format.fill.color = mark.color;
  }
  await context.sync();
  await store.save(ledger);
  return targets.length;
}

export async function clearHighlights(context: Excel.RequestContext, store: LedgerStore): Promise<number> {
  const ledger = store.load();
  for (const entry of ledger) {
    const { sheet, address } = splitAddress(entry.key);
    const fill = context.workbook.worksheets.getItem(sheet).getRange(address).format.fill;
    if (entry.previousColor) fill.color = entry.previousColor;
    else fill.clear();
  }
  await context.sync();
  await store.save([]);
  return ledger.length;
}
