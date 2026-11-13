import React, { useEffect, useState } from 'react';
import { Pressable, StyleSheet, Text, View, type LayoutChangeEvent } from 'react-native';
import Animated, { useAnimatedStyle, useSharedValue, withSpring } from 'react-native-reanimated';
import { palette, space, type as typeScale } from '../theme/tokens';

export type Screen = 'editor' | 'library' | 'console' | 'settings';

const ITEMS: { key: Screen; label: string }[] = [
  { key: 'editor', label: 'Editor' },
  { key: 'library', label: 'Library' },
  { key: 'console', label: 'Console' },
  { key: 'settings', label: 'Settings' },
];

interface BottomNavProps {
  active: Screen;
  onChange: (screen: Screen) => void;
  bottomInset: number;
}

export function BottomNav({ active, onChange, bottomInset }: BottomNavProps) {
  const [width, setWidth] = useState(0);
  const itemWidth = width / ITEMS.length;
  const activeIndex = Math.max(0, ITEMS.findIndex((i) => i.key === active));

  const indicatorX = useSharedValue(0);

  useEffect(() => {
    indicatorX.value = withSpring(activeIndex * itemWidth, { damping: 18, stiffness: 200 });
  }, [activeIndex, itemWidth, indicatorX]);

  const indicatorStyle = useAnimatedStyle(() => ({
    transform: [{ translateX: indicatorX.value }],
    width: itemWidth,
  }));

  return (
    <View
      onLayout={(e: LayoutChangeEvent) => setWidth(e.nativeEvent.layout.width)}
      style={[styles.bar, { paddingBottom: bottomInset }]}
    >
      <Animated.View pointerEvents="none" style={[styles.indicator, indicatorStyle]} />
      {ITEMS.map((item) => {
        const on = item.key === active;
        return (
          <Pressable
            key={item.key}
            onPress={() => onChange(item.key)}
            accessibilityRole="tab"
            accessibilityState={{ selected: on }}
            style={({ pressed }) => [styles.item, pressed && styles.itemPressed]}
          >
            <Text style={[styles.label, on && styles.labelOn]}>{item.label}</Text>
          </Pressable>
        );
      })}
    </View>
  );
}

const styles = StyleSheet.create({
  bar: {
    flexDirection: 'row',
    backgroundColor: palette.surface,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: palette.border,
  },
  indicator: {
    position: 'absolute',
    top: 0,
    left: 0,
    height: 2,
    backgroundColor: palette.accent,
  },
  item: {
    flex: 1,
    alignItems: 'center',
    paddingVertical: space.md,
  },
  itemPressed: {
    opacity: 0.6,
  },
  label: {
    ...typeScale.caption,
    color: palette.textMuted,
  },
  labelOn: {
    color: palette.text,
    fontWeight: '700',
  },
});
