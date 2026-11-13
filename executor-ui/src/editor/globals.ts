export type CompletionKind = 'keyword' | 'global' | 'service' | 'method';

export interface Completion {
  label: string;
  kind: CompletionKind;
  detail: string;
  insert?: string;
}

const keyword = (label: string): Completion => ({ label, kind: 'keyword', detail: 'keyword' });
const global = (label: string, detail: string): Completion => ({ label, kind: 'global', detail });
const service = (label: string): Completion => ({ label, kind: 'service', detail: 'service' });
const method = (label: string, detail: string): Completion => ({ label, kind: 'method', detail, insert: `${label}(` });

const KEYWORDS: Completion[] = [
  'and', 'break', 'continue', 'do', 'else', 'elseif', 'end', 'export', 'false', 'for', 'function',
  'if', 'in', 'local', 'nil', 'not', 'or', 'repeat', 'return', 'then', 'true', 'until', 'while',
].map(keyword);

const GLOBALS: Completion[] = [
  global('game', 'DataModel root'),
  global('workspace', 'Workspace root'),
  global('script', 'Script running this chunk'),
  global('Instance', 'Instance.new(className, parent?)'),
  global('CFrame', 'CFrame constructor'),
  global('Vector3', 'Vector3 constructor'),
  global('Vector2', 'Vector2 constructor'),
  global('Color3', 'Color3 constructor'),
  global('UDim2', 'UDim2 constructor'),
  global('UDim', 'UDim constructor'),
  global('Enum', 'Enum table'),
  global('task', 'task library: spawn, defer, delay, wait, cancel'),
  global('wait', 'yield for n seconds'),
  global('spawn', 'run a function on the next resume'),
  global('delay', 'run a function after n seconds'),
  global('print', 'write to output'),
  global('warn', 'write a warning'),
  global('error', 'raise an error'),
  global('assert', 'raise if falsy'),
  global('pcall', 'protected call'),
  global('xpcall', 'protected call with handler'),
  global('require', 'load a ModuleScript'),
  global('typeof', 'Roblox-aware type name'),
  global('tick', 'unix time in seconds'),
  global('time', 'high-resolution seconds'),
  global('elapsedTime', 'seconds since the game started'),
  global('os', 'os library'),
  global('math', 'math library'),
  global('string', 'string library'),
  global('table', 'table library'),
  global('coroutine', 'coroutine library'),
  global('utf8', 'utf8 library'),
  global('select', 'select varargs'),
  global('next', 'table iterator'),
  global('pairs', 'iterate all keys'),
  global('ipairs', 'iterate array part'),
  global('tonumber', 'convert to number'),
  global('tostring', 'convert to string'),
  global('setmetatable', 'set metatable'),
  global('getmetatable', 'get metatable'),
  global('rawget', 'raw table get'),
  global('rawset', 'raw table set'),
  global('rawequal', 'raw equality'),
  global('rawlen', 'raw length'),
];

const SERVICES: Completion[] = [
  'Players', 'RunService', 'UserInputService', 'ContextActionService', 'ReplicatedStorage',
  'ReplicatedFirst', 'ServerScriptService', 'ServerStorage', 'StarterGui', 'StarterPlayer',
  'Lighting', 'TweenService', 'HttpService', 'Debris', 'SoundService', 'TeleportService',
  'MarketplaceService', 'CollectionService', 'Teams',
].map(service);

const METHODS: Completion[] = [
  method('GetService', 'game:GetService(name)'),
  method('FindFirstChild', 'find direct child by name'),
  method('FindFirstChildOfClass', 'find direct child by class'),
  method('FindFirstAncestorOfClass', 'find ancestor by class'),
  method('WaitForChild', 'yield until child exists'),
  method('GetChildren', 'direct children array'),
  method('GetDescendants', 'all descendants array'),
  method('GetPlayers', 'Players:GetPlayers()'),
  method('IsA', 'class check'),
  method('Clone', 'clone instance'),
  method('Destroy', 'destroy instance'),
  method('Connect', 'connect event handler'),
  method('Disconnect', 'disconnect connection'),
  method('GetPropertyChangedSignal', 'property changed event'),
];

export const COMPLETIONS: readonly Completion[] = [...KEYWORDS, ...GLOBALS, ...SERVICES, ...METHODS];

export const KEYWORD_NAMES: ReadonlySet<string> = new Set(KEYWORDS.map((c) => c.label));

export const BUILTIN_NAMES: ReadonlySet<string> = new Set([...GLOBALS, ...SERVICES].map((c) => c.label));

export function completionsFor(prefix: string, limit = 12): Completion[] {
  if (prefix.length === 0) return [];
  const lower = prefix.toLowerCase();
  return COMPLETIONS.filter((c) => c.label.toLowerCase().startsWith(lower) && c.label !== prefix)
    .sort((a, b) => {
      const exactA = a.label.startsWith(prefix) ? 0 : 1;
      const exactB = b.label.startsWith(prefix) ? 0 : 1;
      return exactA - exactB || a.label.localeCompare(b.label);
    })
    .slice(0, limit);
}
