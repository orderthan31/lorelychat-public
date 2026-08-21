import { useMemo } from 'react';
import { useSuspenseQuery } from '@tanstack/react-query';
import { Pager, ResourceCard } from '../../components/molecules';
import { ResourceListPendingBar } from '../../components/molecules';
import { settingsApi } from '../../api/resources';
import { queryKeys } from '../../hooks/useBootstrapResources';
import { PRESET_TYPE_LABELS } from '../../constants/domain';
import { Badge } from '../../components/atoms';
import { Input } from '../../components/atoms';
import { useI18n } from '../../i18n/I18nProvider';
import { formatNamedAction, type UiMessageKey } from '../../i18n/core';
import { matchesQuery, paginate } from '../../utils/pagination';

export type PresetListItem = {
  id: string;
  preset_type?: string;
  title?: string;
  content?: string;
  description?: string;
  enabled?: boolean;
};

export type PresetsViewProps = {
  navigate: (path: string) => void;
  presetQuery?: string;
  setPresetQuery: (value: string) => void;
  setPresetPage: (page: number) => void;
  loadPresets?: () => void;
  visiblePresets?: PresetListItem[];
  presetPage: number;
  pageCount: (total: number) => number;
  filteredPresets?: PresetListItem[];
};

export function PresetsView({
  navigate,
  presetQuery = '',
  setPresetQuery,
  setPresetPage,
  loadPresets,
  visiblePresets = [],
  presetPage,
  pageCount,
  filteredPresets = []
}: PresetsViewProps) {
  const { locale, t } = useI18n();
  const presetListQuery = useSuspenseQuery({ queryKey: queryKeys.presets, queryFn: settingsApi.presets });
  const loadedPresets = (presetListQuery.data || []) as PresetListItem[];
  const suspenseFilteredPresets = useMemo(() => loadedPresets.filter((preset) => matchesQuery(preset, presetQuery, ['preset_type', 'title', 'content', 'description'])), [loadedPresets, presetQuery]);
  const safePage = Math.min(presetPage, pageCount(suspenseFilteredPresets.length));
  const suspenseVisiblePresets = paginate(suspenseFilteredPresets, safePage);
  return <section className="panel page grid gap-3" aria-busy={presetListQuery.isFetching} data-page="presets-list" data-modernized="uniform resource cards suspense query">
    <h1 className="sr-only">{t('프리셋')}</h1>
    <Input aria-label={t('프리셋 검색')} value={presetQuery} onChange={(e) => { setPresetQuery(e.target.value); setPresetPage(1); }} placeholder={t('타입, 제목, 내용 검색')} />
    <ResourceListPendingBar pending={presetListQuery.isFetching} />
    <div className="grid items-stretch gap-2 sm:grid-cols-2 xl:grid-cols-3">{suspenseVisiblePresets.map((preset) => <ResourceCard
      key={preset.id}
      title={preset.title || t('이름 없는 프리셋')}
      description={preset.content || preset.description || t('내용 없음')}
      meta={<><Badge>{t((PRESET_TYPE_LABELS[preset.preset_type || ''] || preset.preset_type || '프리셋') as UiMessageKey)}</Badge><Badge variant="outline">{t(preset.enabled ? '활성' : '비활성')}</Badge></>}
      openLabel={formatNamedAction(preset.title || t('프리셋'), 'open', locale)}
      onOpen={() => navigate(`/presets/${preset.id}`)}
    />)}</div>
    <Pager page={safePage} total={suspenseFilteredPresets.length} onPage={setPresetPage} />
  </section>;
}
