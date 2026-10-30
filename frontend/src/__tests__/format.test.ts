import { describe, expect, it } from 'vitest';
import { age, ms, num, pct, price, signed, tone, usd } from '../lib/format';

describe('format', () => {
  it('affiche un tiret pour les valeurs absentes ou non finies, jamais 0', () => {
    for (const f of [num, usd, signed, pct, price, age, ms]) {
      expect(f(null as never)).toBe('—');
      expect(f(undefined as never)).toBe('—');
    }
    expect(num(Number.NaN)).toBe('—');
    expect(price(Infinity)).toBe('—');
  });
  it('formate les prix avec les décimales de l’instrument', () => {
    expect(price(1.08512, 5)).toBe('1,08512');
    expect(price(64250.5, 2)).toBe('64250,50');
  });
  it('signe les variations', () => {
    expect(signed(2.5)).toBe('+2,50');
    expect(signed(-2.5)).toBe('-2,50');
    expect(pct(1.234)).toContain('+1,23');
  });
  it('tone', () => {
    expect(tone(1)).toBe('up');
    expect(tone(-1)).toBe('down');
    expect(tone(0)).toBe('flat');
    expect(tone(null)).toBe('flat');
  });
  it('âge lisible', () => {
    expect(age(30)).toBe('30 s');
    expect(age(600)).toBe('10 min');
    expect(age(7200)).toBe('2 h');
  });
});
