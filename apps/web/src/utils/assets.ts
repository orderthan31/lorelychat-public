import { API_BASE } from '../constants/domain';
import type { AppTheme } from '../stores/uiStore';

export const LORELY_SYMBOL_LIGHT_URL = '/brand/lorely-symbol-light.webp';
export const LORELY_SYMBOL_DARK_URL = '/brand/lorely-symbol-dark.webp';
export const LORELY_HORIZONTAL_LOGO_LIGHT_URL = '/brand/lorely-horizontal-light.webp';
export const LORELY_HORIZONTAL_LOGO_DARK_URL = '/brand/lorely-horizontal-dark.webp';
export const LORELY_APP_ICON_LIGHT_URL = '/brand/lorely-app-icon-light.webp';
export const LORELY_APP_ICON_DARK_URL = '/brand/lorely-app-icon-dark.webp';

export const LORELY_SYMBOL_URL = LORELY_SYMBOL_LIGHT_URL;
export const LORELY_HORIZONTAL_LOGO_URL = LORELY_HORIZONTAL_LOGO_LIGHT_URL;
export const LORELY_APP_ICON_URL = LORELY_APP_ICON_LIGHT_URL;

type BrandTheme = AppTheme | undefined;

export function lorelySymbolUrlForTheme(theme: BrandTheme): string {
  return theme === 'dark' ? LORELY_SYMBOL_DARK_URL : LORELY_SYMBOL_LIGHT_URL;
}

export function lorelyHorizontalLogoUrlForTheme(theme: BrandTheme): string {
  return theme === 'dark' ? LORELY_HORIZONTAL_LOGO_DARK_URL : LORELY_HORIZONTAL_LOGO_LIGHT_URL;
}

export function lorelyAppIconUrlForTheme(theme: BrandTheme): string {
  return theme === 'dark' ? LORELY_APP_ICON_DARK_URL : LORELY_APP_ICON_LIGHT_URL;
}

type CharacterAvatarSource = {
  id?: string | null;
  name?: string | null;
  avatar_url?: string | null;
} | null | undefined;

type ConversationThumbnailSource = {
  id?: string | null;
  title?: string | null;
  thumbnail_url?: string | null;
} | null | undefined;

export function assetUrlFor(url: unknown): string {
  const value = String(url || '').trim();
  if (!value) return '';
  if (value.startsWith('/uploads/')) return `${API_BASE}${value}`;
  try {
    const parsed = new URL(value);
    if (parsed.pathname.startsWith('/uploads/')) return `${API_BASE}${parsed.pathname}${parsed.search}${parsed.hash}`;
  } catch {
    // Non-URL values are preserved for backwards compatibility.
  }
  return value;
}

export type ImageVariant = 'thumbnail' | 'full';
const api = API_BASE.replace(/\/$/, '');

/** Authored URLs stay untouched in storage/export; only display URLs are derived. */
export function imageUrlFor(source: unknown, variant: ImageVariant = 'thumbnail'): string {
  const value = String(source || '').trim();
  if (!value) return '';
  if (value.startsWith('/images/cache/') || value.startsWith('/images/render.webp?')) return `${api}${value}`;
  if (isDerivedImageUrl(value)) return value;
  // Only known inline vectors bypass raster preparation. A remote .svg suffix
  // does not prove the response bytes are SVG rather than PNG/JPEG.
  if (/^data:image\/svg\+xml[;,]/i.test(value)) return value;
  let local = value.startsWith('/uploads/') ? value : value.startsWith(`${api}/uploads/`) ? value.slice(api.length) : '';
  if (!local) {
    try {
      const parsed = new URL(value);
      const appOrigin = typeof window === 'undefined' ? 'http://localhost' : window.location.origin;
      const hosts = new Set(['localhost', '127.0.0.1', '[::1]', new URL(appOrigin).hostname, new URL(api, appOrigin).hostname]);
      // Historical loopback URLs and current app/API hosts survive port/base
      // changes; public third-party /uploads paths remain remote sources.
      if (/^https?:$/.test(parsed.protocol) && !parsed.username && !parsed.password
          && hosts.has(parsed.hostname) && parsed.pathname.startsWith('/uploads/')) local = parsed.pathname;
    } catch { /* Relative/unsupported sources go through explicit preparation. */ }
  }
  if (local) {
    local = local.split('#')[0].split('?')[0];
    return `${api}/images/render.webp?${new URLSearchParams({ source: local, variant })}`;
  }
  return value;
}

export function isDerivedImageUrl(value: string): boolean {
  return value.startsWith(`${api}/images/render.webp?`) || value.startsWith(`${api}/images/cache/`);
}

export function avatarUrlFor(character: CharacterAvatarSource, theme?: BrandTheme, variant: ImageVariant = 'thumbnail'): string {
  const avatarUrl = character?.avatar_url?.trim();
  if (avatarUrl) return imageUrlFor(avatarUrl, variant);
  return lorelyAppIconUrlForTheme(theme);
}

export function conversationThumbnailUrlFor(conversation: ConversationThumbnailSource, theme?: BrandTheme, variant: ImageVariant = 'thumbnail'): string {
  const thumbnailUrl = conversation?.thumbnail_url?.trim();
  if (thumbnailUrl) return imageUrlFor(thumbnailUrl, variant);
  return lorelyAppIconUrlForTheme(theme);
}
