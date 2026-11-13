import React, { useMemo, useState } from 'react';
import {
  Alert,
  FlatList,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
  type ListRenderItemInfo,
} from 'react-native';
import Animated, { FadeInDown, useAnimatedStyle, type SharedValue } from 'react-native-reanimated';
import ReanimatedSwipeable from 'react-native-gesture-handler/ReanimatedSwipeable';
import { useStore, type LibraryScript } from '../state/store';
import { fonts, palette, radius, space, type as typeScale } from '../theme/tokens';

const DELETE_WIDTH = 88;

interface LibraryScreenProps {
  onOpen: (script: LibraryScript) => void;
}

export function LibraryScreen({ onOpen }: LibraryScreenProps) {
  const { state, dispatch } = useStore();
  const [query, setQuery] = useState('');
  const [focused, setFocused] = useState(false);
  const [tag, setTag] = useState<string | null>(null);

  const allTags = useMemo(() => {
    const set = new Set<string>();
    state.library.forEach((s) => s.tags.forEach((t) => set.add(t)));
    return Array.from(set).sort();
  }, [state.library]);

  const scripts = useMemo(() => {
    const q = query.trim().toLowerCase();
    return state.library
      .filter((s) => tag === null || s.tags.includes(tag))
      .filter(
        (s) =>
          q === '' ||
          s.name.toLowerCase().includes(q) ||
          s.tags.some((t) => t.includes(q)) ||
          s.source.toLowerCase().includes(q),
      )
      .sort((a, b) => b.updatedAt - a.updatedAt);
  }, [state.library, query, tag]);

  const confirmDelete = (script: LibraryScript) => {
    Alert.alert('Delete script', `Remove "${script.name}" from the library? Open tabs keep their code.`, [
      { text: 'Cancel', style: 'cancel' },
      { text: 'Delete', style: 'destructive', onPress: () => dispatch({ type: 'lib/delete', id: script.id }) },
    ]);
  };

  const renderItem = ({ item, index }: ListRenderItemInfo<LibraryScript>) => (
    <LibraryRow
      script={item}
      index={index}
      onOpen={() => onOpen(item)}
      onDelete={() => confirmDelete(item)}
    />
  );

  return (
    <View style={styles.root}>
      <View style={styles.header}>
        <Text style={styles.title}>Library</Text>
        <Text style={styles.caption}>{state.library.length} saved</Text>
      </View>

      <TextInput
        value={query}
        onChangeText={setQuery}
        onFocus={() => setFocused(true)}
        onBlur={() => setFocused(false)}
        placeholder="Search name, tags, code"
        placeholderTextColor={palette.textFaint}
        autoCapitalize="none"
        autoCorrect={false}
        style={[styles.search, focused && styles.searchFocused]}
      />

      {allTags.length > 0 ? (
        <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.tagRow} style={styles.tagScroll}>
          {[null, ...allTags].map((t) => {
            const on = tag === t;
            return (
              <Pressable
                key={t ?? '__all'}
                onPress={() => setTag(t)}
                style={({ pressed }) => [styles.filter, on && styles.filterOn, pressed && styles.pressed]}
              >
                <Text style={[styles.filterText, on && styles.filterTextOn]}>{t ?? 'all'}</Text>
              </Pressable>
            );
          })}
        </ScrollView>
      ) : null}

      <FlatList
        data={scripts}
        keyExtractor={(s) => s.id}
        renderItem={renderItem}
        contentContainerStyle={styles.list}
        keyboardShouldPersistTaps="handled"
        ListEmptyComponent={
          <View style={styles.empty}>
            <Text style={styles.emptyTitle}>{state.library.length === 0 ? 'Library is empty' : 'No matches'}</Text>
            <Text style={styles.emptyBody}>
              {state.library.length === 0 ? 'Save a script from the editor to keep it here.' : 'Try another search or tag.'}
            </Text>
          </View>
        }
      />
    </View>
  );
}

interface LibraryRowProps {
  script: LibraryScript;
  index: number;
  onOpen: () => void;
  onDelete: () => void;
}

function LibraryRow({ script, index, onOpen, onDelete }: LibraryRowProps) {
  return (
    <Animated.View entering={FadeInDown.delay(Math.min(index, 10) * 30).duration(220)}>
      <ReanimatedSwipeable
        friction={2}
        rightThreshold={48}
        overshootRight={false}
        renderRightActions={(_progress, drag) => <DeleteAction drag={drag} onPress={onDelete} />}
      >
        <Pressable
          onPress={onOpen}
          style={({ pressed }) => [styles.card, pressed && styles.cardPressed]}
        >
          <View style={styles.cardHead}>
            <Text numberOfLines={1} style={styles.cardTitle}>{script.name}</Text>
            <Text style={styles.cardAge}>{relativeAge(script.updatedAt)}</Text>
          </View>
          {script.tags.length > 0 ? (
            <View style={styles.chips}>
              {script.tags.map((t) => (
                <Text key={t} style={styles.chip}>{t}</Text>
              ))}
            </View>
          ) : null}
          <Text numberOfLines={2} style={styles.preview}>{previewOf(script.source)}</Text>
        </Pressable>
      </ReanimatedSwipeable>
    </Animated.View>
  );
}

function DeleteAction({ drag, onPress }: { drag: SharedValue<number>; onPress: () => void }) {
  const style = useAnimatedStyle(() => ({
    transform: [{ translateX: drag.value + DELETE_WIDTH }],
  }));
  return (
    <View style={styles.actionWrap}>
      <Animated.View style={[styles.actionInner, style]}>
        <Pressable
          onPress={onPress}
          style={({ pressed }) => [styles.actionButton, pressed && styles.pressed]}
        >
          <Text style={styles.actionText}>Delete</Text>
        </Pressable>
      </Animated.View>
    </View>
  );
}

function previewOf(source: string): string {
  const lines = source.split('\n').map((l) => l.trim()).filter((l) => l.length > 0);
  return lines.length === 0 ? '(empty)' : lines.slice(0, 2).join('  ·  ');
}

function relativeAge(ts: number): string {
  const minutes = Math.floor((Date.now() - ts) / 60000);
  if (minutes < 1) return 'just now';
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

const styles = StyleSheet.create({
  root: {
    flex: 1,
  },
  header: {
    flexDirection: 'row',
    alignItems: 'baseline',
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
  search: {
    marginHorizontal: space.lg,
    marginBottom: space.sm,
    paddingHorizontal: space.md,
    paddingVertical: 10,
    borderRadius: radius.md,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: palette.border,
    backgroundColor: palette.surface,
    color: palette.text,
    fontSize: 14,
  },
  searchFocused: {
    borderColor: palette.accent,
  },
  tagScroll: {
    flexGrow: 0,
    marginBottom: space.sm,
  },
  tagRow: {
    paddingHorizontal: space.lg,
    gap: space.sm,
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
  pressed: {
    opacity: 0.75,
  },
  list: {
    paddingHorizontal: space.lg,
    paddingBottom: space.xl,
    gap: space.sm,
  },
  card: {
    backgroundColor: palette.surfaceRaised,
    borderRadius: radius.lg,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: palette.border,
    padding: space.md,
    gap: space.sm,
  },
  cardPressed: {
    borderColor: palette.borderStrong,
    backgroundColor: palette.surface,
  },
  cardHead: {
    flexDirection: 'row',
    alignItems: 'baseline',
    justifyContent: 'space-between',
    gap: space.sm,
  },
  cardTitle: {
    ...typeScale.title,
    color: palette.text,
    flexShrink: 1,
  },
  cardAge: {
    ...typeScale.caption,
    color: palette.textFaint,
  },
  chips: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 6,
  },
  chip: {
    fontSize: 11,
    fontWeight: '600',
    color: palette.accent,
    paddingHorizontal: 8,
    paddingVertical: 2,
    borderRadius: radius.pill,
    backgroundColor: palette.accentSoft,
    overflow: 'hidden',
  },
  preview: {
    fontFamily: fonts.mono,
    fontSize: 12,
    lineHeight: 17,
    color: palette.textMuted,
  },
  actionWrap: {
    width: DELETE_WIDTH,
    justifyContent: 'center',
    alignItems: 'flex-end',
  },
  actionInner: {
    flex: 1,
    width: DELETE_WIDTH,
  },
  actionButton: {
    flex: 1,
    borderRadius: radius.lg,
    backgroundColor: palette.error,
    alignItems: 'center',
    justifyContent: 'center',
    marginLeft: space.sm,
  },
  actionText: {
    color: '#1A0A0D',
    fontWeight: '700',
    fontSize: 14,
  },
  empty: {
    alignItems: 'center',
    gap: space.sm,
    paddingVertical: space.xl * 2,
  },
  emptyTitle: {
    ...typeScale.title,
    color: palette.text,
  },
  emptyBody: {
    ...typeScale.body,
    color: palette.textMuted,
    textAlign: 'center',
  },
});
