import { useEffect, useRef, useState } from 'react';
import { createChart, CrosshairMode, LineStyle, type IChartApi, type IPriceLine, type ISeriesApi, type UTCTimestamp } from 'lightweight-charts';
import { api } from '../lib/api';
import { useQuote } from '../lib/live';
import { useResolvedTheme } from '../lib/theme';
import { age, price } from '../lib/format';

export const TFS = ['1m', '5m', '15m', '30m', '1h', '4h', '1d'] as const;
export type TF = (typeof TFS)[number];
const STEP: Record<TF, number> = { '1m': 60, '5m': 300, '15m': 900, '30m': 1800, '1h': 3600, '4h': 14400, '1d': 86400 };

export interface PriceLine { price: number; title: string; kind: 'entry' | 'stop' | 'target' | 'level' }
interface Bar { t: number; o: number; h: number; l: number; c: number }

/** lightweight-charts affiche l'heure UTC ; TradingView affiche l'heure locale. On décale l'axe pour que les deux concordent. */
const tzShift = (t: number) => t - new Date(t * 1000).getTimezoneOffset() * 60;

function css(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}
function themeOptions() {
  return {
    layout: { background: { color: css('--panel') }, textColor: css('--text-2'), fontFamily: css('--font'), fontSize: 12 },
    grid: { vertLines: { color: css('--grid') }, horzLines: { color: css('--grid') } },
    rightPriceScale: { borderColor: css('--border') },
    timeScale: { borderColor: css('--border') },
    crosshair: { mode: CrosshairMode.Normal, vertLine: { color: css('--text-2'), labelBackgroundColor: css('--panel-2') }, horzLine: { color: css('--text-2'), labelBackgroundColor: css('--panel-2') } },
  };
}
const LINE_COLOR = { entry: '--accent', stop: '--error', target: '--success', level: '--warning' } as const;

export function Chart({ symbol, tf, height = 420, lines = [], digits }: { symbol: string; tf: TF; height?: number; lines?: PriceLine[]; digits?: number | null }) {
  const box = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  const series = useRef<ISeriesApi<'Candlestick'> | null>(null);
  const last = useRef<Bar | null>(null);
  const priceLines = useRef<IPriceLine[]>([]);
  const theme = useResolvedTheme();
  const [state, setState] = useState<{ loading: boolean; error: string | null; empty: boolean; fresh: string | null }>({ loading: true, error: null, empty: false, fresh: null });
  const q = useQuote(symbol);

  // création / destruction
  useEffect(() => {
    if (!box.current) return;
    const c = createChart(box.current, { ...themeOptions(), height, autoSize: false, width: box.current.clientWidth });
    const s = c.addCandlestickSeries({ upColor: css('--chart-up'), downColor: css('--chart-down'), wickUpColor: css('--chart-up'), wickDownColor: css('--chart-down'), borderVisible: false });
    chart.current = c;
    series.current = s;
    const ro = new ResizeObserver(() => box.current && c.applyOptions({ width: box.current.clientWidth }));
    ro.observe(box.current);
    return () => {
      ro.disconnect();
      c.remove();
      chart.current = null;
      series.current = null;
      priceLines.current = [];
    };
  }, [height]);

  // thème
  useEffect(() => {
    chart.current?.applyOptions(themeOptions());
    series.current?.applyOptions({ upColor: css('--chart-up'), downColor: css('--chart-down'), wickUpColor: css('--chart-up'), wickDownColor: css('--chart-down') });
  }, [theme]);

  // données
  useEffect(() => {
    let dead = false;
    setState({ loading: true, error: null, empty: false, fresh: null });
    last.current = null;
    api<{ bars: Bar[]; freshness: { status: string; age_s: number | null } }>(`/api/bars/${encodeURIComponent(symbol)}?tf=${tf}&limit=600`)
      .then((r) => {
        if (dead || !series.current) return;
        const d = r.bars.map((b) => ({ time: tzShift(b.t) as UTCTimestamp, open: b.o, high: b.h, low: b.l, close: b.c }));
        const dg = digits ?? 5;
        series.current.applyOptions({ priceFormat: { type: 'price', precision: dg, minMove: 1 / 10 ** dg } });
        series.current.setData(d);
        last.current = r.bars.length ? r.bars[r.bars.length - 1] : null;
        chart.current?.timeScale().fitContent();
        setState({ loading: false, error: null, empty: d.length === 0, fresh: r.freshness.status });
      })
      .catch((e: Error) => !dead && setState({ loading: false, error: e.message, empty: false, fresh: null }));
    return () => {
      dead = true;
    };
  }, [symbol, tf, digits]);

  // bougie courante alimentée par le flux (uniquement si la cotation est fraîche)
  useEffect(() => {
    if (!q || !series.current || q.freshness !== 'live') return;
    const step = STEP[tf];
    const now = Math.floor(new Date(q.recv).getTime() / 1000);
    const bucket = Math.floor(now / step) * step;
    const l = last.current;
    let b: Bar;
    if (l && l.t === bucket) b = { ...l, h: Math.max(l.h, q.mid), l: Math.min(l.l, q.mid), c: q.mid };
    else if (!l || bucket > l.t) b = { t: bucket, o: q.mid, h: q.mid, l: q.mid, c: q.mid };
    else return;
    last.current = b;
    series.current.update({ time: tzShift(b.t) as UTCTimestamp, open: b.o, high: b.h, low: b.l, close: b.c });
  }, [q, tf]);

  // lignes de prix (entrée / stop / cible)
  const key = JSON.stringify(lines);
  useEffect(() => {
    const s = series.current;
    if (!s) return;
    priceLines.current.forEach((p) => s.removePriceLine(p));
    priceLines.current = lines.map((l) => s.createPriceLine({ price: l.price, color: css(LINE_COLOR[l.kind]), lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: l.title }));
  }, [key, state.loading]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="chart-box" style={{ height }} data-testid="chart">
      <div className="chart-legend">
        <b>{symbol}</b>
        <span className="muted">{tf}</span>
        {q && <span className={q.freshness === 'live' ? 'up' : 'warn'}>{price(q.mid, digits)}</span>}
        {q && q.freshness === 'live' && q.lag_s != null && Math.abs(q.lag_s) > 10 && <span className="warn" title="Écart entre l’horodatage du fournisseur et la réception">source en retard de {Math.round(q.lag_s)} s</span>}
        {q && q.freshness !== 'live' && <span className="warn">données {q.freshness === 'closed' ? 'marché fermé' : `périmées (${age(q.age_s)})`}</span>}
      </div>
      <div ref={box} style={{ height }} />
      {state.loading && (
        <div className="chart-overlay" aria-busy="true">
          <span className="spinner" />
        </div>
      )}
      {state.error && (
        <div className="chart-overlay" role="alert">
          <div className="state error">
            <strong>Graphique indisponible</strong>
            <span>{state.error}</span>
          </div>
        </div>
      )}
      {state.empty && !state.error && (
        <div className="chart-overlay">
          <div className="state">
            <strong>Aucune bougie disponible</strong>
            <span>La source n’a pas renvoyé d’historique pour {symbol} en {tf}.</span>
          </div>
        </div>
      )}
    </div>
  );
}
