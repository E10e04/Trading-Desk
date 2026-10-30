import { useState } from 'react';
import { Logo } from './icons';
import { setToken } from '../lib/api';

export function Login({ reason }: { reason: string }) {
  const [v, setV] = useState('');
  return (
    <div style={{ minHeight: '100%', display: 'grid', placeItems: 'center', padding: 16 }}>
      <form className="panel" style={{ width: 'min(440px,100%)' }} onSubmit={(e) => { e.preventDefault(); if (v.trim()) setToken(v.trim()); }}>
        <header><h2 style={{ display: 'flex', gap: 10, alignItems: 'center' }}><Logo /> Trading Desk</h2></header>
        <div className="body stack">
          <p className="muted">{reason}</p>
          <div className="field">
            <label htmlFor="tok">Jeton d’accès (API_TOKEN du backend)</label>
            <input id="tok" className="input" type="password" autoComplete="off" autoFocus value={v} onChange={(e) => setV(e.target.value)} />
            <span className="hint">Défini dans le fichier .env du serveur. Il reste dans ce navigateur et n’est envoyé qu’à ce serveur.</span>
          </div>
          <button className="btn primary" type="submit" disabled={!v.trim()}>Se connecter</button>
        </div>
      </form>
    </div>
  );
}
