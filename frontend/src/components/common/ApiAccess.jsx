import { useEffect, useState } from 'react';
import { verifyApiAccess } from '../../services/api';
import { getApiKey, setApiKey } from '../../services/apiKey';

export default function ApiAccess({ children }) {
  const [state, setState] = useState('checking');
  const [key, setKey] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let active = true;
    const requireKey = () => {
      setState('locked');
      setError('Enter a valid API key to continue.');
    };
    window.addEventListener('aimarker-auth-required', requireKey);
    verifyApiAccess().then(() => {
      if (active) setState('ready');
    }).catch((failure) => {
      if (!active) return;
      setState('locked');
      if (failure.response?.status !== 401) setError('Could not connect to the API. Please try again.');
    });
    return () => {
      active = false;
      window.removeEventListener('aimarker-auth-required', requireKey);
    };
  }, []);

  const unlock = async (event) => {
    event.preventDefault();
    setBusy(true);
    setError('');
    try {
      await verifyApiAccess(key.trim());
      setApiKey(key);
      setKey('');
      setState('ready');
    } catch (failure) {
      setError(failure.response?.status === 401
        ? 'That API key was not accepted. Please try again.'
        : 'Could not connect to the API. Please try again.');
    } finally {
      setBusy(false);
    }
  };

  if (state === 'checking') return <p className="p-8 text-center text-slate-600" role="status">Connecting…</p>;
  if (state === 'ready') return <>
    {getApiKey() && <div className="max-w-7xl mx-auto px-4 text-right">
      <button className="text-sm text-slate-600 underline" onClick={() => {
        setApiKey(''); setKey(''); setError(''); setState('locked');
      }}>Forget API key</button>
    </div>}
    {children}
  </>;
  return <main className="max-w-md mx-auto my-12 rounded-xl border border-slate-200 bg-white p-8 shadow-sm">
    <h1 className="text-2xl font-bold text-slate-900">API access</h1>
    <p className="mt-3 text-slate-600">Enter the API key provided by your service operator. It stays in this browser tab’s session.</p>
    <form onSubmit={unlock} className="mt-6 space-y-4">
      <label className="block text-sm font-medium text-slate-700" htmlFor="api-key">API key</label>
      <input id="api-key" type="password" autoComplete="off" spellCheck={false}
        value={key} onChange={(event) => setKey(event.target.value)} disabled={busy}
        className="w-full rounded-lg border border-slate-300 p-3 focus:outline-blue-600" />
      {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
      <button type="submit" disabled={busy}
        className="w-full rounded-lg bg-blue-600 px-4 py-3 font-semibold text-white disabled:opacity-50">
        {busy ? 'Checking…' : 'Continue'}
      </button>
    </form>
  </main>;
}
