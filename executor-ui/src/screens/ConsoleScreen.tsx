import React, { useEffect, useMemo, useRef, useState } from 'react';
import { FlatList, Pressable, StyleSheet, Text, View, type ListRenderItemInfo } from 'react-native';
import Animated, { FadeInUp } from 'react-native-reanimated';
import type { LogEntry, LogLevel } from '../bridge/types';
import { useStore } from '../state/store';
import { fonts, palette, radius, space, type as typeScale } from '../theme/tokens';

type Filter = 'all' | LogLevel;

const FILTERS: { key: Filter; label: string }[] = [
  { key: 'all', label: 'All' },
  { key: 'output', label: 'Output' },
  { key: 'info', label: 'Info' },
  { key: 'warn', label: 'Warn' },
  { key: 'error', label: 'Error' },
];

const LEVEL_COLOR: Record<LogLevel, string> = {
  output: palette.text,
  info: palette.info,
  warn: palette.warn,
  error: palette.error,
};

export function ConsoleScreen() {
  const { state, dispatch } = useStore();
  const [filter, setFilter] = useState<Filter>('all');
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const listRef = useRef<FlatList<LogEntry>>(null);

  const entries = useMemo(
    () => (filter === 'all' ? state.console : state.console.filter((e) => e.level === filter)),
    [state.console, filter],
  );

  useEffect(() => {
    listRef.current?.scrollToEnd({ animated: true });
  }, [entries.length]);

  const toggle = (id: string) => setExpanded((prev) => ({ ...prev, [id]: !prev[id] }));

  const renderItem = ({ item }: ListRenderItemInfo<LogEntry>) => (
    <ConsoleRow entry={item} open={!!expanded[item.id]} onToggle={() => toggle(item.id)} />
  );

  return (
    <View style={styles.root}>
      <View style={styles.header}>
        <View>
          <Text style={styles.title}>Console</Text>
          <Text style={styles.caption}>{state.console.length} entries</Text>
        </View>
        <Pressable
          onPress={() => dispatch({ type: 'console/clear' })}
          style={({ pressed }) => [styles.clear, pressed && styles.pressed]}
        >
          <Text style={styles.clearText}>Clear</Text>
        </Pressable>
      </View>

      <View style={styles.filters}>
        {FILTERS.map((f) => {
          const on = filter === f.key;
          return (
            <Pressable
              key={f.key}
              onPress={() => setFilter(f.key)}
              style={({ pressed }) => [styles.filter, on && styles.filterOn, pressed && styles.pressed]}
            >
              <Text style={[styles.filterText, on && styles.filterTextOn]}>{f.label}</Text>
            </Pressable>
          );
        })}
      </View>

      <FlatList
        ref={listRef}
        data={entries}
        keyExtractor={(e) => e.id}
        renderItem={renderItem}
        extraData={expanded}
        contentContainerStyle={styles.list}
        ListEmptyComponent={
          <View style={styles.empty}>
            <Text style={styles.emptyText}>Nothing here yet. Run a script or change the filter.</Text>
          </View>
        }
      />
    </View>
  );
}

interface ConsoleRowProps {
  entry: LogEntry;
  open: boolean;
  onToggle: () => void;
}

function ConsoleRow({ entry, open, onToggle }: ConsoleRowProps) {
  const trace = entry.trace ?? [];
  const hasTrace = trace.length > 0;
  const color = LEVEL_COLOR[entry.level];

  return (
    <Animated.View entering={FadeInUp.duration(180)} style={styles.row}>
      <View style={styles.meta}>
        <View style={[styles.levelDot, { backgroundColor: color }]} />
        <Text style={[styles.level, { color }]}>{entry.level}</Text>
        <Text style={styles.time}>{formatTime(entry.at)}</Text>
        {hasTrace ? (
          <Pressable onPress={onToggle} hitSlop={8} style={({ pressed }) => [styles.traceToggle, pressed && styles.pressed]}>
            <Text style={styles.traceToggleText}>{open ? 'hide stack' : `stack · ${trace.length}`}</Text>
          </Pressable>
        ) : null}
      </View>
      <Text selectable style={[styles.message, entry.level === 'error' ? styles.messageError : null]}>
        {entry.message}
      </Text>
      {hasTrace && open ? (
        <View style={styles.trace}>
          {trace.map((line, i) => (
            <Text key={i} selectable style={styles.traceLine}>{line}</Text>
          ))}
        </View>
      ) : null}
    </Animated.View>
  );
}

function formatTime(at: number): string {
  const d = new Date(at);
  return [d.getHours(), d.getMinutes(), d.getSeconds()].map((n) => String(n).padStart(2, '0')).join(':');
}

const styles = StyleSheet.create({
  root: {
    flex: 1,
  },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: space.lg,
    paddingTop: space.lg,
    paddingBottom: space.sm,
  },
  title: {
    ...typeScale.display,
    color: palette.text,
  },
  caption: {
    ...typeScale.caption,
    color: palette.textMuted,
  },
  clear: {
    paddingHorizontal: 14,
    paddingVertical: 8,
    borderRadius: radius.md,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: palette.borderStrong,
  },
  clearText: {
    color: palette.text,
    fontWeight: '600',
    fontSize: 14,
  },
  pressed: {
    opacity: 0.7,
  },
  filters: {
    flexDirection: 'row',
    gap: space.sm,
    paddingHorizontal: space.lg,
    paddingBottom: space.md,
  },
  filter: {
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderRadius: radius.pill,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: palette.border,
  },
  filterOn: {
    backgroundColor: palette.accentSoft,
    borderColor: palette.accent,
  },
  filterText: {
    ...typeScale.caption,
    color: palette.textMuted,
  },
  filterTextOn: {
    color: palette.text,
    fontWeight: '700',
  },
  list: {
    paddingHorizontal: space.lg,
    paddingBottom: space.xl,
  },
  row: {
    paddingVertical: space.sm,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: palette.border,
    gap: 4,
  },
  meta: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
  },
  levelDot: {
    width: 6,
    height: 6,
    borderRadius: 3,
  },
  level: {
    ...typeScale.label,
    fontSize: 10,
  },
  time: {
    fontFamily: fonts.mono,
    fontSize: 11,
    color: palette.textFaint,
  },
  traceToggle: {
    marginLeft: 'auto',
  },
  traceToggleText: {
    fontSize: 11,
    fontWeight: '600',
    color: palette.info,
  },
  message: {
    fontFamily: fonts.mono,
    fontSize: 13,
    lineHeight: 18,
    color: palette.text,
  },
  messageError: {
    color: palette.error,
  },
  trace: {
    marginTop: 4,
    paddingLeft: 10,
    borderLeftWidth: 2,
    borderLeftColor: palette.borderStrong,
    gap: 2,
  },
  traceLine: {
    fontFamily: fonts.mono,
    fontSize: 12,
    lineHeight: 16,
    color: palette.textMuted,
  },
  empty: {
    alignItems: 'center',
    paddingVertical: space.xl * 2,
  },
  emptyText: {
    ...typeScale.body,
    color: palette.textMuted,
    textAlign: 'center',
  },
});
