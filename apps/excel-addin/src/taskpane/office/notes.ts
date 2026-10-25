// Native Excel cell notes (the hover popup) carrying WorldState findings. Notes need ExcelApi 1.18;
// older hosts keep the fill highlights and get one message instead. A note someone else wrote is never
// overwritten or deleted: WorldState only touches notes that start with its own marker.

import { splitAddress } from "./selection";
import type { Status } from "../types";
import { STATUS_VIEW, formatMoney } from "../view";

export const NOTES_API = { name: "ExcelApi", version: "1.18" };
export const NOTE_MARK = "⚠️ WorldState —";
export const NOTES_SETTING = "worldstate.notes";
export const NOTE_CONTENTS_SETTING = "worldstate.noteContents";

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
  loadContents(): Record<string, string>;
  saveContents(contents: Record<string, string>): Promise<void>;
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
    loadContents: () => {
      const value = settings.get(NOTE_CONTENTS_SETTING);
      return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, string>) : {};
    },
    saveContents: (contents) =>
      new Promise((resolve, reject) => {
        settings.set(NOTE_CONTENTS_SETTING, contents);
        settings.saveAsync((result) => (result.status === Office.AsyncResultStatus.Failed ? reject(result.error) : resolve()));
      }),
  };
}

export function memoryNoteStore(initial: string[] = [], initialContents: Record<string, string> = {}): NoteStore & { keys: string[]; contents: Record<string, string> } {
  const store = {
    keys: [...initial],
    contents: { ...initialContents },
    load: () => [...store.keys],
    save: async (keys: string[]) => {
      store.keys = [...keys];
    },
    loadContents: () => ({ ...store.contents }),
    saveContents: async (contents: Record<string, string>) => {
      store.contents = { ...contents };
    },
  };
  return store;
}

export class NotesUnsupportedError extends Error {
  constructor() {
    super("This version of Excel cannot write cell notes from add-ins (needs ExcelApi 1.18).");
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
export type CustomNoteResult = "written" | "updated" | "skipped";

/** Writes a user-authored message as a WorldState-owned native note, preserving other people's notes. */
export async function writeCustomNote(
  context: Excel.RequestContext,
  key: string,
  message: string,
  store: NoteStore,
  supported = supportsNotes(),
): Promise<CustomNoteResult> {
  const body = message.trim();
  if (!body) throw new Error("Write a message before adding the note.");
  if (!supported) throw new NotesUnsupportedError();

  const { sheet, address } = splitAddress(key);
  const notes = context.workbook.worksheets.getItem(sheet).notes;
  const existing = notes.getItemOrNullObject(address);
  existing.load("content");
  await context.sync();

  const content = body;
  let result: CustomNoteResult;
  if (existing.isNullObject) {
    notes.add(address, content);
    result = "written";
  } else if (isWorldStateNote(existing.content) || store.loadContents()[key] === existing.content) {
    existing.content = content;
    result = "updated";
  } else {
    return "skipped";
  }

  await context.sync();
  const ledger = new Set(store.load());
  ledger.add(key);
  const contents = store.loadContents();
  contents[key] = content;
  await store.save([...ledger]);
  await store.saveContents(contents);
  return result;
}

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
  const contents = store.loadContents();
  for (const { finding, address, notes, existing } of lookups) {
    const content = noteText(finding, noteContext);
    if (existing.isNullObject) {
      notes.add(address, content);
      result.written += 1;
    } else if (isWorldStateNote(existing.content) || contents[finding.key] === existing.content) {
      existing.content = content;
      result.updated += 1;
    } else {
      result.skipped.push(finding.key);
      continue;
    }
    ledger.add(finding.key);
    contents[finding.key] = content;
  }
  await context.sync();
  await store.save([...ledger]);
  await store.saveContents(contents);
  return result;
}

/** Deletes the notes WorldState wrote. A note that no longer carries the marker was edited by someone; it stays. */
export async function clearNotes(context: Excel.RequestContext, store: NoteStore, supported = supportsNotes()): Promise<number> {
  const keys = store.load();
  if (!keys.length || !supported) return 0;
  const contents = store.loadContents();
  const lookups = keys.map((key) => {
    const { sheet, address } = splitAddress(key);
    const note = context.workbook.worksheets.getItem(sheet).notes.getItemOrNullObject(address);
    note.load("content");
    return { key, note };
  });
  await context.sync();
  let removed = 0;
  for (const { key, note } of lookups) {
    if (!note.isNullObject && (isWorldStateNote(note.content) || contents[key] === note.content)) {
      note.delete();
      removed += 1;
    }
  }
  await context.sync();
  await store.save([]);
  await store.saveContents({});
  return removed;
}
