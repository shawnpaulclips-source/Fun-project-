import React, { useCallback, useMemo, useState } from 'react';
import {
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
  useWindowDimensions,
  type NativeSyntheticEvent,
  type TextInputSelectionChangeEventData,
} from 'react-native';
import Animated, { FadeIn, FadeOut, LinearTransition } from 'react-native-reanimated';
import { fonts, palette, radius, space } from '../theme/tokens';
import { completionsFor, type Completion } from './globals';
import { tokenize, type Token } from './luau';

const PAD_V = 12;
const PAD_H = 14;
const GUTTER = 52;

const monoBase = {
  fontFamily: fonts.mono,
  includeFontPadding: false,
} as const;

function renderTokens(tokens: Token[]) {
  return tokens.map((t, i) => (
    <Text key={i} style={{ color: palette.syntax[t.kind], fontStyle: t.kind === 'comment' ? 'italic' : 'normal' }}>
      {t.text}
    </Text>
  ));
}

export function HighlightedCode({ code, fontSize }: { code: string; fontSize: number }) {
  const tokens = useMemo(() => tokenize(code), [code]);
  return (
    <Text style={{ ...monoBase, fontSize, lineHeight: Math.round(fontSize * 1.5), color: palette.text }}>
      {renderTokens(tokens)}
    </Text>
  );
}

function wordBeforeCaret(text: string, caret: number): string {
  const m = /[A-Za-z_][A-Za-z0-9_]*$/.exec(text.slice(0, caret));
  return m ? m[0] : '';
}

interface CodeEditorProps {
  value: string;
  onChange: (next: string) => void;
  fontSize: number;
}

export function CodeEditor({ value, onChange, fontSize }: CodeEditorProps) {
  const { width: screenWidth } = useWindowDimensions();
  const [caret, setCaret] = useState(0);
  const [forcedSelection, setForcedSelection] = useState<{ start: number; end: number } | undefined>(undefined);

  const lineHeight = Math.round(fontSize * 1.5);
  const charWidth = fontSize * 0.62;

  const lines = useMemo(() => value.split('\n'), [value]);
  const longest = useMemo(() => lines.reduce((max, line) => Math.max(max, line.length), 0), [lines]);
  const contentWidth = Math.max(
    Math.ceil(longest * charWidth) + PAD_H * 2 + charWidth * 8,
    screenWidth - GUTTER,
  );
  const contentHeight = (lines.length + 1) * lineHeight + PAD_V * 2;
  const tokens = useMemo(() => tokenize(value), [value]);
  const activeLine = value.slice(0, caret).split('\n').length - 1;
  const prefix = wordBeforeCaret(value, caret);
  const suggestions = useMemo(() => completionsFor(prefix), [prefix]);
  const monoStyle = { ...monoBase, fontSize, lineHeight };

  const applyCompletion = useCallback(
    (completion: Completion) => {
      const insert = completion.insert ?? completion.label;
      const start = caret - prefix.length;
      const next = value.slice(0, start) + insert + value.slice(caret);
      const pos = start + insert.length;
      onChange(next);
      setCaret(pos);
      setForcedSelection({ start: pos, end: pos });
    },
    [caret, prefix, value, onChange],
  );

  const handleSelection = (e: NativeSyntheticEvent<TextInputSelectionChangeEventData>) => {
    setCaret(e.nativeEvent.selection.start);
    setForcedSelection(undefined);
  };

  return (
    <View style={styles.root}>
      <ScrollView style={styles.scroll} keyboardShouldPersistTaps="handled">
        <View style={styles.body}>
          <View style={[styles.gutter, { width: GUTTER, paddingTop: PAD_V, height: contentHeight }]}>
            {lines.map((_, index) => (
              <Text
                key={index}
                style={[
                  monoStyle,
                  styles.lineNumber,
                  { color: index === activeLine ? palette.text : palette.textFaint },
                ]}
              >
                {index + 1}
              </Text>
            ))}
          </View>

          <ScrollView horizontal showsHorizontalScrollIndicator={false} keyboardShouldPersistTaps="handled" style={styles.flex}>
            <View style={{ width: contentWidth, height: contentHeight }}>
              <View
                pointerEvents="none"
                style={[
                  styles.activeLine,
                  { top: PAD_V + activeLine * lineHeight, height: lineHeight, width: contentWidth },
                ]}
              />
              <View pointerEvents="none" style={StyleSheet.absoluteFill}>
                <Text style={[monoStyle, { paddingTop: PAD_V, paddingLeft: PAD_H, color: palette.text }]}>
                  {renderTokens(tokens)}
                  {value.endsWith('\n') ? ' ' : null}
                </Text>
              </View>
              <TextInput
                value={value}
                onChangeText={onChange}
                onSelectionChange={handleSelection}
                selection={forcedSelection}
                multiline
                scrollEnabled={false}
                textAlignVertical="top"
                autoCapitalize="none"
                autoCorrect={false}
                autoComplete="off"
                spellCheck={false}
                allowFontScaling={false}
                underlineColorAndroid="transparent"
                cursorColor={palette.accent}
                selectionColor={palette.accentSoft}
                style={[
                  monoStyle,
                  styles.input,
                  { paddingTop: PAD_V, paddingLeft: PAD_H, width: contentWidth, height: contentHeight },
                ]}
              />
            </View>
          </ScrollView>
        </View>
      </ScrollView>

      {suggestions.length > 0 ? (
        <Animated.View
          entering={FadeIn.duration(120)}
          exiting={FadeOut.duration(90)}
          layout={LinearTransition}
          style={styles.strip}
        >
          <ScrollView
            horizontal
            keyboardShouldPersistTaps="always"
            showsHorizontalScrollIndicator={false}
            contentContainerStyle={styles.stripInner}
          >
            {suggestions.map((c) => (
              <Pressable
                key={`${c.kind}-${c.label}`}
                onPress={() => applyCompletion(c)}
                style={({ pressed }) => [styles.chip, pressed && styles.chipPressed]}
              >
                <Text style={styles.chipKind}>{c.kind}</Text>
                <Text style={styles.chipLabel}>{c.label}</Text>
              </Pressable>
            ))}
          </ScrollView>
        </Animated.View>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  root: {
    flex: 1,
    backgroundColor: palette.surfaceSunken,
  },
  flex: {
    flex: 1,
  },
  scroll: {
    flex: 1,
  },
  body: {
    flexDirection: 'row',
  },
  gutter: {
    backgroundColor: palette.surface,
    borderRightWidth: StyleSheet.hairlineWidth,
    borderRightColor: palette.border,
    alignItems: 'flex-end',
    paddingRight: 10,
  },
  lineNumber: {
    textAlign: 'right',
  },
  activeLine: {
    position: 'absolute',
    left: 0,
    backgroundColor: palette.accentLine,
  },
  input: {
    position: 'absolute',
    top: 0,
    left: 0,
    margin: 0,
    color: 'transparent',
  },
  strip: {
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: palette.border,
    backgroundColor: palette.surface,
    paddingVertical: space.sm,
  },
  stripInner: {
    paddingHorizontal: space.md,
    gap: space.sm,
  },
  chip: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    paddingHorizontal: 10,
    paddingVertical: 6,
    borderRadius: radius.pill,
    backgroundColor: palette.surfaceRaised,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: palette.borderStrong,
  },
  chipPressed: {
    backgroundColor: palette.accentSoft,
    borderColor: palette.accent,
  },
  chipKind: {
    fontSize: 9,
    fontWeight: '700',
    letterSpacing: 0.8,
    textTransform: 'uppercase',
    color: palette.textFaint,
  },
  chipLabel: {
    fontFamily: fonts.mono,
    fontSize: 13,
    color: palette.text,
  },
});
