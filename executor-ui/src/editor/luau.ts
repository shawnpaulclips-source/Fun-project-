import { BUILTIN_NAMES, KEYWORD_NAMES } from './globals';

export type TokenKind = 'plain' | 'keyword' | 'builtin' | 'string' | 'comment' | 'number' | 'operator';

export interface Token {
  kind: TokenKind;
  text: string;
}

const NUMBER_RE = /0[xX][0-9a-fA-F_]+|(?:\d[\d_]*\.?[\d_]*|\.\d[\d_]*)(?:[eE][+-]?\d+)?/y;
const WORD_RE = /[A-Za-z_][A-Za-z0-9_]*/y;
const OPERATOR_RE = /[+\-*/%^#&~|<>=(){}[\];:,.]/;

function longBracketLevel(src: string, at: number): number {
  let i = at + 1;
  let level = 0;
  while (src[i] === '=') {
    level += 1;
    i += 1;
  }
  return src[i] === '[' ? level : -1;
}

function closeLongBracket(src: string, at: number, level: number): number {
  const close = `]${'='.repeat(level)}]`;
  const idx = src.indexOf(close, at + level + 2);
  return idx === -1 ? src.length : idx + close.length;
}

export function tokenize(src: string): Token[] {
  const out: Token[] = [];
  const push = (kind: TokenKind, text: string): void => {
    if (text.length === 0) return;
    const last = out[out.length - 1];
    if (last && last.kind === kind) last.text += text;
    else out.push({ kind, text });
  };

  const n = src.length;
  let i = 0;

  while (i < n) {
    const c = src[i];

    if (c === '-' && src[i + 1] === '-') {
      if (src[i + 2] === '[') {
        const level = longBracketLevel(src, i + 2);
        if (level >= 0) {
          const end = closeLongBracket(src, i + 2, level);
          push('comment', src.slice(i, end));
          i = end;
          continue;
        }
      }
      const eol = src.indexOf('\n', i);
      const end = eol === -1 ? n : eol;
      push('comment', src.slice(i, end));
      i = end;
      continue;
    }

    if (c === '[') {
      const level = longBracketLevel(src, i);
      if (level >= 0) {
        const end = closeLongBracket(src, i, level);
        push('string', src.slice(i, end));
        i = end;
        continue;
      }
    }

    if (c === '"' || c === "'") {
      let j = i + 1;
      while (j < n && src[j] !== c && src[j] !== '\n') {
        j += src[j] === '\\' ? 2 : 1;
      }
      j = Math.min(j, n);
      const end = j < n && src[j] === c ? j + 1 : j;
      push('string', src.slice(i, end));
      i = end;
      continue;
    }

    if (/[0-9]/.test(c) || (c === '.' && /[0-9]/.test(src[i + 1] ?? ''))) {
      NUMBER_RE.lastIndex = i;
      const m = NUMBER_RE.exec(src);
      if (m) {
        push('number', m[0]);
        i += m[0].length;
        continue;
      }
    }

    if (/[A-Za-z_]/.test(c)) {
      WORD_RE.lastIndex = i;
      const m = WORD_RE.exec(src);
      const word = m ? m[0] : c;
      const kind: TokenKind = KEYWORD_NAMES.has(word)
        ? 'keyword'
        : BUILTIN_NAMES.has(word)
          ? 'builtin'
          : 'plain';
      push(kind, word);
      i += word.length;
      continue;
    }

    if (/\s/.test(c)) {
      push('plain', c);
      i += 1;
      continue;
    }

    push(OPERATOR_RE.test(c) ? 'operator' : 'plain', c);
    i += 1;
  }

  return out;
}
