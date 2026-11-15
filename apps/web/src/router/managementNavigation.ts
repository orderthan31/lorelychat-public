export const MANAGEMENT_NAV_ITEMS = [
  { page: 'chatCommands', path: '/chat-commands', label: '커맨드' },
  { page: 'presets', path: '/presets', label: '프리셋' },

] as const;

export function isManagementRoute(currentPage: string, page: string) {
  if (page === 'chatCommands') return currentPage.startsWith('chatCommand');
  if (page === 'presets') return currentPage.startsWith('preset');
  return currentPage === page;
}
