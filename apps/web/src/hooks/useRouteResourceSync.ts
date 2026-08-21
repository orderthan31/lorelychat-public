import { useEffect, type Dispatch, type SetStateAction } from 'react';
import { characterApi, settingsApi } from '../api/resources';
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
      setChatCommandDraft?.({ name: '', display_name: '', description: '', prompt: '', generation_prompt: '', postprocess_prompt: '', postprocess_target: 'last_bubble', postprocess_probability: 100, enabled: true, priority: 0 });
    }
    if (route.page === 'chatCommandDetail' && routeId) {
      const local = chatCommands.find((command) => command.id === routeId);
      if (local) setChatCommandDraft?.({ name: '', display_name: '', description: '', prompt: '', generation_prompt: '', postprocess_prompt: '', postprocess_target: 'last_bubble', postprocess_probability: 100, enabled: true, priority: 0, ...local });
      else settingsApi.chatCommands().then((commands) => {
        const found = ((commands || []) as Entity[]).find((command) => command.id === routeId);
        if (found) setChatCommandDraft?.({ name: '', display_name: '', description: '', prompt: '', generation_prompt: '', postprocess_prompt: '', postprocess_target: 'last_bubble', postprocess_probability: 100, enabled: true, priority: 0, ...found });
        else setStatus(t('커맨드를 찾을 수 없습니다.'));
      }).catch((e) => setStatus(e.message));
    }

    if (route.page === 'conversationDetail' && routeId) fetchConversation(routeId).catch((e) => setStatus(e.message));
    if (route.page === 'conversationEdit' && routeId) loadConversationEditDraft(routeId).catch((e) => setStatus(e.message));
  }, [route.page, routeId, t]);
}
