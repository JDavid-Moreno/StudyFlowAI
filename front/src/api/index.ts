// Punto único de acceso a la API.
// Por defecto usa la API real (VITE_API_URL, por defecto http://localhost:8000).
// Con VITE_API_MODO=simulada se usa el cliente simulado, útil para trabajar solo el diseño.
import { apiReal } from './real';
import { apiSimulada } from './simulada';
import type { ClienteApi } from './tipos';

export const api: ClienteApi = import.meta.env.VITE_API_MODO === 'simulada' ? apiSimulada : apiReal;
export * from './tipos';
