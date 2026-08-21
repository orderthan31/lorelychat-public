import { type Dispatch, type SetStateAction, useState } from 'react';

export type PreviewModalState = {
  avatarPreview: unknown;
  setAvatarPreview: Dispatch<SetStateAction<unknown>>;
  cardPreview: unknown;
  setCardPreview: Dispatch<SetStateAction<unknown>>;
};

export function usePreviewModals(): PreviewModalState {
  const [avatarPreview, setAvatarPreview] = useState<unknown>(null);
  const [cardPreview, setCardPreview] = useState<unknown>(null);

  return {
    avatarPreview,
    setAvatarPreview,
    cardPreview,
    setCardPreview,
  };
}
