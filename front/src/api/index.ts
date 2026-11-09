// Punto único de acceso a la API. Por ahora usa el cliente simulado (solo diseño);
// en la fase de conexión se cambia por el cliente real que llama a FastAPI.
import { apiSimulada } from './simulada';
import type { ClienteApi } from './tipos';

export const api: ClienteApi = apiSimulada;
export * from './tipos';
