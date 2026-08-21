import { Button } from '../atoms';
import { Surface } from '../atoms';

export type ChatImageFrameProps = {
  src: string;
  alt: string;
  onClick?: () => void;
};

export function ChatImageFrame({ src, alt, onClick }: ChatImageFrameProps) {
  return <Surface variant="mediaSquare" className="chat-image-frame overflow-hidden rounded-none p-0" data-image-shape="square">
    <Button type="button" variant="ghost" className="bubble-asset-button block w-full rounded-none border-0 bg-transparent p-0 shadow-none hover:bg-transparent" onClick={onClick}>
      <img className="bubble-asset-image block w-full rounded-none object-cover" src={src} alt={alt} />
    </Button>
  </Surface>;
}
