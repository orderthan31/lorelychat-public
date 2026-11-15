import { WebpImage } from '../../components/atoms/WebpImage';
import type { ReactNode } from 'react';
import { ChevronRight, Plus } from 'lucide-react';
import { useSuspenseQueries } from '@tanstack/react-query';
import { characterApi, conversationApi, settingsApi } from '../../api/resources';
import { queryKeys } from '../../hooks/useBootstrapResources';
import { avatarUrlFor, conversationThumbnailUrlFor, lorelyAppIconUrlForTheme } from '../../utils/assets';
import { useUiStore } from '../../stores/uiStore';
import { Button } from '../../components/atoms';
import { useI18n } from '../../i18n/I18nProvider';
import { formatNamedAction, formatProfileImageAlt, formatUnreadCount } from '../../i18n/core';

export type HomeCharacterItem = {
  id: string; name?: string; description?: string; avatar_url?: string | null;
  message_count?: number; room_count?: number; usage_count?: number;
};
export type HomeConversationItem = {
  id: string; title?: string | null; thumbnail_url?: string | null; genre_mode?: unknown;
  world_setting_id?: string | null; participants?: Array<{ type?: string; id?: string }>;
  has_unread?: boolean; unread_count?: number;
};
export type HomeWorldSettingItem = {
  id: string; title?: string | null; thumbnail_url?: string | null; genre_mode?: unknown;
  location?: string | null; mood?: string | null; description?: string | null;
};
export type HomeViewProps = {
  conversations?: HomeConversationItem[]; characters?: HomeCharacterItem[]; allCharacters?: HomeCharacterItem[];
  worldSettings?: HomeWorldSettingItem[]; navigate: (path: string) => void; openConversation: (id: string) => void;
};

function HomeSection({ title, actions, children, className = '' }: { title: string; actions?: ReactNode; children: ReactNode; className?: string }) {
  return <section className={`home-section ${className}`}>
    <header className="home-section-head"><h2>{title}</h2><div className="home-section-actions">{actions}</div></header>
    {children}
  </section>;
}

export function HomeView({ conversations = [], characters = [], allCharacters = characters, worldSettings = [], navigate, openConversation }: HomeViewProps) {
  const theme = useUiStore((state) => state.theme);
  const { locale, t } = useI18n();
  const [conversationQuery, usageQuery, characterQuery, worldQuery] = useSuspenseQueries({ queries: [
    { queryKey: queryKeys.conversations, queryFn: conversationApi.list },
    { queryKey: queryKeys.characterUsageRanking, queryFn: characterApi.usageRanking },
    { queryKey: queryKeys.characters, queryFn: characterApi.list },
    { queryKey: queryKeys.worldSettings, queryFn: settingsApi.worldSettings },
  ] });
  const loadedConversations = conversations.length ? conversations : conversationQuery.data as HomeConversationItem[];
  const rankedCharacters = characters.length ? characters : usageQuery.data as HomeCharacterItem[];
  const loadedAllCharacters = allCharacters.length ? allCharacters : characterQuery.data as HomeCharacterItem[];
  const loadedWorldSettings = worldSettings.length ? worldSettings : (worldQuery.data || []) as HomeWorldSettingItem[];
  // Home is a bounded return-to-work view. Complete inventories remain on their list routes.
  const recentRooms = loadedConversations.slice(0, 6);
  const visibleCharacters = (rankedCharacters.length ? rankedCharacters : loadedAllCharacters).slice(0, 3);
  const worldById = new Map(loadedWorldSettings.map(world => [world.id, world]));
  const characterById = new Map(loadedAllCharacters.map(character => [character.id, character]));
  const visibleWorlds = loadedWorldSettings
    .map(world => ({ world, useCount: loadedConversations.filter(room => room.world_setting_id === world.id).length }))
    .sort((a, b) => b.useCount - a.useCount).slice(0, 3);
  const worldImage = (world?: HomeWorldSettingItem | null) => world?.thumbnail_url?.trim()
    ? conversationThumbnailUrlFor(world, theme) : lorelyAppIconUrlForTheme(theme);

  return <section className="page home-dashboard" data-page="home" data-modernized="추천 메인 design-system primitive marker">
    <HomeSection title={t('최근 대화방')} className="home-recent" actions={<Button type="button" variant="ghost" size="sm" onClick={() => navigate('/conversations')}>{t('전체 보기')}</Button>}>
      <div className="home-room-list">
        {recentRooms.length ? recentRooms.map(room => {
          const title = room.title || t('이름 없는 대화방');
          const world = room.world_setting_id ? worldById.get(room.world_setting_id) : undefined;
          const cast = (room.participants || []).filter(item => item.type === 'character' && item.id)
            .map(item => characterById.get(item.id!)?.name).filter(Boolean).join(' · ');
          return <button type="button" className="home-room-link" key={room.id} aria-label={`${title} · ${t('대화 이어가기')}`} onClick={() => openConversation(room.id)}>
            <span className="home-room-image"><WebpImage src={worldImage(world)} alt="" /></span>
            <span className="home-row-copy"><strong>{title}</strong>{world?.title && <small>{world.title}</small>}{cast && <span className="home-room-cast">{cast}</span>}</span>
            <span className="home-room-action">{!!room.has_unread && <span className="home-unread" aria-label={formatUnreadCount(room.unread_count || 1, locale)}>{room.unread_count && room.unread_count > 9 ? '9+' : room.unread_count || '●'}</span>}<span className="home-continue">{t('대화 이어가기')}</span><ChevronRight size={18} aria-hidden="true" /></span>
          </button>;
        }) : <div className="home-empty"><p>{t('아직 대화방 없음')}</p><p>{t('새 대화방을 만들어 대화를 시작하세요.')}</p></div>}
      </div>
    </HomeSection>

    <div className="home-resources">
      <HomeSection title={t('캐릭터 관리')} actions={<><Button type="button" variant="ghost" size="sm" onClick={() => navigate('/characters')}>{t('전체 보기')}</Button><Button type="button" variant="ghost" size="icon" aria-label={t('새 캐릭터')} title={t('새 캐릭터')} onClick={() => navigate('/characters/new')}><Plus size={18} aria-hidden="true" /></Button></>}>
        <div className="home-resource-list">{visibleCharacters.length ? visibleCharacters.map(character => <button type="button" className="home-resource-link" key={character.id} aria-label={formatNamedAction(character.name || t('캐릭터'), 'edit', locale)} onClick={() => navigate(`/characters/${character.id}`)}>
          <WebpImage src={avatarUrlFor(character, theme)} alt={formatProfileImageAlt(character.name || t('캐릭터'), locale)} />
          <span className="home-row-copy"><strong>{character.name || t('이름 없는 캐릭터')}</strong>{character.description && <small>{character.description}</small>}</span>
          <span className="home-edit-hint">{t('편집')}<ChevronRight size={14} aria-hidden="true" /></span>
        </button>) : <p className="home-empty">{t('등록된 캐릭터 없음')}</p>}</div>
      </HomeSection>
      <HomeSection title={t('세계관 관리')} actions={<><Button type="button" variant="ghost" size="sm" onClick={() => navigate('/world-settings')}>{t('전체 보기')}</Button><Button type="button" variant="ghost" size="icon" aria-label={t('새 세계관')} title={t('새 세계관')} onClick={() => navigate('/world-settings/new')}><Plus size={18} aria-hidden="true" /></Button></>}>
        <div className="home-resource-list">{visibleWorlds.length ? visibleWorlds.map(({world}) => <button type="button" className="home-resource-link" key={world.id} aria-label={formatNamedAction(world.title || t('세계관'), 'edit', locale)} onClick={() => navigate(`/world-settings/${world.id}`)}>
          <WebpImage src={worldImage(world)} alt="" />
          <span className="home-row-copy"><strong>{world.title || t('이름 없는 세계관')}</strong>{(world.description || world.location || world.mood) && <small>{world.description || world.location || world.mood}</small>}</span>
          <span className="home-edit-hint">{t('편집')}<ChevronRight size={14} aria-hidden="true" /></span>
        </button>) : <p className="home-empty">{t('아직 연결된 세계관 없음')}</p>}</div>
      </HomeSection>
      <nav className="home-tools" aria-label={t('설정')}><Button type="button" variant="ghost" onClick={() => navigate('/chat-commands')}>{t('커맨드')}</Button><Button type="button" variant="ghost" onClick={() => navigate('/presets')}>{t('프리셋')}</Button><Button type="button" variant="ghost" onClick={() => navigate('/model-settings')}>{t('모델 설정')}</Button></nav>
    </div>
  </section>;
}
