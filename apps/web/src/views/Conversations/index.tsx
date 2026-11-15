import { WebpImage } from '../../components/atoms/WebpImage';
import './participants.scss';
import { useEffect, useMemo, useState, type MouseEvent } from 'react';
import { useSuspenseQuery } from '@tanstack/react-query';
import { Pencil, Trash2 } from 'lucide-react';
import { conversationApi } from '../../api/resources';
import { queryKeys } from '../../hooks/useBootstrapResources';
import { genreModeLabel } from '../../utils/genre';
import { avatarUrlFor, conversationThumbnailUrlFor, lorelyAppIconUrlForTheme } from '../../utils/assets';
import { useUiStore } from '../../stores/uiStore';
import { LIST_PAGE_SIZE } from '../../constants/domain';
import { Button, Input } from '../../components/atoms';
import { ResourceWorkspace } from '../../components/molecules/ResourceWorkspace';
import { Pager, ResourceCard, ResourceCardAction, ResourceListPendingBar } from '../../components/molecules';
import { useI18n } from '../../i18n/I18nProvider';
import { formatNamedAction, formatParticipantCount, formatProfileImageAlt, formatUiCount, formatUnreadCount, type Translator, type UiMessageKey } from '../../i18n/core';

export type ConversationCharacterItem = {
  id: string;
  name?: string | null;
  avatar_url?: string | null;
};

export type ConversationWorldSettingItem = {
  id: string;
  title?: string | null;
};

export type ConversationListItem = {
  id: string;
  title?: string;
  thumbnail_url?: string | null;
  mode?: string;
  genre_mode?: unknown;
  world_setting_id?: string | null;
  auto_mode?: boolean;
  participants?: Array<{ type?: string; id?: string }>;
  has_unread?: boolean;
  unread_count?: number;
};

export type ConversationsListViewProps = {
  conversations?: ConversationListItem[];
  busy?: boolean;
  navigate: (path: string) => void;
  openConversation: (id: string) => void;
  deleteConversation: (id: string, event: MouseEvent<HTMLButtonElement>) => void;
  loadConversations?: () => void;
  allCharacters?: ConversationCharacterItem[];
  worldSettings?: ConversationWorldSettingItem[];
};

function participantCharactersFor(participants: ConversationListItem['participants'] = [], characterById: Map<string, ConversationCharacterItem>) {
  return participants
    .filter((item) => item.type === 'character' && item.id)
    .map((item) => characterById.get(item.id || '') || { id: item.id || '', name: '' });
}

function ParticipantProfiles({ participants = [], characterById }: { participants?: Array<{ type?: string; id?: string }>; characterById: Map<string, ConversationCharacterItem> }) {
  const theme = useUiStore((state) => state.theme);
  const { locale, t } = useI18n();
  const participantCharacters = participantCharactersFor(participants, characterById);
  if (!participantCharacters.length) return null;
  const visible = participantCharacters.slice(0, 2);
  const extraCount = Math.max(0, participantCharacters.length - visible.length);
  return <span className="conversation-participant-profiles" aria-label={formatParticipantCount(participantCharacters.length, locale)}>
    {visible.map((character) => <WebpImage key={character.id} className="conversation-participant-avatar" src={avatarUrlFor(character, theme)} alt={formatProfileImageAlt(character.name || t('캐릭터'), locale)} title={character.name || t('캐릭터')} />)}
    {extraCount > 0 && <span className="conversation-participant-extra">+{extraCount}</span>}
  </span>;
}

function ConversationThumbnail({ conversation }: { conversation: ConversationListItem }) {
  const theme = useUiStore((state) => state.theme);
  const { t } = useI18n();
  const title = conversation.title?.trim() || t('이름 없는 대화방');
  const thumbnailUrl = conversation.thumbnail_url?.trim() ? conversationThumbnailUrlFor(conversation, theme) : '';
  return <div className="relative h-full w-full overflow-hidden bg-surface-muted">
    {thumbnailUrl
      ? <WebpImage className="h-full w-full object-cover" src={thumbnailUrl} alt={t('세계관 썸네일')} />
      : <WebpImage className="h-full w-full object-contain object-center bg-surface-muted p-7" src={lorelyAppIconUrlForTheme(theme)} alt={`${title} · ${t('기본 썸네일')}`} />}
  </div>;
}

function conversationSearchText(conversation: ConversationListItem, characterById: Map<string, ConversationCharacterItem>, worldById: Map<string, ConversationWorldSettingItem>, t: Translator) {
  const participantNames = participantCharactersFor(conversation.participants, characterById).map((character) => character.name || t('캐릭터')).join(' ');
  const worldTitle = conversation.world_setting_id ? worldById.get(conversation.world_setting_id)?.title : '';
  return [
    conversation.title,
    worldTitle,
    String(conversation.genre_mode || ''),
    t(genreModeLabel(conversation.genre_mode) as UiMessageKey),
    participantNames,
  ].filter(Boolean).join(' ').toLowerCase();
}

export function ConversationsListView({ conversations = [], busy = false, navigate, openConversation, deleteConversation, allCharacters = [], worldSettings = [] }: ConversationsListViewProps) {
  const { locale, t } = useI18n();
  const conversationQuery = useSuspenseQuery({ queryKey: queryKeys.conversations, queryFn: conversationApi.list });
  const loadedConversations = conversations.length ? conversations : conversationQuery.data as ConversationListItem[];
  const characterById = useMemo(() => new Map(allCharacters.map((character) => [character.id, character])), [allCharacters]);
  const worldById = useMemo(() => new Map(worldSettings.map((world) => [world.id, world])), [worldSettings]);
  const [query, setQuery] = useState('');
  const [page, setPage] = useState(1);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  useEffect(() => setPage(1), [query, loadedConversations.length]);

  const filteredConversations = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase();
    if (!normalizedQuery) return loadedConversations;
    return loadedConversations.filter((conversation) => conversationSearchText(conversation, characterById, worldById, t).includes(normalizedQuery));
  }, [characterById, loadedConversations, query, t, worldById]);

  const total = filteredConversations.length;
  const maxPage = Math.max(1, Math.ceil(total / LIST_PAGE_SIZE));
  const safePage = Math.min(page, maxPage);
  const visibleConversations = filteredConversations.slice((safePage - 1) * LIST_PAGE_SIZE, safePage * LIST_PAGE_SIZE);
  const selected = visibleConversations.find(room => room.id === selectedId);

  return <section className="panel page grid gap-3" aria-busy={conversationQuery.isFetching} data-page="conversations-list" data-modernized="uniform resource cards and corner actions suspense query">
    <h1 className="sr-only">{t('대화방 목록')}</h1>
    <Input aria-label={t('대화방 검색')} value={query} onChange={(event) => setQuery(event.target.value)} placeholder={t('대화방명, 장르, 참여 캐릭터 검색')} />
    <ResourceListPendingBar pending={conversationQuery.isFetching} />
    <ResourceWorkspace selectionKey={selected?.id} onClear={() => setSelectedId(null)} selection={selected && <>
      <h2>{selected.title || t('이름 없는 대화방')}</h2>
      <p>{selected.world_setting_id ? worldById.get(selected.world_setting_id)?.title : t('연결된 세계관 없음')}</p>
      <ul className="resource-preview-cast">{participantCharactersFor(selected.participants, characterById).map(character => <li key={character.id}>{character.name || t('캐릭터')}</li>)}</ul>
      <Button type="button" onClick={() => openConversation(selected.id)}>{t('대화 시작')}</Button>
      <Button type="button" variant="ghost" onClick={() => navigate(`/conversations/${selected.id}/edit`)}>{t('편집')}</Button>
    </>}>
    <div className="grid items-stretch gap-2 sm:grid-cols-2 xl:grid-cols-3">
      {visibleConversations.map((c) => {
        const participantCount = participantCharactersFor(c.participants, characterById).length;
        const worldTitle = c.world_setting_id ? worldById.get(c.world_setting_id)?.title : '';
        return <ResourceCard
          key={c.id}
          variant="media"
          title={<span className="flex min-w-0 items-center gap-2"><span className="truncate">{c.title || t('이름 없는 대화방')}</span>{!!c.has_unread && <b className="inline-flex h-5 w-5 flex-none items-center justify-center rounded-full bg-danger text-[11px] font-bold leading-none text-white" aria-label={formatUnreadCount(c.unread_count || 1, locale)}>{c.unread_count && c.unread_count > 9 ? '9+' : c.unread_count || ''}</b>}</span>}
          description={worldTitle ? `${t('세계관')} · ${worldTitle}` : t('연결된 세계관 없음')}
          descriptionLines={1}
          meta={<span className="conversation-card-metadata"><span className="conversation-card-summary"><span className="min-w-0 truncate">{t(genreModeLabel(c.genre_mode) as UiMessageKey)}</span><span className="shrink-0">{formatUiCount(participantCount, 'people', locale)}</span></span><ParticipantProfiles participants={c.participants} characterById={characterById} /></span>}
          media={<ConversationThumbnail conversation={c} />}
          actions={<><ResourceCardAction label={formatNamedAction(c.title || t('대화방'), 'edit', locale)} disabled={busy} onClick={() => navigate(`/conversations/${c.id}/edit`)}><Pencil aria-hidden="true" className="size-4" /></ResourceCardAction><ResourceCardAction label={formatNamedAction(c.title || t('대화방'), 'delete', locale)} variant="destructive" disabled={busy} onClick={(event) => deleteConversation(c.id, event)}><Trash2 aria-hidden="true" className="size-4" /></ResourceCardAction></>}
          openLabel={formatNamedAction(c.title || t('대화방'), 'open', locale)}
          onOpen={() => openConversation(c.id)}
          onPreview={() => setSelectedId(c.id)} previewLabel={t('미리보기')} selected={selected?.id === c.id}
        />;
      })}
      {!visibleConversations.length && <div className="rounded-xl border border-dashed border-border py-10 text-center text-sm text-muted-foreground">{t('검색 결과가 없습니다.')}</div>}
    </div>
    </ResourceWorkspace>
    <Pager page={safePage} total={total} onPage={setPage} />
  </section>;
}
