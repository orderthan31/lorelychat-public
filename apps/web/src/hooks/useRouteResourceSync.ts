import { useEffect, type Dispatch, type SetStateAction } from 'react';
import { characterApi, settingsApi } from '../api/resources';
import { normalizeChatCommandDraft, type ChatCommandItem } from '../types/chatCommands';
import type { LegacyRoute } from '../router/legacyRouter';
import {
  DEFAULT_FORBIDDEN_RULES,
  DEFAULT_TRAIT_SCORES,
  EMPTY_CHARACTER,
  EMPTY_PRESET,
} from '../constants/domain';
import { useI18n } from '../i18n/I18nProvider';

type Setter<T> = Dispatch<SetStateAction<T>>;
export type Entity = { id?: string; trait_scores?: Record<string, unknown>; [key: string]: unknown };

export type RouteResourceSyncOptions = {
  route: LegacyRoute;
  characters: Entity[];
  presets: Entity[];
  chatCommands?: Entity[];
  setDraft: Setter<Entity>;
  setCharacterAssets: Setter<unknown[]>;
  loadCharacterAssets: (id: string) => Promise<unknown> | unknown;
  setPresetDraft: Setter<Entity>;
  setChatCommandDraft?: Setter<Entity>;
  fetchConversation: (id: string) => Promise<unknown>;
  loadConversationEditDraft: (id: string) => Promise<unknown>;
  setStatus: Setter<string>;
};

function normalizeCharacterDraft(character: Entity): Entity {
  return {
    ...EMPTY_CHARACTER,
    ...character,
    trait_scores: { ...DEFAULT_TRAIT_SCORES, ...(character.trait_scores || {}) },
  };
}

export function useRouteResourceSync({
  route,
  characters,
  presets,
  chatCommands = [],
  setDraft,
  setCharacterAssets,
  loadCharacterAssets,
  setPresetDraft,
  setChatCommandDraft,
  fetchConversation,
  loadConversationEditDraft,
  setStatus,
}: RouteResourceSyncOptions): void {
  const { t } = useI18n();
  const routeId = 'id' in route ? route.id : undefined;

  useEffect(() => {
    // Each effect visit owns its command request, even when revisiting the same ID.
    let commandRequestActive = true;
    if (route.page === 'characterNew') {
      setDraft({ ...EMPTY_CHARACTER, forbidden_rules: [...DEFAULT_FORBIDDEN_RULES], trait_scores: { ...DEFAULT_TRAIT_SCORES } });
      setCharacterAssets([]);
    }

    if ((route.page === 'characterDetail' || route.page === 'characterAssets') && routeId) {
      const local = characters.find((c) => c.id === routeId);
      if (local) setDraft(normalizeCharacterDraft(local));
      else characterApi.get(routeId).then((character) => setDraft(normalizeCharacterDraft((character || {}) as Entity))).catch((e) => setStatus(e.message));
      loadCharacterAssets(routeId);
    }

    if (route.page === 'presetNew') setPresetDraft({ ...EMPTY_PRESET });
    if (route.page === 'presetDetail' && routeId) {
      const local = presets.find((preset) => preset.id === routeId);
      if (local) setPresetDraft(local);
      else settingsApi.preset(routeId).then((preset) => setPresetDraft((preset || {}) as Entity)).catch((e) => setStatus(e.message));
    }


    if (route.page === 'chatCommandNew') {
      setChatCommandDraft?.(normalizeChatCommandDraft());
    }
    if (route.page === 'chatCommandDetail' && routeId) {
      const local = chatCommands.find((command) => command.id === routeId);
      if (local) setChatCommandDraft?.(normalizeChatCommandDraft(local as ChatCommandItem));
      else settingsApi.chatCommands().then((commands) => {
        if (!commandRequestActive) return;
        const found = ((commands || []) as Entity[]).find((command) => command.id === routeId);
        if (found) setChatCommandDraft?.(normalizeChatCommandDraft(found as ChatCommandItem));
        else setStatus(t('커맨드를 찾을 수 없습니다.'));
      }).catch((e) => {
        if (commandRequestActive) setStatus(e.message);
      });
    }

    if (route.page === 'conversationDetail' && routeId) fetchConversation(routeId).catch((e) => setStatus(e.message));
    if (route.page === 'conversationEdit' && routeId) loadConversationEditDraft(routeId).catch((e) => setStatus(e.message));
    return () => { commandRequestActive = false; };
  }, [route.page, routeId, t]);
}
