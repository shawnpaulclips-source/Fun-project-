import type { BridgeEvent, BridgeStatus, ExecutorBridge, LogLevel } from './types';

type Listener = (event: BridgeEvent) => void;

const LOG_LEVELS: readonly LogLevel[] = ['output', 'info', 'warn', 'error'];

abstract class BaseBridge implements ExecutorBridge {
  abstract readonly name: string;
  private status: BridgeStatus = 'detached';
  private readonly listeners = new Set<Listener>();

  getStatus(): BridgeStatus {
    return this.status;
  }

  subscribe(listener: Listener): () => void {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  }

  abstract attach(): Promise<void>;
  abstract detach(): void;
  abstract execute(source: string, chunkName: string): Promise<void>;

  protected setStatus(status: BridgeStatus, detail?: string): void {
    this.status = status;
    this.emit({ kind: 'status', status, detail });
  }

  protected log(level: LogLevel, message: string, trace?: string[]): void {
    this.emit({ kind: 'log', entry: { level, message, trace } });
  }

  private emit(event: BridgeEvent): void {
    this.listeners.forEach((listener) => listener(event));
  }
}

export class DetachedBridge extends BaseBridge {
  readonly name = 'none';

  async attach(): Promise<void> {
    this.setStatus('error', 'No backend configured. Set a backend URL in Settings.');
  }

  detach(): void {
    this.setStatus('detached');
  }

  async execute(): Promise<void> {
    this.log('error', 'No backend attached. Nothing ran.');
  }
}

/**
 * Plain JSON-over-WebSocket transport.
 *
 * Client -> backend:  { "op": "exec", "chunk": string, "source": string }
 * Backend -> client:  { "op": "log", "level": "output"|"info"|"warn"|"error", "message": string, "trace"?: string[] }
 */
export class WebSocketBridge extends BaseBridge {
  readonly name: string;
  private socket: WebSocket | null = null;

  constructor(private readonly url: string) {
    super();
    this.name = url;
  }

  attach(): Promise<void> {
    if (this.socket) return Promise.resolve();

    return new Promise<void>((resolve, reject) => {
      this.setStatus('connecting', this.url);

      let ws: WebSocket;
      try {
        ws = new WebSocket(this.url);
      } catch (err) {
        this.setStatus('error', err instanceof Error ? err.message : 'Invalid backend URL');
        reject(err);
        return;
      }
      this.socket = ws;

      ws.onopen = () => {
        this.setStatus('attached', this.url);
        resolve();
      };

      ws.onmessage = (event) => {
        this.handleFrame(event.data);
      };

      ws.onerror = () => {
        if (this.getStatus() === 'connecting') {
          this.setStatus('error', `Could not reach ${this.url}`);
          reject(new Error(`Could not reach ${this.url}`));
        }
      };

      ws.onclose = () => {
        if (this.socket !== ws) return;
        this.socket = null;
        if (this.getStatus() !== 'error') this.setStatus('detached');
      };
    });
  }

  detach(): void {
    const ws = this.socket;
    this.socket = null;
    ws?.close();
    this.setStatus('detached');
  }

  async execute(source: string, chunkName: string): Promise<void> {
    const ws = this.socket;
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      this.log('error', 'Not attached. Nothing ran.');
      return;
    }
    ws.send(JSON.stringify({ op: 'exec', chunk: chunkName, source }));
  }

  private handleFrame(raw: unknown): void {
    if (typeof raw !== 'string') return;

    let parsed: unknown;
    try {
      parsed = JSON.parse(raw);
    } catch {
      this.log('warn', 'Backend sent a malformed frame.');
      return;
    }
    if (typeof parsed !== 'object' || parsed === null) return;

    const frame = parsed as Record<string, unknown>;
    if (frame.op !== 'log') return;

    const level = LOG_LEVELS.find((l) => l === frame.level) ?? 'info';
    const message = typeof frame.message === 'string' ? frame.message : '';
    const trace = Array.isArray(frame.trace)
      ? frame.trace.filter((line): line is string => typeof line === 'string')
      : undefined;

    this.log(level, message, trace);
  }
}

export function createBridge(url: string): ExecutorBridge {
  const trimmed = url.trim();
  return trimmed.length > 0 ? new WebSocketBridge(trimmed) : new DetachedBridge();
}
