import React, {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useReducer,
  useRef,
  useState,
  type Dispatch,
  type ReactNode,
} from 'react';
import AsyncStorage from '@react-native-async-storage/async-storage';
import type { LogEntry } from '../bridge/types';
import { newId } from './ids';

export interface ScriptTab {
  id: string;
  name: string;
  tags: string[];
  source: string;
  savedSource: string;
  libraryId: string | null;
}

export interface LibraryScript {
  id: string;
  name: string;
  tags: string[];
  source: string;
  createdAt: number;
  updatedAt: number;
}

export interface Settings {
  backendUrl: string;
  fontSize: number;
}

export interface PersistedState {
  tabs: ScriptTab[];
  activeTabId: string | null;
  library: LibraryScript[];
  settings: Settings;
}

export interface AppState extends PersistedState {
  console: LogEntry[];
}

export type Action =
  | { type: 'hydrate'; state: PersistedState }
  | { type: 'tab/new'; id: string; name: string }
  | { type: 'tab/open'; tab: ScriptTab }
  | { type: 'tab/close'; id: string }
  | { type: 'tab/activate'; id: string }
  | { type: 'tab/update'; id: string; patch: Partial<Pick<ScriptTab, 'name' | 'tags' | 'source'>> }
  | { type: 'tab/saved'; id: string; libraryId: string; savedSource: string }
  | { type: 'lib/upsert'; script: LibraryScript }
  | { type: 'lib/delete'; id: string }
  | { type: 'console/append'; entry: LogEntry }
  | { type: 'console/clear' }
  | { type: 'settings/patch'; patch: Partial<Settings> };

const STORAGE_KEY = 'reading-room/v1';
const MAX_CONSOLE = 2000;
const SAVE_DEBOUNCE_MS = 400;
const DEFAULT_SETTINGS: Settings = { backendUrl: '', fontSize: 14 };
export const STARTER_SOURCE = '-- Reading Room\nprint("hello from the desk")\n';

export function parseTags(raw: string): string[] {
  const tags = raw
    .split(',')
    .map((t) => t.trim().toLowerCase())
    .filter((t) => t.length > 0);
  return Array.from(new Set(tags));
}

export function createInitialState(): AppState {
  const id = newId();
  return {
    tabs: [{ id, name: 'untitled', tags: [], source: STARTER_SOURCE, savedSource: '', libraryId: null }],
    activeTabId: id,
    library: [],
    settings: DEFAULT_SETTINGS,
    console: [],
  };
}

function reducer(state: AppState, action: Action): AppState {
  switch (action.type) {
    case 'hydrate':
      return { ...state, ...action.state, settings: { ...DEFAULT_SETTINGS, ...action.state.settings } };

    case 'tab/new': {
      const tab: ScriptTab = { id: action.id, name: action.name, tags: [], source: '', savedSource: '', libraryId: null };
      return { ...state, tabs: [...state.tabs, tab], activeTabId: tab.id };
    }

    case 'tab/open': {
      const linked = action.tab.libraryId
        ? state.tabs.find((t) => t.libraryId === action.tab.libraryId)
        : undefined;
      if (linked) return { ...state, activeTabId: linked.id };
      return { ...state, tabs: [...state.tabs, action.tab], activeTabId: action.tab.id };
    }

    case 'tab/close': {
      const index = state.tabs.findIndex((t) => t.id === action.id);
      if (index === -1) return state;
      const tabs = state.tabs.filter((t) => t.id !== action.id);
      let activeTabId = state.activeTabId;
      if (activeTabId === action.id) {
        const neighbour = tabs[Math.min(index, tabs.length - 1)];
        activeTabId = neighbour ? neighbour.id : null;
      }
      return { ...state, tabs, activeTabId };
    }

    case 'tab/activate':
      return { ...state, activeTabId: action.id };

    case 'tab/update':
      return {
        ...state,
        tabs: state.tabs.map((t) => (t.id === action.id ? { ...t, ...action.patch } : t)),
      };

    case 'tab/saved':
      return {
        ...state,
        tabs: state.tabs.map((t) =>
          t.id === action.id ? { ...t, libraryId: action.libraryId, savedSource: action.savedSource } : t,
        ),
      };

    case 'lib/upsert': {
      const exists = state.library.some((s) => s.id === action.script.id);
      const library = exists
        ? state.library.map((s) => (s.id === action.script.id ? action.script : s))
        : [action.script, ...state.library];
      return { ...state, library };
    }

    case 'lib/delete':
      return {
        ...state,
        library: state.library.filter((s) => s.id !== action.id),
        tabs: state.tabs.map((t) => (t.libraryId === action.id ? { ...t, libraryId: null } : t)),
      };

    case 'console/append':
      return { ...state, console: [...state.console, action.entry].slice(-MAX_CONSOLE) };

    case 'console/clear':
      return { ...state, console: [] };

    case 'settings/patch':
      return { ...state, settings: { ...state.settings, ...action.patch } };

    default:
      return state;
  }
}

function parsePersisted(raw: string): PersistedState | null {
  try {
    const data: unknown = JSON.parse(raw);
    if (typeof data !== 'object' || data === null) return null;
    const d = data as Partial<PersistedState>;
    if (!Array.isArray(d.tabs) || !Array.isArray(d.library)) return null;
    return {
      tabs: d.tabs,
      activeTabId: typeof d.activeTabId === 'string' ? d.activeTabId : null,
      library: d.library,
      settings: { ...DEFAULT_SETTINGS, ...(d.settings ?? {}) },
    };
  } catch {
    return null;
  }
}

interface StoreApi {
  state: AppState;
  dispatch: Dispatch<Action>;
  hydrated: boolean;
}

const StoreContext = createContext<StoreApi | null>(null);

export function StoreProvider({ children }: { children: ReactNode }) {
  const [state, dispatch] = useReducer(reducer, undefined, createInitialState);
  const [hydrated, setHydrated] = useState(false);
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    let cancelled = false;
    AsyncStorage.getItem(STORAGE_KEY)
      .then((raw) => {
        if (cancelled || !raw) return;
        const parsed = parsePersisted(raw);
        if (parsed) dispatch({ type: 'hydrate', state: parsed });
      })
      .catch(() => undefined)
      .finally(() => {
        if (!cancelled) setHydrated(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!hydrated) return;
    if (saveTimer.current) clearTimeout(saveTimer.current);
    saveTimer.current = setTimeout(() => {
      const payload: PersistedState = {
        tabs: state.tabs,
        activeTabId: state.activeTabId,
        library: state.library,
        settings: state.settings,
      };
      AsyncStorage.setItem(STORAGE_KEY, JSON.stringify(payload)).catch(() => undefined);
    }, SAVE_DEBOUNCE_MS);
  }, [hydrated, state.tabs, state.activeTabId, state.library, state.settings]);

  const value = useMemo<StoreApi>(() => ({ state, dispatch, hydrated }), [state, hydrated]);
  return <StoreContext.Provider value={value}>{children}</StoreContext.Provider>;
}

export function useStore(): StoreApi {
  const value = useContext(StoreContext);
  if (!value) throw new Error('useStore must be used inside StoreProvider');
  return value;
}
