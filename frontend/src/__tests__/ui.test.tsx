import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { Async, Confirm, DataTable, Empty, ErrorState, Pill, Segmented, Switch, Unavailable } from '../components/ui';

describe('ui', () => {
  it('Async : chargement, erreur avec réessai, données', () => {
    const reload = vi.fn();
    const { rerender } = render(<Async state={{ data: null, error: null, loading: true, reload }}>{() => <div>ok</div>}</Async>);
    expect(screen.getByText('Chargement…')).toBeTruthy();
    rerender(<Async state={{ data: null, error: 'panne', loading: false, reload }}>{() => <div>ok</div>}</Async>);
    expect(screen.getByRole('alert').textContent).toContain('panne');
    fireEvent.click(screen.getByText('Réessayer'));
    expect(reload).toHaveBeenCalled();
    rerender(<Async state={{ data: 1, error: null, loading: false, reload }}>{() => <div>ok</div>}</Async>);
    expect(screen.getByText('ok')).toBeTruthy();
  });
  it('Async conserve les données si une actualisation échoue', () => {
    render(<Async state={{ data: 5, error: 'blip', loading: false, reload: () => {} }}>{(d) => <div>valeur {d}</div>}</Async>);
    expect(screen.getByText('valeur 5')).toBeTruthy();
  });
  it('Unavailable n’invente aucune valeur', () => {
    render(<Unavailable what="Graphique" />);
    expect(screen.getByText('Graphique indisponible')).toBeTruthy();
  });
  it('DataTable : tri, ligne cliquable, état vide', () => {
    const on = vi.fn();
    const rows = [{ id: 1, n: 'B' }, { id: 2, n: 'A' }];
    const { rerender } = render(<DataTable rows={rows} rowKey={(r) => r.id} onRow={on} cols={[{ key: 'n', header: 'Nom', sort: (r) => r.n, render: (r) => r.n }]} />);
    fireEvent.click(screen.getByText('Nom'));
    expect(screen.getAllByRole('row')[1].textContent).toBe('A');
    fireEvent.click(screen.getByText('A'));
    expect(on).toHaveBeenCalledWith(rows[1]);
    rerender(<DataTable rows={[]} rowKey={(r: any) => r.id} cols={[]} empty={<Empty title="Rien" />} />);
    expect(screen.getByText('Rien')).toBeTruthy();
  });
  it('Switch et Segmented sont accessibles', () => {
    const a = vi.fn(); const b = vi.fn();
    render(<><Switch checked={false} onChange={a} label="Auto" /><Segmented label="Mode" value="x" onChange={b} options={[{ value: 'x', label: 'X' }, { value: 'y', label: 'Y' }]} /></>);
    fireEvent.click(screen.getByRole('switch', { name: 'Auto' }));
    expect(a).toHaveBeenCalledWith(true);
    fireEvent.click(screen.getByText('Y'));
    expect(b).toHaveBeenCalledWith('y');
    expect(screen.getByText('X').getAttribute('aria-pressed')).toBe('true');
  });
  it('Confirm : annuler ne déclenche pas l’action', () => {
    const ok = vi.fn(); const close = vi.fn();
    render(<Confirm title="T" message="m" confirmLabel="Oui" onConfirm={ok} onClose={close} />);
    fireEvent.click(screen.getByText('Annuler'));
    expect(ok).not.toHaveBeenCalled();
    expect(close).toHaveBeenCalled();
  });
  it('Pill et ErrorState', () => {
    render(<><Pill kind="ok">Actif</Pill><ErrorState message="boum" /></>);
    expect(screen.getByText('Actif')).toBeTruthy();
    expect(screen.getByText('boum')).toBeTruthy();
  });
});

describe('pagination', () => {
  it('liste les pages avec des points de suspension', async () => {
    const { pageList } = await import('../components/ui');
    expect(pageList(1, 4)).toEqual([1, 2, 3, 4]);
    expect(pageList(1, 12)).toEqual([1, 2, '…', 11, 12]);
    expect(pageList(6, 12)).toEqual([1, 2, '…', 5, 6, 7, '…', 11, 12]);
    expect(pageList(12, 12)).toEqual([1, 2, '…', 11, 12]);
  });
});
