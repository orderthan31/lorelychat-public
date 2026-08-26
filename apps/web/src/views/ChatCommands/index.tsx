import { useState, useTransition } from 'react';
import { useSuspenseQuery } from '@tanstack/react-query';
import { settingsApi } from '../../api/resources';
import { LIST_PAGE_SIZE } from '../../constants/domain';
import { Badge } from '../../components/atoms';
import { Button } from '../../components/atoms';

import { Input } from '../../components/atoms';
import { Select } from '../../components/atoms';
import { Switch } from '../../components/atoms';
import { FormSection, Pager, ResourceCard, ResourceListPendingBar, TextareaWithExpand } from '../../components/molecules';
import { useI18n } from '../../i18n/I18nProvider';
import { formatNamedAction, type UiMessageKey } from '../../i18n/core';
import type { LegacyRoute } from '../../router/legacyRouter';

export type PostprocessContextOption = 'scene' | 'world' | 'characters' | 'relationship_memory' | 'recent_messages' | 'turn_messages';

export type ChatCommandItem = {
  id?: string;
  name: string;
  display_name?: string;
  description?: string;
  prompt?: string;
  generation_prompt?: string;
  postprocess_prompt?: string;
  postprocess_target?: 'first_bubble' | 'last_bubble' | 'all_bubbles';
  postprocess_probability?: number;
  postprocess_context_options?: PostprocessContextOption[];
  enabled?: boolean;
  priority?: number;
};

const DEFAULT_POSTPROCESS_CONTEXT_OPTIONS: PostprocessContextOption[] = ['scene', 'world', 'turn_messages'];

const POSTPROCESS_CONTEXT_OPTIONS: { value: PostprocessContextOption; label: string; description: string }[] = [
  { value: 'scene', label: '현재 장면', description: '장소, 분위기, 현재 갈등/주제, 마지막 사건, 장기 장면 메모리' },
  { value: 'world', label: '세계관', description: '세계관 핵심 전제 / world_seed' },
  { value: 'characters', label: '캐릭터', description: '방 참가 캐릭터 카드, 성향 점수, 역할/말하기 우선순위' },
  { value: 'relationship_memory', label: '관계/기억', description: '캐릭터별 관계 상태, 유저노트/장기 기억, 무대 밖 인물 회상' },
  { value: 'recent_messages', label: '최근 메시지', description: '최근 대화 히스토리' },
  { value: 'turn_messages', label: '현재 턴 메시지', description: '유저/source 메시지와 1차 응답 메시지' },
];

const emptyDraft: ChatCommandItem = {
  name: '',
  display_name: '',
  description: '',
  prompt: '',
  generation_prompt: '',
  postprocess_prompt: '',
  postprocess_target: 'last_bubble',
  postprocess_probability: 100,
  postprocess_context_options: DEFAULT_POSTPROCESS_CONTEXT_OPTIONS,
  enabled: true,
  priority: 0,
};

const targetLabels: Record<string, string> = {
  first_bubble: '처음에 후처리 전용 버블 삽입',
  last_bubble: '마지막에 후처리 전용 버블 삽입',
  all_bubbles: '기존 모든 버블에 확률적으로 끼워넣기',
};

type Navigate = (path: string) => void;

export type ChatCommandsViewProps = {
  route: LegacyRoute;
  navigate: Navigate;
  chatCommands?: ChatCommandItem[];
  chatCommandDraft: ChatCommandItem;
  setChatCommandDraft: (value: ChatCommandItem | ((current: ChatCommandItem) => ChatCommandItem)) => void;
  saveChatCommand: () => void;
  deleteChatCommand: (command: ChatCommandItem) => void;
  loadChatCommands: () => void;
  busy?: boolean;
};

function normalizeDraft(command?: ChatCommandItem): ChatCommandItem {
  return {
    ...emptyDraft,
    ...(command || {}),
    generation_prompt: command?.generation_prompt || command?.prompt || '',
    postprocess_prompt: command?.postprocess_prompt || command?.prompt || '',
    postprocess_target: command?.postprocess_target || 'last_bubble',
    postprocess_probability: command?.postprocess_probability ?? 100,
    postprocess_context_options: command?.postprocess_context_options?.length ? command.postprocess_context_options : DEFAULT_POSTPROCESS_CONTEXT_OPTIONS,
  };
}


function PostprocessContextSelector({ selected, onChange }: { selected: PostprocessContextOption[]; onChange: (value: PostprocessContextOption[]) => void }) {
  const { t } = useI18n();
  const values = new Set(selected || []);
  const toggle = (value: PostprocessContextOption) => {
    const next = values.has(value) ? selected.filter((item) => item !== value) : [...selected, value];
    onChange(next);
  };
  return <section className="grid gap-2">
    <strong className="text-sm font-semibold text-foreground">{t('후속프롬프트 참고 컨텍스트')}</strong>
    <div className="divide-y divide-border border-y border-border sm:grid sm:grid-cols-2 sm:divide-y-0">
      {POSTPROCESS_CONTEXT_OPTIONS.map((option) => {
        const checked = values.has(option.value);
        return <label key={option.value} className={`flex min-h-11 cursor-pointer gap-2 border-border p-3 transition sm:border-b ${checked ? 'bg-primary/10 text-foreground' : 'text-muted-foreground hover:bg-muted/40'}`}>
          <input className="mt-1 h-4 w-4 accent-primary" type="checkbox" checked={checked} onChange={() => toggle(option.value)} />
          <span className="grid gap-0.5">
            <span className="text-sm font-semibold">{t(option.label as UiMessageKey)}</span>
            <span className="text-xs leading-relaxed">{t(option.description as UiMessageKey)}</span>
          </span>
        </label>;
      })}
    </div>
  </section>;
}

function CommandForm({ draft, updateDraft, saveChatCommand, deleteChatCommand, busy, isNew, navigate }: {
  draft: ChatCommandItem;
  updateDraft: (patch: Partial<ChatCommandItem>) => void;
  saveChatCommand: () => void;
  deleteChatCommand: (command: ChatCommandItem) => void;
  busy?: boolean;
  isNew: boolean;
  navigate: Navigate;
}) {
  const { t } = useI18n();
  const target = draft.postprocess_target || 'last_bubble';
  const canSave = !!draft.name?.trim() && (!!draft.generation_prompt?.trim() || !!draft.postprocess_prompt?.trim() || !!draft.prompt?.trim());

  return <FormSection title={isNew ? t('새 커맨드 등록') : `${draft.name || t('커맨드')} · ${t('상세')}`}>
      <label>{t('커맨드명')}<Input value={draft.name || ''} onChange={(e) => { const name = e.target.value.replace(/^!/, ''); updateDraft({ name, display_name: name }); }} placeholder={t('예: 방송마지막')} /></label>
      <label>{t('설명')}<Input value={draft.description || ''} onChange={(e) => updateDraft({ description: e.target.value })} placeholder={t('자동완성에 표시할 설명')} /></label>
      <TextareaWithExpand label={t('메시지 생성 프롬프트')} rows={5} value={draft.generation_prompt || ''} onChange={(value) => updateDraft({ generation_prompt: value, prompt: draft.postprocess_prompt || value })} placeholder={t('메시지를 만들 때 적용할 지시')} />
      <TextareaWithExpand label={t('후처리 프롬프트')} rows={7} value={draft.postprocess_prompt || ''} onChange={(value) => updateDraft({ postprocess_prompt: value, prompt: value || draft.generation_prompt || '' })} placeholder={t('생성된 메시지에 적용할 후처리 지시')} />
      <PostprocessContextSelector selected={draft.postprocess_context_options || DEFAULT_POSTPROCESS_CONTEXT_OPTIONS} onChange={(value) => updateDraft({ postprocess_context_options: value })} />
      <div className="grid md:grid-cols-2 gap-3">
        <label>{t('후처리 대상')}
          <Select value={target} onChange={(e) => updateDraft({ postprocess_target: e.target.value as ChatCommandItem['postprocess_target'] })}>
            <option value="first_bubble">{t('첫 메시지 앞에 추가')}</option>
            <option value="last_bubble">{t('마지막 메시지 뒤에 추가')}</option>
            <option value="all_bubbles">{t('모든 메시지에 확률적으로 적용')}</option>
          </Select>
        </label>
        {target === 'all_bubbles' && <label>{t('메시지별 적용 확률 (%)')}<Input type="number" min="0" max="100" value={draft.postprocess_probability ?? 100} onChange={(e) => updateDraft({ postprocess_probability: Math.max(0, Math.min(100, Number(e.target.value || 0))) })} /></label>}
      </div>
      <Switch checked={draft.enabled !== false} onChange={(e) => updateDraft({ enabled: e.target.checked })} label={t('채팅에서 사용')} />
      <div className="form-action-row">
        <Button type="button" disabled={busy || !canSave} onClick={saveChatCommand}>{t(busy ? '저장 중…' : '저장')}</Button>
        {!isNew && draft.id && <Button type="button" variant="destructive" onClick={() => deleteChatCommand(draft)}>{t('삭제')}</Button>}
      </div>
  </FormSection>;
}

function ChatCommandsList({ navigate }: { navigate: Navigate }) {
  const { locale, t } = useI18n();
  const [queryInput, setQueryInput] = useState('');
  const [query, setQuery] = useState('');
  const [page, setPage] = useState(1);
  const [isPending, startTransition] = useTransition();
  const pageQuery = useSuspenseQuery({
    queryKey: ['chat-commands', 'page', page, query],
    queryFn: () => settingsApi.chatCommandsPage({ page, page_size: LIST_PAGE_SIZE, q: query }),
  });
  const pageData = pageQuery.data;
  const visibleCommands = pageData.items as ChatCommandItem[];
  const total = pageData.total;
  const updateQuery = (value: string) => {
    setQueryInput(value);
    startTransition(() => { setQuery(value); setPage(1); });
  };

  return <section className="panel page grid gap-3" aria-busy={isPending || pageQuery.isFetching} data-page="chat-commands-list" data-modernized="uniform resource cards suspense query">
    <h1 className="sr-only">{t('커맨드')}</h1>
    <Input aria-label={t('커맨드 검색')} value={queryInput} onChange={(e) => updateQuery(e.target.value)} placeholder={t('커맨드명, 설명, 프롬프트 검색')} />
    <ResourceListPendingBar pending={isPending || pageQuery.isFetching} />
    <div className={`grid items-stretch gap-2 transition-opacity sm:grid-cols-2 xl:grid-cols-3 ${isPending ? 'opacity-60' : 'opacity-100'}`}>
      {visibleCommands.map((command) => <ResourceCard
        key={command.id || command.name}
        title={`!${command.name}`}
        description={command.description || command.postprocess_prompt || command.generation_prompt || command.prompt || t('설명 없음')}
        meta={<><Badge variant={command.enabled === false ? 'outline' : 'secondary'}>{t(command.enabled === false ? '비활성' : '활성')}</Badge><span className="min-w-0 flex-1 truncate">{t('후처리')}: {t(targetLabels[command.postprocess_target || 'last_bubble'] as UiMessageKey)}{command.postprocess_target === 'all_bubbles' ? ` · ${command.postprocess_probability ?? 100}%` : ''}</span></>}
        openLabel={formatNamedAction(`!${command.name}`, 'open', locale)}
        onOpen={() => navigate(`/chat-commands/${command.id}`)}
      />)}
      {!visibleCommands.length && <div className="rounded-xl border border-dashed border-border py-10 text-center text-sm text-muted-foreground">{t('검색 결과가 없습니다.')}</div>}
    </div>
    <Pager page={pageData.page || page} total={total} onPage={(nextPage) => startTransition(() => setPage(nextPage))} />
  </section>;
}

export function ChatCommandsView({ route, navigate, chatCommands = [], chatCommandDraft, setChatCommandDraft, saveChatCommand, deleteChatCommand, loadChatCommands, busy }: ChatCommandsViewProps) {
  const { t } = useI18n();
  const draft = normalizeDraft(chatCommandDraft);
  const updateDraft = (patch: Partial<ChatCommandItem>) => setChatCommandDraft((current) => ({ ...normalizeDraft(current), ...patch }));
  const isNew = route.page === 'chatCommandNew';
  const routeId = 'id' in route ? route.id : '';

  if (route.page === 'chatCommandNew' || route.page === 'chatCommandDetail') {
    const detailCommand = route.page === 'chatCommandDetail' ? chatCommands.find((command) => command.id === routeId) : null;
    const formDraft = route.page === 'chatCommandDetail' && draft.id !== routeId && detailCommand
      ? normalizeDraft(detailCommand)
      : normalizeDraft(draft);
    return <section className="panel page grid gap-3" data-modernized="커맨드 상세 design-system primitive marker">
      <CommandForm draft={formDraft} updateDraft={updateDraft} saveChatCommand={saveChatCommand} deleteChatCommand={deleteChatCommand} busy={busy} isNew={isNew} navigate={navigate} />
    </section>;
  }

  return <ChatCommandsList navigate={navigate} />;
}
