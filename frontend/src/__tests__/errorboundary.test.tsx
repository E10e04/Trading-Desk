import { render, screen } from '@testing-library/react';
import { ErrorBoundary } from '../components/ErrorBoundary';

function Boom(): JSX.Element { throw new Error('affichage cassé'); }

test('une erreur de rendu reste dans la page au lieu de vider toute l’application', () => {
  const spy = vi.spyOn(console, 'error').mockImplementation(() => {});
  render(<div><nav>menu</nav><ErrorBoundary><Boom /></ErrorBoundary></div>);
  expect(screen.getByText('menu')).toBeTruthy();
  expect(screen.getByRole('alert').textContent).toContain('affichage cassé');
  spy.mockRestore();
});
