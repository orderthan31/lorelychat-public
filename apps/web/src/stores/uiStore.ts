import { create } from 'zustand';
import { createJSONStorage, persist } from 'zustand/middleware';

export type AppTheme = 'light' | 'dark';
export type ChatViewMode = 'bubble' | 'scene';

const THEME_STORAGE_KEY = 'lorely-chat-theme';

function storedTheme(): AppTheme {
  try { return localStorage.getItem(THEME_STORAGE_KEY) === 'dark' ? 'dark' : 'light'; } catch { return 'light'; }
}

function persistTheme(theme: AppTheme) {
  try { localStorage.setItem(THEME_STORAGE_KEY, theme); } catch { /* Persistence is best effort. */ }
}

export type UiStoreState = {
  drawerOpen: boolean;
  contextDrawerOpen: boolean;
  renderChatImages: boolean;
  chatViewMode: ChatViewMode;
  theme: AppTheme;
  setDrawerOpen: (drawerOpen: boolean) => void;
  setContextDrawerOpen: (contextDrawerOpen: boolean) => void;
  setRenderChatImages: (renderChatImages: boolean) => void;
  setChatViewMode: (chatViewMode: ChatViewMode) => void;
  setTheme: (theme: AppTheme) => void;
  toggleTheme: () => void;
  closeDrawers: () => void;
};

type PersistedUiStoreState = Pick<UiStoreState, 'renderChatImages' | 'chatViewMode'>;

export const useUiStore = create<UiStoreState>()(persist<UiStoreState, [], [], PersistedUiStoreState>((set) => ({
  drawerOpen: false,
  contextDrawerOpen: false,
  renderChatImages: true,
  chatViewMode: 'bubble',
  theme: storedTheme(),
  setDrawerOpen: (drawerOpen) => set({ drawerOpen }),
  setContextDrawerOpen: (contextDrawerOpen) => set({ contextDrawerOpen }),
  setRenderChatImages: (renderChatImages) => set({ renderChatImages }),
  setChatViewMode: (chatViewMode) => set({ chatViewMode }),
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
  storage: createJSONStorage(() => ({
    getItem: (key) => { try { return localStorage.getItem(key); } catch { return null; } },
    setItem: (key, value) => { try { localStorage.setItem(key, value); } catch { /* In-memory state remains usable. */ } },
    removeItem: (key) => { try { localStorage.removeItem(key); } catch { /* Best effort. */ } },
  })),
  merge: (persisted, current) => {
    const saved = persisted as Partial<PersistedUiStoreState> | undefined;
    return { ...current, renderChatImages: typeof saved?.renderChatImages === 'boolean' ? saved.renderChatImages : true, chatViewMode: saved?.chatViewMode === 'scene' ? 'scene' : 'bubble' };
  },
  partialize: ({ renderChatImages, chatViewMode }) => ({ renderChatImages, chatViewMode }),
}));
