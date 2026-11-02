import { Component, type ErrorInfo, type ReactNode } from 'react';

const RELOADED = 'td.reloaded';
const stale = (e: Error) => /dynamically imported module|Loading chunk|Importing a module script failed/i.test(e.message);

/** Sans garde-fou, une erreur d'affichage dans React démonte TOUTE l'interface (écran vide). Ici l'erreur reste dans la page concernée. */
export class ErrorBoundary extends Component<{ children: ReactNode; resetKey?: string; full?: boolean }, { error: Error | null }> {
  state = { error: null as Error | null };
  static getDerivedStateFromError(error: Error) { return { error }; }
  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('Erreur d’affichage', error, info.componentStack);
    // Après une mise à jour, d'anciens fichiers de page n'existent plus : un rechargement les remplace (une seule fois, pas de boucle).
    if (stale(error)) {
      try { if (!sessionStorage.getItem(RELOADED)) { sessionStorage.setItem(RELOADED, '1'); location.reload(); } } catch { /* ignore */ }
    }
  }
  componentDidUpdate(prev: { resetKey?: string }) {
    if (this.state.error && prev.resetKey !== this.props.resetKey) this.setState({ error: null });
  }
  render() {
    const e = this.state.error;
    if (!e) return this.props.children;
    return (
      <div role="alert" className="panel" style={{ margin: 24, maxWidth: 640 }}>
        <h2 style={{ marginTop: 0 }}>Cette page a rencontré une erreur</h2>
        <p>Le reste de l’application fonctionne toujours. Vos portefeuilles et vos ordres ne sont pas affectés.</p>
        <pre style={{ whiteSpace: 'pre-wrap', fontSize: 12, opacity: 0.8 }}>{e.message}</pre>
        <div className="row" style={{ gap: 8 }}>
          <button className="btn primary" onClick={() => this.setState({ error: null })}>Réessayer</button>
          <button className="btn" onClick={() => location.reload()}>Recharger l’application</button>
          {!this.props.full && <a className="btn" href="#/">Retour à l’accueil</a>}
        </div>
      </div>
    );
  }
}
