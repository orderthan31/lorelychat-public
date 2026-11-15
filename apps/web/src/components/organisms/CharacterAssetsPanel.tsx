import { WebpImage } from '../atoms/WebpImage';
import { useEffect, useRef, useState, type Dispatch, type SetStateAction } from 'react';
import { useMediaMatch } from '../../hooks/useMediaMatch';
import { Field, FormSection } from '../molecules';
import { imageUrlFor } from '../../utils/assets';
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
  const wideInspector = useMediaMatch('(min-width: 1100px)');
  const dialogRef = useRef<HTMLDivElement>(null);
  const returnFocus = useRef<HTMLElement | null>(null);
  useEffect(() => {
    if (!selectedAsset || wideInspector) return;
    const previousOverflow = document.documentElement.style.overflow;
    document.documentElement.style.overflow = 'hidden';
    const dialog = dialogRef.current;
    const focusable = () => [...(dialog?.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), summary') || [])].filter(node => node.getClientRects().length > 0);
    focusable()[0]?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (document.querySelector('.swal2-container')) return;
      if (event.key === 'Escape') { event.preventDefault(); setSelectedAsset(null); }
      if (event.key === 'Tab') {
        const nodes = focusable(), first = nodes[0], last = nodes[nodes.length - 1];
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
      }
    };
    document.addEventListener('keydown', onKeyDown);
    return () => { document.removeEventListener('keydown', onKeyDown); document.documentElement.style.overflow = previousOverflow; if (returnFocus.current?.isConnected) returnFocus.current.focus(); };
  }, [selectedAsset?.id, wideInspector]);
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
  return <section className="asset-workspace grid gap-3.5" data-asset-inspector={wideInspector && !!selectedAsset} data-modernized="캐릭터 에셋 패널 design-system primitive marker">
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
    <div className="asset-grid grid grid-cols-2 gap-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5">{assets.map((asset) => <Button type="button" variant="ghost" className="group relative aspect-square min-h-0 w-full overflow-hidden !rounded-none border border-border bg-white/[.055] p-0 shadow-[0_10px_24px_rgba(0,0,0,.2)]" key={asset.id} onClick={(event) => { returnFocus.current = event.currentTarget; openAsset(asset); }}>
      <WebpImage className="block h-full w-full !rounded-none object-cover transition-transform duration-200 group-hover:scale-[1.04]" src={imageUrlFor(asset.thumbnail_url || asset.image_url)} alt={asset.label || t('캐릭터 에셋')} />
      <span className="absolute inset-x-0 bottom-0 overflow-hidden text-ellipsis whitespace-nowrap bg-[linear-gradient(180deg,transparent,rgba(5,8,16,.82))] px-2 pb-2 pt-5 text-left text-xs leading-tight text-white [text-shadow:0_1px_8px_rgba(0,0,0,.8)]">{asset.label}{asset.is_default ? ` · ${t('기본')}` : ''}</span>
    </Button>)}</div>
    {selected && <div ref={dialogRef} className={wideInspector ? 'asset-inspector' : 'fixed inset-0 z-[90] grid place-items-center p-4'} role={wideInspector ? 'region' : 'dialog'} aria-label={selected.label || t('캐릭터 에셋')} aria-modal={wideInspector ? undefined : true}>
      {!wideInspector && <div className="fixed inset-0 bg-black/70 backdrop-blur-[10px]" aria-hidden="true" onClick={() => setSelectedAsset(null)} />}
      <Card className={wideInspector ? 'asset-inspector-card' : 'asset-dialog-card'}>
        <Button type="button" variant="ghost" size="icon" className="gallery-preview-close absolute right-3 top-3 z-[2] size-11 min-h-11 rounded-full border border-white/20 bg-black/45 text-2xl leading-none text-white shadow-[0_10px_28px_rgba(0,0,0,.38)] backdrop-blur-md hover:bg-black/60 hover:text-white" aria-label={t('에셋 상세 닫기')} onClick={() => setSelectedAsset(null)}>×</Button>
        <WebpImage variant="full" className="asset-preview-image w-full object-contain" src={imageUrlFor(selected.image_url, 'full')} alt={selected.label || t('캐릭터 에셋')} />
        <div className="asset-inspector-details grid content-start gap-3">
          <h3 className="m-0 text-[22px]">{selected.label}{selected.is_default ? ` · ${t('기본')}` : ''}</h3>
          {!editDraft && <p className="m-0 leading-snug text-muted">{selected.description || selected.image_url}</p>}
          <div className="flex flex-wrap gap-2 text-xs text-muted-foreground"><span>{t('우선순위')} {selected.priority}</span><span>{selected.metadata?.asset_selection_v2?.scope || t('미분류')}</span></div>
          <div className="flex flex-wrap gap-1.5">{selectedTags.length ? selectedTags.map((tag, index) => <Badge key={`${tag}-${index}`}>{tag}</Badge>) : <Badge>{t('태그 없음')}</Badge>}</div>
          {editDraft && <div className="asset-editor-fields grid gap-2">
            <Field label={t('라벨')}><Input value={editDraft.label} onChange={(e) => updateEdit('label', e.target.value)} /></Field>
            <Field label={t('설명')}><Input value={editDraft.description} onChange={(e) => updateEdit('description', e.target.value)} /></Field>
            <Field label={t('일반 태그')}><Input value={editDraft.tags} onChange={(e) => updateEdit('tags', e.target.value)} placeholder="profile, realistic" /></Field>
            <Field label={t('감정 태그')}><Input value={editDraft.mood_tags} onChange={(e) => updateEdit('mood_tags', e.target.value)} placeholder="happy, relaxed, confident" /></Field>
            <Field label={t('장면 태그')}><Input value={editDraft.scene_tags} onChange={(e) => updateEdit('scene_tags', e.target.value)} placeholder="indoor, cafe, beach" /></Field>
            <Field label={t('복장 태그')}><Input value={editDraft.outfit_tags} onChange={(e) => updateEdit('outfit_tags', e.target.value)} placeholder="casual, officewear, swimsuit" /></Field>
            <Field label={t('포즈 태그')}><Input value={editDraft.pose_tags} onChange={(e) => updateEdit('pose_tags', e.target.value)} placeholder="sitting, standing, holding_object" /></Field>
            <Field label={t('표정 태그')}><Input value={editDraft.expression_tags} onChange={(e) => updateEdit('expression_tags', e.target.value)} placeholder="smile, serious, shy" /></Field>
            <Field label={t('우선순위')}><Input type="number" min="0" max="100" value={editDraft.priority} onChange={(e) => updateEdit('priority', Number(e.target.value || 50))} /></Field>

          </div>}
          <div className="asset-management-actions"><Switch label={t('활성')} checked={!!selected.enabled} disabled={saving} onChange={() => onPatch?.(selected.id, { enabled: !selected.enabled })}/><Button type="button" variant="ghost" size="sm" disabled={saving || selected.is_default} onClick={() => onDefault?.(selected.id)}>{t('기본 이미지')}</Button><Button type="button" variant="destructive" size="sm" disabled={saving} onClick={() => { onDelete?.(selected.id); setSelectedAsset(null); setEditDraft(null); }}>{t('삭제')}</Button></div>
        </div>
        {editDraft && <div className="asset-save-bar"><Button type="button" className="asset-save-action" disabled={saving || !editDraft.label.trim()} onClick={saveSelected}>{t('태그와 정보 저장')}</Button></div>}
      </Card>
    </div>}
  </section>;
}
