export const IMPL: Record<string, { label: string; kind: 'ok' | 'warn' }> = {
  full: { label: 'Implémentation complète', kind: 'ok' },
  partial: { label: 'Partiellement implémenté', kind: 'warn' },
};
export const RUN_KIND: Record<string, 'ok' | 'warn' | 'err' | 'mute'> = { ok: 'ok', partial: 'warn', skipped: 'mute', error: 'err' };
export const AGENT_LABEL: Record<string, string> = {
  scanner: 'Scanner', news: 'News', entry: 'Entry', exit: 'Exit', timemachine: 'TimeMachine', risk: 'Risk', budget: 'Budget', broker: 'Broker', alerts: 'Alerts', analyst: 'Analyst', pelosi: 'Pelosi',
};
export const RUN_EXAMPLE: Record<string, object> = {
  scanner: {}, news: { symbol: 'EURUSD' }, entry: { symbol: 'EURUSD' }, exit: { symbol: 'EURUSD' }, timemachine: {}, risk: {}, budget: {}, alerts: {}, analyst: { symbol: 'EURUSD' }, pelosi: {},
};
