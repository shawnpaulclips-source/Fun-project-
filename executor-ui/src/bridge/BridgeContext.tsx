import React, { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import { useStore } from '../state/store';
import { newId } from '../state/ids';
import { createBridge } from './bridges';
import type { BridgeStatus, ExecutorBridge } from './types';

export const STATUS_LABEL: Record<BridgeStatus, string> = {
  detached: 'Detached',
  connecting: 'Connecting',
  attached: 'Attached',
  error: 'Error',
};

export interface BridgeApi {
  status: BridgeStatus;
  detail?: string;
  backendName: string;
  attach: () => Promise<void>;
  detach: () => void;
  execute: (source: string, chunkName: string) => Promise<void>;
}

const BridgeContext = createContext<BridgeApi | null>(null);

export function BridgeProvider({ url, children }: { url: string; children: ReactNode }) {
  const { dispatch } = useStore();
  const bridge: ExecutorBridge = useMemo(() => createBridge(url), [url]);
  const [status, setStatus] = useState<BridgeStatus>(bridge.getStatus());
  const [detail, setDetail] = useState<string | undefined>(undefined);

  useEffect(() => {
    setStatus(bridge.getStatus());
    setDetail(undefined);

    const unsubscribe = bridge.subscribe((event) => {
      if (event.kind === 'status') {
        setStatus(event.status);
        setDetail(event.detail);
        return;
      }
      dispatch({ type: 'console/append', entry: { ...event.entry, id: newId(), at: Date.now() } });
    });

    if (bridge.name !== 'none') {
      bridge.attach().catch(() => undefined);
    }

    return () => {
      unsubscribe();
      bridge.detach();
    };
  }, [bridge, dispatch]);

  const attach = useCallback(() => bridge.attach().catch(() => undefined), [bridge]);
  const detach = useCallback(() => bridge.detach(), [bridge]);
  const execute = useCallback((source: string, chunkName: string) => bridge.execute(source, chunkName), [bridge]);

  const api = useMemo<BridgeApi>(
    () => ({ status, detail, backendName: bridge.name, attach, detach, execute }),
    [status, detail, bridge, attach, detach, execute],
  );

  return <BridgeContext.Provider value={api}>{children}</BridgeContext.Provider>;
}

export function useBridge(): BridgeApi {
  const value = useContext(BridgeContext);
  if (!value) throw new Error('useBridge must be used inside BridgeProvider');
  return value;
}
