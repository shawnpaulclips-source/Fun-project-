import React, { useEffect, useState, type ReactNode } from 'react';
import { Pressable, ScrollView, StyleSheet, Text, TextInput, View } from 'react-native';
import { STATUS_LABEL, useBridge } from '../bridge/BridgeContext';
import { HighlightedCode } from '../editor/CodeEditor';
import { useStore } from '../state/store';
import { palette, radius, space, type as typeScale } from '../theme/tokens';

const MIN_FONT = 11;
const MAX_FONT = 22;
const PREVIEW = 'local function greet(name)\n  print("hello, " .. name) -- 42\nend';

export function SettingsScreen() {
  const { state, dispatch } = useStore();
  const bridge = useBridge();
  const [draftUrl, setDraftUrl] = useState(state.settings.backendUrl);

  useEffect(() => {
    setDraftUrl(state.settings.backendUrl);
  }, [state.settings.backendUrl]);

  const trimmedDraft = draftUrl.trim();
  const urlDirty = trimmedDraft !== state.settings.backendUrl.trim();
  const hasUrl = state.settings.backendUrl.trim().length > 0;
  const attached = bridge.status === 'attached';
  const connecting = bridge.status === 'connecting';

  const saveUrl = () => {
    if (!urlDirty) return;
    dispatch({ type: 'settings/patch', patch: { backendUrl: trimmedDraft } });
  };

  const setFont = (size: number) => {
    dispatch({ type: 'settings/patch', patch: { fontSize: Math.min(MAX_FONT, Math.max(MIN_FONT, size)) } });
  };

  const statusDetail = bridge.detail ?? (hasUrl ? state.settings.backendUrl : 'No backend configured');

  return (
    <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
      <Section title="Backend">
        <View style={styles.statusRow}>
          <View style={[styles.statusDot, { backgroundColor: statusColor(bridge.status) }]} />
          <Text style={styles.statusLabel}>{STATUS_LABEL[bridge.status]}</Text>
        </View>
        <Text style={styles.statusDetail} numberOfLines={2}>{statusDetail}</Text>

        <TextInput
          value={draftUrl}
          onChangeText={setDraftUrl}
          onSubmitEditing={saveUrl}
          placeholder="ws://127.0.0.1:8765"
          placeholderTextColor={palette.textFaint}
          autoCapitalize="none"
          autoCorrect={false}
          keyboardType="url"
          returnKeyType="done"
          style={styles.input}
        />

        <View style={styles.row}>
          <Pressable
            onPress={saveUrl}
            disabled={!urlDirty}
            style={({ pressed }) => [styles.btnPrimary, pressed && styles.pressed, !urlDirty && styles.disabled]}
          >
            <Text style={styles.btnPrimaryText}>{hasUrl ? 'Save & reconnect' : 'Save'}</Text>
          </Pressable>
          {attached ? (
            <Pressable
              onPress={() => bridge.detach()}
              style={({ pressed }) => [styles.btnGhost, pressed && styles.pressed]}
            >
              <Text style={styles.btnGhostText}>Detach</Text>
            </Pressable>
          ) : (
            <Pressable
              onPress={() => {
                bridge.attach().catch(() => undefined);
              }}
              disabled={!hasUrl || connecting}
              style={({ pressed }) => [styles.btnGhost, pressed && styles.pressed, (!hasUrl || connecting) && styles.disabled]}
            >
              <Text style={styles.btnGhostText}>{connecting ? 'Connecting…' : 'Attach'}</Text>
            </Pressable>
          )}
        </View>
      </Section>

      <Section title="Editor">
        <View style={styles.row}>
          <Text style={styles.label}>Font size</Text>
          <View style={styles.stepper}>
            <Pressable
              onPress={() => setFont(state.settings.fontSize - 1)}
              disabled={state.settings.fontSize <= MIN_FONT}
              style={({ pressed }) => [styles.stepBtn, pressed && styles.pressed]}
            >
              <Text style={styles.stepGlyph}>−</Text>
            </Pressable>
            <Text style={styles.stepValue}>{state.settings.fontSize}</Text>
            <Pressable
              onPress={() => setFont(state.settings.fontSize + 1)}
              disabled={state.settings.fontSize >= MAX_FONT}
              style={({ pressed }) => [styles.stepBtn, pressed && styles.pressed]}
            >
              <Text style={styles.stepGlyph}>+</Text>
            </Pressable>
          </View>
        </View>
        <View style={styles.preview}>
          <HighlightedCode code={PREVIEW} fontSize={state.settings.fontSize} />
        </View>
      </Section>

      <Section title="About">
        <Text style={styles.body}>
          The UI talks to exactly one thing: an ExecutorBridge. Point the backend URL at anything that
          speaks the wire format in README.md and the console, run button and status all light up.
        </Text>
      </Section>
    </ScrollView>
  );
}

function statusColor(status: keyof typeof STATUS_LABEL): string {
  switch (status) {
    case 'attached':
      return palette.success;
    case 'connecting':
      return palette.warn;
    case 'error':
      return palette.error;
    default:
      return palette.textFaint;
  }
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <View style={styles.section}>
      <Text style={styles.sectionTitle}>{title}</Text>
      <View style={styles.card}>{children}</View>
    </View>
  );
}

const styles = StyleSheet.create({
  content: {
    padding: space.lg,
    gap: space.lg,
    paddingBottom: space.xl * 2,
  },
  section: {
    gap: space.sm,
  },
  sectionTitle: {
    ...typeScale.label,
    color: palette.textMuted,
  },
  card: {
    backgroundColor: palette.surfaceRaised,
    borderRadius: radius.lg,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: palette.border,
    padding: space.md,
    gap: space.md,
  },
  statusRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: space.sm,
  },
  statusDot: {
    width: 8,
    height: 8,
    borderRadius: 4,
  },
  statusLabel: {
    ...typeScale.title,
    color: palette.text,
  },
  statusDetail: {
    ...typeScale.caption,
    color: palette.textMuted,
    marginTop: -space.xs,
  },
  input: {
    paddingHorizontal: space.md,
    paddingVertical: 10,
    borderRadius: radius.md,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: palette.borderStrong,
    backgroundColor: palette.surface,
    color: palette.text,
    fontSize: 14,
  },
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: space.sm,
  },
  label: {
    ...typeScale.body,
    color: palette.text,
    flex: 1,
  },
  btnPrimary: {
    flex: 1,
    backgroundColor: palette.accent,
    paddingVertical: 10,
    borderRadius: radius.md,
    alignItems: 'center',
  },
  btnPrimaryText: {
    color: '#1A1407',
    fontWeight: '700',
    fontSize: 14,
  },
  btnGhost: {
    paddingHorizontal: 16,
    paddingVertical: 10,
    borderRadius: radius.md,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: palette.borderStrong,
  },
  btnGhostText: {
    color: palette.text,
    fontWeight: '600',
    fontSize: 14,
  },
  stepper: {
    flexDirection: 'row',
    alignItems: 'center',
    borderRadius: radius.md,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: palette.borderStrong,
    overflow: 'hidden',
  },
  stepBtn: {
    width: 40,
    height: 36,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: palette.surface,
  },
  stepGlyph: {
    fontSize: 18,
    color: palette.text,
  },
  stepValue: {
    width: 40,
    textAlign: 'center',
    color: palette.accent,
    fontWeight: '700',
    fontSize: 14,
  },
  preview: {
    backgroundColor: palette.surfaceSunken,
    borderRadius: radius.md,
    padding: space.md,
  },
  body: {
    ...typeScale.body,
    color: palette.textMuted,
  },
  pressed: {
    opacity: 0.75,
  },
  disabled: {
    opacity: 0.35,
  },
});
