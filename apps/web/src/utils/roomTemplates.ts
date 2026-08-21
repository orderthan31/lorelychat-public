export type RoomTemplateDraft = never;

export function roomTemplateFromContent() {
  return { template: null, error: new Error('room templates are deprecated') };
}

export function serializeRoomTemplate(): string {
  return '{}';
}
