export type LogLevel = 'output' | 'info' | 'warn' | 'error';

export interface LogEntry {
  id: string;
  level: LogLevel;
  message: string;
  trace?: string[];
  at: number;
}

export type BridgeStatus = 'detached' | 'connecting' | 'attached' | 'error';

export type BridgeEvent =
  | { kind: 'log'; entry: { level: LogLevel; message: string; trace?: string[] } }
  | { kind: 'status'; status: BridgeStatus; detail?: string };

/**
 * The only surface the UI knows about. Whatever sits behind it (local socket,
 * IPC, a bundled service) implements this and nothing else.
 */
export interface ExecutorBridge {
  readonly name: string;
  getStatus(): BridgeStatus;
  attach(): Promise<void>;
  detach(): void;
  execute(source: string, chunkName: string): Promise<void>;
  subscribe(listener: (event: BridgeEvent) => void): () => void;
}
