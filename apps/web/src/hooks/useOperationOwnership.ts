import { useRef, type Dispatch, type SetStateAction } from 'react';
import type { RoomWork } from '../utils/roomWork';

type Setter<T> = Dispatch<SetStateAction<T>>;
type Callbacks = Record<string, (...args: any[]) => unknown>;
export type GuardUI = <T extends Callbacks>(operation: string, setters: T) => T & { setBusy: Setter<boolean>; setStatus: Setter<string> };

/** One route visit/operation epoch owns feedback, including late finally handlers.
 * Domain controllers supply only their own setters; the busy token is shared. */
export function useOperationOwnership(routeWork: RoomWork, setBusy: Setter<boolean>, setStatus: Setter<string>): GuardUI {
  const versions = useRef(new Map<string, symbol>()).current;
  const busyOwner = useRef<symbol | null>(null);
  return (operation, domainSetters) => {
    const visit = routeWork.capture();
    const token = Symbol(operation); versions.set(operation, token);
    const current = () => visit.current && versions.get(operation) === token;
    const setters = { ...domainSetters, setBusy, setStatus };
    return Object.fromEntries(Object.entries(setters).map(([name, setter]) => [name, (...args: unknown[]) => {
      if (!current()) return;
      if (name === 'setBusy') {
        if (args[0]) busyOwner.current = token;
        else if (busyOwner.current !== token) return;
      }
      return Reflect.apply(setter, undefined, args);
    }])) as typeof setters;
  };
}
