// Recovery hints contain identifiers/version only, never conversation prose.
// Durable jobs remain authoritative; storage may be unavailable or corrupted.
export type TerminalJobHint = { conversationId: string; jobId: string; incomingMessageId: string; stateVersion: number };
export type UnknownSubmissionHint = { conversationId: string; requestId: string };
const UNKNOWN_KEY = 'lorechat-unknown-submissions-v1';
export function unknownSubmissionsForRoom(conversationId: string): UnknownSubmissionHint[] {
  try {
    const hints: unknown = JSON.parse(localStorage.getItem(UNKNOWN_KEY) || '[]');
    return Array.isArray(hints) ? hints.filter(hint => hint && hint.conversationId === conversationId && typeof hint.requestId === 'string').slice(-128) : [];
  } catch { return []; }
}
function updateUnknownSubmission(hint: UnknownSubmissionHint, keep: boolean) {
  try {
    const stored: unknown = JSON.parse(localStorage.getItem(UNKNOWN_KEY) || '[]');
    const hints = Array.isArray(stored) ? stored.filter(item => item && typeof item.conversationId === 'string' && typeof item.requestId === 'string' && !(item.conversationId === hint.conversationId && item.requestId === hint.requestId)) : [];
    localStorage.setItem(UNKNOWN_KEY, JSON.stringify([...hints, ...(keep ? [hint] : [])].slice(-128)));
  } catch { /* Active-job discovery still operates when storage is blocked. */ }
}
export function rememberUnknownSubmission(hint: UnknownSubmissionHint) { updateUnknownSubmission(hint, true); }
export function forgetUnknownSubmission(hint: UnknownSubmissionHint) { updateUnknownSubmission(hint, false); }

const KEY = 'lorechat-terminal-generation-jobs-v1';
function readHints(): TerminalJobHint[] {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(KEY) || '[]');
    if (!Array.isArray(value)) return [];
    return value.filter((hint): hint is TerminalJobHint => Boolean(hint) && typeof hint.conversationId === 'string' && typeof hint.jobId === 'string' && typeof hint.incomingMessageId === 'string' && Number.isInteger(hint.stateVersion) && hint.stateVersion >= 0).slice(-128);
  } catch { return []; }
}
function writeHints(hints: TerminalJobHint[]) { try { localStorage.setItem(KEY, JSON.stringify(hints.slice(-128))); } catch { /* Best effort; active discovery does not depend on storage. */ } }
export function terminalJobsForRoom(conversationId: string) { return readHints().filter(hint => hint.conversationId === conversationId); }
export function rememberTerminalJob(hint: TerminalJobHint) {
  if (!hint.incomingMessageId || !Number.isInteger(hint.stateVersion)) return;
  writeHints([...readHints().filter(item => item.jobId !== hint.jobId || item.conversationId !== hint.conversationId), hint]);
}
export function forgetTerminalJob(conversationId: string, jobId: string) { writeHints(readHints().filter(item => item.conversationId !== conversationId || item.jobId !== jobId)); }
