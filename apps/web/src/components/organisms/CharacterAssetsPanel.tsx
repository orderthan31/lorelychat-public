import { useState, type Dispatch, type SetStateAction } from 'react';
import { Field, FormSection } from '../molecules';
import { assetUrlFor } from '../../utils/assets';
import { Badge } from '../atoms';
import { Button } from '../atoms';
import { Card } from '../atoms';
import { Input } from '../atoms';
import { Switch } from '../atoms';
import { useI18n } from '../../i18n/I18nProvider';
import { formatUiCount } from '../../i18n/core';

export type CharacterAsset = {
  id: string;
  label?: string;
  image_url?: string;
  thumbnail_url?: string;
  description?: string;
  tags?: string[];
  mood_tags?: string[];
  scene_tags?: string[];
  outfit_tags?: string[];
  pose_tags?: string[];
  expression_tags?: string[];
  priority?: number;
  enabled?: boolean;
  is_default?: boolean;
  metadata?: { asset_selection_v2?: { scope?: string; required_scene_tags?: string[]; preferred_scene_tags?: string[] } };
};

export type CharacterAssetDraft = {
  label?: string;
  image_url?: string;
  description?: string;
  tags?: string;
  mood_tags?: string;
  scene_tags?: string;
  outfit_tags?: string;
  pose_tags?: string;
  expression_tags?: string;
  priority?: number;
  enabled?: boolean;
  is_default?: boolean;
};

type CharacterAssetEditDraft = {
  label: string;
  description: string;
  tags: string;
  mood_tags: string;
  scene_tags: string;
  outfit_tags: string;
  pose_tags: string;
  expression_tags: string;
  priority: number;
};

const tagsToText = (tags?: string[]) => (tags || []).join(', ');
const textToTags = (text: string) => [...new Set(text.split(/[\n,]/).map((tag) => tag.trim().toLowerCase().replace(/\s+/g, '_')).filter(Boolean))];

export type CharacterAssetsPanelProps = {
  character?: { id?: string; name?: string } | null;
  assets?: CharacterAsset[];
  loading?: boolean;
  saving?: boolean;
  draft: CharacterAssetDraft;
  setDraft: Dispatch<SetStateAction<CharacterAssetDraft>>;
  onCreate?: () => void;
  onUpload?: (file?: File) => Promise<void> | void;
  onPatch?: (id: string, patch: Partial<CharacterAsset>) => void;
  onDelete?: (id: string) => void;
  onDefault?: (id: string) => void;
};

export function CharacterAssetsPanel({ character, assets = [], loading = false, saving = false, draft, setDraft, onCreate, onUpload, onPatch, onDelete, onDefault }: CharacterAssetsPanelProps) {
  const { locale, t } = useI18n();
  const [showCreate, setShowCreate] = useState(false);
  const [selectedAsset, setSelectedAsset] = useState<CharacterAsset | null>(null);
  const [editDraft, setEditDraft] = useState<CharacterAssetEditDraft | null>(null);
  const update = <K extends keyof CharacterAssetDraft>(key: K, value: CharacterAssetDraft[K]) => setDraft((current) => ({ ...current, [key]: value }));
  const updateEdit = <K extends keyof CharacterAssetEditDraft>(key: K, value: CharacterAssetEditDraft[K]) => setEditDraft((current) => current ? { ...current, [key]: value } : current);
  const selected = selectedAsset ? assets.find((asset) => asset.id === selectedAsset.id) || selectedAsset : null;
  const selectedTags = selected ? [...(selected.tags || []), ...(selected.mood_tags || []), ...(selected.scene_tags || []), ...(selected.outfit_tags || []), ...(selected.pose_tags || []), ...(selected.expression_tags || [])] : [];
  const openAsset = (asset: CharacterAsset) => {
    setSelectedAsset(asset);
    setEditDraft({
      label: asset.label || '',
      description: asset.description || '',
      tags: tagsToText(asset.tags),
      mood_tags: tagsToText(asset.mood_tags),
      scene_tags: tagsToText(asset.scene_tags),
      outfit_tags: tagsToText(asset.outfit_tags),
      pose_tags: tagsToText(asset.pose_tags),
      expression_tags: tagsToText(asset.expression_tags),
      priority: Number(asset.priority ?? 50),
    });
  };
  const saveSelected = () => {
    if (!selected || !editDraft?.label.trim()) return;
    onPatch?.(selected.id, {
      label: editDraft.label.trim(),
      description: editDraft.description.trim(),
      tags: textToTags(editDraft.tags),
      mood_tags: textToTags(editDraft.mood_tags),
      scene_tags: textToTags(editDraft.scene_tags),
      outfit_tags: textToTags(editDraft.outfit_tags),
      pose_tags: textToTags(editDraft.pose_tags),
      expression_tags: textToTags(editDraft.expression_tags),
      priority: Number(editDraft.priority || 50),
    });
  };
  if (!character?.id) return null;
  return <section className="grid gap-3.5" data-modernized="캐릭터 에셋 패널 shadcn primitive marker">
    <FormSection
      title={`${character.name || t('캐릭터')} · ${t('에셋 갤러리')}`}
      action={<Button type="button" className="w-auto" onClick={() => setShowCreate((value) => !value)}>{t(showCreate ? '추가 닫기' : '+ 에셋 추가')}</Button>}
      data-modernized="캐릭터 에셋 컨트롤 flat form section"
    >
      <small className="text-xs text-muted">{loading ? t('불러오는 중…') : formatUiCount(assets.length, 'items', locale)}</small>
      {showCreate && <div className="grid gap-2.5 border-t border-border pt-3">
        <Field label={t('라벨')}><Input value={draft.label || ''} onChange={(e) => update('label', e.target.value)} placeholder={t('예: 웃는 얼굴')} /></Field>
        <Field label={t('이미지 URL')}><Input value={draft.image_url || ''} onChange={(e) => update('image_url', e.target.value)} placeholder="/uploads/character-assets/..." /></Field>
        <Field label={t('설명')}><Input value={draft.description || ''} onChange={(e) => update('description', e.target.value)} placeholder={t('언제 쓰기 좋은 이미지인지')} /></Field>
        <div className="grid two compact"><Field label={t('일반 태그')}><Input value={draft.tags || ''} onChange={(e) => update('tags', e.target.value)} placeholder="profile, realistic" /></Field><Field label={t('감정 태그')}><Input value={draft.mood_tags || ''} onChange={(e) => update('mood_tags', e.target.value)} placeholder="happy, relaxed, confident" /></Field></div>
        <div className="grid two compact"><Field label={t('장면 태그')}><Input value={draft.scene_tags || ''} onChange={(e) => update('scene_tags', e.target.value)} placeholder="indoor, cafe, beach" /></Field><Field label={t('복장 태그')}><Input value={draft.outfit_tags || ''} onChange={(e) => update('outfit_tags', e.target.value)} placeholder="casual, officewear, swimsuit" /></Field></div>
        <div className="grid two compact"><Field label={t('포즈 태그')}><Input value={draft.pose_tags || ''} onChange={(e) => update('pose_tags', e.target.value)} placeholder="sitting, standing, holding_object" /></Field><Field label={t('표정 태그')}><Input value={draft.expression_tags || ''} onChange={(e) => update('expression_tags', e.target.value)} placeholder="smile, serious, shy" /></Field></div>
        <Field label={t('우선순위')}><Input type="number" min="0" max="100" value={draft.priority || 50} onChange={(e) => update('priority', Number(e.target.value || 50))} /></Field>
        <div className="flex flex-wrap items-center gap-2.5 [&>button]:w-auto">
          <Switch label={t('활성화')} checked={draft.enabled !== false} onChange={(e) => update('enabled', e.target.checked)} />
          <Switch label={t('기본 프사')} checked={!!draft.is_default} onChange={(e) => update('is_default', e.target.checked)} />
          <Button type="button" disabled={saving || !draft.label?.trim() || !draft.image_url?.trim()} onClick={() => onCreate?.()}>{t('URL 에셋 추가')}</Button>
        </div>
        <Field label={t('파일 업로드')}><Input type="file" accept="image/png,image/jpeg,image/webp,image/gif" disabled={saving} onChange={async (e) => { await onUpload?.(e.target.files?.[0] || undefined); e.target.value = ''; }} /></Field>
      </div>}
    </FormSection>
    <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5">{assets.map((asset) => <Button type="button" variant="ghost" className="group relative aspect-square min-h-0 w-full overflow-hidden !rounded-none border border-border bg-white/[.055] p-0 shadow-[0_10px_24px_rgba(0,0,0,.2)]" key={asset.id} onClick={() => openAsset(asset)}>
      <img className="block h-full w-full !rounded-none object-cover transition-transform duration-200 group-hover:scale-[1.04]" src={assetUrlFor(asset.thumbnail_url || asset.image_url)} alt={asset.label || t('캐릭터 에셋')} />
      <span className="absolute inset-x-0 bottom-0 overflow-hidden text-ellipsis whitespace-nowrap bg-[linear-gradient(180deg,transparent,rgba(5,8,16,.82))] px-2 pb-2 pt-5 text-left text-xs leading-tight text-white [text-shadow:0_1px_8px_rgba(0,0,0,.8)]">{asset.label}{asset.is_default ? ` · ${t('기본')}` : ''}</span>
    </Button>)}</div>
    {selected && <div className="fixed inset-0 z-[90] grid place-items-center p-4" role="dialog" aria-modal="true">
      <div className="fixed inset-0 bg-black/70 backdrop-blur-[10px]" aria-hidden="true" onClick={() => setSelectedAsset(null)} />
      <Card className="relative z-[1] grid max-h-[92dvh] w-[min(94vw,880px)] grid-cols-1 overflow-auto rounded-3xl border border-border bg-[#10172b] shadow-[0_30px_90px_rgba(0,0,0,.5)] md:grid-cols-[minmax(0,1.25fr)_minmax(280px,.75fr)]">
        <Button type="button" variant="ghost" size="icon" className="gallery-preview-close absolute right-3 top-3 z-[2] size-11 min-h-11 rounded-full border border-white/20 bg-black/45 text-2xl leading-none text-white shadow-[0_10px_28px_rgba(0,0,0,.38)] backdrop-blur-md hover:bg-black/60 hover:text-white" aria-label={t('에셋 상세 닫기')} onClick={() => setSelectedAsset(null)}>×</Button>
        <img className="h-full min-h-[360px] w-full max-h-[92dvh] object-contain bg-black/30" src={assetUrlFor(selected.image_url)} alt={selected.label || t('캐릭터 에셋')} />
        <div className="grid content-start gap-3 p-4.5">
          <h3 className="m-0 text-[22px]">{selected.label}{selected.is_default ? ` · ${t('기본')}` : ''}</h3>
          <p className="m-0 leading-snug text-muted">{selected.description || selected.image_url}</p>
          <div className="flex flex-wrap gap-2"><span className="rounded-full border border-border bg-white/10 px-2.5 py-1 text-xs text-[#dfe7ff]">{t(selected.enabled ? '활성' : '비활성')}</span><span className="rounded-full border border-border bg-white/10 px-2.5 py-1 text-xs text-[#dfe7ff]">{t('우선순위')} {selected.priority}</span><span className="rounded-full border border-border bg-white/10 px-2.5 py-1 text-xs text-[#dfe7ff]">{selected.metadata?.asset_selection_v2?.scope || t('미분류')}</span></div>
          <div className="flex flex-wrap gap-1.5">{selectedTags.length ? selectedTags.map((tag, index) => <Badge key={`${tag}-${index}`}>{tag}</Badge>) : <Badge>{t('태그 없음')}</Badge>}</div>
          {editDraft && <div className="grid gap-2 rounded-2xl border border-border bg-black/15 p-3">
            <Field label={t('라벨')}><Input value={editDraft.label} onChange={(e) => updateEdit('label', e.target.value)} /></Field>
            <Field label={t('설명')}><Input value={editDraft.description} onChange={(e) => updateEdit('description', e.target.value)} /></Field>
            <Field label={t('일반 태그')}><Input value={editDraft.tags} onChange={(e) => updateEdit('tags', e.target.value)} placeholder="profile, realistic" /></Field>
            <Field label={t('감정 태그')}><Input value={editDraft.mood_tags} onChange={(e) => updateEdit('mood_tags', e.target.value)} placeholder="happy, relaxed, confident" /></Field>
            <Field label={t('장면 태그')}><Input value={editDraft.scene_tags} onChange={(e) => updateEdit('scene_tags', e.target.value)} placeholder="indoor, cafe, beach" /></Field>
            <Field label={t('복장 태그')}><Input value={editDraft.outfit_tags} onChange={(e) => updateEdit('outfit_tags', e.target.value)} placeholder="casual, officewear, swimsuit" /></Field>
            <Field label={t('포즈 태그')}><Input value={editDraft.pose_tags} onChange={(e) => updateEdit('pose_tags', e.target.value)} placeholder="sitting, standing, holding_object" /></Field>
            <Field label={t('표정 태그')}><Input value={editDraft.expression_tags} onChange={(e) => updateEdit('expression_tags', e.target.value)} placeholder="smile, serious, shy" /></Field>
            <Field label={t('우선순위')}><Input type="number" min="0" max="100" value={editDraft.priority} onChange={(e) => updateEdit('priority', Number(e.target.value || 50))} /></Field>
            <Button type="button" disabled={saving || !editDraft.label.trim()} onClick={saveSelected}>{t('태그와 정보 저장')}</Button>
          </div>}
          <div className="flex gap-1.5 p-0 [&>button]:min-h-9 [&>button]:flex-1 [&>button]:px-2 [&>button]:py-1.5 [&>button]:text-xs"><Button type="button" variant="ghost" size="sm" disabled={saving || selected.is_default} onClick={() => onDefault?.(selected.id)}>{t('기본 이미지')}</Button><Button type="button" variant="ghost" size="sm" disabled={saving} onClick={() => onPatch?.(selected.id, { enabled: !selected.enabled })}>{t(selected.enabled ? '비활성화' : '활성화')}</Button><Button type="button" variant="destructive" size="sm" disabled={saving} onClick={() => { onDelete?.(selected.id); setSelectedAsset(null); setEditDraft(null); }}>{t('삭제')}</Button></div>
        </div>
      </Card>
    </div>}
  </section>;
}
