/** Asset request schema parity: split commas/newlines, trim, preserve case,
 * internal whitespace, ordering and duplicates. Not character/world tag policy. */
// Python str.strip() whitespace used by schemas/assets.py (unlike JS trim,
// includes NEL/control separators and does not strip authored BOM characters).
const ASSET_TAG_EDGES = /^[\u0009-\u000D\u001C-\u0020\u0085\u00A0\u1680\u2000-\u200A\u2028\u2029\u202F\u205F\u3000]+|[\u0009-\u000D\u001C-\u0020\u0085\u00A0\u1680\u2000-\u200A\u2028\u2029\u202F\u205F\u3000]+$/g;
export function parseAssetTags(value: string | null | undefined): string[] {
  return (value || '').split(/[\n,]/).map(tag => tag.replace(ASSET_TAG_EDGES, '')).filter(Boolean);
}
