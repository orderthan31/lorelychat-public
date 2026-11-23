import { useEffect, useRef, useState } from 'react';
import { GalleryHorizontal, List } from 'lucide-react';
import { Button } from '../../components/atoms';
import { WebpImage } from '../../components/atoms/WebpImage';
import { sceneAssetSource, type MessageAsset } from '../../components/organisms/BubbleList';
import { useI18n } from '../../i18n/I18nProvider';
import { useUiStore } from '../../stores/uiStore';

export function SceneModeControl() {
  const { t } = useI18n();
  const mode = useUiStore(state => state.chatViewMode);
  const select = useUiStore(state => state.setChatViewMode);
  const label = t(mode === 'scene' ? 'chat.bubbleView' : 'chat.sceneView');
  return <Button type="button" variant="ghost" size="icon" className="chat-header-button" data-chat-mode={mode} title={label} aria-label={label} onClick={() => select(mode === 'scene' ? 'bubble' : 'scene')}>
    {mode === 'scene' ? <GalleryHorizontal size={18} aria-hidden="true" /> : <List size={18} aria-hidden="true" />}
  </Button>;
}

export function useSceneArtwork(background?: MessageAsset, room?: string) {
  const [preview, setPreview] = useState<{ asset: MessageAsset; room?: string }>();
  const shown = background && preview?.room === room ? preview?.asset : undefined;
  return { background, preview: shown, show: (asset: MessageAsset) => setPreview({ asset, room }), close: () => setPreview(undefined) };
}

export function SceneArtwork({ background, preview, close }: ReturnType<typeof useSceneArtwork>) {
  const { t } = useI18n();
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const node = dialog.current;
    if (!node || !preview) return;
    node.showModal();
    return () => node.close();
  }, [preview]);
  const dismiss = () => {
    // Close while the dialog is still connected so native focus restoration runs.
    dialog.current?.close();
    close();
  };
  return <>
    {background && <WebpImage className="scene-art-background" src={sceneAssetSource(background)} variant="full" alt="" />}
    {preview && <dialog ref={dialog} className="scene-art-full" aria-label={t('chat.viewArtwork')} onCancel={event => { event.preventDefault(); dismiss(); }}>
      <button type="button" className="scene-art-dismiss" onClick={dismiss} aria-label={t('chat.showChat')}>
        <WebpImage src={sceneAssetSource(preview)} variant="full" alt={preview.label || t('상황별 이미지')} />
        <span>{t('chat.showChat')}</span>
      </button>
    </dialog>}
  </>;
}
