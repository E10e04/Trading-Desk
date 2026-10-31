import { useEffect, useMemo, useRef, useState } from 'react';
import { Icons } from './icons';
import { Avatar } from './Visual';
import { CLASS_LABEL } from '../lib/format';
import type { Instrument } from '../lib/market';

/** Liste déroulante d'instruments avec barre de recherche (symbole ou description), navigation au clavier. */
export function InstrumentPicker({ instruments, value, onChange, label = 'Instrument', placeholder = 'Choisir un instrument…' }: {
  instruments: Instrument[]; value: string; onChange: (symbol: string) => void; label?: string; placeholder?: string;
}) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState('');
  const [idx, setIdx] = useState(0);
  const ref = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const norm = (s: string) => s.toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '');
  const list = useMemo(() => {
    const t = norm(text.trim());
    const rows = !t ? instruments : instruments.filter((i) => norm(i.symbol).includes(t) || norm(i.description ?? '').includes(t) || norm(CLASS_LABEL[i.asset_class] ?? i.asset_class).includes(t));
    // les symboles qui commencent par la saisie d'abord, puis ordre alphabétique
    return [...rows].sort((a, b) => Number(norm(b.symbol).startsWith(t)) - Number(norm(a.symbol).startsWith(t)) || a.symbol.localeCompare(b.symbol)).slice(0, 150);
  }, [instruments, text]);
  useEffect(() => setIdx(0), [text, open]);
  useEffect(() => {
    if (!open) return;
    input.current?.focus();
    const on = (e: MouseEvent) => !ref.current?.contains(e.target as Node) && setOpen(false);
    document.addEventListener('mousedown', on);
    return () => document.removeEventListener('mousedown', on);
  }, [open]);
  useEffect(() => { ref.current?.querySelector('[aria-selected="true"]')?.scrollIntoView({ block: 'nearest' }); }, [idx]);
  const choose = (s: string) => { onChange(s); setOpen(false); setText(''); };
  const cur = instruments.find((i) => i.symbol === value);
  return (
    <div className="rel picker" ref={ref}>
      <button type="button" className="input picker-btn" aria-label={label} aria-haspopup="listbox" aria-expanded={open} onClick={() => setOpen(!open)}>
        {cur ? <><Avatar symbol={cur.symbol} size={20} /><b>{cur.symbol}</b><span className="muted picker-d">{cur.description}</span></> : <span className="muted">{placeholder}</span>}
        <span className="si">{Icons.chevron}</span>
      </button>
      {open && (
        <div className="menu picker-menu" onKeyDown={(e) => {
          if (e.key === 'Escape') setOpen(false);
          if (e.key === 'ArrowDown') { e.preventDefault(); setIdx((i) => Math.min(list.length - 1, i + 1)); }
          if (e.key === 'ArrowUp') { e.preventDefault(); setIdx((i) => Math.max(0, i - 1)); }
          if (e.key === 'Enter') { e.preventDefault(); if (list[idx]) choose(list[idx].symbol); }
        }}>
          <div className="head"><input ref={input} className="input" placeholder={`Rechercher parmi ${instruments.length} instruments (symbole ou nom)`} aria-label={`Rechercher : ${label}`} value={text} onChange={(e) => setText(e.target.value)} /></div>
          <div className="switcher-list" role="listbox" aria-label={label}>
            {!list.length && <div className="state"><strong>Aucun résultat</strong><span>Essayez un autre symbole ou un autre nom.</span></div>}
            {list.map((i, n) => (
              <button type="button" key={i.symbol} className="switcher-item" role="option" aria-selected={n === idx} onMouseEnter={() => setIdx(n)} onClick={() => choose(i.symbol)}>
                <Avatar symbol={i.symbol} size={22} /><b style={{ minWidth: 76 }}>{i.symbol}</b><span className="d">{i.description || CLASS_LABEL[i.asset_class]}</span><span className="muted">{CLASS_LABEL[i.asset_class] ?? i.asset_class}</span>
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
