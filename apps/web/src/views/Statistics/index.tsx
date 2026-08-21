import { useEffect, useMemo, useState } from 'react';
import { conversationApi } from '../../api/resources';
import { Badge } from '../../components/atoms';
import { Card, CardContent, CardHeader, CardTitle } from '../../components/atoms';
import { Input } from '../../components/atoms';
import { Select } from '../../components/atoms';
import { useI18n } from '../../i18n/I18nProvider';
import type { Translator, UiMessageKey } from '../../i18n/core';

export type ConversationSummary = { id: string; title?: string | null };
type UsageBucket = { provider?: string; model?: string; purpose?: string; calls?: number; total_tokens?: number; prompt_tokens?: number; completion_tokens?: number; estimated_calls?: number };
type UsageSummary = { conversation_id?: string | null; buckets?: UsageBucket[]; total_calls?: number; prompt_tokens?: number; completion_tokens?: number; total_tokens?: number };
type RangePreset = '7d' | '30d' | '90d' | 'all' | 'custom';
type UsageGroup = { provider: string; model: string; calls: number; prompt_tokens: number; completion_tokens: number; total_tokens: number; estimated_calls: number; buckets: UsageBucket[] };

export type StatisticsViewProps = {
  conversations?: ConversationSummary[];
};

function purposeLabel(purpose: string | undefined, t: Translator) {
  const labels: Record<string, UiMessageKey> = {
    chat_generation: '채팅',
    conversation_compression: '압축',
    chat_command_postprocess: '커맨드 후처리',
    tts_generation: '음성 생성',
    full_room_recompression: '전체방 재압축',
    full_room_recompression_final: '최종 재압축',
  };
  const key: UiMessageKey = labels[purpose || ''] || '기타';
  return t(key);
}
function toLocalDateInput(date: Date) {
  const offset = date.getTimezoneOffset() * 60000;
  return new Date(date.getTime() - offset).toISOString().slice(0, 10);
}
function startOfDayIso(value?: string) { return value ? `${value}T00:00:00` : undefined; }
function endOfDayIso(value?: string) { return value ? `${value}T23:59:59` : undefined; }
function defaultStart(days: number) {
  const date = new Date();
  date.setDate(date.getDate() - days + 1);
  return toLocalDateInput(date);
}
function StatCards({ usage }: { usage?: UsageSummary | null }) {
  const { locale, t } = useI18n();
  const statValue = (value: unknown) => new Intl.NumberFormat(locale).format(Number(value || 0));
  return <div className="grid grid-cols-2 gap-2 md:grid-cols-4 [&>span]:grid [&>span]:gap-0.5 [&>span]:rounded-[14px] [&>span]:border [&>span]:border-border [&>span]:bg-white/10 [&>span]:p-2.5 [&_b]:text-lg [&_b]:font-black [&_b]:text-primary [&_small]:text-xs [&_small]:text-foreground/80"><span><b>{statValue(usage?.total_calls)}</b><small>{t('호출')}</small></span><span><b>{statValue(usage?.prompt_tokens)}</b><small>{t('프롬프트 토큰')}</small></span><span><b>{statValue(usage?.completion_tokens)}</b><small>{t('응답 토큰')}</small></span><span><b>{statValue(usage?.total_tokens)}</b><small>{t('전체 토큰')}</small></span></div>;
}
function groupUsageBuckets(buckets: UsageBucket[] = []): UsageGroup[] {
  const groups = new Map<string, UsageGroup>();
  for (const bucket of buckets) {
    const provider = bucket.provider || 'unknown';
    const model = bucket.model || 'unknown';
    const key = `${provider}\n${model}`;
    const current = groups.get(key) || { provider, model, calls: 0, prompt_tokens: 0, completion_tokens: 0, total_tokens: 0, estimated_calls: 0, buckets: [] };
    current.calls += Number(bucket.calls || 0);
    current.prompt_tokens += Number(bucket.prompt_tokens || 0);
    current.completion_tokens += Number(bucket.completion_tokens || 0);
    current.total_tokens += Number(bucket.total_tokens || 0);
    current.estimated_calls += Number(bucket.estimated_calls || 0);
    current.buckets.push(bucket);
    groups.set(key, current);
  }
  return [...groups.values()].sort((a, b) => b.total_tokens - a.total_tokens);
}
function UsageBuckets({ usage }: { usage?: UsageSummary | null }) {
  const { locale, t } = useI18n();
  const statValue = (value: unknown) => new Intl.NumberFormat(locale).format(Number(value || 0));
  const groups = groupUsageBuckets(usage?.buckets || []);
  if (!groups.length) return <p className="context-muted">{t('선택한 기간의 사용량 기록이 없습니다.')}</p>;
  return <div className="grid gap-2.5">{groups.map((group) => <details className="overflow-hidden rounded-[18px] border border-[color-mix(in_srgb,var(--primary)_18%,var(--border))] bg-[color-mix(in_srgb,var(--card)_94%,var(--primary)_6%)] dark:bg-slate-900/95" key={`${group.provider}:${group.model}`}>
    <summary className="grid cursor-pointer list-none grid-cols-1 items-center gap-3 p-3.5 marker:hidden md:grid-cols-[minmax(0,1fr)_auto] [&::-webkit-details-marker]:hidden">
      <span className="grid min-w-0 grid-cols-1 items-start gap-1.5"><Badge>{group.provider}</Badge><strong className="block max-w-[min(58vw,560px)] min-w-0 overflow-hidden text-ellipsis whitespace-nowrap font-black text-primary" title={group.model}>{group.model}</strong></span>
      <span className="grid grid-cols-[auto_auto] items-baseline justify-start gap-x-2 gap-y-1 whitespace-nowrap text-foreground md:grid-cols-[auto_auto_auto_auto] md:justify-end [&_b]:font-black [&_small]:font-extrabold [&_small]:text-muted"><small>{t('토큰')}</small><b>{statValue(group.total_tokens)}</b><small>{t('호출')}</small><b>{statValue(group.calls)}</b></span>
    </summary>
    <div className="grid gap-2 px-3 pb-3">{group.buckets.map((bucket, index) => <article className="grid grid-cols-2 items-center gap-2 rounded-[14px] bg-white/70 p-2.5 text-xs text-foreground dark:bg-white/10 md:grid-cols-[minmax(96px,1.2fr)_repeat(4,minmax(80px,auto))]" key={`${bucket.purpose}:${index}`}>
      <strong className="col-span-full font-black text-primary md:col-span-1">{purposeLabel(bucket.purpose, t)}</strong>
      <span>{t('호출')} {statValue(bucket.calls)}</span>
      <span>{t('프롬프트')} {statValue(bucket.prompt_tokens)}</span>
      <span>{t('응답')} {statValue(bucket.completion_tokens)}</span>
      <span>{t('전체')} {statValue(bucket.total_tokens)}</span>
    </article>)}</div>
  </details>)}</div>;
}

export function StatisticsView({ conversations = [] }: StatisticsViewProps) {
  const { t } = useI18n();
  const [preset, setPreset] = useState<RangePreset>('30d');
  const [startDate, setStartDate] = useState(defaultStart(30));
  const [endDate, setEndDate] = useState(toLocalDateInput(new Date()));
  const [selectedConversationId, setSelectedConversationId] = useState('');
  const [globalUsage, setGlobalUsage] = useState<UsageSummary | null>(null);
  const [conversationUsage, setConversationUsage] = useState<UsageSummary | null>(null);
  const selectedConversation = conversations.find((conversation) => conversation.id === selectedConversationId);
  const query = useMemo(() => preset === 'all' ? {} : { start_at: startOfDayIso(startDate), end_at: endOfDayIso(endDate) }, [preset, startDate, endDate]);

  useEffect(() => {
    if (preset === '7d') { setStartDate(defaultStart(7)); setEndDate(toLocalDateInput(new Date())); }
    if (preset === '30d') { setStartDate(defaultStart(30)); setEndDate(toLocalDateInput(new Date())); }
    if (preset === '90d') { setStartDate(defaultStart(90)); setEndDate(toLocalDateInput(new Date())); }
  }, [preset]);

  async function loadStats(conversationId = selectedConversationId) {
    const [all, room] = await Promise.all([
      conversationApi.usageSummary(query),
      conversationId ? conversationApi.usage(conversationId, query) : Promise.resolve(null),
    ]);
    setGlobalUsage(all as UsageSummary);
    setConversationUsage(room as UsageSummary | null);
  }

  useEffect(() => { loadStats(); }, [query, selectedConversationId]);

  return <section className="panel page grid gap-3 [&_[data-slot=card-description]]:text-foreground/80 [&_[data-slot=card-title]]:text-primary" data-modernized="통계 페이지 shadcn primitive marker">
    <Card>
      <CardHeader>
        <CardTitle>{t('통계')}</CardTitle>
      </CardHeader>
      <CardContent className="grid grid-cols-1 gap-2.5 min-[820px]:grid-cols-[1.1fr_repeat(2,minmax(0,1fr))] min-[820px]:items-end [&_label]:grid [&_label]:gap-1.5 [&_label]:text-sm [&_label]:font-extrabold [&_label]:text-foreground">
        <label>{t('기간 프리셋')}<Select value={preset} onChange={(event) => setPreset(event.target.value as RangePreset)}><option value="7d">{t('최근 7일')}</option><option value="30d">{t('최근 30일')}</option><option value="90d">{t('최근 90일')}</option><option value="all">{t('전체 기간')}</option><option value="custom">{t('직접 선택')}</option></Select></label>
        <label>{t('시작일')}<Input type="date" value={startDate} disabled={preset === 'all'} onChange={(event) => { setPreset('custom'); setStartDate(event.target.value); }} /></label>
        <label>{t('종료일')}<Input type="date" value={endDate} disabled={preset === 'all'} onChange={(event) => { setPreset('custom'); setEndDate(event.target.value); }} /></label>
      </CardContent>
    </Card>

    <Card>
      <CardHeader><CardTitle>{t('전체 통계')}</CardTitle></CardHeader>
      <CardContent className="grid gap-3"><StatCards usage={globalUsage} /><UsageBuckets usage={globalUsage} /></CardContent>
    </Card>

    <Card>
      <CardHeader><CardTitle>{t('대화방별 통계')}</CardTitle></CardHeader>
      <CardContent className="grid gap-3"><label className="grid max-w-[520px] gap-1.5 text-sm font-black text-foreground">{t('대화방')}<Select value={selectedConversationId} onChange={(event) => setSelectedConversationId(event.target.value)}><option value="">{t('대화방 선택')}</option>{conversations.map((conversation) => <option key={conversation.id} value={conversation.id}>{conversation.title || conversation.id}</option>)}</Select></label>{selectedConversationId ? <><StatCards usage={conversationUsage} /><UsageBuckets usage={conversationUsage} /></> : null}</CardContent>
    </Card>
  </section>;
}
