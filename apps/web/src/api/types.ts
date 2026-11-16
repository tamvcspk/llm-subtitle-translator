// Shapes of the worker's answers (docs/API.md). Only what a screen reads is typed; add fields as screens need them.

export type ProviderState = "closed" | "open" | "exhausted" | "invalid" | "half_open";

export interface ProviderHealth {
  model: string;
  state: ProviderState;
  reason: string;
  /** Unix seconds when the provider may be tried again. */
  until: number | null;
  failures: number;
  usable: boolean;
}

export interface KeyWarning {
  kind: "key-invalid" | "provider-unconfigured" | "model-not-found" | (string & {});
  message: string;
  provider?: string;
  model?: string;
  key_id?: number | string;
  label?: string;
}

/** `GET /api/queue` counts and `GET /api/status` queue: jobs per console state. */
export interface QueueCounts {
  pending: number;
  processing: number;
  deferred: number;
  done: number;
  failed: number;
}

export interface StatusResponse {
  queue: QueueCounts;
  daily_quota_pause: { retry_at: number } | null;
  providers: ProviderHealth[];
  settings: { key_warnings: KeyWarning[] };
}

export interface CatalogStatus {
  /** Unix seconds of the last accepted read of Bazarr, or null before the first one. */
  synced_at: number | null;
  attempted_at: number | null;
  status: "ok" | "kept" | "";
  warning: string;
  items: number;
}

export interface AuthMe {
  user: string | null;
  via: "session" | "basic" | "off";
  can_change_password: boolean;
}
