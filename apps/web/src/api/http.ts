import axios, { AxiosHeaders, type AxiosRequestConfig } from 'axios';
import { API_BASE } from '../constants/domain';

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
    const detail = response.data?.detail || response.data?.error || response.statusText;
    throw new Error(`${response.status} ${detail}`);
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
  return response.data === '' ? null : response.data;
}
