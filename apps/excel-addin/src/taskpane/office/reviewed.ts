export const REVIEWED_SETTING = "comma.reviewed.v1";

export interface ReviewedStore {
  load(): string[];
  save(keys: string[]): Promise<void>;
}

export function reviewKey(cell: string, asOf: string, actorId: string): string {
  return `${cell}|${asOf}|${actorId}`;
}

/** Stores review decisions in the workbook's local add-in settings, never in the API. */
export function reviewStateStore(settings: Office.Settings = Office.context.document.settings): ReviewedStore {
  return {
    load: () => {
      const value = settings.get(REVIEWED_SETTING);
      return Array.isArray(value) ? value.filter((key): key is string => typeof key === "string") : [];
    },
    save: (keys) =>
      new Promise((resolve, reject) => {
        settings.set(REVIEWED_SETTING, [...new Set(keys)]);
        settings.saveAsync((result) => (result.status === Office.AsyncResultStatus.Failed ? reject(result.error) : resolve()));
      }),
  };
}
