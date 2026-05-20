const jsonHeaders = { "Content-Type": "application/json" };

async function handle<T>(res: Response): Promise<T> {
  if (res.status === 401) {
    throw new Error("unauthorized");
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error((data as { error?: string }).error || res.statusText);
  }
  return data as T;
}

function browseParams(folderId: number | null, q: string): string {
  const p = new URLSearchParams();
  if (folderId != null) p.set("folder_id", String(folderId));
  else p.set("folder_id", "root");
  const t = q.trim();
  if (t) p.set("q", t);
  return p.toString();
}

export const api = {
  async me(): Promise<{ logged_in: boolean; app_version?: string }> {
    const res = await fetch("/api/me", { credentials: "include" });
    return handle(res);
  },

  async login(username: string, password: string): Promise<void> {
    const res = await fetch("/api/login", {
      method: "POST",
      credentials: "include",
      headers: jsonHeaders,
      body: JSON.stringify({ username, password }),
    });
    await handle(res);
  },

  async logout(): Promise<void> {
    const res = await fetch("/api/logout", {
      method: "POST",
      credentials: "include",
    });
    await handle(res);
  },

  async browse(
    folderId: number | null,
    q: string,
  ): Promise<{
    breadcrumb: { id: number; label: string }[];
    folders: FolderRow[];
    accounts: AccountRow[];
    search_active: boolean;
  }> {
    const res = await fetch(`/api/browse?${browseParams(folderId, q)}`, {
      credentials: "include",
    });
    return handle(res);
  },

  async listFoldersFlat(): Promise<FolderRow[]> {
    const res = await fetch("/api/folders", { credentials: "include" });
    const d = await handle<{ items: FolderRow[] }>(res);
    return d.items;
  },

  async createFolder(body: {
    label: string;
    parent_id?: number | null;
  }): Promise<{ id: number }> {
    const res = await fetch("/api/folders", {
      method: "POST",
      credentials: "include",
      headers: jsonHeaders,
      body: JSON.stringify(body),
    });
    return handle(res);
  },

  async deleteFolder(id: number): Promise<void> {
    const res = await fetch(`/api/folders/${id}`, {
      method: "DELETE",
      credentials: "include",
    });
    await handle(res);
  },

  async updateFolder(
    id: number,
    body: {
      label?: string;
      parent_id?: number | null;
    },
  ): Promise<void> {
    const res = await fetch(`/api/folders/${id}`, {
      method: "PATCH",
      credentials: "include",
      headers: jsonHeaders,
      body: JSON.stringify(body),
    });
    await handle(res);
  },

  async listAccounts(): Promise<AccountRow[]> {
    const res = await fetch("/api/accounts", { credentials: "include" });
    const d = await handle<{ items: AccountRow[] }>(res);
    return d.items;
  },

  async createAccount(body: Record<string, unknown>): Promise<{ id: number }> {
    const res = await fetch("/api/accounts", {
      method: "POST",
      credentials: "include",
      headers: jsonHeaders,
      body: JSON.stringify(body),
    });
    return handle(res);
  },

  async updateAccount(id: number, body: Record<string, unknown>): Promise<void> {
    const res = await fetch(`/api/accounts/${id}`, {
      method: "PATCH",
      credentials: "include",
      headers: jsonHeaders,
      body: JSON.stringify(body),
    });
    await handle(res);
  },

  async deleteAccount(id: number): Promise<void> {
    const res = await fetch(`/api/accounts/${id}`, {
      method: "DELETE",
      credentials: "include",
    });
    await handle(res);
  },

  async listBuckets(accountId: number): Promise<BucketRow[]> {
    const res = await fetch(`/api/s3/${accountId}/buckets`, {
      credentials: "include",
    });
    const d = await handle<{ items: BucketRow[] }>(res);
    return d.items;
  },

  async listObjects(
    accountId: number,
    bucket: string,
    path: string,
    q = "",
  ): Promise<{ prefix: string; folders: S3FolderEntry[]; files: S3FileEntry[] }>
  {
    const body: Record<string, string> = { bucket, path };
    if (q.trim()) body.q = q.trim();
    const res = await fetch(`/api/s3/${accountId}/objects/list`, {
      method: "POST",
      credentials: "include",
      headers: jsonHeaders,
      body: JSON.stringify(body),
    });
    return handle(res);
  },

  async mkdir(
    accountId: number,
    bucket: string,
    path: string,
    name: string,
  ): Promise<void> {
    const res = await fetch(`/api/s3/${accountId}/objects/mkdir`, {
      method: "POST",
      credentials: "include",
      headers: jsonHeaders,
      body: JSON.stringify({ bucket, path, name }),
    });
    await handle(res);
  },

  async deleteObject(
    accountId: number,
    bucket: string,
    key: string,
  ): Promise<void> {
    const res = await fetch(`/api/s3/${accountId}/objects/delete`, {
      method: "POST",
      credentials: "include",
      headers: jsonHeaders,
      body: JSON.stringify({ bucket, key }),
    });
    await handle(res);
  },

  async renameObject(
    accountId: number,
    bucket: string,
    oldKey: string,
    newKey: string,
  ): Promise<void> {
    const res = await fetch(`/api/s3/${accountId}/objects/rename`, {
      method: "POST",
      credentials: "include",
      headers: jsonHeaders,
      body: JSON.stringify({ bucket, old_key: oldKey, new_key: newKey }),
    });
    await handle(res);
  },

  async uploadObject(
    accountId: number,
    bucket: string,
    path: string,
    file: File,
  ): Promise<void> {
    const fd = new FormData();
    fd.set("bucket", bucket);
    fd.set("path", path);
    fd.set("file", file);
    const res = await fetch(`/api/s3/${accountId}/objects/upload`, {
      method: "POST",
      credentials: "include",
      body: fd,
    });
    await handle(res);
  },

  downloadUrl(accountId: number, bucket: string, key: string): string {
    const q = new URLSearchParams({ bucket, key });
    return `/api/s3/${accountId}/objects/download?${q}`;
  },
};

export interface FolderRow {
  id: number;
  label: string;
  parent_id: number | null;
}

export interface AccountRow {
  id: number;
  folder_id: number | null;
  label: string;
  endpoint_url: string | null;
  region: string | null;
  force_path_style: number | boolean;
  folder_label?: string | null;
}

export interface BucketRow {
  name: string;
  created_at?: string | null;
}

export interface S3FolderEntry {
  prefix: string;
  name: string;
}

export interface S3FileEntry {
  key: string;
  name: string;
  size: number;
  last_modified?: string | null;
}
