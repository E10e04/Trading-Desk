import { createContext, useCallback, useContext, useEffect, useId, useRef, useState, type ReactNode } from 'react';
import { Icons } from './icons';

/* ---------------------------------------------------------------- toasts */
type Level = 'info' | 'success' | 'warning' | 'error';
interface ToastItem { id: number; level: Level; title: string; body?: string }
const ToastCtx = createContext<(level: Level, title: string, body?: string) => void>(() => {});
export const useToast = () => useContext(ToastCtx);
export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const seq = useRef(0);
  const push = useCallback((level: Level, title: string, body?: string) => {
    const id = ++seq.current;
    setItems((x) => [...x.slice(-3), { id, level, title, body }]);
    window.setTimeout(() => setItems((x) => x.filter((i) => i.id !== id)), level === 'error' ? 9000 : 5000);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="toasts" role="status" aria-live="polite">
        {items.map((t) => (
          <div key={t.id} className={`toast ${t.level}`}>
            <b>{t.title}</b>
            {t.body && <span className="muted">{t.body}</span>}
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}

/* ---------------------------------------------------------------- structure */
export function Page({ title, subtitle, actions, children }: { title: string; subtitle?: ReactNode; actions?: ReactNode; children: ReactNode }) {
  useEffect(() => {
    document.title = `${title} — Monkora`;
  }, [title]);
  return (
    <>
      <div className="page-head">
        <div>
          <h1>{title}</h1>
          {subtitle && <p>{subtitle}</p>}
        </div>
        {actions && <div className="row">{actions}</div>}
      </div>
      <div className="stack">{children}</div>
    </>
  );
}
export function Panel({ title, actions, children, flush, className = '' }: { title?: ReactNode; actions?: ReactNode; children: ReactNode; flush?: boolean; className?: string }) {
  return (
    <section className={`panel ${className}`}>
      {(title || actions) && (
        <header>
          <h2>{title}</h2>
          {actions && <div className="row">{actions}</div>}
        </header>
      )}
      <div className={`body ${flush ? 'flush' : ''}`}>{children}</div>
    </section>
  );
}
export function Kpi({ label, value, sub, tone }: { label: string; value: ReactNode; sub?: ReactNode; tone?: 'up' | 'down' | 'flat' | 'warn' }) {
  return (
    <div className="panel kpi">
      <div className="l">{label}</div>
      <div className={`v ${tone ?? ''}`}>{value}</div>
      {sub && <div className="s">{sub}</div>}
    </div>
  );
}

/* ---------------------------------------------------------------- états */
export function Pill({ kind = 'mute', children, plain }: { kind?: 'ok' | 'warn' | 'err' | 'info' | 'mute'; children: ReactNode; plain?: boolean }) {
  return <span className={`pill ${kind} ${plain ? 'plain' : ''}`}>{children}</span>;
}
export function Loading({ rows = 4 }: { rows?: number }) {
  return (
    <div className="state" aria-busy="true" aria-live="polite" style={{ alignItems: 'stretch', gap: 10, padding: 18 }}>
      <span className="sr-only">Chargement…</span>
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="skeleton" style={{ width: `${95 - i * 12}%` }} />
      ))}
    </div>
  );
}
export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="state error" role="alert">
      <strong>Impossible de charger les données</strong>
      <span>{message}</span>
      {onRetry && (
        <button className="btn sm" onClick={onRetry}>
          Réessayer
        </button>
      )}
    </div>
  );
}
export function Empty({ title, hint, action }: { title: string; hint?: ReactNode; action?: ReactNode }) {
  return (
    <div className="state">
      <strong>{title}</strong>
      {hint && <span style={{ maxWidth: '52ch' }}>{hint}</span>}
      {action}
    </div>
  );
}
export function Unavailable({ what, why }: { what: string; why?: string }) {
  return <Empty title={`${what} indisponible`} hint={why ?? 'La source ne répond pas ou ne fournit pas cette donnée pour le moment. Rien n’est estimé à sa place.'} />;
}
export function Banner({ kind = 'info', children }: { kind?: 'info' | 'warn' | 'err'; children: ReactNode }) {
  return <div className={`banner ${kind}`} role={kind === 'err' ? 'alert' : undefined}>{children}</div>;
}
export function Async<T>({ state, children, empty }: { state: { data: T | null; error: string | null; loading: boolean; reload: () => void }; children: (d: T) => ReactNode; empty?: (d: T) => boolean }) {
  if (state.error && !state.data) return <ErrorState message={state.error} onRetry={state.reload} />;
  if (state.loading && !state.data) return <Loading />;
  if (!state.data) return null;
  void empty;
  return <>{children(state.data)}</>;
}

/* ---------------------------------------------------------------- formulaires */
export function Field({ label, hint, children }: { label: string; hint?: ReactNode; children: (id: string) => ReactNode }) {
  const id = useId();
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      {children(id)}
      {hint && <span className="hint">{hint}</span>}
    </div>
  );
}
export function Switch({ checked, onChange, label, disabled }: { checked: boolean; onChange: (v: boolean) => void; label: string; disabled?: boolean }) {
  return (
    <label className="switch" title={label}>
      <input type="checkbox" role="switch" aria-label={label} checked={checked} disabled={disabled} onChange={(e) => onChange(e.target.checked)} />
      <span />
    </label>
  );
}
export function Segmented<T extends string>({ value, onChange, options, label }: { value: T; onChange: (v: T) => void; options: { value: T; label: ReactNode; title?: string }[]; label: string }) {
  return (
    <div className="seg" role="group" aria-label={label}>
      {options.map((o) => (
        <button key={o.value} type="button" aria-pressed={value === o.value} title={o.title} onClick={() => onChange(o.value)}>
          {o.label}
        </button>
      ))}
    </div>
  );
}
export function Tabs<T extends string>({ value, onChange, tabs }: { value: T; onChange: (v: T) => void; tabs: { id: T; label: string }[] }) {
  return (
    <div className="tabs" role="tablist">
      {tabs.map((t) => (
        <button key={t.id} role="tab" aria-selected={value === t.id} onClick={() => onChange(t.id)}>
          {t.label}
        </button>
      ))}
    </div>
  );
}

/* ---------------------------------------------------------------- modale */
export function Modal({ title, onClose, children, footer }: { title: string; onClose: () => void; children: ReactNode; footer?: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const prev = document.activeElement as HTMLElement | null;
    ref.current?.querySelector<HTMLElement>('input,select,textarea,button')?.focus();
    const on = (e: KeyboardEvent) => e.key === 'Escape' && onClose();
    window.addEventListener('keydown', on);
    return () => {
      window.removeEventListener('keydown', on);
      prev?.focus?.();
    };
  }, [onClose]);
  return (
    <div className="modal-back" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal" role="dialog" aria-modal="true" aria-label={title} ref={ref}>
        <header>
          <h2>{title}</h2>
          <button className="btn ghost sm" onClick={onClose} aria-label="Fermer">
            {Icons.x}
          </button>
        </header>
        <div className="body">{children}</div>
        {footer && <footer>{footer}</footer>}
      </div>
    </div>
  );
}
export function Confirm({ title, message, confirmLabel, danger, onConfirm, onClose }: { title: string; message: ReactNode; confirmLabel: string; danger?: boolean; onConfirm: () => void | Promise<void>; onClose: () => void }) {
  const [busy, setBusy] = useState(false);
  return (
    <Modal
      title={title}
      onClose={onClose}
      footer={
        <>
          <button className="btn" onClick={onClose}>Annuler</button>
          <button
            className={`btn ${danger ? 'danger' : 'primary'}`}
            disabled={busy}
            onClick={async () => {
              setBusy(true);
              try {
                await onConfirm();
              } finally {
                setBusy(false);
                onClose();
              }
            }}
          >
            {confirmLabel}
          </button>
        </>
      }
    >
      {message}
    </Modal>
  );
}

export function JsonBlock({ data }: { data: unknown }) {
  return <pre className="json">{JSON.stringify(data, null, 2)}</pre>;
}
export function KV({ rows }: { rows: [string, ReactNode][] }) {
  return (
    <dl className="kv" style={{ margin: 0 }}>
      {rows.map(([k, v]) => (
        <div key={k} style={{ display: 'contents' }}>
          <dt>{k}</dt>
          <dd>{v ?? '—'}</dd>
        </div>
      ))}
    </dl>
  );
}

/* ---------------------------------------------------------------- tableau */
export interface Col<T> { key: string; header: ReactNode; render: (row: T) => ReactNode; num?: boolean; hideSm?: boolean; sort?: (row: T) => number | string }
export function DataTable<T>({ rows, cols, rowKey, onRow, selected, empty, maxHeight }: { rows: T[]; cols: Col<T>[]; rowKey: (r: T) => string | number; onRow?: (r: T) => void; selected?: string | number | null; empty?: ReactNode; maxHeight?: number }) {
  const [sort, setSort] = useState<{ key: string; dir: 1 | -1 } | null>(null);
  const col = cols.find((c) => c.key === sort?.key);
  const view = col?.sort && sort ? [...rows].sort((a, b) => (col.sort!(a) > col.sort!(b) ? 1 : col.sort!(a) < col.sort!(b) ? -1 : 0) * sort.dir) : rows;
  if (!rows.length) return <>{empty ?? <Empty title="Aucune donnée" />}</>;
  return (
    <div className="tbl-wrap" style={maxHeight ? { maxHeight } : undefined}>
      <table className="tbl">
        <thead>
          <tr>
            {cols.map((c) => (
              <th key={c.key} className={`${c.num ? 'num' : ''} ${c.hideSm ? 'hide-sm' : ''}`} aria-sort={sort?.key === c.key ? (sort.dir === 1 ? 'ascending' : 'descending') : undefined}>
                {c.sort ? (
                  <button className="btn ghost sm" style={{ border: 0, height: 'auto', padding: 0, color: 'inherit' }} onClick={() => setSort(sort?.key === c.key ? { key: c.key, dir: (sort.dir * -1) as 1 | -1 } : { key: c.key, dir: 1 })}>
                    {c.header}
                    {sort?.key === c.key ? (sort.dir === 1 ? ' ▲' : ' ▼') : ''}
                  </button>
                ) : (
                  c.header
                )}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {view.map((r) => {
            const k = rowKey(r);
            return (
              <tr key={k} className={`${onRow ? 'click' : ''} ${selected === k ? 'sel' : ''}`} onClick={onRow ? () => onRow(r) : undefined} tabIndex={onRow ? 0 : undefined} onKeyDown={onRow ? (e) => e.key === 'Enter' && onRow(r) : undefined}>
                {cols.map((c) => (
                  <td key={c.key} className={`${c.num ? 'num' : ''} ${c.hideSm ? 'hide-sm' : ''}`}>
                    {c.render(r)}
                  </td>
                ))}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}


/** Pages de 50 : « Précédent · 1 2 3 … 9 · Suivant », en bas d'un tableau. */
export function pageList(page: number, pages: number): (number | '…')[] {
  if (pages <= 7) return Array.from({ length: pages }, (_, i) => i + 1);
  const set = new Set([1, 2, pages - 1, pages, page - 1, page, page + 1]);
  const nums = [...set].filter((n) => n >= 1 && n <= pages).sort((a, b) => a - b);
  const out: (number | '…')[] = [];
  nums.forEach((n, i) => { if (i && n - nums[i - 1] > 1) out.push('…'); out.push(n); });
  return out;
}
export function Pager({ page, total, per = 50, onChange }: { page: number; total: number; per?: number; onChange: (p: number) => void }) {
  const pages = Math.max(1, Math.ceil(total / per));
  if (total <= 0) return null;
  const from = (page - 1) * per + 1;
  const to = Math.min(page * per, total);
  return (
    <nav className="pager" aria-label="Pagination">
      <span className="muted">{from}–{to} sur {total}</span>
      {pages > 1 && <span className="pager-pages">
        <button className="btn sm" disabled={page <= 1} onClick={() => onChange(page - 1)}>Précédent</button>
        {pageList(page, pages).map((n, i) => n === '…' ? <span key={`e${i}`} className="muted">…</span>
          : <button key={n} className={`btn sm${n === page ? ' primary' : ''}`} aria-label={`Page ${n}`} aria-current={n === page ? 'page' : undefined} onClick={() => onChange(n)}>{n}</button>)}
        <button className="btn sm" disabled={page >= pages} onClick={() => onChange(page + 1)}>Suivant</button>
      </span>}
    </nav>
  );
}
