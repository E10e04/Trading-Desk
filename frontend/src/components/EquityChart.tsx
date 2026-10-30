import { useEffect, useRef } from 'react';
import { createChart, type IChartApi, type ISeriesApi, type UTCTimestamp } from 'lightweight-charts';
import { useResolvedTheme } from '../lib/theme';

const css = (n: string) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
export function EquityChart({ points, height = 220 }: { points: { ts: string; equity: number }[]; height?: number }) {
  const box = useRef<HTMLDivElement>(null);
  const ch = useRef<IChartApi | null>(null);
  const se = useRef<ISeriesApi<'Area'> | null>(null);
  const theme = useResolvedTheme();
  useEffect(() => {
    if (!box.current) return;
    const c = createChart(box.current, { height, width: box.current.clientWidth, layout: { background: { color: css('--panel') }, textColor: css('--text-2'), fontFamily: css('--font') }, grid: { vertLines: { visible: false }, horzLines: { color: css('--grid') } }, rightPriceScale: { borderColor: css('--border') }, timeScale: { borderColor: css('--border'), timeVisible: true } });
    const s = c.addAreaSeries({ lineColor: css('--accent'), topColor: css('--accent-soft'), bottomColor: 'transparent', lineWidth: 2 });
    ch.current = c;
    se.current = s;
    const ro = new ResizeObserver(() => box.current && c.applyOptions({ width: box.current.clientWidth }));
    ro.observe(box.current);
    return () => { ro.disconnect(); c.remove(); };
  }, [height]);
  useEffect(() => {
    ch.current?.applyOptions({ layout: { background: { color: css('--panel') }, textColor: css('--text-2') }, grid: { horzLines: { color: css('--grid') } }, rightPriceScale: { borderColor: css('--border') }, timeScale: { borderColor: css('--border') } });
    se.current?.applyOptions({ lineColor: css('--accent'), topColor: css('--accent-soft') });
  }, [theme]);
  useEffect(() => {
    const seen = new Set<number>();
    const d = points.map((p) => ({ time: Math.floor(new Date(p.ts).getTime() / 1000) as UTCTimestamp, value: p.equity })).filter((p) => (seen.has(p.time) ? false : (seen.add(p.time), true))).sort((a, b) => a.time - b.time);
    se.current?.setData(d);
    ch.current?.timeScale().fitContent();
  }, [points]);
  return <div ref={box} style={{ height }} data-testid="equity-chart" />;
}
