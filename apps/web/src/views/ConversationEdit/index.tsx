import { EditorActionBar } from '../../components/molecules/EditorActionBar';
import { EditorHelp } from '../../components/molecules/EditorHelp';
import { Field, FormSection } from '../../components/molecules';
import { CAST_ROLE_PRESETS, USER_SETTING_PRESETS } from '../../constants/domain';
import { Button } from '../../components/atoms';
import { Input } from '../../components/atoms';
import { Select } from '../../components/atoms';
import { Textarea } from '../../components/atoms';
import { Plus, Trash2 } from 'lucide-react';
import { useI18n } from '../../i18n/I18nProvider';
import { formatCharacterSlot, formatSelectedCharacters, type UiMessageKey } from '../../i18n/core';

type CharacterOption = { id?: string; name?: string; [key: string]: unknown };

type RoomParticipant = {
  id: string;
  type: string;
  role?: string | null;
  order_index?: number | null;
};

export type ConversationEditViewProps = Record<string, any> & {
  characters?: CharacterOption[];
};

export function ConversationEditView({
  busy,
  saveConversationRoomEdit,
  roomEditDraft,
  setRoomEditDraft,
  applyRoomEditPreset,
  worldSettings = [],
  characters = [],
}: ConversationEditViewProps) {
  const { locale, t } = useI18n();

  const participants: RoomParticipant[] = roomEditDraft.participants || [];
  const characterById = new Map(characters.map((character) => [String(character.id || ''), character]));
  const characterParticipants = participants.filter((participant) => participant.type === 'character');
  const selectedCharacterIds = new Set(characterParticipants.map((participant) => participant.id).filter(Boolean));
  const uniqueCharacterCount = new Set(characterParticipants.map((participant) => participant.id).filter(Boolean)).size;
  const hasDuplicateCharacters = uniqueCharacterCount !== characterParticipants.filter((participant) => !!participant.id).length;
  const characterSelectionReady = characterParticipants.length >= 1 && characterParticipants.every((participant) => !!participant.id) && !hasDuplicateCharacters;
  const characterSelectionHint = characterParticipants.length < 1
    ? t('캐릭터 1명 이상 선택 필요')
    : characterParticipants.some((participant) => !participant.id)
      ? t('비어있는 캐릭터 칸이 있어요.')
      : hasDuplicateCharacters
        ? t('같은 캐릭터가 중복 선택되어 있어요.')
        : formatSelectedCharacters(characterParticipants.length, locale);

  const updateCharacterAt = (characterIndex: number, id: string) => {
    let seen = -1;
    setRoomEditDraft({
      ...roomEditDraft,
      participants: participants.map((participant) => {
        if (participant.type !== 'character') return participant;
        seen += 1;
        return seen === characterIndex ? { ...participant, id } : participant;
      }),
    });
  };
  const updateCharacterRoleAt = (characterIndex: number, role: string) => {
    let seen = -1;
    setRoomEditDraft({
      ...roomEditDraft,
      participants: participants.map((participant) => {
        if (participant.type !== 'character') return participant;
        seen += 1;
        return seen === characterIndex ? { ...participant, role: role || null } : participant;
      }),
    });
  };
  const addCharacterSlot = () => setRoomEditDraft({
    ...roomEditDraft,
    participants: [...participants, { type: 'character', id: '', role: null, order_index: participants.length }],
  });
  const removeCharacterAt = (characterIndex: number) => {
    let seen = -1;
    setRoomEditDraft({
      ...roomEditDraft,
      participants: participants.filter((participant) => {
        if (participant.type !== 'character') return true;
        seen += 1;
        return seen !== characterIndex || characterParticipants.length <= 1;
      }),
    });
  };

  return <section className="panel page grid gap-4 editor-surface" data-modernized="대화방 수정 world-select-only marker flat form sections">
    <EditorActionBar label={t('페이지 액션')}><Button type="button" disabled={busy} onClick={saveConversationRoomEdit}>{t(busy ? '저장 중…' : '저장')}</Button><Button type="button" variant="secondary" onClick={addCharacterSlot}><Plus className="size-4" aria-hidden="true" />{t('참여 캐릭터')} · {t('추가')}</Button></EditorActionBar>
    <div className="editor-columns">
    <FormSection title={t('대화방 정보')}>
      <Field label={t('대화방 제목')}><Input value={roomEditDraft.title || ''} onChange={(e) => setRoomEditDraft({ ...roomEditDraft, title: e.target.value })} placeholder={t('대화방 제목')} /></Field>
      <Field label={t('세계관 (선택)')}><Select data-world-select value={roomEditDraft.world_setting_id || ''} onChange={(e) => setRoomEditDraft({ ...roomEditDraft, world_setting_id: e.target.value })}><option value="">{t('세계관 없음')}</option>{worldSettings.filter((world) => world.enabled !== false || world.id === roomEditDraft.world_setting_id).map((world) => <option key={world.id} value={world.id}>{world.title || world.id}</option>)}</Select></Field>
    </FormSection>

    <FormSection title={t('참여 캐릭터')}>
      {characterParticipants.map((participant, index) => <div className="relative grid min-w-0 gap-2.5 border-t border-border pt-3 first:border-t-0 first:pt-0" key={`character-${index}`}>
        <Field className={characterParticipants.length > 1 ? 'min-w-0 pr-12' : 'min-w-0'} label={formatCharacterSlot(index + 1, locale)}><Select value={participant.id} onChange={(e) => updateCharacterAt(index, e.target.value)}><option value="">{t('캐릭터 선택')}</option>{characters.map((character) => { const id = String(character.id || ''); const alreadySelected = selectedCharacterIds.has(id) && id !== participant.id; return <option key={id} value={id} disabled={alreadySelected}>{character.name || id}</option>; })}</Select></Field>
        <Field label={t('캐릭터 역할')}><Select value={participant.role || ''} disabled={busy} onChange={(e) => updateCharacterRoleAt(index, e.target.value)}>{participant.role && !CAST_ROLE_PRESETS.some(preset => preset.key === participant.role) && <option value={participant.role}>{participant.role}</option>}{CAST_ROLE_PRESETS.map(preset => <option key={preset.key || 'none'} value={preset.key}>{t(preset.label as UiMessageKey)}</option>)}</Select></Field>
        {characterParticipants.length > 1 && <Button type="button" variant="ghost" size="icon" className="absolute right-0 top-0 text-danger" aria-label={`${formatCharacterSlot(index + 1, locale)} ${t('삭제')}`} title={`${formatCharacterSlot(index + 1, locale)} ${t('삭제')}`} onClick={() => removeCharacterAt(index)}><Trash2 className="size-4" aria-hidden="true" /></Button>}
      </div>)}
      <span className="flex items-center gap-2 text-xs text-muted-foreground"><span className={`size-2 rounded-full ${characterSelectionReady ? 'bg-success' : 'bg-warning'}`} aria-hidden="true" />{characterSelectionHint}</span>
      <EditorHelp summary={t('역할 안내')}><ul className="cast-role-help">{CAST_ROLE_PRESETS.map(preset => <li key={preset.key || 'none'}><strong>{t(preset.label as UiMessageKey)}</strong> · {t(preset.hint as UiMessageKey)}</li>)}</ul></EditorHelp>
    </FormSection>

    </div>
    <FormSection title={t('유저페르소나')}>
      <Field label={t('설명')}><Textarea rows={4} value={roomEditDraft.user_description || ''} onChange={(e) => setRoomEditDraft({ ...roomEditDraft, user_description: e.target.value })} placeholder={t('예: 사용자는 개발자이고 캐릭터와 가까운 관계다.')} /></Field>
      <div className="preset-inline"><Select defaultValue="" onChange={(e) => { if (e.target.value) { applyRoomEditPreset('user_setting', e.target.value); e.target.value = ''; } }}><option value="">{t('유저페르소나 프리셋 불러오기')}</option>{USER_SETTING_PRESETS.map((preset) => <option key={preset.title} value={preset.content}>{t(preset.title as UiMessageKey)}</option>)}</Select></div>
    </FormSection>
  </section>;
}
