import { create } from 'zustand';
import { createJSONStorage, persist } from 'zustand/middleware';

export type AppTheme = 'light' | 'dark';

const THEME_STORAGE_KEY = 'lorely-chat-theme';

function storedTheme(): AppTheme {
  if (typeof localStorage === 'undefined') return 'light';
  return localStorage.getItem(THEME_STORAGE_KEY) === 'dark' ? 'dark' : 'light';
}

function persistTheme(theme: AppTheme) {
  if (typeof localStorage !== 'undefined') localStorage.setItem(THEME_STORAGE_KEY, theme);
}

export type UiStoreState = {
  drawerOpen: boolean;
  contextDrawerOpen: boolean;
  renderChatImages: boolean;
  theme: AppTheme;
  setDrawerOpen: (drawerOpen: boolean) => void;
  setContextDrawerOpen: (contextDrawerOpen: boolean) => void;
  setRenderChatImages: (renderChatImages: boolean) => void;
  setTheme: (theme: AppTheme) => void;
  toggleTheme: () => void;
  closeDrawers: () => void;
};

type PersistedUiStoreState = Pick<UiStoreState, 'renderChatImages'>;

export const useUiStore = create<UiStoreState>()(persist<UiStoreState, [], [], PersistedUiStoreState>((set) => ({
  drawerOpen: false,
  contextDrawerOpen: false,
  renderChatImages: true,
  theme: storedTheme(),
  setDrawerOpen: (drawerOpen) => set({ drawerOpen }),
  setContextDrawerOpen: (contextDrawerOpen) => set({ contextDrawerOpen }),
  setRenderChatImages: (renderChatImages) => set({ renderChatImages }),
  setTheme: (theme) => {
    persistTheme(theme);
    set({ theme });
  },
  toggleTheme: () => set((state) => {
    const theme = state.theme === 'dark' ? 'light' : 'dark';
    persistTheme(theme);
    return { theme };
  }),
  closeDrawers: () => set({ drawerOpen: false, contextDrawerOpen: false }),
}), {
  name: 'character-runtime-ui',
  storage: createJSONStorage(() => localStorage),
  partialize: (state) => ({ renderChatImages: state.renderChatImages }),
}));
