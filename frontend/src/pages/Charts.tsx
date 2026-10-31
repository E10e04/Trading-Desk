import { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import { Page, Panel, Segmented } from '../components/ui';
import { Chart, TFS, type TF } from '../components/Chart';
import { usePersisted } from '../lib/hooks';
import { useMarket } from '../lib/market';

export default function Charts() {
  const { symbol: current, setSymbol, instruments, byId } = useMarket();
  const params = useParams();
  useEffect(() => { if (params.symbol) setSymbol(params.symbol.toUpperCase()); }, [params.symbol]); // eslint-disable-line react-hooks/exhaustive-deps
  const [layout, setLayout] = usePersisted<'1' | '2' | '4'>('charts.layout', '1');
  const [tf, setTf] = usePersisted<TF>('tf', '5m');
  const [extra, setExtra] = usePersisted<string[]>('charts.extra', []);
  const [, force] = useState(0);
  const [vh, setVh] = useState(typeof window === 'undefined' ? 900 : window.innerHeight);
  useEffect(() => { const f = () => setVh(window.innerHeight); window.addEventListener('resize', f); return () => window.removeEventListener('resize', f); }, []);
  const n0 = Number(layout);
  const h = n0 === 4 ? Math.max(240, Math.floor((vh - 330) / 2)) : Math.max(380, vh - 260);
  const n = Number(layout);
  const symbols = [params.symbol?.toUpperCase() ?? current, ...extra].slice(0, n);
  while (symbols.length < n) symbols.push(instruments.find((i) => !symbols.includes(i.symbol))?.symbol ?? current);
  const setAt = (i: number, s: string) => {
    if (i === 0) setSymbol(s);
    else { const e = [...extra]; e[i - 1] = s; setExtra(e); }
    force((x) => x + 1);
  };
  return (
    <Page title="Graphiques" subtitle="Bougies en temps réel. Le dernier prix met à jour la bougie courante uniquement si la cotation est fraîche."
      actions={<><Segmented label="Disposition" value={layout} onChange={setLayout} options={[{ value: '1', label: '1' }, { value: '2', label: '2' }, { value: '4', label: '4' }]} /><Segmented label="Période" value={tf} onChange={setTf} options={TFS.map((t) => ({ value: t, label: t }))} /></>}>
      <div className={`grid ${n === 1 ? '' : 'c2'}`}>
        {symbols.map((s, i) => (
          <Panel key={`${i}`} title={<select className="input" style={{ width: 'auto' }} aria-label={`Instrument du graphique ${i + 1}`} value={s} onChange={(e) => setAt(i, e.target.value)}>{(instruments.some((x) => x.symbol === s) ? instruments : [{ symbol: s }, ...instruments]).slice(0, 2000).map((x) => <option key={x.symbol} value={x.symbol}>{x.symbol}</option>)}</select>}>
            <Chart symbol={s} tf={tf} height={h} digits={byId(s)?.digits} />
          </Panel>
        ))}
      </div>
    </Page>
  );
}
