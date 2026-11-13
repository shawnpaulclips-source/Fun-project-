import React from 'react';
import { Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import Animated, { FadeIn, FadeOut, LinearTransition } from 'react-native-reanimated';
import type { ScriptTab } from '../state/store';
import { palette, radius, space } from '../theme/tokens';

interface TabBarProps {
  tabs: ScriptTab[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onClose: (id: string) => void;
  onNew: () => void;
}

export function TabBar({ tabs, activeId, onSelect, onClose, onNew }: TabBarProps) {
  return (
    <ScrollView
      horizontal
      showsHorizontalScrollIndicator={false}
      style={styles.bar}
      contentContainerStyle={styles.row}
      keyboardShouldPersistTaps="handled"
    >
      {tabs.map((tab) => {
        const active = tab.id === activeId;
        const dirty = tab.source !== tab.savedSource;
        return (
          <Animated.View
            key={tab.id}
            entering={FadeIn.duration(160)}
            exiting={FadeOut.duration(120)}
            layout={LinearTransition.springify().damping(18)}
          >
            <Pressable
              onPress={() => onSelect(tab.id)}
              style={({ pressed }) => [styles.tab, active && styles.tabActive, pressed && styles.pressed]}
            >
              {dirty ? <View style={styles.dirty} /> : null}
              <Text numberOfLines={1} style={[styles.tabName, active && styles.tabNameActive]}>
                {tab.name || 'untitled'}
              </Text>
              <Pressable
                onPress={() => onClose(tab.id)}
                hitSlop={10}
                style={({ pressed }) => [styles.close, pressed && styles.closePressed]}
              >
                <Text style={styles.closeGlyph}>×</Text>
              </Pressable>
            </Pressable>
          </Animated.View>
        );
      })}
      <Pressable onPress={onNew} style={({ pressed }) => [styles.newTab, pressed && styles.pressed]}>
        <Text style={styles.newGlyph}>+</Text>
      </Pressable>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  bar: {
    flexGrow: 0,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: palette.border,
  },
  row: {
    paddingHorizontal: space.md,
    paddingVertical: space.sm,
    gap: space.sm,
    alignItems: 'center',
  },
  tab: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    height: 34,
    paddingLeft: 12,
    paddingRight: 6,
    maxWidth: 180,
    borderRadius: radius.md,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: palette.border,
    backgroundColor: palette.surface,
  },
  tabActive: {
    backgroundColor: palette.accentSoft,
    borderColor: palette.accent,
  },
  pressed: {
    opacity: 0.85,
  },
  dirty: {
    width: 6,
    height: 6,
    borderRadius: 3,
    backgroundColor: palette.accent,
  },
  tabName: {
    flexShrink: 1,
    fontSize: 13,
    fontWeight: '500',
    color: palette.textMuted,
  },
  tabNameActive: {
    color: palette.text,
    fontWeight: '600',
  },
  close: {
    width: 22,
    height: 22,
    borderRadius: 11,
    alignItems: 'center',
    justifyContent: 'center',
  },
  closePressed: {
    backgroundColor: palette.surfaceRaised,
  },
  closeGlyph: {
    fontSize: 16,
    lineHeight: 18,
    color: palette.textMuted,
  },
  newTab: {
    width: 34,
    height: 34,
    borderRadius: radius.md,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: palette.border,
    alignItems: 'center',
    justifyContent: 'center',
  },
  newGlyph: {
    fontSize: 20,
    lineHeight: 22,
    color: palette.accent,
  },
});
