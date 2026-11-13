# Reading Room

React Native (bare) UI for a script workbench: tabbed Luau editor, script library, console and settings.
This directory is the JavaScript layer only. It has no execution core. Execution goes through the
`ExecutorBridge` interface in `src/bridge/types.ts`, and whatever backend you attach implements that.

## What's in it

- **Editor**: tabbed scripts with dirty markers, line numbers, active-line highlight, Luau syntax
  highlighting, completion chips for Roblox globals/services/methods and Luau keywords, Save/Update
  into the library, Run through the bridge.
- **Library**: local persistence, tags, search, tag filter, swipe-to-delete, tap to open in a tab.
- **Console**: streamed output with level filters, collapsible stack traces, selectable text.
- **Settings**: backend URL, attach/detach, font size with live preview.
- **Theme**: `src/theme/tokens.ts`. Ink-and-brass palette, mono for code, one type scale.

## Wire format (WebSocket bridge)

Set the backend URL in Settings. The app connects and sends:

```json
{ "op": "exec", "chunk": "my script", "source": "print('hi')" }
```

and renders anything the backend sends back:

```json
{ "op": "log", "level": "output", "message": "hi", "trace": ["line 1", "line 2"] }
```

Levels: `output`, `info`, `warn`, `error`. Frames that aren't valid JSON show up as warnings.

## Setup

The repo holds only the JS side. Generate the native shell once, then copy `src/`, `index.js`,
`app.json`, `babel.config.js` and `metro.config.js` over it:

```bash
npx @react-native-community/cli@15 init ReadingRoom --version 0.76.5 --skip-install
# copy this directory's files over the generated project, then:
npm install
npx react-native run-android
```

Reanimated and gesture-handler need the normal native setup from their docs. The Babel plugin is already
in `babel.config.js`.

## Status

- Passes `npm run typecheck` (strict, 16 source files). Luau tokenizer round-trips source text exactly.
- Not yet run on a device. First launch on Android is the real test.
- Autocomplete is a curated list in `src/editor/globals.ts`. Replace it with the Roblox API dump when you
  want the full surface. Only the list needs to change.
