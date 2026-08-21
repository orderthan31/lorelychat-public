import { useEffect, useState, useTransition } from 'react';
import { useSuspenseQuery } from '@tanstack/react-query';
import { Field, FormSection, ResourceCard } from '../../components/molecules';
import { TextareaWithExpand } from '../../components/molecules';
import { GenreModeField } from '../../components/molecules';
import { Pager, ResourceListPendingBar } from '../../components/molecules';
import { settingsApi } from '../../api/resources';
import { LIST_PAGE_SIZE } from '../../constants/domain';
import { RELATIONSHIP_ARCHETYPE_PRESETS, ROOM_TONE_PRESETS, DEFAULT_GENRE_MODE } from '../../constants/domain';
import { Button } from '../../components/atoms';

import { Input } from '../../components/atoms';
import { Select } from '../../components/atoms';
import { Switch } from '../../components/atoms';
import { conversationThumbnailUrlFor, lorelyAppIconUrlForTheme } from '../../utils/assets';
import { useUiStore } from '../../stores/uiStore';
import { genreModeLabel } from '../../utils/genre';
import { useI18n } from '../../i18n/I18nProvider';
import { formatNamedAction, type UiMessageKey } from '../../i18n/core';

export type WorldSettingItem = {
  id?: string;
  title: string;
  thumbnail_url?: string | null;
  description?: string | null;
  genre_mode?: string | null;
  location?: string | null;
  mood?: string | null;
  world_seed?: string | null;
  opening_scene?: string | null;
  opening_line?: string | null;
  tone_preset?: string | null;
  relationship_archetype?: string | null;
  compression_focus?: string | null;
  tags?: string[];
  enabled?: boolean;
};

export const emptyWorld: WorldSettingItem = {
  title: '',
  thumbnail_url: '',
  description: '',
  genre_mode: DEFAULT_GENRE_MODE,
  location: '',
  mood: '',
  world_seed: '',
  opening_scene: '',
  opening_line: '',
  tone_preset: '',
  relationship_archetype: '',
  compression_focus: '',
  tags: [],
  enabled: true,
};

export function normalizeWorld(world?: WorldSettingItem): WorldSettingItem {
  return { ...emptyWorld, ...(world || {}), genre_mode: world?.genre_mode || DEFAULT_GENRE_MODE };
}

export type WorldSettingsListViewProps = {
  worldSettings?: WorldSettingItem[];
  navigate: (path: string) => void;
  loadWorldSettings: () => void;
};

export function WorldSettingsListView({ worldSettings = [], navigate, loadWorldSettings }: WorldSettingsListViewProps) {
  const theme = useUiStore((state) => state.theme);
  const { locale, t } = useI18n();
  const [queryInput, setQueryInput] = useState('');
  const [query, setQuery] = useState('');
  const [page, setPage] = useState(1);
  const [isPending, startTransition] = useTransition();
  const pageQuery = useSuspenseQuery({
    queryKey: ['world-settings', 'page', page, query],
    queryFn: () => settingsApi.worldSettingsPage({ page, page_size: LIST_PAGE_SIZE, q: query }),
  });
  const pageData = pageQuery.data;
  const visibleWorldSettings = pageData.items as WorldSettingItem[];
  const total = pageData.total;
  const updateQuery = (value: string) => {
    setQueryInput(value);
    startTransition(() => { setQuery(value); setPage(1); });
  };
  return <section className="panel page grid gap-3" aria-busy={isPending || pageQuery.isFetching} data-page="world-settings-list" data-modernized="uniform resource cards suspense query">
    <h1 className="sr-only">{t('세계관')}</h1>
    <Input aria-label={t('세계관 검색')} value={queryInput} onChange={(e) => updateQuery(e.target.value)} placeholder={t('세계관 이름, 설명, 배경 검색')} />
    <ResourceListPendingBar pending={isPending || pageQuery.isFetching} />
    <div className={`grid items-stretch gap-2 transition-opacity sm:grid-cols-2 xl:grid-cols-3 ${isPending ? 'opacity-60' : 'opacity-100'}`}>
      {visibleWorldSettings.map((world) => <ResourceCard
        key={world.id || world.title}
        title={world.title || t('이름 없는 세계관')}
        description={world.description || world.world_seed || t('설명 없음')}
        meta={<><span>{t(world.enabled === false ? '비활성' : '활성')}</span><span>·</span><span>{t(genreModeLabel(world.genre_mode) as UiMessageKey)}</span></>}
        media={<img className={`h-full w-full object-cover ${world.thumbnail_url ? '' : 'object-contain bg-surface-muted p-1.5'}`} src={world.thumbnail_url ? conversationThumbnailUrlFor(world, theme) : lorelyAppIconUrlForTheme(theme)} alt={t('세계관 썸네일')} />}
        openLabel={formatNamedAction(world.title || t('세계관'), 'open', locale)}
        onOpen={() => navigate(`/world-settings/${world.id}`)}
      />)}
      {!visibleWorldSettings.length && <div className="rounded-xl border border-dashed border-border py-10 text-center text-sm text-muted-foreground">{t('세계관이 없습니다.')}</div>}
    </div>
    <Pager page={pageData.page || page} total={total} onPage={(nextPage) => startTransition(() => setPage(nextPage))} />
  </section>;
}

export type WorldSettingsDetailViewProps = {
  route: { page: string; id?: string };
  worldSettings?: WorldSettingItem[];
  worldSettingDraft: WorldSettingItem;
  setWorldSettingDraft: (value: WorldSettingItem | ((current: WorldSettingItem) => WorldSettingItem)) => void;
  saveWorldSetting: () => void;
  deleteWorldSetting: (world: WorldSettingItem) => void;
  uploadWorldThumbnail: (file: File | null) => void;
  busy?: boolean;
  navigate: (path: string) => void;
};

export function WorldSettingsDetailView({ route, worldSettings = [], worldSettingDraft, setWorldSettingDraft, saveWorldSetting, deleteWorldSetting, uploadWorldThumbnail, busy = false, navigate }: WorldSettingsDetailViewProps) {
  const theme = useUiStore((state) => state.theme);
  const { locale, t } = useI18n();
  const draft = normalizeWorld(worldSettingDraft);
  const updateDraft = (patch: Partial<WorldSettingItem>) => setWorldSettingDraft((current) => ({ ...normalizeWorld(current), ...patch }));
  const canSave = !!draft.title?.trim() && !!draft.world_seed?.trim();
  const isNew = route.page === 'worldSettingNew';
  const current = !isNew && route.id ? worldSettings.find((world) => world.id === route.id) : null;
  useEffect(() => {
    if (isNew) {
      if (draft.id) setWorldSettingDraft({ ...emptyWorld });
      return;
    }
    if (route.id && current && current.id !== draft.id) setWorldSettingDraft(normalizeWorld(current));
  }, [isNew, route.id, current?.id, draft.id, setWorldSettingDraft]);

  return <section className="panel page grid gap-3" data-modernized="세계관 상세 separated marker">
    <FormSection title={draft.id ? formatNamedAction(draft.title || t('세계관'), 'edit', locale) : t('새 세계관 등록')}>
        <Field label={t('세계관 이름')}><Input value={draft.title || ''} onChange={(e) => updateDraft({ title: e.target.value })} placeholder={t('예: 사내 비밀 프로젝트실')} /></Field>
        <div className="grid gap-3 sm:grid-cols-[148px_minmax(0,1fr)] sm:items-start">
          <div className="overflow-hidden rounded-xl border border-border bg-background">
            <div className="aspect-square w-full">
              <img className={`h-full w-full object-cover ${draft.thumbnail_url ? '' : '!object-contain !object-center bg-[radial-gradient(circle_at_20%_12%,rgba(255,255,255,.95),transparent_28%),linear-gradient(135deg,#fff7f9,#ffe8ec_52%,#fffafc)] p-5 shadow-[inset_0_1px_0_rgba(255,255,255,.82),0_10px_24px_rgba(240,90,104,.10)]'}`} src={draft.thumbnail_url ? conversationThumbnailUrlFor(draft, theme) : lorelyAppIconUrlForTheme(theme)} alt={t('세계관 썸네일')} />
            </div>
          </div>
          <div className="grid gap-2">
            <Field label={t('세계관 썸네일 URL')}><Input value={draft.thumbnail_url || ''} onChange={(e) => updateDraft({ thumbnail_url: e.target.value })} placeholder={t('이미지 URL 또는 /uploads/...')} /></Field>
            <div className="grid grid-cols-2 gap-2">
              <input id="world-thumbnail-upload" type="file" accept="image/png,image/jpeg,image/webp,image/gif" className="sr-only" aria-label={t('세계관 썸네일')} disabled={busy} onChange={(e) => { const file = e.target.files?.[0] || null; uploadWorldThumbnail(file); e.currentTarget.value = ''; }} />
              <Button type="button" variant="secondary" disabled={busy} onClick={() => document.getElementById('world-thumbnail-upload')?.click()}>{t(busy ? '업로드 중…' : '파일 업로드')}</Button>
              <Button type="button" variant="ghost" disabled={busy || !draft.thumbnail_url} onClick={() => updateDraft({ thumbnail_url: '' })}>{t('썸네일 비우기')}</Button>
            </div>
            <small className="text-xs leading-relaxed text-muted-foreground">{t('파일은 5MB 이하 jpg/png/webp/gif만 가능합니다. 업로드 성공 시 URL 입력칸에 자동 반영됩니다.')}</small>
          </div>
        </div>
        <Field label={t('설명')}><Input value={draft.description || ''} onChange={(e) => updateDraft({ description: e.target.value })} placeholder={t('목록에서 구분하기 위한 짧은 설명')} /></Field>
        <GenreModeField value={draft.genre_mode || DEFAULT_GENRE_MODE} onChange={(value) => updateDraft({ genre_mode: value })} />
        <div className="grid two"><Field label={t('시작 장소')}><Input value={draft.location || ''} onChange={(e) => updateDraft({ location: e.target.value })} /></Field><Field label={t('시작 분위기')}><Input value={draft.mood || ''} onChange={(e) => updateDraft({ mood: e.target.value })} /></Field></div>
        <div className="grid two"><Field label={t('방 대화 톤')}><Select value={draft.tone_preset || ''} onChange={(e) => updateDraft({ tone_preset: e.target.value })}>{ROOM_TONE_PRESETS.map((preset) => <option key={preset.key || 'default'} value={preset.key}>{t(preset.label as UiMessageKey)} · {t(preset.hint as UiMessageKey)}</option>)}</Select></Field><Field label={t('관계 진행 타입')}><Select value={draft.relationship_archetype || ''} onChange={(e) => updateDraft({ relationship_archetype: e.target.value })}>{RELATIONSHIP_ARCHETYPE_PRESETS.map((preset) => <option key={preset.key || 'default'} value={preset.key}>{t(preset.label as UiMessageKey)} · {t(preset.hint as UiMessageKey)}</option>)}</Select></Field></div>
        <TextareaWithExpand label={t('세계관/규칙')} rows={5} value={draft.world_seed || ''} onChange={(value) => updateDraft({ world_seed: value })} placeholder={t('방의 기본 전제, 세계 규칙, 분위기, 금지할 전개 등을 적어주세요.')} />
        <div className="grid two"><TextareaWithExpand label={t('첫 상황')} rows={3} value={draft.opening_scene || ''} onChange={(value) => updateDraft({ opening_scene: value })} placeholder={t('방 시작 장면')} /><TextareaWithExpand label={t('첫 대사')} rows={3} value={draft.opening_line || ''} onChange={(value) => updateDraft({ opening_line: value })} placeholder={t('첫 캐릭터 버블로 넣을 대사')} /></div>
        <TextareaWithExpand label={t('주요 압축포인트')} rows={3} value={draft.compression_focus || ''} onChange={(value) => updateDraft({ compression_focus: value })} placeholder={t('압축 시 반드시 유지할 단서/관계 변화/장면 앵커')} />
        <Switch checked={draft.enabled !== false} onChange={(e) => updateDraft({ enabled: e.target.checked })} label={t('활성화')} />
        <div className="form-action-row">
          <Button type="button" disabled={busy || !canSave} onClick={saveWorldSetting}>{t(busy ? '저장 중…' : '저장')}</Button>
          {draft.id && <Button type="button" variant="destructive" onClick={() => deleteWorldSetting(draft)}>{t('삭제')}</Button>}
        </div>
    </FormSection>
  </section>;
}
