import { API_BASE } from '../constants/domain';
import type { AppTheme } from '../stores/uiStore';

export const LORELY_SYMBOL_LIGHT_URL = '/brand/lorely-symbol-light.png';
export const LORELY_SYMBOL_DARK_URL = '/brand/lorely-symbol-dark.png';
export const LORELY_HORIZONTAL_LOGO_LIGHT_URL = '/brand/lorely-horizontal-light.png';
export const LORELY_HORIZONTAL_LOGO_DARK_URL = '/brand/lorely-horizontal-dark.png';
export const LORELY_APP_ICON_LIGHT_URL = '/brand/lorely-app-icon-light.png';
export const LORELY_APP_ICON_DARK_URL = '/brand/lorely-app-icon-dark.png';

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

export function avatarUrlFor(character: CharacterAvatarSource, theme?: BrandTheme): string {
  const avatarUrl = character?.avatar_url?.trim();
  if (avatarUrl) return assetUrlFor(avatarUrl);
  return lorelyAppIconUrlForTheme(theme);
}

export function conversationThumbnailUrlFor(conversation: ConversationThumbnailSource, theme?: BrandTheme): string {
  const thumbnailUrl = conversation?.thumbnail_url?.trim();
  if (thumbnailUrl) return assetUrlFor(thumbnailUrl);
  return lorelyAppIconUrlForTheme(theme);
}
