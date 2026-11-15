import { useEffect, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Button, Input, Select, Skeleton } from '../../components/atoms';
import { ResourceListPendingBar } from '../../components/molecules';
import { conversationApi } from '../../api/resources';
import { useI18n } from '../../i18n/I18nProvider';
import type { Translator, UiMessageKey } from '../../i18n/core';
export type ConversationSummary = {
    id: string;
    title?: string | null;
};
type UsageBucket = {
    provider?: string;
    model?: string;
    purpose?: string;
    calls?: number;
    total_tokens?: number;
    prompt_tokens?: number;
    completion_tokens?: number;
    estimated_calls?: number;
};
type UsageSummary = {
    conversation_id?: string | null;
    buckets?: UsageBucket[];
    total_calls?: number;
    prompt_tokens?: number;
    completion_tokens?: number;
    total_tokens?: number;
};
type RangePreset = '7d' | '30d' | '90d' | 'all' | 'custom';
type UsageGroup = {
    provider: string;
    model: string;
    calls: number | null;
    prompt_tokens: number | null;
    completion_tokens: number | null;
    total_tokens: number | null;
    estimated_calls: number;
    buckets: UsageBucket[];
};
export type StatisticsViewProps = {
    conversations?: ConversationSummary[];
};
function purposeLabel(purpose: string | undefined, t: Translator) {
    const labels: Record<string, UiMessageKey> = { chat_generation: '채팅', conversation_compression: '압축', chat_command_postprocess: '커맨드 후처리', tts_generation: '음성 생성', full_room_recompression: '전체방 재압축', full_room_recompression_final: '최종 재압축' };
    return t(labels[purpose || ''] || '기타');
}
function toLocalDateInput(date: Date) { const offset = date.getTimezoneOffset() * 60000; return new Date(date.getTime() - offset).toISOString().slice(0, 10); }
function startOfDayIso(value?: string) { return value ? `${value}T00:00:00` : undefined; }
function endOfDayIso(value?: string) { return value ? `${value}T23:59:59` : undefined; }
function defaultStart(days: number) { const date = new Date(); date.setDate(date.getDate() - days + 1); return toLocalDateInput(date); }
function StatCards({ usage }: {
    usage?: UsageSummary | null;
}) {
    const { locale, t } = useI18n();
    const statValue = (value: unknown) => value == null || !Number.isFinite(Number(value)) ? '—' : new Intl.NumberFormat(locale).format(Number(value));
    return <dl className="usage-metrics" data-usage-summary>
    {([[usage?.total_calls, '호출'], [usage?.prompt_tokens, '프롬프트 토큰'], [usage?.completion_tokens, '응답 토큰'], [usage?.total_tokens, '전체 토큰']] as const).map(([value, key]) => <div key={key}><dt>{t(key)}</dt><dd>{statValue(value)}</dd></div>)}
  </dl>;
}
function groupUsageBuckets(buckets: UsageBucket[] = []): UsageGroup[] {
    const groups = new Map<string, UsageGroup>();
    const add = (a: number | null, b: unknown) => a == null || b == null || !Number.isFinite(Number(b)) ? null : a + Number(b);
    for (const bucket of buckets) {
        const provider = bucket.provider || 'unknown', model = bucket.model || 'unknown', key = `${provider}\n${model}`;
        const current = groups.get(key) || { provider, model, calls: 0, prompt_tokens: 0, completion_tokens: 0, total_tokens: 0, estimated_calls: 0, buckets: [] };
        current.calls = add(current.calls, bucket.calls);
        current.prompt_tokens = add(current.prompt_tokens, bucket.prompt_tokens);
        current.completion_tokens = add(current.completion_tokens, bucket.completion_tokens);
        current.total_tokens = add(current.total_tokens, bucket.total_tokens);
        current.estimated_calls += Number(bucket.estimated_calls || 0);
        current.buckets.push(bucket);
        groups.set(key, current);
    }
    return [...groups.values()].sort((a, b) => (b.total_tokens ?? -1) - (a.total_tokens ?? -1));
}
function UsageBuckets({ usage }: {
    usage?: UsageSummary | null;
}) {
    const { locale, t } = useI18n();
    const statValue = (value: unknown) => value == null || !Number.isFinite(Number(value)) ? '—' : new Intl.NumberFormat(locale).format(Number(value));
    const groups = groupUsageBuckets(usage?.buckets || []);
    if (!groups.length)
        return <p className="context-muted">{t('선택한 기간의 사용량 기록이 없습니다.')}</p>;
    return <div className="usage-groups">{groups.map(group => <details className="usage-group" key={`${group.provider}:${group.model}`}>
    <summary><span className="usage-model"><small>{group.provider}</small><strong title={group.model}>{group.model}</strong></span><span className="usage-totals"><span>{t('토큰')} <b>{statValue(group.total_tokens)}</b></span><span>{t('호출')} <b>{statValue(group.calls)}</b></span></span></summary>
    <div className="usage-breakdown">{group.buckets.map((bucket, index) => <article className="usage-bucket" key={`${bucket.purpose}:${index}`}><strong>{purposeLabel(bucket.purpose, t)}</strong><span>{t('호출')} {statValue(bucket.calls)}</span><span>{t('프롬프트')} {statValue(bucket.prompt_tokens)}</span><span>{t('응답')} {statValue(bucket.completion_tokens)}</span><span>{t('전체')} {statValue(bucket.total_tokens)}</span></article>)}</div>
  </details>)}</div>;
}
function UsageSkeleton() { return <div className="usage-skeleton" aria-hidden="true"><Skeleton className="h-16 w-full"/><Skeleton className="h-20 w-full"/><Skeleton className="h-20 w-full"/></div>; }
export function StatisticsView({ conversations = [] }: StatisticsViewProps) {
    const { t } = useI18n();
    const [preset, setPreset] = useState<RangePreset>('30d');
    const [startDate, setStartDate] = useState(defaultStart(30));
    const [endDate, setEndDate] = useState(toLocalDateInput(new Date()));
    const [selectedConversationId, setSelectedConversationId] = useState('');
    const query = useMemo(() => preset === 'all' ? {} : { start_at: startOfDayIso(startDate), end_at: endOfDayIso(endDate) }, [preset, startDate, endDate]);
    useEffect(() => { if (preset === '7d' || preset === '30d' || preset === '90d') {
        setStartDate(defaultStart(preset === '7d' ? 7 : preset === '30d' ? 30 : 90));
        setEndDate(toLocalDateInput(new Date()));
    } }, [preset]);
    const stats = useQuery({ queryKey: ['usage-statistics', selectedConversationId, query], queryFn: async () => { const [all, room] = await Promise.all([conversationApi.usageSummary(query), selectedConversationId ? conversationApi.usage(selectedConversationId, query) : Promise.resolve(null)]); return { all: all as UsageSummary, room: room as UsageSummary | null }; } });
    return <section className="panel page statistics-workspace" data-page="statistics" aria-busy={stats.isFetching} data-modernized="통계 페이지 design-system primitive marker">
    <header className="statistics-toolbar">
      <label>{t('기간 프리셋')}<Select value={preset} onChange={e => setPreset(e.target.value as RangePreset)}><option value="7d">{t('최근 7일')}</option><option value="30d">{t('최근 30일')}</option><option value="90d">{t('최근 90일')}</option><option value="all">{t('전체 기간')}</option><option value="custom">{t('직접 선택')}</option></Select></label>
      <label>{t('시작일')}<Input type="date" value={startDate} disabled={preset === 'all'} onChange={e => { setPreset('custom'); setStartDate(e.target.value); }}/></label>
      <label>{t('종료일')}<Input type="date" value={endDate} disabled={preset === 'all'} onChange={e => { setPreset('custom'); setEndDate(e.target.value); }}/></label>
    </header>
    <ResourceListPendingBar pending={stats.isFetching}/>
    {stats.isError && <div role="alert" className="statistics-error">{stats.error.message}<Button type="button" onClick={() => void stats.refetch()}>{t('재시도')}</Button></div>}
    <div className="statistics-panels">
      <section className="statistics-section"><h2>{t('전체 통계')}</h2>{stats.data ? <><StatCards usage={stats.data.all}/><UsageBuckets usage={stats.data.all}/></> : stats.isPending ? <div role="status" aria-label={t('불러오는 중…')}><UsageSkeleton /></div> : null}</section>
      <section className="statistics-section"><h2>{t('대화방별 통계')}</h2><label>{t('대화방')}<Select value={selectedConversationId} onChange={e => setSelectedConversationId(e.target.value)}><option value="">{t('대화방 선택')}</option>{conversations.map(room => <option key={room.id} value={room.id}>{room.title || room.id}</option>)}</Select></label>{selectedConversationId && (stats.data ? <><StatCards usage={stats.data.room}/><UsageBuckets usage={stats.data.room}/></> : stats.isPending ? <div role="status" aria-label={t('불러오는 중…')}><UsageSkeleton /></div> : null)}</section>
    </div>
  </section>;
}
