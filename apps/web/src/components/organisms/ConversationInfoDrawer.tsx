import { useEffect, useState, type FormEvent, type ReactNode } from 'react';
import { avatarUrlFor } from '../../utils/assets';
import { useUiStore } from '../../stores/uiStore';
import { Button } from '../atoms';
import { Badge } from '../atoms';
import { Card } from '../atoms';
import { Input } from '../atoms';
import { Select } from '../atoms';
import { Textarea } from '../atoms';
import { CAST_ROLE_PRESETS, RELATIONSHIP_ARCHETYPE_PRESETS } from '../../constants/domain';
import { useI18n } from '../../i18n/I18nProvider';
import { formatBattleResult, formatBattleStanding, formatStoryArcLimit, type UiMessageKey } from '../../i18n/core';

type Character = { id?: string; name?: string; avatar_url?: string; [key: string]: unknown };
type Participant = { id: string; type: string; role?: string | null };
type Conversation = { id?: string; title?: string; genre_mode?: string };
type BattleMatch = Record<string, unknown> & { match_order?: number | null; matchup_key?: string; participant_a_name?: string; participant_b_name?: string; winner_name?: string; loser_name?: string; result_status?: string; process_summary?: string; decisive_moment?: string };
type BattleStanding = Record<string, unknown> & { character_id?: string; name?: string; wins?: number; losses?: number; points?: number; rank?: number | null };
export type BattleState = { participants?: Character[]; active_match?: BattleMatch | null; recent_matches?: BattleMatch[]; standings?: BattleStanding[] };
type SceneContext = Record<string, unknown> & { summary?: string; relationship_archetype?: string; compression_revision?: number };
type MemoryItem = Record<string, unknown> & { id?: string; character_id?: string; memory_type?: string; importance?: number; content?: string };
type ConversationContext = { scene?: SceneContext; memories?: MemoryItem[] };
type UserMemoryDraft = { content?: string; character_id?: string; importance?: number; [key: string]: unknown };
type DrawerTab = 'manage' | 'state' | 'battle' | 'memory';

export type ConversationInfoDrawerProps = {
  open: boolean;
  onClose?: () => void;
  context?: ConversationContext | null;
  loading?: boolean;
  conversation?: Conversation | null;
  participants: Participant[];
  characters: Character[];
  participantName: (id?: string) => string;
  onRenameConversation?: (title: string) => Promise<void> | void;
  onInviteCharacter?: (characterId: string) => Promise<void> | void;
  onRemoveCharacter?: (characterId: string) => Promise<void> | void;
  onUpdateCharacterRole?: (characterId: string, role: string) => Promise<void> | void;
  battleState?: BattleState | null;
  onUpdateSceneSummary?: (summary: string, expectedRevision: number) => Promise<SceneContext | null> | SceneContext | null;
  userMemoryDraft?: UserMemoryDraft;
  onUserMemoryDraftChange?: (draft: UserMemoryDraft) => void;
  onCreateUserMemory?: (draft: UserMemoryDraft) => Promise<void> | void;
  onUpdateMemory?: (memory: MemoryItem, draft: UserMemoryDraft) => Promise<void> | void;
  onDeleteMemory?: (memory: MemoryItem) => Promise<void> | void;
  busy?: boolean;
};

export function ConversationInfoDrawer({ open, onClose, context, loading, conversation, participants, characters, participantName, onRenameConversation, onInviteCharacter, onRemoveCharacter, onUpdateCharacterRole, battleState, onUpdateSceneSummary, userMemoryDraft, onUserMemoryDraftChange, onCreateUserMemory, onUpdateMemory, onDeleteMemory, busy = false }: ConversationInfoDrawerProps) {
  const { locale, t } = useI18n();
  const theme = useUiStore((state) => state.theme);
  const [titleDraft, setTitleDraft] = useState('');
  const [inviteCharacterId, setInviteCharacterId] = useState('');
  const [activeDrawerTab, setActiveDrawerTab] = useState<DrawerTab>('manage');
  const [editingMemoryId, setEditingMemoryId] = useState('');
  const [memoryEditDraft, setMemoryEditDraft] = useState<UserMemoryDraft>({});
  const [editingSceneSummary, setEditingSceneSummary] = useState(false);
  const [sceneSummaryDraft, setSceneSummaryDraft] = useState('');
  useEffect(() => {
    if (open) setTitleDraft(conversation?.title || '');
  }, [open, conversation?.id, conversation?.title]);
  useEffect(() => {
    if (open) setActiveDrawerTab('manage');
    setEditingMemoryId('');
    setMemoryEditDraft({});
    setEditingSceneSummary(false);
  }, [open, conversation?.id]);
  useEffect(() => {
    if (!editingSceneSummary) setSceneSummaryDraft(context?.scene?.summary || '');
  }, [context?.scene?.summary, editingSceneSummary]);
  const characterById = new Map(characters.map((c) => [c.id, c]));
  const characterParticipants = participants.filter((p) => p.type === 'character');
  const invitedCharacterIds = new Set(characterParticipants.map((p) => p.id));
  const inviteOptions = characters.filter((character) => !invitedCharacterIds.has(String(character.id || '')));
  const scene = context?.scene;
  const memories = context?.memories || [];
  const commonMemories = memories.filter((memory) => memory.character_id === '__room__');
  const memoriesByCharacter = (characterId: string) => memories.filter((memory) => memory.character_id === characterId);

  const compactSceneMemoryText = scene?.summary || '';
  const compressionText = compactSceneMemoryText || t('저장된 Story Arc 없음');
  const relationshipArchetypeLabel = RELATIONSHIP_ARCHETYPE_PRESETS.find((preset) => preset.key === String(scene?.relationship_archetype || ''))?.label ? t(RELATIONSHIP_ARCHETYPE_PRESETS.find((preset) => preset.key === String(scene?.relationship_archetype || ''))!.label as UiMessageKey) : t('기본 관계');
  const isBattleConversation = conversation?.genre_mode === 'battle';
  const drawerTabs: Array<[DrawerTab, string]> = [
    ['manage', t('관리')], ['state', t('상태')], ...(isBattleConversation ? ([['battle', t('배틀')]] as Array<[DrawerTab, string]>) : []), ['memory', t('유저노트')],
  ];
  const submitTitle = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const title = titleDraft.trim();
    if (!title || !conversation?.id) return;
    await onRenameConversation?.(title);
  };
  const startSceneSummaryEdit = () => {
    setSceneSummaryDraft(scene?.summary || '[Rolling Story Arc]\n- ');
    setEditingSceneSummary(true);
  };
  const cancelSceneSummaryEdit = () => {
    setSceneSummaryDraft(scene?.summary || '');
    setEditingSceneSummary(false);
  };
  const submitSceneSummary = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const summary = sceneSummaryDraft.trim();
    if (!summary || !conversation?.id) return;
    const saved = await onUpdateSceneSummary?.(summary, Number(scene?.compression_revision || 0));
    if (!saved) return;
    setSceneSummaryDraft(saved.summary || summary);
    setEditingSceneSummary(false);
  };
  const submitInvite = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!inviteCharacterId || !conversation?.id) return;
    await onInviteCharacter?.(inviteCharacterId);
    setInviteCharacterId('');
  };
  const submitUserMemory = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const content = (userMemoryDraft?.content || '').trim();
    if (!content || !conversation?.id) return;
    await onCreateUserMemory?.({ ...(userMemoryDraft || {}), content });
  };
  const startMemoryEdit = (memory: MemoryItem) => {
    setEditingMemoryId(String(memory.id || ''));
    setMemoryEditDraft({
      character_id: memory.character_id || '__room__',
      memory_type: memory.memory_type || 'user_note',
      content: memory.content || '',
      importance: Number(memory.importance || 5),
    });
  };
  const cancelMemoryEdit = () => {
    setEditingMemoryId('');
    setMemoryEditDraft({});
  };
  const submitMemoryEdit = async (event: FormEvent<HTMLFormElement>, memory: MemoryItem) => {
    event.preventDefault();
    const content = (memoryEditDraft.content || '').trim();
    if (!content || !conversation?.id || !memory.id) return;
    await onUpdateMemory?.(memory, { ...memoryEditDraft, content });
    cancelMemoryEdit();
  };
  const memoryCharacterSelect = (draft: UserMemoryDraft, onChange: (draft: UserMemoryDraft) => void) => <Select value={draft.character_id || '__room__'} onChange={(e) => onChange({ ...draft, character_id: e.target.value })}><option value="__room__">{t('공통 · 모든 캐릭터')}</option>{characterParticipants.map((participant) => <option key={participant.id} value={participant.id}>{participantName(participant.id)} · {t('전용')}</option>)}</Select>;
  const memoryImportanceSelect = (draft: UserMemoryDraft, onChange: (draft: UserMemoryDraft) => void) => <Select value={String(draft.importance || 5)} onChange={(e) => onChange({ ...draft, importance: Number(e.target.value) })}><option value="5">{t('중요도')} 5</option><option value="4">{t('중요도')} 4</option><option value="3">{t('중요도')} 3</option><option value="2">{t('중요도')} 2</option><option value="1">{t('중요도')} 1</option></Select>;
  const managementPanel = <div className="drawer-tab-panel" role="tabpanel" aria-label={t('대화방 관리')} data-modernized="대화 정보 드로어 design-system primitive marker">
    <section className="context-section"><h3>{t('대화방 편집')}</h3><form className="drawer-edit-row" onSubmit={submitTitle}><Input aria-label={t('대화방명 편집')} value={titleDraft} onChange={(e) => setTitleDraft(e.target.value)} placeholder={t('대화방 이름')} /><Button type="submit" disabled={!titleDraft.trim()}>{t('저장')}</Button></form></section>
    <section className="context-section"><div className="context-section-title"><h3>{t('캐릭터 역할')}</h3><Badge variant="outline" title={t('캐릭터 초대')}>＋ {t('초대')}</Badge></div><div className="context-card-list role-card-list grid gap-3">{characterParticipants.map((participant) => <div className="context-card role-card" key={`role-${participant.id}`}><div className="flex items-center justify-between gap-2"><div className="context-character-title min-w-0"><img src={avatarUrlFor(characterById.get(participant.id) || { id: participant.id, name: participantName(participant.id) }, theme)} alt="" /><strong className="truncate">{participantName(participant.id)}</strong></div><Button type="button" variant="ghost" size="sm" className="min-h-8 shrink-0 px-3 text-xs" title={t('내보내기')} disabled={busy} onClick={() => onRemoveCharacter?.(participant.id)}>{t('내보내기')}</Button></div><Select aria-label={`${participantName(participant.id)} · ${t('캐릭터 역할')}`} value={participant.role || ''} onChange={(event) => onUpdateCharacterRole?.(participant.id, event.target.value)}>{CAST_ROLE_PRESETS.map((preset) => <option key={preset.key || 'none'} value={preset.key}>{t(preset.label as UiMessageKey)} · {t(preset.hint as UiMessageKey)}</option>)}</Select></div>)}</div><form className="drawer-invite-row" onSubmit={submitInvite}><Select aria-label={t('초대할 캐릭터 선택')} value={inviteCharacterId} onChange={(e) => setInviteCharacterId(e.target.value)}><option value="">{t('초대할 캐릭터 선택')}</option>{inviteOptions.map((character) => <option key={character.id} value={character.id}>{character.name}</option>)}</Select><Button type="submit" disabled={!inviteCharacterId}>{t('초대')}</Button></form>{!inviteOptions.length && <p className="context-muted">{t('초대 가능한 캐릭터 없음')}</p>}</section>
  </div>;
  const statePanel = <div className="drawer-tab-panel" role="tabpanel" aria-label={t('대화방 상태')}>
    <section className="context-section"><h3>{t('현재 장면 상태')}</h3>{scene ? <div className="context-card"><p><b>{t('장소')}</b> {String(scene.location || '-')}</p><p><b>{t('분위기')}</b> {String(scene.mood || '-')}</p><p><b>{t('관계 진행 타입')}</b> {relationshipArchetypeLabel} <span className="memory-tag">{t('공략맛 프로필')}</span></p><p><b>{t('현재 갈등/주제')}</b> {String(scene.current_conflict || '-')}</p><p><b>{t('마지막 이벤트')}</b> {String(scene.last_event || '-')}</p><p><b>{t('긴장도')}</b> {String(scene.tension_level || '')} · <b>{t('로맨스')}</b> {String(scene.romance_level || '')}</p></div> : <p className="context-muted">{t('저장된 장면 상태 없음')}</p>}</section>
    <section className="context-section"><div className="context-section-title"><h3>{t('저장된 Story Arc')}</h3>{!editingSceneSummary && <Button type="button" variant="secondary" size="sm" disabled={busy || loading} onClick={startSceneSummaryEdit}>{t(compactSceneMemoryText ? '수정' : '작성')}</Button>}</div>{editingSceneSummary ? <form className="grid gap-3" onSubmit={submitSceneSummary}><Textarea rows={14} maxLength={2200} value={sceneSummaryDraft} onChange={(event) => setSceneSummaryDraft(event.target.value)} aria-label={t('Story Arc 수정')} placeholder={'[Rolling Story Arc]\n- 사건 또는 전환점'} /><div className="flex items-center justify-between gap-3"><small className="text-muted-foreground">{formatStoryArcLimit(sceneSummaryDraft.length, locale)}</small><div className="flex gap-2"><Button type="button" variant="ghost" size="sm" disabled={busy} onClick={cancelSceneSummaryEdit}>{t('취소')}</Button><Button type="submit" size="sm" disabled={busy || !sceneSummaryDraft.trim().startsWith('[Rolling Story Arc]') || !sceneSummaryDraft.includes('\n- ')}>{t(busy ? '저장 중…' : 'Arc 저장')}</Button></div></div></form> : <pre className="context-summary">{compressionText}</pre>}</section>
  </div>;
  const activeMatch = battleState?.active_match;
  const recentMatches = battleState?.recent_matches || [];
  const standings = battleState?.standings || [];
  const battlePanel = <div className="drawer-tab-panel gap-4" role="tabpanel" aria-label={t('배틀 기록')}>
    <section className="context-section"><h3>{t('현재 경기')}</h3>{activeMatch ? <div className="context-card"><strong>{activeMatch.participant_a_name} vs {activeMatch.participant_b_name}</strong><p><b>{t('상태')}</b> {String(activeMatch.result_status || '-')}</p>{activeMatch.process_summary && <p>{String(activeMatch.process_summary)}</p>}</div> : <p className="context-muted">{t('현재 진행중인 배틀 없음')}</p>}</section>
    <section className="context-section"><h3>{t('배틀 리포트')}</h3>{recentMatches.length ? <div className="context-card-list grid gap-3">{recentMatches.map((match) => <div className="context-card" key={String(match.id || match.matchup_key)}><strong>#{String(match.match_order || '?')} {String(match.participant_a_name || '-')} vs {String(match.participant_b_name || '-')}</strong><p><b>{t('결과')}</b> {match.winner_name ? formatBattleResult(match.winner_name, match.loser_name || '-', locale) : String(match.result_status || '-')}</p>{match.process_summary && <p>{String(match.process_summary)}</p>}{match.decisive_moment && <p><b>{t('결정적 장면')}</b> {String(match.decisive_moment)}</p>}</div>)}</div> : <p className="context-muted">{t('저장된 배틀 리포트 없음')}</p>}</section>
    <section className="context-section"><h3>{t('순위')}</h3>{standings.length ? <div className="context-card-list grid gap-3">{standings.map((standing) => <div className="context-card battle-standing-row" key={String(standing.character_id || standing.name)}><div className="flex items-center justify-between gap-3"><strong className="min-w-0 truncate">{formatBattleStanding(String(standing.rank || '?'), String(standing.name || standing.character_id || '-'), Number(standing.wins || 0), Number(standing.losses || 0), Number(standing.points || 0), locale)}</strong></div></div>)}</div> : <p className="context-muted">{t('순위표 없음')}</p>}</section>
  </div>;

  const renderMemoryItem = (memory: MemoryItem) => {
    const isEditing = editingMemoryId === String(memory.id || '');
    if (isEditing) {
      return <li className="memory-row memory-row-edit" key={memory.id}><form className="memory-edit-card" onSubmit={(event) => submitMemoryEdit(event, memory)}><Textarea rows={3} value={memoryEditDraft.content || ''} onChange={(e) => setMemoryEditDraft({ ...memoryEditDraft, content: e.target.value })} aria-label={t('유저노트 내용 수정')} /> <div className="memory-compose-actions">{memoryCharacterSelect(memoryEditDraft, setMemoryEditDraft)}{memoryImportanceSelect(memoryEditDraft, setMemoryEditDraft)}<Button type="submit" size="sm" disabled={busy || !(memoryEditDraft.content || '').trim()}>{t('수정 저장')}</Button><Button type="button" variant="ghost" size="sm" disabled={busy} onClick={cancelMemoryEdit}>{t('취소')}</Button></div></form></li>;
    }
    return <li className="memory-row" key={memory.id}><span className="memory-row-main"><Badge variant="outline">{t('유저노트')} · {memory.importance}/5</Badge><span className="memory-content">{memory.content}</span></span><div className="memory-row-actions"><Button type="button" variant="secondary" size="sm" disabled={busy} onClick={() => startMemoryEdit(memory)}>{t('수정')}</Button><Button type="button" variant="destructive" size="sm" disabled={busy} onClick={() => onDeleteMemory?.(memory)}>{t('삭제')}</Button></div></li>;
  };
  const memoryPanel = <div className="drawer-tab-panel" role="tabpanel" aria-label={t('대화방 유저노트')}>
    <section className="context-section"><h3>{t('유저노트 추가')}</h3><form className="memory-compose-card" onSubmit={submitUserMemory}><Textarea rows={3} value={userMemoryDraft?.content || ''} onChange={(e) => onUserMemoryDraftChange?.({ ...(userMemoryDraft || {}), content: e.target.value })} placeholder={t('기억할 내용을 입력하세요')} /><div className="memory-compose-actions">{memoryCharacterSelect(userMemoryDraft || {}, (draft) => onUserMemoryDraftChange?.(draft))}{memoryImportanceSelect(userMemoryDraft || {}, (draft) => onUserMemoryDraftChange?.(draft))}<Button type="submit" disabled={busy || !(userMemoryDraft?.content || '').trim()}>{t('유저노트 추가')}</Button></div></form></section>
    <section className="context-section"><h3>{t('유저노트')}</h3><div className="context-card"><strong>{t('공통 유저노트')}</strong>{commonMemories.length ? <ul className="memory-list">{commonMemories.map(renderMemoryItem)}</ul> : <p className="context-muted">{t('저장된 공통 유저노트 없음')}</p>}</div>{characterParticipants.map((participant) => {
      const items = memoriesByCharacter(participant.id);
      return <div className="context-card" key={`memory-${participant.id}`}><strong>{participantName(participant.id)}</strong>{items.length ? <ul className="memory-list">{items.map(renderMemoryItem)}</ul> : <p className="context-muted">{t('저장된 캐릭터별 유저노트 없음')}</p>}</div>;
    })}</section>
  </div>;
  const panels: Record<DrawerTab, ReactNode> = { manage: managementPanel, state: statePanel, battle: battlePanel, memory: memoryPanel };
  if (!open) return null;
  return <>
    <button type="button" aria-label={t('대화 정보 닫기')} className="context-drawer-backdrop" onClick={onClose}></button>
    <aside className="context-drawer open" data-modernized="대화 정보 드로어 design-system primitive marker">
      <header className="context-drawer-head"><div><strong>{t('대화방 정보')}</strong><small>{conversation?.title || conversation?.id || t('현재 대화방')}</small></div><Button type="button" variant="ghost" size="icon" className="drawer-close" aria-label={t('대화 정보 닫기')} onClick={onClose}>×</Button></header>
      <div className="context-drawer-body">
        {loading && <p className="context-muted">{t('불러오는 중…')}</p>}
        <nav className="drawer-tab-bar" aria-label={t('대화방 정보 영역')}>{drawerTabs.map(([key, label]) => <Button type="button" key={key} variant={activeDrawerTab === key ? 'default' : 'ghost'} size="sm" aria-pressed={activeDrawerTab === key} onClick={() => setActiveDrawerTab(key)}>{label}</Button>)}</nav>
        <div className="drawer-tab-content">
          {panels[activeDrawerTab] || managementPanel}
        </div>
      </div>
    </aside>
  </>;
}
