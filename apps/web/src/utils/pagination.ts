import { LIST_PAGE_SIZE } from '../constants/domain';

type SearchableRecord = Record<string, unknown> | null | undefined;

export function matchesQuery(item: SearchableRecord, query: unknown, fields: string[]): boolean {
  const needle = String(query || '').trim().toLowerCase();
  if (!needle) return true;
  return fields.some((field) => String(item?.[field] || '').toLowerCase().includes(needle));
}

export function pageCount(total: number): number {
  return Math.max(1, Math.ceil(total / LIST_PAGE_SIZE));
}

export function paginate<T>(items: T[], page: number): T[] {
  return items.slice((page - 1) * LIST_PAGE_SIZE, page * LIST_PAGE_SIZE);
}
