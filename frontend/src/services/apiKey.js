const STORAGE_KEY = 'aimarker-api-key';
let apiKey = '';
try {
  apiKey = sessionStorage.getItem(STORAGE_KEY) || '';
} catch { /* Memory-only access when browser storage is unavailable. */ }

export const getApiKey = () => apiKey;

export const setApiKey = (value) => {
  apiKey = value.trim();
  try {
    if (apiKey) sessionStorage.setItem(STORAGE_KEY, apiKey);
    else sessionStorage.removeItem(STORAGE_KEY);
  } catch { /* Keep the key in memory for this page. */ }
};
