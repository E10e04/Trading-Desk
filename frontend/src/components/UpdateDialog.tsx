import { Banner, Modal } from './ui';
import { useUpdate } from '../lib/update';

export function UpdateDialog() {
  const { info, open, setOpen, install, installing, progress, error } = useUpdate();
  if (!open || !info?.latest) return null;
  const desk = !!window.desktop;
  const mac = window.desktop?.platform === 'darwin';
  return (
    <Modal
      title="Nouvelle version disponible"
      onClose={() => !installing && setOpen(false)}
      footer={
        <>
          <button className="btn" disabled={installing} onClick={() => setOpen(false)}>Plus tard</button>
          <button className="btn primary" disabled={installing} onClick={install}>{installing ? 'Téléchargement…' : desk ? 'Mettre à jour maintenant' : 'Voir la release'}</button>
        </>
      }
    >
      <div className="stack">
        <p>La version <b>{info.latest.version}</b> est disponible (vous utilisez la version <b>{info.current}</b>).</p>
        {info.latest.notes && <pre className="json" style={{ maxHeight: 180, whiteSpace: 'pre-wrap' }}>{info.latest.notes}</pre>}
        {desk && <p className="muted">{mac ? 'Le fichier d’installation s’ouvrira : glissez l’application dans « Applications » en remplaçant l’ancienne. Aucune désinstallation n’est nécessaire.' : 'L’installateur se lance par-dessus la version actuelle : aucune désinstallation n’est nécessaire. Vos données sont conservées.'}</p>}
        {!desk && <p className="muted">Vous utilisez l’interface web : mettez à jour le serveur depuis la page de la release.</p>}
        {installing && <div className="bar" role="progressbar" aria-valuenow={Math.round((progress ?? 0) * 100)} aria-valuemin={0} aria-valuemax={100}><i style={{ width: `${Math.round((progress ?? 0) * 100)}%` }} /></div>}
        {error && <Banner kind="err">{error}</Banner>}
      </div>
    </Modal>
  );
}
