import { useState } from 'react';
import { createRoot } from 'react-dom/client';
import { WebpImage, type WebpImageProps } from '../src/components/atoms/WebpImage';
import { ChatImageFrame } from '../src/components/molecules/ChatImageFrame';
import { AvatarPreviewModal } from '../src/components/organisms/AvatarPreviewModal';
import { I18nProvider } from '../src/i18n/I18nProvider';
import * as assets from '../src/utils/assets';
import * as images from '../src/utils/webpImages';

type Item = WebpImageProps & { id: string };
function Fixture() {
  const [items, setItems] = useState<Item[]>([]);
  const [surface, setSurface] = useState('');
  Object.assign(window, { imageFixture: { setItems, setSurface, assets, images } });
  return <><div id="items">{items.map(({ id, ...props }) => <WebpImage key={id} {...props}
    id={id} ref={node => { (window as any).imageRefs[id] = node; }}
    onLoad={() => (window as any).imageEvents.push(['load', id])}
    onError={() => (window as any).imageEvents.push(['error', id])}
    onClick={() => (window as any).imageEvents.push(['click', id])}
  />)}</div>{surface && <I18nProvider><ChatImageFrame src={surface} alt="Chat asset" /><AvatarPreviewModal preview={{ src: surface, character: { name: 'Preview' } }} /></I18nProvider>}</>;
}
Object.assign(window, { imageEvents: [], imageRefs: {} });
createRoot(document.getElementById('root')!).render(<Fixture />);
