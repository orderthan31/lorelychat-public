import type { Dispatch, SetStateAction } from 'react';
import type { BubbleListProps } from '../../components/organisms/BubbleList';
import type { RuntimeSettingValue } from '../../components/organisms/RuntimeSettings';
import type { BattleState } from '../../components/organisms/ConversationInfoDrawer';
import type { ChatCommandItem } from '../../types/chatCommands';
import type { ConversationComposerOptions, ConversationComposerState } from './hooks/useConversationComposer';

type Setter<T> = Dispatch<SetStateAction<T>>;
export type ChatConversation = NonNullable<BubbleListProps['conversation']> & {
  genre_mode?: string;
  active_command_ids?: string[];
  active_command_id?: string;
  tts_enabled?: boolean;
};
export type BattleControlDraft = {
  action: string;
  advantage?: number;
  progress_balance?: number;
  current_phase?: string;
  participant_a_id?: string;
  participant_b_id?: string;
  winner_id?: string;
  decisive_moment?: string;
};
export type ChatBattleState = Omit<BattleState, 'active_match'> & {
  active_match?: NonNullable<BattleState['active_match']> & {
    id?: string;
    participant_a_id?: string;
    participant_b_id?: string;
  };
};
export type BattleControlsProps = {
  conversation: ChatConversation | null;
  battleState?: ChatBattleState | null;
  battleControlDraft?: BattleControlDraft;
  setBattleControlDraft: Setter<BattleControlDraft>;
};
export type ConversationComposerProps = ConversationComposerOptions & ConversationComposerState & {
  conversation: ChatConversation | null;
  setConversation: Setter<ChatConversation | null>;
  busy: boolean;
  speakerId: string;
  setSpeakerId: Setter<string>;
  speakerOptions: string[];
  participantName: (id?: string) => string;
  chatSettingsOpen: boolean;
  setChatSettingsOpen: Setter<boolean>;
  conversationRuntimeSetting: RuntimeSettingValue;
  setConversationRuntimeSetting: (setting: RuntimeSettingValue) => void;
  saveConversationRuntimeSetting: () => void;
  renderChatImages: boolean;
  setRenderChatImages: (value: boolean) => void;
  compressConversationNow: () => void;
  setCompose: Setter<string>;
  composeExpanded: boolean;
  setComposeExpanded: Setter<boolean>;
  chatCommands?: ChatCommandItem[];
};
export type ConversationDetailViewProps = ConversationComposerProps & BattleControlsProps & {
  selectedConversationId: string;
  status: string;
  navigate: (path: string) => void;
  openConversationContextDrawer: () => void;
  messageSelectionMode: boolean;
  selectedMessageIds: string[];
  clearMessageSelection: () => void;
  deleteSelectedMessages: () => void;
  messages: BubbleListProps['messages'];
  characters: BubbleListProps['characters'];
  participants: BubbleListProps['participants'];
  setAvatarPreview: BubbleListProps['onAvatarPreview'];
  chatEndRef: BubbleListProps['bottomRef'];
  chatThreadRef: BubbleListProps['threadRef'];
  loadOlderMessages: BubbleListProps['onLoadOlderMessages'];
  hasOlderMessages: boolean;
  loadingOlderMessages: boolean;
  olderPagingReady: boolean;
  retryMessage: BubbleListProps['onRetryMessage'];
  generateMessageTts: BubbleListProps['onGenerateTts'];
  regenerateFromMessage: BubbleListProps['onRegenerateMessage'];
  deleteMessageBubble: BubbleListProps['onDeleteMessage'];
  updateMessageBubble: BubbleListProps['onUpdateMessage'];
  enterMessageSelectionMode: BubbleListProps['onEnterSelectionMode'];
  toggleMessageSelection: BubbleListProps['onToggleMessageSelection'];
  setShowJumpToLatest: (away: boolean) => void;
  showJumpToLatest: boolean;
  scrollToLatest: () => void;
  clearActiveCommand?: (commandId?: string) => void;
};
