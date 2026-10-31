import type { ReactNode } from 'react';

const P = (d: ReactNode) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    {d}
  </svg>
);
export const Icons = {
  overview: P(<><rect x="3" y="3" width="7" height="9" rx="1.5" /><rect x="14" y="3" width="7" height="5" rx="1.5" /><rect x="14" y="12" width="7" height="9" rx="1.5" /><rect x="3" y="16" width="7" height="5" rx="1.5" /></>),
  markets: P(<><path d="M3 17l5-6 4 3 5-8 4 5" /><path d="M3 21h18" /></>),
  instrument: P(<><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></>),
  charts: P(<><path d="M5 21V11M12 21V4M19 21v-7" /></>),
  agents: P(<><rect x="5" y="8" width="14" height="11" rx="3" /><path d="M12 8V5M9 13h.01M15 13h.01M9 16h6" /><circle cx="12" cy="4" r="1" /></>),
  orders: P(<><path d="M8 6h12M8 12h12M8 18h12" /><path d="M3.5 6h.01M3.5 12h.01M3.5 18h.01" /></>),
  positions: P(<><path d="M4 20V10l5-4 5 4 6-5v15" /><path d="M4 20h16" /></>),
  simulator: P(<><path d="M9 3h6M10 3v6l-5 9a2 2 0 0 0 1.8 3h10.4A2 2 0 0 0 19 18l-5-9V3" /></>),
  portfolios: P(<><rect x="3" y="7" width="18" height="13" rx="2" /><path d="M8 7V5a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2M3 13h18" /></>),
  strategies: P(<><circle cx="6" cy="6" r="2.2" /><circle cx="18" cy="18" r="2.2" /><path d="M8 6h6a4 4 0 0 1 4 4v6" /></>),
  journal: P(<><path d="M6 3h11a2 2 0 0 1 2 2v16l-4-2-4 2-4-2-3 2V5a2 2 0 0 1 2-2z" /><path d="M9 8h6M9 12h6" /></>),
  bell: P(<><path d="M6 9a6 6 0 1 1 12 0c0 5 2 6 2 6H4s2-1 2-6" /><path d="M10 19a2 2 0 0 0 4 0" /></>),
  settings: P(<><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.6 1.7 1.7 0 0 0-1.9.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.9 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.6-1.1 1.7 1.7 0 0 0-.3-1.9l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.9.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.9-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.9V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z" /></>),
  diagnostics: P(<><path d="M3 12h4l2-6 4 12 2-6h6" /></>),
  sun: P(<><circle cx="12" cy="12" r="4" /><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" /></>),
  moon: P(<path d="M21 13a9 9 0 1 1-10-10 7 7 0 0 0 10 10z" />),
  monitor: P(<><rect x="3" y="4" width="18" height="12" rx="2" /><path d="M8 20h8M12 16v4" /></>),
  search: P(<><circle cx="11" cy="11" r="7" /><path d="M20 20l-3.5-3.5" /></>),
  chevron: P(<path d="M6 9l6 6 6-6" />),
  stop: P(<><circle cx="12" cy="12" r="9" /><rect x="9" y="9" width="6" height="6" rx="1" /></>),
  play: P(<path d="M7 5l12 7-12 7z" />),
  refresh: P(<><path d="M20 11a8 8 0 0 0-14.9-3M4 5v4h4" /><path d="M4 13a8 8 0 0 0 14.9 3M20 19v-4h-4" /></>),
  plus: P(<path d="M12 5v14M5 12h14" />),
  trash: P(<><path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3" /></>),
  update: P(<><path d="M12 3v12M7 10l5 5 5-5" /><path d="M4 20h16" /></>),
  x: P(<path d="M6 6l12 12M18 6L6 18" />),
};
export const Logo = () => (
  <svg width="28" height="28" viewBox="0 0 32 32" aria-hidden="true">
    <rect width="32" height="32" rx="7" fill="var(--panel-2)" stroke="var(--border)" />
    <path d="M5 22l6-7 5 4 6-9 5 6" fill="none" stroke="var(--accent)" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round" />
  </svg>
);
