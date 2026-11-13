import { Platform } from 'react-native';

export const palette = {
  bg: '#0B0D10',
  surface: '#12151A',
  surfaceRaised: '#181C23',
  surfaceSunken: '#0E1115',
  border: '#232831',
  borderStrong: '#343B48',
  text: '#E7E9EE',
  textMuted: '#8D95A3',
  textFaint: '#5B6372',
  accent: '#E9B44C',
  accentSoft: 'rgba(233, 180, 76, 0.14)',
  accentLine: 'rgba(233, 180, 76, 0.06)',
  success: '#86C98A',
  warn: '#E8893C',
  error: '#E5606F',
  info: '#7DB0E0',
  syntax: {
    keyword: '#E9B44C',
    builtin: '#7DB0E0',
    string: '#A9D18E',
    comment: '#5B6372',
    number: '#F0A07A',
    operator: '#9AA3B2',
    plain: '#E7E9EE',
  },
} as const;

export const fonts = {
  mono: Platform.select({ ios: 'Menlo', android: 'monospace', default: 'monospace' }),
};

export const space = { xs: 4, sm: 8, md: 12, lg: 16, xl: 24 } as const;

export const radius = { sm: 6, md: 10, lg: 14, pill: 999 } as const;

export const type = {
  display: { fontSize: 26, lineHeight: 32, fontWeight: '700', letterSpacing: -0.3 },
  title: { fontSize: 17, lineHeight: 22, fontWeight: '600' },
  body: { fontSize: 15, lineHeight: 21, fontWeight: '400' },
  caption: { fontSize: 12, lineHeight: 16, fontWeight: '500' },
  label: { fontSize: 11, lineHeight: 14, fontWeight: '700', letterSpacing: 1.2, textTransform: 'uppercase' },
} as const;
