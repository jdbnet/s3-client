<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import {
  Download,
  Folder,
  File,
  RefreshCcw,
  Trash2,
  Pencil,
  Upload,
  Search,
  Plus,
} from "lucide-vue-next";
import { api, type BucketRow, type S3FileEntry, type S3FolderEntry } from "@/api";

const props = defineProps<{
  accountId: number;
  accountLabel: string;
  visible: boolean;
}>();

const buckets = ref<BucketRow[]>([]);
const bucket = ref("");
const path = ref("/");
const folders = ref<S3FolderEntry[]>([]);
const files = ref<S3FileEntry[]>([]);
const err = ref("");
const searchQuery = ref("");
const bucketsLoading = ref(false);
const objectsLoading = ref(false);
const renameTarget = ref<{
  kind: "folder" | "file";
  name: string;
  key: string;
} | null>(null);
const renameValue = ref("");

let searchTimer: number | undefined;

const currentPrefix = computed(() => {
  if (!path.value || path.value === "/") return "";
  return path.value.replace(/^\//, "");
});

function joinPath(base: string, name: string): string {
  const cleaned = base === "/" ? "" : base.replace(/\/$/, "");
  const parts = [cleaned, name].filter(Boolean);
  return `/${parts.join("/")}/`;
}

function parentPath(): string {
  if (path.value === "/") return "/";
  const parts = path.value.replace(/^\//, "").replace(/\/$/, "").split("/");
  parts.pop();
  return parts.length ? `/${parts.join("/")}/` : "/";
}

function fmtDate(ts?: string | null): string {
  if (!ts) return "-";
  const d = new Date(ts);
  return Number.isNaN(d.getTime()) ? ts : d.toLocaleString();
}

function fmtSize(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} MB`;
  return `${(n / (1024 * 1024 * 1024)).toFixed(1)} GB`;
}

async function loadBuckets() {
  err.value = "";
  bucketsLoading.value = true;
  try {
    buckets.value = await api.listBuckets(props.accountId);
    if (!bucket.value && buckets.value.length) {
      bucket.value = buckets.value[0].name;
      await loadObjects();
    }
  } catch (e) {
    err.value = e instanceof Error ? e.message : "Failed to load buckets";
  } finally {
    bucketsLoading.value = false;
  }
}

async function loadObjects() {
  if (!bucket.value) return;
  err.value = "";
  objectsLoading.value = true;
  try {
    const res = await api.listObjects(props.accountId, bucket.value, path.value, searchQuery.value);
    folders.value = res.folders;
    files.value = res.files;
  } catch (e) {
    err.value = e instanceof Error ? e.message : "Failed to load objects";
  } finally {
    objectsLoading.value = false;
  }
}

function selectBucket(name: string) {
  bucket.value = name;
  path.value = "/";
  searchQuery.value = "";
  loadObjects();
}

function enterFolder(folder: S3FolderEntry) {
  const name = folder.name || folder.prefix.replace(/\/$/, "").split("/").pop() || "";
  if (!name) return;
  path.value = joinPath(path.value, name);
  loadObjects();
}

function goUp() {
  path.value = parentPath();
  loadObjects();
}

async function onUpload(ev: Event) {
  const input = ev.target as HTMLInputElement;
  const file = input.files?.[0];
  input.value = "";
  if (!file || !bucket.value) return;
  try {
    await api.uploadObject(props.accountId, bucket.value, path.value, file);
    await loadObjects();
  } catch (e) {
    err.value = e instanceof Error ? e.message : "Upload failed";
  }
}

async function downloadFile(entry: S3FileEntry) {
  if (!bucket.value) return;
  err.value = "";
  try {
    const res = await fetch(api.downloadUrl(props.accountId, bucket.value, entry.key), {
      credentials: "include",
    });
    if (!res.ok) throw new Error(await res.text());
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = entry.name;
    a.click();
    URL.revokeObjectURL(url);
  } catch (e) {
    err.value = e instanceof Error ? e.message : "Download failed";
  }
}

async function removeEntry(entry: S3FileEntry | S3FolderEntry, kind: "file" | "folder") {
  if (!bucket.value) return;
  if (!confirm(`Delete ${"name" in entry ? entry.name : "item"}?`)) return;
  const key = kind === "folder" ? (entry as S3FolderEntry).prefix : (entry as S3FileEntry).key;
  try {
    await api.deleteObject(props.accountId, bucket.value, key);
    await loadObjects();
  } catch (e) {
    err.value = e instanceof Error ? e.message : "Delete failed";
  }
}

function startRename(entry: S3FileEntry | S3FolderEntry, kind: "file" | "folder") {
  renameTarget.value = {
    kind,
    name: "name" in entry ? entry.name : "",
    key: kind === "folder" ? (entry as S3FolderEntry).prefix : (entry as S3FileEntry).key,
  };
  renameValue.value = renameTarget.value.name;
}

async function confirmRename() {
  if (!renameTarget.value || !bucket.value) return;
  const name = renameValue.value.trim();
  if (!name) return;
  let newKey = name;
  if (renameTarget.value.kind === "folder") {
    const base = currentPrefix.value;
    newKey = base ? `${base}${name}/` : `${name}/`;
  } else {
    const base = currentPrefix.value;
    newKey = base ? `${base}${name}` : name;
  }
  try {
    await api.renameObject(props.accountId, bucket.value, renameTarget.value.key, newKey);
    renameTarget.value = null;
    await loadObjects();
  } catch (e) {
    err.value = e instanceof Error ? e.message : "Rename failed";
  }
}

async function mkdir() {
  if (!bucket.value) return;
  const name = prompt("Folder name");
  if (!name?.trim()) return;
  try {
    await api.mkdir(props.accountId, bucket.value, path.value, name.trim());
    await loadObjects();
  } catch (e) {
    err.value = e instanceof Error ? e.message : "Create folder failed";
  }
}

function queueSearch() {
  window.clearTimeout(searchTimer);
  searchTimer = window.setTimeout(() => {
    void loadObjects();
  }, 250);
}

watch(
  () => props.accountId,
  () => {
    bucket.value = "";
    path.value = "/";
    searchQuery.value = "";
    folders.value = [];
    files.value = [];
    void loadBuckets();
  },
  { immediate: true },
);

watch(
  () => props.visible,
  (visible) => {
    if (visible && !buckets.value.length) {
      void loadBuckets();
    }
  },
);
</script>

<template>
  <div class="flex h-full min-h-0 flex-col gap-4">
    <div class="grid gap-4 lg:grid-cols-[280px_minmax(0,1fr)]">
      <div class="panel p-4">
        <div class="flex items-center justify-between gap-3">
          <div>
            <p class="text-xs font-medium uppercase tracking-wide text-slate-500">Buckets</p>
            <h2 class="font-sans text-base font-semibold text-white">{{ accountLabel }}</h2>
          </div>
          <button type="button" class="button-secondary" @click="loadBuckets">
            <RefreshCcw class="h-4 w-4" />
            Refresh
          </button>
        </div>

        <div class="mt-4">
          <p v-if="bucketsLoading" class="text-xs text-slate-500">Loading buckets...</p>
          <ul v-else class="space-y-2">
            <li v-for="b in buckets" :key="b.name">
              <button
                type="button"
                class="w-full rounded-lg border border-slate-800 bg-surface-overlay px-3 py-2 text-left text-sm transition hover:border-slate-600"
                :class="bucket === b.name ? 'border-accent bg-accent/15 text-white' : 'text-slate-300'"
                @click="selectBucket(b.name)"
              >
                <div class="flex items-center justify-between gap-2">
                  <span class="truncate font-medium">{{ b.name }}</span>
                  <span class="text-[11px] text-slate-500">{{ fmtDate(b.created_at) }}</span>
                </div>
              </button>
            </li>
          </ul>
        </div>
      </div>

      <div class="panel flex min-h-[420px] flex-col">
        <div class="border-b border-slate-800 px-4 py-3">
          <div class="flex flex-wrap items-center gap-3">
            <div class="flex-1">
              <p class="text-xs font-medium uppercase tracking-wide text-slate-500">Bucket path</p>
              <p class="font-mono text-xs text-slate-300">
                {{ bucket ? `s3://${bucket}${path}` : "Select a bucket" }}
              </p>
            </div>
            <div class="flex flex-wrap gap-2">
              <button type="button" class="button-secondary" @click="goUp">
                <Folder class="h-4 w-4" />
                Up
              </button>
              <button type="button" class="button-secondary" @click="loadObjects">
                <RefreshCcw class="h-4 w-4" />
                Refresh
              </button>
              <button type="button" class="button-secondary" @click="mkdir">
                <Plus class="h-4 w-4" />
                Folder
              </button>
              <label class="button-primary cursor-pointer">
                <Upload class="h-4 w-4" />
                Upload
                <input type="file" class="hidden" @change="onUpload" />
              </label>
            </div>
          </div>

          <div class="mt-3 flex items-center gap-2">
            <div class="relative flex-1">
              <Search class="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-slate-500" />
              <input
                v-model="searchQuery"
                class="field pl-9"
                @input="queueSearch"
              />
            </div>
            <button type="button" class="button-secondary" @click="searchQuery = ''; loadObjects()">
              Clear
            </button>
          </div>

          <p v-if="err" class="mt-3 text-sm text-red-400">{{ err }}</p>
        </div>

        <div class="min-h-0 flex-1 overflow-auto p-4">
          <p v-if="objectsLoading" class="text-sm text-slate-500">Loading objects...</p>
          <div v-else class="space-y-6">
            <div>
              <p class="text-xs font-medium uppercase tracking-wide text-slate-500">Folders</p>
              <ul class="mt-2 space-y-1">
                <li
                  v-for="f in folders"
                  :key="f.prefix"
                  class="group flex items-center gap-2 rounded-lg px-3 py-2 hover:bg-surface-overlay"
                >
                  <button type="button" class="flex-1 truncate text-left text-sm text-slate-200" @click="enterFolder(f)">
                    <span class="inline-flex items-center gap-2">
                      <Folder class="h-4 w-4 text-accent" />
                      {{ f.name }}
                    </span>
                  </button>
                  <button
                    type="button"
                    class="text-xs text-slate-500 opacity-0 transition group-hover:opacity-100"
                    @click="startRename(f, 'folder')"
                  >
                    <Pencil class="h-4 w-4" />
                  </button>
                  <button
                    type="button"
                    class="text-xs text-red-400/80 opacity-0 transition group-hover:opacity-100"
                    @click="removeEntry(f, 'folder')"
                  >
                    <Trash2 class="h-4 w-4" />
                  </button>
                </li>
                <li v-if="!folders.length" class="text-xs text-slate-500">No folders</li>
              </ul>
            </div>

            <div>
              <p class="text-xs font-medium uppercase tracking-wide text-slate-500">Files</p>
              <ul class="mt-2 space-y-1">
                <li
                  v-for="f in files"
                  :key="f.key"
                  class="group flex items-center gap-3 rounded-lg px-3 py-2 hover:bg-surface-overlay"
                >
                  <div class="flex-1 truncate text-sm text-slate-200">
                    <span class="inline-flex items-center gap-2">
                      <File class="h-4 w-4" />
                      {{ f.name }}
                    </span>
                  </div>
                  <span class="text-xs text-slate-500">{{ fmtSize(f.size) }}</span>
                  <button
                    type="button"
                    class="text-xs text-slate-400 opacity-0 transition group-hover:opacity-100"
                    @click="downloadFile(f)"
                  >
                    <Download class="h-4 w-4" />
                  </button>
                  <button
                    type="button"
                    class="text-xs text-slate-400 opacity-0 transition group-hover:opacity-100"
                    @click="startRename(f, 'file')"
                  >
                    <Pencil class="h-4 w-4" />
                  </button>
                  <button
                    type="button"
                    class="text-xs text-red-400/80 opacity-0 transition group-hover:opacity-100"
                    @click="removeEntry(f, 'file')"
                  >
                    <Trash2 class="h-4 w-4" />
                  </button>
                </li>
                <li v-if="!files.length" class="text-xs text-slate-500">No files</li>
              </ul>
            </div>
          </div>
        </div>

        <div v-if="renameTarget" class="border-t border-slate-800 px-4 py-3">
          <div class="flex flex-wrap items-center gap-2">
            <input v-model="renameValue" class="field flex-1" @keyup.enter="confirmRename" />
            <button type="button" class="button-primary" @click="confirmRename">Save</button>
            <button type="button" class="button-secondary" @click="renameTarget = null">Cancel</button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
