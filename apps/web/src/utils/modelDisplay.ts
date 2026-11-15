import type { RuntimeModelOption } from '../types/domain';

/** Runtime labels wrap the authored label with `${account.alias} · ` on the API.
 * Remove exactly that one wrapper, never provider-like words or model-ID paths.
 * Catalog ModelOption.label is already authored text and must not use this helper.
 */
export function runtimeModelDisplayName(option: RuntimeModelOption): string {
  const label = option.label;
  const prefix = option.provider_account_alias ? `${option.provider_account_alias} · ` : '';
  if (label && prefix && label.startsWith(prefix) && label.length > prefix.length) {
    return label.slice(prefix.length);
  }
  return label || option.model || option.key || '';
}
