import { useCallback, useLayoutEffect, useRef, useState, type Dispatch, type SetStateAction } from 'react';
import type { Translator } from '../../../i18n/core';
import type { RoomWork } from '../../../utils/roomWork';

export function useMessageSelection(roomWork: RoomWork, setStatus: Dispatch<SetStateAction<string>>, t: Translator) {
  const [messageSelectionMode, setMessageSelectionMode] = useState(false);
  const [selectedMessageIds, setSelectedMessageIds] = useState<string[]>([]);
  const live = useRef({ setStatus, t });
  useLayoutEffect(() => { live.current = { setStatus, t }; });
  const instruction = useRef<string | null>(null);
  const enterMessageSelectionMode = useCallback((messageId?: string) => {
    if (!messageId) return;
    setMessageSelectionMode(true);
    setSelectedMessageIds((current) => current.includes(messageId) ? current : [...current, messageId]);
    instruction.current = live.current.t('복사하거나 삭제할 버블을 선택하세요');
    live.current.setStatus(instruction.current);
  }, []);
  const toggleMessageSelection = useCallback((messageId?: string) => {
    if (!messageId) return;
    setSelectedMessageIds((current) => current.includes(messageId) ? current.filter((id) => id !== messageId) : [...current, messageId]);
  }, []);
  const clearMessageSelection = useCallback(() => {
    setMessageSelectionMode(false);
    setSelectedMessageIds([]);
    const previousInstruction = instruction.current;
    instruction.current = null;
    // Do not erase a newer mutation result, or enqueue a status write on a new visit.
    if (previousInstruction) {
      const visit = roomWork.capture();
      live.current.setStatus((current) => visit.current && current === previousInstruction ? live.current.t('준비됨') : current);
    }
  }, [roomWork]);
  return { messageSelectionMode, selectedMessageIds, enterMessageSelectionMode, toggleMessageSelection, clearMessageSelection };
}
