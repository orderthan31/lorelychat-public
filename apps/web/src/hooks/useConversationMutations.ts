import { useMutation, useQueryClient, type UseMutationResult } from '@tanstack/react-query';
import { conversationApi } from '../api/resources';
import { queryKeys } from './useBootstrapResources';

type ConversationMutation<TVariables = unknown> = UseMutationResult<unknown, Error, TVariables, unknown>;
type IdPayload = { id: string; payload: unknown };
type ConversationPayload = { conversationId: string; payload: unknown };
type MessagePayload = { conversationId: string; messageId: string };
type RegeneratePayload = MessagePayload & { replaceExisting?: boolean };
type BulkDeletePayload = { conversationId: string; messageIds: string[] };

export type ConversationMutationsState = {
  createConversation: ConversationMutation;
  updateConversation: ConversationMutation<IdPayload>;
  deleteConversation: ConversationMutation<string>;
  generateMessageTts: ConversationMutation<string>;
  sendMessage: ConversationMutation<ConversationPayload>;
  createMessageJob: ConversationMutation<ConversationPayload>;
  regenerateMessage: ConversationMutation<RegeneratePayload>;
  deleteMessage: ConversationMutation<MessagePayload>;
  bulkDeleteMessages: ConversationMutation<BulkDeletePayload>;
  saveRuntimeSetting: ConversationMutation<ConversationPayload>;
};

export function useConversationMutations(): ConversationMutationsState {
  const queryClient = useQueryClient();
  const invalidateConversations = () => queryClient.invalidateQueries({ queryKey: queryKeys.conversations });

  return {
    createConversation: useMutation({
      mutationFn: conversationApi.create,
      onSuccess: invalidateConversations,
    }),
    updateConversation: useMutation({
      mutationFn: ({ id, payload }: IdPayload) => conversationApi.update(id, payload),
      onSuccess: invalidateConversations,
    }),
    deleteConversation: useMutation({
      mutationFn: conversationApi.remove,
      onSuccess: invalidateConversations,
    }),
    generateMessageTts: useMutation({
      mutationFn: conversationApi.generateMessageTts,
    }),
    sendMessage: useMutation({
      mutationFn: ({ conversationId, payload }: ConversationPayload) => conversationApi.sendMessage(conversationId, payload),
      onSuccess: invalidateConversations,
    }),
    createMessageJob: useMutation({
      mutationFn: ({ conversationId, payload }: ConversationPayload) => conversationApi.createMessageJob(conversationId, payload),
      onSuccess: invalidateConversations,
    }),
    regenerateMessage: useMutation({
      mutationFn: ({ conversationId, messageId, replaceExisting = false }: RegeneratePayload) => conversationApi.regenerateMessage(conversationId, messageId, { replace_existing: replaceExisting }),
      onSuccess: invalidateConversations,
    }),
    deleteMessage: useMutation({
      mutationFn: ({ conversationId, messageId }: MessagePayload) => conversationApi.deleteMessage(conversationId, messageId),
      onSuccess: invalidateConversations,
    }),
    bulkDeleteMessages: useMutation({
      mutationFn: ({ conversationId, messageIds }: BulkDeletePayload) => conversationApi.bulkDeleteMessages(conversationId, messageIds),
      onSuccess: invalidateConversations,
    }),
    saveRuntimeSetting: useMutation({
      mutationFn: ({ conversationId, payload }: ConversationPayload) => conversationApi.saveRuntimeSetting(conversationId, payload),
    }),
  };
}
