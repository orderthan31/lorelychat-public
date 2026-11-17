import type { QueryClient, QueryKey } from '@tanstack/react-query';

/** The write is already committed. Surface refresh failures without suggesting
 * another write, and use the existing active observers for exactly one refresh. */
export async function refreshAfterMutation(queryClient: QueryClient, queryKey: QueryKey): Promise<void> {
  try {
    await queryClient.invalidateQueries({ queryKey }, { throwOnError: true });
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    throw new Error(`Mutation completed; refreshing the list failed: ${message}. Reload instead of submitting again.`);
  }
}
