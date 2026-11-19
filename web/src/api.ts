export const sitePrefix = import.meta.env.BASE_URL.replace(/\/$/, "");

export function sitePath(path: string) {
  return sitePrefix + path;
}

export type Session = { username: string | null; demo_studio: boolean; guest: boolean; csrf_token: string };
export type Video = {
  id: string; title: string; state: string; watch_url?: string;
  job_id?: string; can_retry?: boolean; retry_advice?: string;
};

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly retryAfter: boolean = false,
  ) {
    super(message);
  }
}

export async function api<T>(
  path: string,
  options?: { csrf: string; data?: object; traceparent?: string },
): Promise<T> {
  const response = await fetch(sitePath(path), options ? {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-CSRFToken": options.csrf,
      ...(options.traceparent ? { traceparent: options.traceparent } : {}) },
    body: JSON.stringify(options.data ?? {}),
  } : undefined);
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    const message = body?.error ?? "Сервис вернул ошибку. Повторите запрос.";
    throw new ApiError(message, response.status, response.headers.has("Retry-After"));
  }
  return body as T;
}

export function sendFile(id: string, file: File, csrf: string, onProgress: (percent: number) => void) {
  return new Promise<string | undefined>((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open("PUT", sitePath("/api/uploads/" + id + "/content"));
    request.responseType = "json";
    request.setRequestHeader("Content-Type", "application/octet-stream");
    request.setRequestHeader("X-CSRFToken", csrf);
    request.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress(Math.floor((event.loaded * 100) / event.total));
    };
    request.onload = () => {
      if (request.status >= 200 && request.status < 300) {
        const parent = request.getResponseHeader("traceparent");
        resolve(parent && /^00-[0-9a-f]{32}-[0-9a-f]{16}-[0-9a-f]{2}$/.test(parent) ? parent : undefined);
      } else {
        reject(new ApiError(request.response?.error ?? "Файл не передан. Повторите загрузку.", request.status));
      }
    };
    request.onerror = () => reject(new Error("Передача прервалась. Загрузите файл заново."));
    request.onabort = () => reject(new Error("Передача прервалась. Загрузите файл заново."));
    request.send(file);
  });
}
