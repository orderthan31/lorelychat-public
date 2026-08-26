import { Children, cloneElement, isValidElement, useCallback, useEffect, useRef, type ReactNode } from 'react';
import { useSuspenseQueries } from '@tanstack/react-query';
import { characterApi, conversationApi, settingsApi } from '../../api/resources';
import { queryKeys } from '../../hooks/useBootstrapResources';
import { avatarUrlFor, conversationThumbnailUrlFor, lorelyAppIconUrlForTheme } from '../../utils/assets';
import { useUiStore } from '../../stores/uiStore';
import { Button } from '../../components/atoms';
import { useI18n } from '../../i18n/I18nProvider';
import { formatCharacterActivity, formatParticipantCount, formatProfileImageAlt, formatRoomCount, formatUnreadCount, type UiMessageKey } from '../../i18n/core';
import { genreModeLabel } from '../../utils/genre';

export type HomeCharacterItem = {
  id: string;
  name?: string;
  description?: string;
  avatar_url?: string | null;
  message_count?: number;
  room_count?: number;
  usage_count?: number;
};

export type HomeConversationItem = {
  id: string;
  title?: string | null;
  thumbnail_url?: string | null;
  genre_mode?: unknown;
  world_setting_id?: string | null;
  participants?: Array<{ type?: string; id?: string }>;
  has_unread?: boolean;
  unread_count?: number;
};

export type HomeWorldSettingItem = {
  id: string;
  title?: string | null;
  thumbnail_url?: string | null;
  genre_mode?: unknown;
  location?: string | null;
  mood?: string | null;
  description?: string | null;
};

export type HomeViewProps = {
  conversations?: HomeConversationItem[];
  characters?: HomeCharacterItem[];
  allCharacters?: HomeCharacterItem[];
  worldSettings?: HomeWorldSettingItem[];
  navigate: (path: string) => void;
  openConversation: (id: string) => void;
};

const LOOP_COPIES = 3;

function Rail({ title, children, action, auto = false }: { title: string; children: ReactNode; action?: ReactNode; auto?: boolean }) {
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const childItems = Children.toArray(children);
  const shouldLoop = auto && childItems.length > 1;
  const loopedItems = shouldLoop
    ? Array.from({ length: LOOP_COPIES }).flatMap((_, cycle) => childItems.map((child, index) => isValidElement(child)
      ? cloneElement(child, { key: `loop-${cycle}-${index}`, ...(cycle !== 1 ? { 'aria-hidden': true, tabIndex: -1 } : {}) })
      : child))
    : childItems;

  const normalizeLoopScroll = useCallback(() => {
    const node = scrollRef.current;
    if (!node || !shouldLoop) return;
    const segmentWidth = node.scrollWidth / LOOP_COPIES;
    if (!segmentWidth) return;
    if (node.scrollLeft < segmentWidth * 0.45) node.scrollLeft += segmentWidth;
    else if (node.scrollLeft > segmentWidth * 1.55) node.scrollLeft -= segmentWidth;
  }, [shouldLoop]);

  useEffect(() => {
    const node = scrollRef.current;
    if (!node || !shouldLoop) return;
    const frame = requestAnimationFrame(() => {
      node.scrollLeft = node.scrollWidth / LOOP_COPIES;
    });
    return () => cancelAnimationFrame(frame);
  }, [shouldLoop, childItems.length]);

  return <section className="recommend-rail"><div className="recommend-rail-head"><strong>{title}</strong>{action}</div><div ref={scrollRef} onScroll={normalizeLoopScroll} className={`recommend-scroll touch-auto overscroll-x-contain ${shouldLoop ? 'is-looping' : ''}`}><div className="recommend-marquee-track pointer-events-auto">{loopedItems}</div></div></section>;
}

function WorldThumbnail({ world, title }: { world?: HomeWorldSettingItem | null; title: string }) {
  const theme = useUiStore((state) => state.theme);
  const { t } = useI18n();
  const thumbnailUrl = world?.thumbnail_url?.trim() ? conversationThumbnailUrlFor(world, theme) : '';
  if (thumbnailUrl) return <img src={thumbnailUrl} alt={t('세계관 썸네일')} />;
  return <span className="room-thumbnail-default" role="img" aria-label={`${title} · ${t('기본 썸네일')}`} data-default-thumbnail>
    <img src={lorelyAppIconUrlForTheme(theme)} alt="" aria-hidden="true" />
  </span>;
}

function ParticipantAvatarStack({ participants = [], characterById }: { participants?: Array<{ type?: string; id?: string }>; characterById: Map<string, HomeCharacterItem> }) {
  const theme = useUiStore((state) => state.theme);
  const { locale, t } = useI18n();
  const participantCharacters = participants
    .filter((item) => item.type === 'character' && item.id)
    .map((item) => characterById.get(item.id || '') || { id: item.id || '', name: '' });
  if (!participantCharacters.length) return null;
  const visible = participantCharacters.slice(0, 2);
  const extraCount = Math.max(0, participantCharacters.length - visible.length);
  return <span className="room-participant-stack" aria-label={formatParticipantCount(participantCharacters.length, locale)}>
    {visible.map((character) => <img key={character.id} src={avatarUrlFor(character, theme)} alt={formatProfileImageAlt(character.name || t('캐릭터'), locale)} />)}
    {extraCount > 0 && <span className="room-participant-more">+{extraCount}</span>}
  </span>;
}

function RoomThumbnail({ room, world, characterById }: { room: HomeConversationItem; world?: HomeWorldSettingItem | null; characterById: Map<string, HomeCharacterItem> }) {
  const { t } = useI18n();
  const title = world?.title?.trim() || room.title?.trim() || t('이름 없는 대화방');
  return <span className="room-thumbnail-wrap">
    <WorldThumbnail world={world} title={title} />
    <ParticipantAvatarStack participants={room.participants} characterById={characterById} />
  </span>;
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
  const loadedCharacters = characters.length ? characters : usageQuery.data as HomeCharacterItem[];
  const loadedAllCharacters = allCharacters.length ? allCharacters : characterQuery.data as HomeCharacterItem[];
  const loadedWorldSettings = worldSettings.length ? worldSettings : (worldQuery.data || []) as HomeWorldSettingItem[];
  const recentRooms = loadedConversations.slice(0, 10);
  const favoriteCharacters = loadedCharacters.slice(0, 12);
  const worldById = new Map(loadedWorldSettings.map((world) => [world.id, world]));
  const characterById = new Map(loadedAllCharacters.map((character) => [character.id, character]));
  const favoriteWorlds = loadedWorldSettings
    .map((world) => ({ world, useCount: loadedConversations.filter((room) => room.world_setting_id === world.id).length }))
    .filter((item) => item.useCount > 0)
    .sort((a, b) => b.useCount - a.useCount)
    .slice(0, 10);
  return <section className="page home-dashboard" data-modernized="추천 메인 design-system primitive marker">
    <Rail title={t('최근 대화방')} action={<Button type="button" variant="ghost" size="sm" onClick={() => navigate('/conversations')}>{t('전체 보기')}</Button>}>
      {recentRooms.length ? recentRooms.map((room) => <button type="button" key={room.id} className="recommend-card room-recommend-card" onClick={() => openConversation(room.id)}>
        <RoomThumbnail room={room} world={room.world_setting_id ? worldById.get(room.world_setting_id) : null} characterById={characterById} />
        <span><span className="inline-flex items-center gap-2"><strong>{room.title || t('이름 없는 대화방')}</strong>{!!room.has_unread && <b className="inline-flex h-5 w-5 flex-none items-center justify-center rounded-full bg-danger text-[11px] font-bold leading-none text-white shadow-sm shadow-danger/25" aria-label={formatUnreadCount(room.unread_count || 1, locale)}>{room.unread_count && room.unread_count > 9 ? '9+' : room.unread_count || ''}</b>}</span><small>{(room.world_setting_id && worldById.get(room.world_setting_id)?.title) || t(genreModeLabel(room.genre_mode) as UiMessageKey)}</small></span>
      </button>) : <div className="empty-recommend-card"><strong>{t('아직 대화방 없음')}</strong></div>}
    </Rail>

    <Rail title={t('자주 사용하는 캐릭터')} action={<Button type="button" variant="ghost" size="sm" onClick={() => navigate('/characters')}>{t('캐릭터 보기')}</Button>} auto>
      {favoriteCharacters.length ? favoriteCharacters.map((character) => <button type="button" key={character.id} className="recommend-card character-recommend-card touch-auto" onClick={() => navigate(`/characters/${character.id}`)}>
        <img src={avatarUrlFor(character, theme)} alt={formatProfileImageAlt(character.name || t('캐릭터'), locale)} />
        <span><strong>{character.name || t('이름 없는 캐릭터')}</strong>{character.message_count || character.description ? <small>{character.message_count ? formatCharacterActivity(character.message_count, character.room_count || 0, locale) : character.description}</small> : null}</span>
      </button>) : <div className="empty-recommend-card"><strong>{t('등록된 캐릭터 없음')}</strong></div>}
    </Rail>

    <Rail title={t('자주 사용하는 세계관')} action={<Button type="button" variant="ghost" size="sm" onClick={() => navigate('/world-settings')}>{t('세계관 보기')}</Button>} auto>
      {favoriteWorlds.length ? favoriteWorlds.map(({ world, useCount }) => <button type="button" key={world.id} className="recommend-card room-recommend-card touch-auto" onClick={() => navigate(`/world-settings/${world.id}`)}>
        <WorldThumbnail world={world} title={world.title || t('세계관')} />
        <span><strong>{world.title || t('이름 없는 세계관')}</strong><small>{formatRoomCount(useCount, locale)} · {world.location || world.mood || t(genreModeLabel(world.genre_mode) as UiMessageKey)}</small></span>
      </button>) : <div className="empty-recommend-card"><strong>{t('아직 연결된 세계관 없음')}</strong></div>}
    </Rail>

    <div className="home-quick-grid">
      <button type="button" className="home-quick-card" onClick={() => navigate('/conversations/new')}><strong>{t('새 방 생성')}</strong></button>
      <button type="button" className="home-quick-card" onClick={() => navigate('/characters')}><strong>{t('캐릭터 관리')}</strong></button>
      <button type="button" className="home-quick-card" onClick={() => navigate('/world-settings')}><strong>{t('세계관 관리')}</strong></button>
      <button type="button" className="home-quick-card" onClick={() => navigate('/chat-commands')}><strong>{t('커맨드')}</strong></button>
    </div>
  </section>;
}
