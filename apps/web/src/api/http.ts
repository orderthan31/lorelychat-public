import axios, { AxiosHeaders, type AxiosRequestConfig } from 'axios';
import { API_BASE } from '../constants/domain';

export class ApiError extends Error {
  constructor(readonly status: number | undefined, readonly detail: unknown, readonly code?: string, readonly cause?: unknown) {
    super(`${status || 'Transport'} ${typeof detail === 'string' ? detail : JSON.stringify(detail)}`);
    this.name = 'ApiError';
  }
  get permanent() { return this.status === 404 || this.status === 410; }
  get unknownOutcome() { return !this.status || this.status >= 500 || this.code === 'PROTOCOL_ERROR'; }
}

export type ApiOptions = Omit<AxiosRequestConfig, 'url' | 'data'> & {
  body?: BodyInit | Record<string, unknown> | string | null;
};

export const http = axios.create({
  baseURL: API_BASE,
  timeout: 120000,
});

http.interceptors.request.use((config) => {
  const data = config.data;
  const isFormData = typeof FormData !== 'undefined' && data instanceof FormData;
  if (!config.headers) config.headers = new AxiosHeaders();
  if (!isFormData && data !== undefined && data !== null && !config.headers['Content-Type']) {
    config.headers['Content-Type'] = 'application/json';
  }
  return config;
});

http.interceptors.response.use(
  (response) => response,
  (error) => {
    const response = error.response;
    if (!response) throw error;
    const detail = response.data?.detail ?? response.data?.error ?? response.statusText;
    throw new ApiError(response.status, detail, error.code, error);
  },
);

export async function api<T = unknown>(path: string, options: ApiOptions = {}): Promise<T | null> {
  const { body, method = 'GET', headers, ...rest } = options;
  const response = await http.request<T | ''>({
    url: path,
    method,
    data: body,
    headers,
    ...rest,
  });
  if (response.status === 204) return null;
  const data = response.data;
  if (data === null || typeof data !== 'object') throw new ApiError(response.status, 'Expected JSON object or array', 'PROTOCOL_ERROR');
  const pathname = path.split('?')[0];
  const list = /^\/(characters|conversations|presets|chat-commands|world-settings|model-options|model-provider-accounts)$/.test(pathname) || /\/(messages|participants|assets|usage-ranking|voices|models)$/.test(pathname);
  if (String(method).toUpperCase() === 'GET' && list) {
    if (path.includes('paginated=true') ? (!Array.isArray((data as any).items) || !Number.isFinite((data as any).total)) : !Array.isArray(data)) throw new ApiError(response.status, 'Invalid list envelope', 'PROTOCOL_ERROR');
  }
  if (Array.isArray(data) && data.some(item => !item || typeof item !== 'object' || Array.isArray(item))) throw new ApiError(response.status, 'Invalid list member', 'PROTOCOL_ERROR');
  const detailTarget = pathname.match(/^\/(characters|conversations|presets|chat-commands|world-settings)\/([^/]+)$/);
  if (detailTarget && !Array.isArray(data) && ['GET', 'PATCH'].includes(String(method).toUpperCase()) && (data as any).id !== detailTarget[2]) throw new ApiError(response.status, 'Detail identity does not match request', 'PROTOCOL_ERROR');
  return data;
}
