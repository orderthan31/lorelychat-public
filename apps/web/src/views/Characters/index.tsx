import { useState, useTransition } from 'react';
import { useSuspenseQuery } from '@tanstack/react-query';
import { characterApi } from '../../api/resources';
import { LIST_PAGE_SIZE } from '../../constants/domain';
import { avatarUrlFor } from '../../utils/assets';
import { useUiStore } from '../../stores/uiStore';
import { Input } from '../../components/atoms';
import { Pager, ResourceCard, ResourceListPendingBar } from '../../components/molecules';
import { useI18n } from '../../i18n/I18nProvider';
import { formatNamedAction, formatProfileImageAlt } from '../../i18n/core';

export type CharacterListItem = {
  id: string;
  name?: string;
  description?: string;
  persona?: string;
  avatar_url?: string;
};

export type CharactersListViewProps = {
  characters?: CharacterListItem[];
  navigate: (path: string) => void;
};

export function CharactersListView({ characters = [], navigate }: CharactersListViewProps) {
  const theme = useUiStore((state) => state.theme);
  const { locale, t } = useI18n();
  const [queryInput, setQueryInput] = useState('');
  const [query, setQuery] = useState('');
  const [page, setPage] = useState(1);
  const [isPending, startTransition] = useTransition();
  const pageQuery = useSuspenseQuery({
    queryKey: ['characters', 'page', page, query],
    queryFn: () => characterApi.page({ page, page_size: LIST_PAGE_SIZE, q: query }),
  });
  const pageData = pageQuery.data;
  const visibleCharacters = pageData.items as CharacterListItem[];
  const total = pageData.total;
  const updateQuery = (value: string) => {
    setQueryInput(value);
    startTransition(() => { setQuery(value); setPage(1); });
  };
  return <section className="panel page grid gap-3" aria-busy={isPending || pageQuery.isFetching} data-page="characters-list" data-modernized="uniform resource cards suspense query">
    <h1 className="sr-only">{t('캐릭터 목록')}</h1>
    <Input aria-label={t('캐릭터 검색')} value={queryInput} onChange={(e) => updateQuery(e.target.value)} placeholder={t('캐릭터명, 설명, 페르소나 검색')} />
    <ResourceListPendingBar pending={isPending || pageQuery.isFetching} />
    <div className={`grid items-stretch gap-2 transition-opacity sm:grid-cols-2 xl:grid-cols-3 ${isPending ? 'opacity-60' : 'opacity-100'}`}>{visibleCharacters.map((c) => <ResourceCard
      key={c.id}
      title={c.name || t('이름 없는 캐릭터')}
      description={c.description || c.persona || t('설명 없음')}
      media={<img className="h-full w-full object-cover object-top" src={avatarUrlFor(c, theme)} alt={formatProfileImageAlt(c.name || t('캐릭터'), locale)} />}
      openLabel={formatNamedAction(c.name || t('캐릭터'), 'open', locale)}
      onOpen={() => navigate(`/characters/${c.id}`)}
    />)}
      {!visibleCharacters.length && <div className="rounded-xl border border-dashed border-border py-10 text-center text-sm text-muted-foreground">{t('검색 결과가 없습니다.')}</div>}
    </div>
    <Pager page={pageData.page || page} total={total} onPage={(nextPage) => startTransition(() => setPage(nextPage))} />
  </section>;
}
