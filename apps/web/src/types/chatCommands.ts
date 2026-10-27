export type PostprocessBlockType = 'text' | 'note' | 'table' | 'comments' | 'commentary' | 'checklist' | 'timeline' | 'vote' | 'cards';
export type PostprocessContextOption = 'scene' | 'world' | 'characters' | 'relationship_memory' | 'recent_messages' | 'turn_messages';

export type ChatCommandItem = {
  id?: string;
  name: string;
  display_name?: string;
  description?: string;
  prompt?: string;
  generation_prompt?: string;
  postprocess_prompt?: string;
  postprocess_target?: 'first_bubble' | 'last_bubble' | 'all_bubbles';
  postprocess_probability?: number;
  postprocess_context_options?: PostprocessContextOption[];
  postprocess_block_types?: PostprocessBlockType[];
  enabled?: boolean;
  priority?: number;
};

export const DEFAULT_POSTPROCESS_CONTEXT_OPTIONS: PostprocessContextOption[] = ['scene', 'world', 'turn_messages'];

const emptyDraft: ChatCommandItem = {
  name: '', display_name: '', description: '', prompt: '', generation_prompt: '', postprocess_prompt: '',
  postprocess_target: 'last_bubble', postprocess_probability: 100,
  postprocess_context_options: DEFAULT_POSTPROCESS_CONTEXT_OPTIONS,
  postprocess_block_types: [], enabled: true, priority: 0,
};

export function normalizeChatCommandDraft(command?: ChatCommandItem): ChatCommandItem {
  return {
    ...emptyDraft,
    ...(command || {}),
    // Missing legacy fields and explicit [] both mean all types. Preserve unknown
    // server values as well: the server, not this selector, validates the contract.
    postprocess_block_types: [...(command?.postprocess_block_types ?? [])],
  };
}

export function chatCommandSavePayload(draft: ChatCommandItem): ChatCommandItem {
  const generationPrompt = draft.generation_prompt || '';
  const postprocessPrompt = draft.postprocess_prompt || draft.prompt || '';
  return {
    ...draft,
    name: draft.name.trim().replace(/^!/, ''),
    display_name: draft.name.trim().replace(/^!/, ''),
    description: draft.description || '',
    prompt: postprocessPrompt || generationPrompt,
    generation_prompt: generationPrompt,
    postprocess_prompt: postprocessPrompt,
    postprocess_target: draft.postprocess_target || 'last_bubble',
    postprocess_probability: Math.max(0, Math.min(100, Number(draft.postprocess_probability ?? 100))),
    postprocess_context_options: Array.isArray(draft.postprocess_context_options) ? draft.postprocess_context_options : DEFAULT_POSTPROCESS_CONTEXT_OPTIONS,
    postprocess_block_types: [...(draft.postprocess_block_types ?? [])],
    priority: Number(draft.priority || 0),
    enabled: draft.enabled !== false,
  };
}
