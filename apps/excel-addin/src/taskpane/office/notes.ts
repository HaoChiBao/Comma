// Native Excel cell notes (the hover popup) carrying WorldState findings. Notes need ExcelApi 1.18;
// older hosts keep the fill highlights and get one message instead. A note someone else wrote is never
// overwritten or deleted: WorldState only touches notes that start with its own marker.

import { splitAddress } from "./selection";
import type { Status } from "../types";
import { STATUS_VIEW, formatMoney } from "../view";

export const NOTES_API = { name: "ExcelApi", version: "1.18" };
export const NOTE_MARK = "⚠️ WorldState —";
export const NOTES_SETTING = "worldstate.notes";

export type NoteFinding = {
  key: string;
  status: Status;
  title: string;
  summary: string;
  difference: { amount: string; currency: string } | null;
};

export type NoteContext = { asOfLabel: string; actorLabel: string };

/** Cell keys (`Accruals!F2`) whose note WorldState wrote. */
export interface NoteStore {
  load(): string[];
  save(keys: string[]): Promise<void>;
}

export function noteSettingsStore(settings: Office.Settings = Office.context.document.settings): NoteStore {
  return {
    load: () => {
      const value = settings.get(NOTES_SETTING);
      return Array.isArray(value) ? (value as string[]) : [];
    },
    save: (keys) =>
      new Promise((resolve, reject) => {
        settings.set(NOTES_SETTING, keys);
        settings.saveAsync((result) => (result.status === Office.AsyncResultStatus.Failed ? reject(result.error) : resolve()));
      }),
  };
}

export function memoryNoteStore(initial: string[] = []): NoteStore & { keys: string[] } {
  const store = {
    keys: [...initial],
    load: () => [...store.keys],
    save: async (keys: string[]) => {
      store.keys = [...keys];
    },
  };
  return store;
}

export class NotesUnsupportedError extends Error {
  constructor() {
    super("This version of Excel cannot write cell notes from add-ins (needs ExcelApi 1.18). Highlights were still applied.");
  }
}

export function supportsNotes(isSetSupported: (name: string, version: string) => boolean = defaultIsSetSupported): boolean {
  return isSetSupported(NOTES_API.name, NOTES_API.version);
}

function defaultIsSetSupported(name: string, version: string): boolean {
  return typeof Office !== "undefined" && Office.context.requirements.isSetSupported(name, version);
}

export function noteText(finding: NoteFinding, context: NoteContext): string {
  const lines = [`${NOTE_MARK} ${STATUS_VIEW[finding.status].label}`, "", finding.title, finding.summary];
  if (finding.difference) lines.push(`Difference: ${formatMoney(finding.difference.amount, finding.difference.currency)}`);
  lines.push("", `As of ${context.asOfLabel} · viewed as ${context.actorLabel}`);
  return lines.join("\n");
}

export function isWorldStateNote(content: string | null | undefined): boolean {
  return typeof content === "string" && content.startsWith(NOTE_MARK);
}

export type NoteResult = { written: number; updated: number; skipped: string[] };

/** Adds or updates a note on each review or unknown cell. Supported cells get no note. */
export async function writeFindingNotes(
  context: Excel.RequestContext,
  findings: NoteFinding[],
  store: NoteStore,
  noteContext: NoteContext,
  supported = supportsNotes(),
): Promise<NoteResult> {
  const flagged = findings.filter((finding) => finding.status !== "supported");
  if (!flagged.length) return { written: 0, updated: 0, skipped: [] };
  if (!supported) throw new NotesUnsupportedError();
  const lookups = flagged.map((finding) => {
    const { sheet, address } = splitAddress(finding.key);
    const notes = context.workbook.worksheets.getItem(sheet).notes;
    const existing = notes.getItemOrNullObject(address);
    existing.load("content");
    return { finding, address, notes, existing };
  });
  await context.sync();
  const result: NoteResult = { written: 0, updated: 0, skipped: [] };
  const ledger = new Set(store.load());
  for (const { finding, address, notes, existing } of lookups) {
    const content = noteText(finding, noteContext);
    if (existing.isNullObject) {
      notes.add(address, content);
      result.written += 1;
    } else if (isWorldStateNote(existing.content)) {
      existing.content = content;
      result.updated += 1;
    } else {
      result.skipped.push(finding.key);
      continue;
    }
    ledger.add(finding.key);
  }
  await context.sync();
  await store.save([...ledger]);
  return result;
}

/** Deletes the notes WorldState wrote. A note that no longer carries the marker was edited by someone; it stays. */
export async function clearNotes(context: Excel.RequestContext, store: NoteStore, supported = supportsNotes()): Promise<number> {
  const keys = store.load();
  if (!keys.length || !supported) return 0;
  const lookups = keys.map((key) => {
    const { sheet, address } = splitAddress(key);
    const note = context.workbook.worksheets.getItem(sheet).notes.getItemOrNullObject(address);
    note.load("content");
    return note;
  });
  await context.sync();
  let removed = 0;
  for (const note of lookups) {
    if (!note.isNullObject && isWorldStateNote(note.content)) {
      note.delete();
      removed += 1;
    }
  }
  await context.sync();
  await store.save([]);
  return removed;
}
