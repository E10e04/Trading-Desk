import { lazy, Suspense, useEffect, useState } from 'react';
import { Navigate, Route, Routes } from 'react-router-dom';
import { Layout } from './components/Layout';
import { Login } from './components/Login';
import { Loading } from './components/ui';
import { bootstrapToken, getToken, onTokenChange } from './lib/api';
import { MarketProvider } from './lib/market';
import { UpdateProvider } from './lib/update';

const page = (f: () => Promise<{ default: React.ComponentType }>) => lazy(f);
const Overview = page(() => import('./pages/Overview'));
const Markets = page(() => import('./pages/Markets'));
const Instrument = page(() => import('./pages/Instrument'));
const Charts = page(() => import('./pages/Charts'));
const Agents = page(() => import('./pages/Agents'));
const AgentDetail = page(() => import('./pages/AgentDetail'));
const Orders = page(() => import('./pages/Orders'));
const Positions = page(() => import('./pages/Positions'));
const Simulator = page(() => import('./pages/Simulator'));
const Portfolios = page(() => import('./pages/Portfolios'));
const Strategies = page(() => import('./pages/Strategies'));
const Analyses = page(() => import('./pages/Analyses'));
const Notifications = page(() => import('./pages/Notifications'));
const Settings = page(() => import('./pages/Settings'));
const Diagnostics = page(() => import('./pages/Diagnostics'));

export default function App() {
  const [token, setTok] = useState<string | null>(getToken());
  const [boot, setBoot] = useState(!getToken());
  useEffect(() => onTokenChange(() => setTok(getToken())), []);
  useEffect(() => {
    if (!getToken()) void bootstrapToken().finally(() => setBoot(false));
  }, []);
  if (boot) return <Loading />;
  if (!token) return <Login reason="Saisissez le jeton d’accès pour ouvrir l’interface. Sur l’ordinateur qui héberge le backend, il est récupéré automatiquement." />;
  return (
    <MarketProvider>
      <UpdateProvider>
      <Suspense fallback={<Loading />}>
        <Routes>
          <Route element={<Layout />}>
            <Route index element={<Overview />} />
            <Route path="markets" element={<Markets />} />
            <Route path="instrument" element={<Instrument />} />
            <Route path="instrument/:symbol" element={<Instrument />} />
            <Route path="charts" element={<Charts />} />
            <Route path="charts/:symbol" element={<Charts />} />
            <Route path="agents" element={<Agents />} />
            <Route path="agents/:id" element={<AgentDetail />} />
            <Route path="orders" element={<Orders />} />
            <Route path="positions" element={<Positions />} />
            <Route path="simulator" element={<Simulator />} />
            <Route path="portfolios" element={<Portfolios />} />
            <Route path="strategies" element={<Strategies />} />
            <Route path="analyses" element={<Analyses />} />
            <Route path="notifications" element={<Notifications />} />
            <Route path="settings" element={<Settings />} />
            <Route path="diagnostics" element={<Diagnostics />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Route>
        </Routes>
      </Suspense>
      </UpdateProvider>
    </MarketProvider>
  );
}
