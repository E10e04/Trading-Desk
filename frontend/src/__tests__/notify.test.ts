import { afterEach, describe, expect, it, vi } from 'vitest';
import { createCoalescer, sendSystemNotification } from '../lib/desktopNotify';

afterEach(() => { vi.useRealTimers(); delete (window as any).desktop; localStorage.clear(); });

describe('notifications du bureau', () => {
  it('regroupe une rafale en une seule notification', () => {
    vi.useFakeTimers();
    const send = vi.fn();
    const c = createCoalescer(send, 1000);
    ['A', 'B', 'C', 'D'].forEach((t) => c.push({ title: t }));
    vi.advanceTimersByTime(1100);
    expect(send).toHaveBeenCalledTimes(1);
    expect(send.mock.calls[0][0].title).toBe('4 nouvelles notifications');
  });

  it('transmet telle quelle une notification isolée', () => {
    vi.useFakeTimers();
    const send = vi.fn();
    const c = createCoalescer(send, 500);
    c.push({ title: 'BTCUSD +3 %', body: 'x' });
    vi.advanceTimersByTime(600);
    expect(send).toHaveBeenCalledWith({ title: 'BTCUSD +3 %', body: 'x' });
  });

  it('envoie au système quand la fenêtre est en arrière-plan, ou en test forcé', async () => {
    const notify = vi.fn().mockResolvedValue(true);
    (window as any).desktop = { isDesktop: true, platform: 'win32', notify };
    vi.spyOn(document, 'hasFocus').mockReturnValue(true);
    expect(await sendSystemNotification({ title: 'a' })).toBe(false); // au premier plan : la bulle suffit
    expect(notify).not.toHaveBeenCalled();
    expect(await sendSystemNotification({ title: 'a' }, { force: true })).toBe(true);
    vi.spyOn(document, 'hasFocus').mockReturnValue(false);
    expect(await sendSystemNotification({ title: 'b', body: 'c' })).toBe(true);
    expect(notify).toHaveBeenLastCalledWith(expect.objectContaining({ title: 'b', route: '/notifications' }));
  });

  it('respecte l’interrupteur', async () => {
    const notify = vi.fn().mockResolvedValue(true);
    (window as any).desktop = { isDesktop: true, platform: 'win32', notify };
    vi.spyOn(document, 'hasFocus').mockReturnValue(false);
    localStorage.setItem('td.notif.desktop', 'false');
    expect(await sendSystemNotification({ title: 'x' })).toBe(false);
  });
});
