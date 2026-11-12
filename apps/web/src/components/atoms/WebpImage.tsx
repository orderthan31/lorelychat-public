import { useOptionalI18n } from '../../i18n/I18nProvider';
import { translateUiText } from '../../i18n/core';
import { forwardRef, useEffect, useRef, useState, type ImgHTMLAttributes } from 'react';
import { imageUrlFor, isDirectImageUrl, prepareImageUrl, type ImageVariant } from '../../utils/webpImages';

export type WebpImageProps = ImgHTMLAttributes<HTMLImageElement> & { variant?: ImageVariant };
export const IMAGE_LOAD_TIMEOUT_MS = 20_000;

/** An actual img preserves layout, refs and native accessibility without a provider. */
export const WebpImage = forwardRef<HTMLImageElement, WebpImageProps>(function WebpImage(props, ref) {
  return <OwnedWebpImage key={`${props.variant || 'thumbnail'}:${props.src || ''}`} {...props} ref={ref} />;
});

const OwnedWebpImage = forwardRef<HTMLImageElement, WebpImageProps>(function OwnedWebpImage(
  { src, variant = 'thumbnail', srcSet: _srcSet, onLoad, onError, ...props }, ref,
) {
  const i18n = useOptionalI18n();
  const unavailable = translateUiText('이미지를 불러올 수 없어요', i18n?.locale || 'en');
  const source = typeof src === 'string' ? src.trim() : '';
  const candidate = imageUrlFor(source, variant);
  const direct = isDirectImageUrl(candidate) ? candidate : '';
  const identity = `${variant}:${source}`;
  const [prepared, setPrepared] = useState<{ identity: string; url: string; failed: boolean }>();
  const [load, setLoad] = useState<{ identity: string; state: 'ready' | 'error' }>();
  const owner = useRef(0);
  // A changed source can never show the previous source while its effect catches up.
  const current = prepared?.identity === identity ? prepared : undefined;
  const url = direct || current?.url || '';
  const status = !source || current?.failed || (load?.identity === identity && load.state === 'error')
    ? 'error' : load?.identity === identity && load.state === 'ready' ? 'ready' : 'loading';

  useEffect(() => {
    const visit = ++owner.current;
    if (!source || direct) return;
    let alive = true;
    prepareImageUrl(candidate, variant).then(
      value => { if (alive && owner.current === visit) setPrepared({ identity, url: value, failed: false }); },
      () => { if (alive && owner.current === visit) setPrepared({ identity, url: '', failed: true }); },
    );
    return () => { alive = false; };
  }, [candidate, direct, identity, source, variant]);

  useEffect(() => {
    if (!url) return;
    const visit = owner.current;
    const timer = setTimeout(() => {
      if (owner.current === visit) setLoad(previous => previous?.identity === identity ? previous : { identity, state: 'error' });
    }, IMAGE_LOAD_TIMEOUT_MS);
    return () => clearTimeout(timer);
  }, [identity, url]);

  return <img {...props} ref={ref}
    alt={status === 'error' ? [props.alt, unavailable].filter(Boolean).join(' — ') : props.alt}
    title={status === 'error' ? unavailable : props.title}
    src={status === 'error' ? undefined : url || undefined}
    data-image-state={status} aria-busy={status === 'loading' || undefined}
    onLoad={event => { setLoad({ identity, state: 'ready' }); onLoad?.(event); }}
    onError={event => { setLoad({ identity, state: 'error' }); onError?.(event); }} />;
});
