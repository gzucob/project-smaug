/** Non-throwing result shared by the server API client and favorite toggle. */
export type ApiResult<T> =
  | { ok: true; data: T }
  | { ok: false; status: number; message: string };
