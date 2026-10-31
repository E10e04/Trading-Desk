// Flux temps réel : une seule WebSocket partagée, abonnements comptés, reconnexion automatique.
import { useEffect, useMemo, useRef, useSyncExternalStore } from 'react';
import { getToken, onTokenChange } from './api';

export interface Quote {
  symbol: string;
  bid: number | null;
  ask: number | null;
  mid: number;
  spread: number | null;
  ts: string;
  recv: string;
  lag_s?: number | null;
  source?: string;
  freshness: 'live' | 'stale' | 'closed' | 'unknown';
  age_s: number | null;
  market_state: string;
  digits: number | null;
}
export type ConnState = 'connecting' | 'open' | 'closed';
type Handler = (data: any) => void;

class Live {
  quotes = new Map<string, Quote>();
  state: ConnState = 'closed';
  lastMessageAt = 0;
  private ws: WebSocket | null = null;
  private refs = new Map<string, number>();
  private symListeners = new Map<string, Set<() => void>>();
  private stateListeners = new Set<() => void>();
  private handlers = new Map<string, Set<Handler>>();
  private retry = 0;
  private timer: number | null = null;
  private versions = new Map<string, number>();
  private stateVersion = 0;
  private started = false;

  start(): void {
    if (this.started) return;
    this.started = true;
    onTokenChange(() => {
      this.ws?.close();
    });
    this.connect();
  }

  private setState(s: ConnState): void {
    this.state = s;
    this.stateVersion++;
    this.stateListeners.forEach((l) => l());
  }

  private connect(): void {
    if (this.timer !== null) clearTimeout(this.timer);
    this.timer = null;
    const token = getToken();
    if (!token) {
      this.timer = window.setTimeout(() => this.connect(), 1500);
      return;
    }
    this.setState('connecting');
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    const ws = new WebSocket(`${proto}://${location.host}/ws?token=${encodeURIComponent(token)}`);
    this.ws = ws;
    ws.onopen = () => {
      this.retry = 0;
      this.setState('open');
      this.sendSubs();
    };
    ws.onmessage = (ev) => {
      this.lastMessageAt = Date.now();
      let msg: any;
      try {
        msg = JSON.parse(ev.data);
      } catch {
        return;
      }
      if (msg.type === 'ticks') {
        for (const q of msg.data as Quote[]) this.setQuote(q);
      } else {
        this.handlers.get(msg.type)?.forEach((h) => h(msg.data));
      }
    };
    ws.onclose = () => {
      this.ws = null;
      this.setState('closed');
      const delay = Math.min(15000, 500 * 2 ** this.retry++);
      this.timer = window.setTimeout(() => this.connect(), delay);
    };
    ws.onerror = () => ws.close();
  }

  private setQuote(q: Quote): void {
    this.quotes.set(q.symbol, q);
    this.versions.set(q.symbol, (this.versions.get(q.symbol) ?? 0) + 1);
    this.symListeners.get(q.symbol)?.forEach((l) => l());
    this.symListeners.get('*')?.forEach((l) => l());
  }

  private sendSubs(): void {
    if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify({ op: 'subscribe', symbols: [...this.refs.keys()] }));
  }

  watch(symbols: string[]): () => void {
    let changed = false;
    for (const s of symbols) {
      const n = this.refs.get(s) ?? 0;
      if (n === 0) changed = true;
      this.refs.set(s, n + 1);
    }
    if (changed) this.sendSubs();
    return () => {
      let ch = false;
      for (const s of symbols) {
        const n = (this.refs.get(s) ?? 1) - 1;
        if (n <= 0) {
          this.refs.delete(s);
          ch = true;
        } else this.refs.set(s, n);
      }
      if (ch) this.sendSubs();
    };
  }

  onSymbol(symbol: string, cb: () => void): () => void {
    if (!this.symListeners.has(symbol)) this.symListeners.set(symbol, new Set());
    this.symListeners.get(symbol)!.add(cb);
    return () => this.symListeners.get(symbol)?.delete(cb);
  }
  version(symbol: string): number {
    return this.versions.get(symbol) ?? 0;
  }
  onState(cb: () => void): () => void {
    this.stateListeners.add(cb);
    return () => this.stateListeners.delete(cb);
  }
  on(type: string, h: Handler): () => void {
    if (!this.handlers.has(type)) this.handlers.set(type, new Set());
    this.handlers.get(type)!.add(h);
    return () => this.handlers.get(type)?.delete(h);
  }
  seed(q: Quote): void {
    const cur = this.quotes.get(q.symbol);
    if (!cur || cur.recv < q.recv) this.setQuote(q);
  }
}

export const live = new Live();

export function useConnState(): ConnState {
  return useSyncExternalStore(
    (cb) => live.onState(cb),
    () => live.state,
  );
}

/** Cotation temps réel d'un instrument (abonnement automatique). */
export function useQuote(symbol: string | null | undefined): Quote | undefined {
  useEffect(() => (symbol ? live.watch([symbol]) : undefined), [symbol]);
  useSyncExternalStore(
    (cb) => (symbol ? live.onSymbol(symbol, cb) : () => {}),
    () => (symbol ? live.version(symbol) : 0),
  );
  return symbol ? live.quotes.get(symbol) : undefined;
}

/** Plusieurs cotations ; se met à jour au plus 4 fois par seconde. */
export function useQuotes(symbols: string[]): Map<string, Quote> {
  const key = symbols.join(',');
  const list = useMemo(() => symbols, [key]); // eslint-disable-line react-hooks/exhaustive-deps
  const tick = useRef(0);
  useEffect(() => live.watch(list), [list]);
  useSyncExternalStore(
    (cb) => {
      let t: number | null = null;
      const throttled = () => {
        if (t === null)
          t = window.setTimeout(() => {
            t = null;
            tick.current++;
            cb();
          }, 250);
      };
      const offs = list.map((s) => live.onSymbol(s, throttled));
      return () => {
        offs.forEach((o) => o());
        if (t !== null) clearTimeout(t);
      };
    },
    () => tick.current + list.reduce((a, s) => a + live.version(s), 0),
  );
  return live.quotes;
}

export function useLiveEvent(type: string, handler: Handler): void {
  const ref = useRef(handler);
  ref.current = handler;
  useEffect(() => live.on(type, (d) => ref.current(d)), [type]);
}
