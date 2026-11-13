import React, { useState, type ReactNode } from 'react';
import { StyleSheet, Text, View } from 'react-native';
import { GestureHandlerRootView } from 'react-native-gesture-handler';
import Animated, { FadeIn, FadeOut } from 'react-native-reanimated';
import { SafeAreaProvider, useSafeAreaInsets } from 'react-native-safe-area-context';
import { BottomNav, type Screen } from './components/BottomNav';
import { BridgeProvider, STATUS_LABEL, useBridge } from './bridge/BridgeContext';
import type { BridgeStatus } from './bridge/types';
import { ConsoleScreen } from './screens/ConsoleScreen';
import { EditorScreen } from './screens/EditorScreen';
import { LibraryScreen } from './screens/LibraryScreen';
import { SettingsScreen } from './screens/SettingsScreen';
import { newId } from './state/ids';
import { StoreProvider, useStore, type LibraryScript } from './state/store';
import { palette, radius, space, type as typeScale } from './theme/tokens';

export default function App() {
  return (
    <GestureHandlerRootView style={styles.fill}>
      <SafeAreaProvider>
        <StoreProvider>
          <Root />
        </StoreProvider>
      </SafeAreaProvider>
    </GestureHandlerRootView>
  );
}

function Root() {
  const { state, hydrated } = useStore();

  if (!hydrated) {
    return (
      <View style={styles.splash}>
        <Text style={styles.splashText}>Reading Room</Text>
      </View>
    );
  }

  return (
    <BridgeProvider url={state.settings.backendUrl}>
      <Shell />
    </BridgeProvider>
  );
}

const STATUS_COLOR: Record<BridgeStatus, string> = {
  detached: palette.textFaint,
  connecting: palette.warn,
  attached: palette.success,
  error: palette.error,
};

function Shell() {
  const insets = useSafeAreaInsets();
  const { dispatch } = useStore();
  const { status } = useBridge();
  const [screen, setScreen] = useState<Screen>('editor');

  const openFromLibrary = (script: LibraryScript) => {
    dispatch({
      type: 'tab/open',
      tab: {
        id: newId(),
        name: script.name,
        tags: script.tags,
        source: script.source,
        savedSource: script.source,
        libraryId: script.id,
      },
    });
    setScreen('editor');
  };

  let body: ReactNode;
  switch (screen) {
    case 'editor':
      body = <EditorScreen />;
      break;
    case 'library':
      body = <LibraryScreen onOpen={openFromLibrary} />;
      break;
    case 'console':
      body = <ConsoleScreen />;
      break;
    case 'settings':
      body = <SettingsScreen />;
      break;
  }

  return (
    <View style={[styles.fill, styles.shell, { paddingTop: insets.top }]}>
      <View style={styles.header}>
        <Text style={styles.brand}>Reading Room</Text>
        <View style={styles.statusPill}>
          <View style={[styles.statusDot, { backgroundColor: STATUS_COLOR[status] }]} />
          <Text style={styles.statusText}>{STATUS_LABEL[status]}</Text>
        </View>
      </View>

      <View style={styles.fill}>
        <Animated.View key={screen} entering={FadeIn.duration(180)} exiting={FadeOut.duration(120)} style={styles.fill}>
          {body}
        </Animated.View>
      </View>

      <BottomNav active={screen} onChange={setScreen} bottomInset={insets.bottom} />
    </View>
  );
}

const styles = StyleSheet.create({
  fill: {
    flex: 1,
  },
  shell: {
    backgroundColor: palette.bg,
  },
  splash: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: palette.bg,
  },
  splashText: {
    ...typeScale.display,
    color: palette.accent,
  },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: space.lg,
    paddingVertical: space.md,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: palette.border,
  },
  brand: {
    ...typeScale.label,
    color: palette.accent,
  },
  statusPill: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    paddingHorizontal: 10,
    paddingVertical: 4,
    borderRadius: radius.pill,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: palette.border,
    backgroundColor: palette.surface,
  },
  statusDot: {
    width: 6,
    height: 6,
    borderRadius: 3,
  },
  statusText: {
    ...typeScale.caption,
    color: palette.textMuted,
  },
});
