import React, { useEffect, useState } from 'react';
import { Alert, Pressable, StyleSheet, Text, TextInput, View } from 'react-native';
import { useBridge } from '../bridge/BridgeContext';
import { CodeEditor } from '../editor/CodeEditor';
import { TabBar } from '../editor/TabBar';
import { newId } from '../state/ids';
import { parseTags, useStore, type ScriptTab } from '../state/store';
import { fonts, palette, space, type as typeScale } from '../theme/tokens';

export function EditorScreen() {
  const { state, dispatch } = useStore();
  const bridge = useBridge();
  const tab = state.tabs.find((t) => t.id === state.activeTabId) ?? null;
  const [tagDraft, setTagDraft] = useState(tab ? tab.tags.join(', ') : '');

  useEffect(() => {
    setTagDraft(tab ? tab.tags.join(', ') : '');
    // Only reset the draft when the open tab changes, not on every tag commit.
  }, [tab?.id]);

  const dirty = tab ? tab.source !== tab.savedSource : false;
  const isSaved = tab ? tab.libraryId !== null : false;

  const update = (patch: Partial<Pick<ScriptTab, 'name' | 'tags' | 'source'>>) => {
    if (!tab) return;
    dispatch({ type: 'tab/update', id: tab.id, patch });
  };

  const commitTags = () => {
    if (!tab) return;
    const tags = parseTags(tagDraft);
    setTagDraft(tags.join(', '));
    update({ tags });
  };

  const run = () => {
    if (!tab) return;
    const name = tab.name.trim() || 'untitled';
    dispatch({ type: 'console/append', entry: { id: newId(), level: 'info', message: `running ${name}`, at: Date.now() } });
    bridge.execute(tab.source, name).catch(() => undefined);
  };

  const save = () => {
    if (!tab) return;
    const now = Date.now();
    const libraryId = tab.libraryId ?? newId();
    const existing = state.library.find((s) => s.id === libraryId);
    dispatch({
      type: 'lib/upsert',
      script: {
        id: libraryId,
        name: tab.name.trim() || 'untitled',
        tags: tab.tags,
        source: tab.source,
        createdAt: existing?.createdAt ?? now,
        updatedAt: now,
      },
    });
    dispatch({ type: 'tab/saved', id: tab.id, libraryId, savedSource: tab.source });
  };

  const newTab = () => dispatch({ type: 'tab/new', id: newId(), name: 'untitled' });

  const requestClose = (id: string) => {
    const target = state.tabs.find((t) => t.id === id);
    if (!target) return;
    const close = () => dispatch({ type: 'tab/close', id });
    if (target.source === target.savedSource) {
      close();
      return;
    }
    Alert.alert(
      'Unsaved changes',
      `"${target.name || 'untitled'}" has edits that aren't in the library.`,
      [
        { text: 'Keep editing', style: 'cancel' },
        { text: 'Close without saving', style: 'destructive', onPress: close },
      ],
    );
  };

  const saveDisabled = !tab || (isSaved && !dirty);

  return (
    <View style={styles.root}>
      <TabBar
        tabs={state.tabs}
        activeId={state.activeTabId}
        onSelect={(id) => dispatch({ type: 'tab/activate', id })}
        onClose={requestClose}
        onNew={newTab}
      />

      {tab ? (
        <>
          <View style={styles.header}>
            <View style={styles.titleBlock}>
              <TextInput
                value={tab.name}
                onChangeText={(name) => update({ name })}
                placeholder="script name"
                placeholderTextColor={palette.textFaint}
                maxLength={60}
                style={styles.name}
              />
              <TextInput
                value={tagDraft}
                onChangeText={setTagDraft}
                onBlur={commitTags}
                onSubmitEditing={commitTags}
                placeholder="tags, comma separated"
                placeholderTextColor={palette.textFaint}
                autoCapitalize="none"
                autoCorrect={false}
                returnKeyType="done"
                style={styles.tags}
              />
            </View>
            <View style={styles.actions}>
              <Pressable
                onPress={save}
                disabled={saveDisabled}
                style={({ pressed }) => [styles.btnGhost, pressed && styles.pressed, saveDisabled && styles.disabled]}
              >
                <Text style={styles.btnGhostText}>{isSaved ? 'Update' : 'Save'}</Text>
              </Pressable>
              <Pressable onPress={run} style={({ pressed }) => [styles.btnRun, pressed && styles.pressed]}>
                <Text style={styles.btnRunText}>Run</Text>
              </Pressable>
            </View>
          </View>
          <CodeEditor value={tab.source} onChange={(source) => update({ source })} fontSize={state.settings.fontSize} />
        </>
      ) : (
        <View style={styles.empty}>
          <Text style={styles.emptyTitle}>Nothing open</Text>
          <Text style={styles.emptyBody}>Start a new script or pull one from the library.</Text>
          <Pressable onPress={newTab} style={({ pressed }) => [styles.btnRun, pressed && styles.pressed]}>
            <Text style={styles.btnRunText}>New script</Text>
          </Pressable>
        </View>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  root: {
    flex: 1,
  },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: space.md,
    paddingHorizontal: space.lg,
    paddingVertical: space.sm,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: palette.border,
  },
  titleBlock: {
    flex: 1,
    gap: 2,
  },
  name: {
    ...typeScale.title,
    color: palette.text,
    paddingVertical: 2,
    padding: 0,
  },
  tags: {
    fontFamily: fonts.mono,
    fontSize: 12,
    color: palette.textMuted,
    paddingVertical: 2,
    padding: 0,
  },
  actions: {
    flexDirection: 'row',
    gap: space.sm,
  },
  btnRun: {
    backgroundColor: palette.accent,
    paddingHorizontal: 16,
    paddingVertical: 9,
    borderRadius: 10,
  },
  btnRunText: {
    color: '#1A1407',
    fontWeight: '700',
    fontSize: 14,
  },
  btnGhost: {
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: palette.borderStrong,
    paddingHorizontal: 14,
    paddingVertical: 8,
    borderRadius: 10,
  },
  btnGhostText: {
    color: palette.text,
    fontWeight: '600',
    fontSize: 14,
  },
  pressed: {
    opacity: 0.75,
  },
  disabled: {
    opacity: 0.35,
  },
  empty: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    gap: space.md,
    padding: space.xl,
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
