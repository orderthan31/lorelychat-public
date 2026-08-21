import { createRootRoute, createRoute, createRouter, type Router } from '@tanstack/react-router';
import type { UiMessageKey } from '../i18n/core';

export const routeNames = {
  home: '/',
  characters: '/characters',
  characterNew: '/characters/new',
  characterDetail: '/characters/$characterId',
  characterAssets: '/characters/$characterId/assets',
  conversations: '/conversations',
  conversationNew: '/conversations/new',
  conversationDetail: '/conversations/$conversationId',
  conversationEdit: '/conversations/$conversationId/edit',
  presets: '/presets',
  presetNew: '/presets/new',
  presetDetail: '/presets/$presetId',
  chatCommands: '/chat-commands',
  chatCommandNew: '/chat-commands/new',
  chatCommandDetail: '/chat-commands/$commandId',
  modelSettings: '/model-settings',
  worldSettings: '/world-settings',
  worldSettingNew: '/world-settings/new',
  worldSettingDetail: '/world-settings/$worldSettingId',
  statistics: '/statistics',
  settings: '/settings',
} as const;

export type RouteName = keyof typeof routeNames;
export type RoutePage = RouteName;

export type LegacyRoute =
  | { page: 'home' }
  | { page: 'characters' }
  | { page: 'characterNew' }
  | { page: 'characterDetail'; id: string }
  | { page: 'characterAssets'; id: string }
  | { page: 'conversations' }
  | { page: 'conversationNew' }
  | { page: 'conversationDetail'; id: string }
  | { page: 'conversationEdit'; id: string }
  | { page: 'settings' }
  | { page: 'presets' }
  | { page: 'presetNew' }
  | { page: 'presetDetail'; id: string }
  | { page: 'chatCommands' }
  | { page: 'chatCommandNew' }
  | { page: 'chatCommandDetail'; id: string }
  | { page: 'modelSettings' }
  | { page: 'worldSettings' }
  | { page: 'worldSettingNew' }
  | { page: 'worldSettingDetail'; id: string }
  | { page: 'statistics' };

type DynamicParamName = 'characterId' | 'conversationId' | 'presetId' | 'commandId' | 'worldSettingId';

type RouteDescriptor = {
  name: RouteName;
  path: string;
  title: UiMessageKey;
  paramName?: DynamicParamName;
  toLegacyRoute: (id?: string) => LegacyRoute;
};

export const routeDescriptors: RouteDescriptor[] = [
  { name: 'home', path: routeNames.home, title: '홈', toLegacyRoute: () => ({ page: 'home' }) },
  { name: 'conversations', path: routeNames.conversations, title: '대화방 목록', toLegacyRoute: () => ({ page: 'conversations' }) },
  { name: 'conversationNew', path: routeNames.conversationNew, title: '새 대화방', toLegacyRoute: () => ({ page: 'conversationNew' }) },
  { name: 'conversationEdit', path: routeNames.conversationEdit, title: '대화방 수정', paramName: 'conversationId', toLegacyRoute: (id = '') => ({ page: 'conversationEdit', id }) },
  { name: 'conversationDetail', path: routeNames.conversationDetail, title: '대화방', paramName: 'conversationId', toLegacyRoute: (id = '') => ({ page: 'conversationDetail', id }) },
  { name: 'worldSettings', path: routeNames.worldSettings, title: '세계관', toLegacyRoute: () => ({ page: 'worldSettings' }) },
  { name: 'worldSettingNew', path: routeNames.worldSettingNew, title: '새 세계관', toLegacyRoute: () => ({ page: 'worldSettingNew' }) },
  { name: 'worldSettingDetail', path: routeNames.worldSettingDetail, title: '세계관 상세', paramName: 'worldSettingId', toLegacyRoute: (id = '') => ({ page: 'worldSettingDetail', id }) },
  { name: 'characters', path: routeNames.characters, title: '캐릭터 목록', toLegacyRoute: () => ({ page: 'characters' }) },
  { name: 'characterNew', path: routeNames.characterNew, title: '새 캐릭터', toLegacyRoute: () => ({ page: 'characterNew' }) },
  { name: 'characterAssets', path: routeNames.characterAssets, title: '에셋 갤러리', paramName: 'characterId', toLegacyRoute: (id = '') => ({ page: 'characterAssets', id }) },
  { name: 'characterDetail', path: routeNames.characterDetail, title: '캐릭터 상세', paramName: 'characterId', toLegacyRoute: (id = '') => ({ page: 'characterDetail', id }) },
  { name: 'settings', path: routeNames.settings, title: '설정', toLegacyRoute: () => ({ page: 'settings' }) },
  { name: 'presets', path: routeNames.presets, title: '프리셋', toLegacyRoute: () => ({ page: 'presets' }) },
  { name: 'presetNew', path: routeNames.presetNew, title: '프리셋', toLegacyRoute: () => ({ page: 'presetNew' }) },
  { name: 'presetDetail', path: routeNames.presetDetail, title: '프리셋', paramName: 'presetId', toLegacyRoute: (id = '') => ({ page: 'presetDetail', id }) },
  { name: 'chatCommands', path: routeNames.chatCommands, title: '커맨드', toLegacyRoute: () => ({ page: 'chatCommands' }) },
  { name: 'chatCommandNew', path: routeNames.chatCommandNew, title: '새 커맨드', toLegacyRoute: () => ({ page: 'chatCommandNew' }) },
  { name: 'chatCommandDetail', path: routeNames.chatCommandDetail, title: '커맨드 상세', paramName: 'commandId', toLegacyRoute: (id = '') => ({ page: 'chatCommandDetail', id }) },
  { name: 'modelSettings', path: routeNames.modelSettings, title: '모델 설정', toLegacyRoute: () => ({ page: 'modelSettings' }) },
  { name: 'statistics', path: routeNames.statistics, title: '통계', toLegacyRoute: () => ({ page: 'statistics' }) },
];

function descriptorPathPattern(path: string) {
  const expression = path
    .replace(/^\//, '')
    .split('/')
    .map((part) => part.startsWith('$') ? '([^/]+)' : part.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))
    .join('/');
  return new RegExp(`^${expression}$`);
}

export function parseRoute(pathname: string = window.location.pathname): LegacyRoute {
  const normalized = pathname.split('?')[0].replace(/^\//, '');
  if (!normalized) return { page: 'home' };
  for (const descriptor of routeDescriptors) {
    const match = normalized.match(descriptorPathPattern(descriptor.path));
    if (match) return descriptor.toLegacyRoute(match[1] ? decodeURIComponent(match[1]) : undefined);
  }
  return { page: 'home' };
}

export function pageTitle(route: LegacyRoute): UiMessageKey {
  const descriptor = routeDescriptors.find((item) => item.name === route.page);
  return descriptor?.title || '홈';
}

export function createAppRouter(): Router<any, any> {
  const rootRoute = createRootRoute();
  const routeEntries = routeDescriptors.map((descriptor) => createRoute({
    id: descriptor.name,
    path: descriptor.path === '/' ? '/' : descriptor.path.replace(/^\//, ''),
    getParentRoute: () => rootRoute,
  }));

  const makeRouter = createRouter as unknown as (options: { routeTree: unknown }) => Router<any, any>;
  return makeRouter({ routeTree: rootRoute.addChildren(routeEntries) });
}

export const appRouter = {
  routeDescriptors,
  navigate({ to }: { to?: string }) {
    window.history.pushState({}, '', toRouterPath(to));
    return Promise.resolve();
  },
};

export function toRouterPath(path?: string): string {
  return path || routeNames.home;
}
