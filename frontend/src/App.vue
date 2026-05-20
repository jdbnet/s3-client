<script setup lang="ts">
import { onMounted, ref } from "vue";
import {
  Boxes,
  ChevronRight,
  Folder,
  LogOut,
  Pencil,
  Plus,
  Search,
  Trash2,
  Cloud,
} from "lucide-vue-next";
import {
  api,
  type AccountRow,
  type FolderRow,
} from "@/api";
import LoginForm from "@/components/LoginForm.vue";
import AccountTab from "@/components/AccountTab.vue";

interface TabItem {
  id: string;
  accountId: number;
  label: string;
}

const loggedIn = ref(false);
const checking = ref(true);
const appVersion = ref("unknown");
const loadErr = ref("");

const allAccounts = ref<AccountRow[]>([]);
const allFolders = ref<FolderRow[]>([]);
const browseFolders = ref<FolderRow[]>([]);
const browseAccounts = ref<AccountRow[]>([]);
const breadcrumb = ref<{ id: number; label: string }[]>([]);
const searchActive = ref(false);
const currentFolderId = ref<number | null>(null);
const searchQuery = ref("");
const sidebarOpen = ref(true);

const tabs = ref<TabItem[]>([]);
const activeTabId = ref<string | null>(null);

let searchDebounceTimer = 0;

const showAccountForm = ref(false);
const showEditAccount = ref(false);
const showFolderForm = ref(false);
const showEditFolder = ref(false);
const newFolderLabel = ref("");
const showingFolderSearch = ref(false);

const accountForm = ref({
  label: "",
  endpoint_url: "",
  region: "",
  force_path_style: false,
  access_key: "",
  secret_key: "",
  session_token: "",
});

const editAccountForm = ref({
  id: 0,
  label: "",
  endpoint_url: "",
  region: "",
  force_path_style: false,
  access_key: "",
  secret_key: "",
  session_token: "",
  folder_id: null as number | null,
});

const editFolderForm = ref({
  id: 0,
  label: "",
  parent_id: null as number | null,
});

function folderOptionLabel(id: number | null): string {
  if (id == null) return "(root)";
  const byId = new Map(allFolders.value.map((f) => [f.id, f]));
  const parts: string[] = [];
  let cur: number | null | undefined = id;
  const guard = new Set<number>();
  while (cur != null && !guard.has(cur)) {
    guard.add(cur);
    const f = byId.get(cur);
    if (!f) break;
    parts.unshift(f.label);
    cur = f.parent_id;
  }
  return parts.join(" / ") || `#${id}`;
}

async function refreshBrowse() {
  try {
    const d = await api.browse(currentFolderId.value, searchQuery.value);
    browseFolders.value = d.folders;
    browseAccounts.value = d.accounts;
    breadcrumb.value = d.breadcrumb;
    searchActive.value = d.search_active;
  } catch (e) {
    loadErr.value = e instanceof Error ? e.message : "Browse failed";
  }
}

function onSearchInput() {
  window.clearTimeout(searchDebounceTimer);
  searchDebounceTimer = window.setTimeout(() => {
    void refreshBrowse();
  }, 300);
}

function goToFolder(id: number | null) {
  currentFolderId.value = id;
  searchQuery.value = "";
  showingFolderSearch.value = false;
  void refreshBrowse();
}

function openSearchMode() {
  showingFolderSearch.value = true;
}

function clearSearch() {
  searchQuery.value = "";
  showingFolderSearch.value = false;
  void refreshBrowse();
}

async function refreshData() {
  loadErr.value = "";
  try {
    allAccounts.value = await api.listAccounts();
    allFolders.value = await api.listFoldersFlat();
    await refreshBrowse();
  } catch (e) {
    loadErr.value = e instanceof Error ? e.message : "Failed to load data";
  }
}

onMounted(async () => {
  try {
    const m = await api.me();
    loggedIn.value = m.logged_in;
    if (m.app_version) {
      appVersion.value = m.app_version;
    }
    if (loggedIn.value) await refreshData();
  } catch {
    loggedIn.value = false;
  } finally {
    checking.value = false;
  }
});

async function onLoggedIn() {
  loggedIn.value = true;
  await refreshData();
}

async function logout() {
  await api.logout();
  tabs.value = [];
  activeTabId.value = null;
  loggedIn.value = false;
}

function openTab(a: AccountRow) {
  const id = crypto.randomUUID();
  tabs.value.push({ id, accountId: a.id, label: a.label });
  activeTabId.value = id;
  if (window.matchMedia("(max-width: 1023px)").matches) {
    sidebarOpen.value = false;
  }
}

function closeTab(id: string) {
  tabs.value = tabs.value.filter((t) => t.id !== id);
  if (activeTabId.value === id) {
    activeTabId.value = tabs.value.length ? tabs.value[tabs.value.length - 1].id : null;
  }
}

async function submitAccount() {
  const f = accountForm.value;
  await api.createAccount({
    label: f.label.trim(),
    endpoint_url: f.endpoint_url.trim() || null,
    region: f.region.trim() || null,
    force_path_style: f.force_path_style,
    access_key: f.access_key.trim(),
    secret_key: f.secret_key.trim(),
    session_token: f.session_token.trim(),
    folder_id: currentFolderId.value,
  });
  showAccountForm.value = false;
  accountForm.value = {
    label: "",
    endpoint_url: "",
    region: "",
    force_path_style: false,
    access_key: "",
    secret_key: "",
    session_token: "",
  };
  await refreshData();
}

function openEditAccount(a: AccountRow) {
  editAccountForm.value = {
    id: a.id,
    label: a.label,
    endpoint_url: a.endpoint_url || "",
    region: a.region || "",
    force_path_style: Boolean(a.force_path_style),
    access_key: "",
    secret_key: "",
    session_token: "",
    folder_id: a.folder_id,
  };
  showEditAccount.value = true;
}

async function submitEditAccount() {
  const f = editAccountForm.value;
  const body: Record<string, unknown> = {
    label: f.label.trim(),
    endpoint_url: f.endpoint_url.trim() || null,
    region: f.region.trim() || null,
    force_path_style: f.force_path_style,
    folder_id: f.folder_id,
  };
  if (f.access_key.trim()) body.access_key = f.access_key.trim();
  if (f.secret_key.trim()) body.secret_key = f.secret_key.trim();
  if (f.session_token.trim()) body.session_token = f.session_token.trim();
  await api.updateAccount(f.id, body);
  showEditAccount.value = false;
  await refreshData();
}

async function deleteAccount(a: AccountRow) {
  if (!confirm(`Delete ${a.label}?`)) return;
  await api.deleteAccount(a.id);
  tabs.value = tabs.value.filter((t) => t.accountId !== a.id);
  if (activeTabId.value && !tabs.value.find((t) => t.id === activeTabId.value)) {
    activeTabId.value = tabs.value.length ? tabs.value[tabs.value.length - 1].id : null;
  }
  await refreshData();
}

async function submitFolder() {
  const label = newFolderLabel.value.trim();
  if (!label) return;
  await api.createFolder({ label, parent_id: currentFolderId.value });
  newFolderLabel.value = "";
  showFolderForm.value = false;
  await refreshBrowse();
}

function openEditFolder(f: FolderRow) {
  editFolderForm.value = {
    id: f.id,
    label: f.label,
    parent_id: f.parent_id,
  };
  showEditFolder.value = true;
}

async function submitEditFolder() {
  const f = editFolderForm.value;
  await api.updateFolder(f.id, {
    label: f.label.trim(),
    parent_id: f.parent_id,
  });
  showEditFolder.value = false;
  await refreshBrowse();
}

async function deleteFolder(f: FolderRow) {
  if (!confirm(`Delete ${f.label}?`)) return;
  await api.deleteFolder(f.id);
  await refreshBrowse();
}
</script>

<template>
  <div v-if="checking" class="flex min-h-screen items-center justify-center text-slate-400">
    Loading...
  </div>
  <LoginForm v-else-if="!loggedIn" @logged-in="onLoggedIn" />
  <div v-else class="min-h-screen">
    <header class="border-b border-slate-800/80 bg-surface/80 backdrop-blur">
      <div class="mx-auto flex max-w-7xl items-center justify-between px-6 py-4">
        <div class="flex items-center gap-3">
          <div class="rounded-2xl bg-accent/20 p-2 text-accent">
            <Boxes class="h-5 w-5" />
          </div>
          <div>
            <h1 class="font-sans text-lg font-semibold tracking-tight text-white">S3 Client</h1>
            <p class="text-xs text-slate-500">{{ appVersion }}</p>
          </div>
        </div>
        <button type="button" class="button-secondary" @click="logout">
          <LogOut class="h-4 w-4" />
          Sign out
        </button>
      </div>
    </header>

    <div class="mx-auto flex max-w-7xl flex-1 gap-6 px-6 py-6">
      <aside
        class="panel w-80 shrink-0 flex-col"
        :class="sidebarOpen ? 'flex' : 'hidden lg:flex'"
      >
        <div class="border-b border-slate-800/80 p-4">
          <div class="flex items-center justify-between">
            <div>
              <p class="text-xs font-semibold uppercase tracking-wide text-slate-500">Workspace</p>
              <h2 class="text-base font-semibold text-white">Accounts</h2>
            </div>
            <button
              type="button"
              class="button-secondary"
              @click="showAccountForm = true"
            >
              <Plus class="h-4 w-4" />
              New
            </button>
          </div>
          <div class="mt-4 space-y-2">
            <div class="relative">
              <Search class="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-slate-500" />
              <input
                v-model="searchQuery"
                type="search"
                class="field pl-9"
                @input="onSearchInput"
                @focus="openSearchMode"
              />
            </div>
            <div class="flex items-center gap-2">
              <button
                type="button"
                class="button-secondary flex-1"
                @click="showFolderForm = true"
              >
                <Folder class="h-4 w-4" />
                New folder
              </button>
              <button
                v-if="searchQuery"
                type="button"
                class="button-secondary"
                @click="clearSearch"
              >
                Clear
              </button>
            </div>
          </div>
        </div>

        <div class="flex-1 overflow-auto p-4">
          <p v-if="loadErr" class="mb-3 text-sm text-red-400">{{ loadErr }}</p>

          <div v-if="breadcrumb.length" class="mb-4 flex flex-wrap items-center gap-2 text-xs text-slate-400">
            <button type="button" class="hover:text-slate-200" @click="goToFolder(null)">Root</button>
            <ChevronRight class="h-3 w-3" />
            <button
              v-for="b in breadcrumb"
              :key="b.id"
              type="button"
              class="hover:text-slate-200"
              @click="goToFolder(b.id)"
            >
              {{ b.label }}
            </button>
          </div>

          <div>
            <p class="text-xs font-semibold uppercase tracking-wide text-slate-500">Folders</p>
            <ul class="mt-2 space-y-1">
              <li
                v-for="f in browseFolders"
                :key="f.id"
                class="group flex items-center gap-2 rounded-lg px-3 py-2 hover:bg-surface-overlay"
              >
                <button
                  type="button"
                  class="flex-1 truncate text-left text-sm text-slate-200"
                  @click="goToFolder(f.id)"
                >
                  <span class="inline-flex items-center gap-2">
                    <Folder class="h-4 w-4 text-accent" />
                    {{ f.label }}
                  </span>
                </button>
                <button
                  type="button"
                  class="text-xs text-slate-500 opacity-0 transition group-hover:opacity-100"
                  @click="openEditFolder(f)"
                >
                  <Pencil class="h-4 w-4" />
                </button>
                <button
                  type="button"
                  class="text-xs text-red-400/80 opacity-0 transition group-hover:opacity-100"
                  @click="deleteFolder(f)"
                >
                  <Trash2 class="h-4 w-4" />
                </button>
              </li>
              <li v-if="!browseFolders.length" class="text-xs text-slate-500">No folders</li>
            </ul>
          </div>

          <div class="mt-6">
            <p class="text-xs font-semibold uppercase tracking-wide text-slate-500">
              {{ searchQuery ? "Search results" : "Accounts" }}
            </p>
            <ul class="mt-2 space-y-2">
              <li
                v-for="a in browseAccounts"
                :key="a.id"
                class="rounded-lg border border-slate-800 bg-surface-overlay p-3"
              >
                <button type="button" class="w-full text-left" @click="openTab(a)">
                  <div class="flex items-center justify-between">
                    <div class="min-w-0">
                      <p class="truncate text-sm font-semibold text-white">{{ a.label }}</p>
                      <p class="truncate text-xs text-slate-500">{{ a.endpoint_url || "AWS S3" }}</p>
                    </div>
                    <Cloud class="h-4 w-4 text-accent" />
                  </div>
                </button>
                <div class="mt-3 flex items-center gap-2 text-xs text-slate-400">
                  <button type="button" class="hover:text-slate-200" @click="openEditAccount(a)">Edit</button>
                  <button type="button" class="hover:text-red-400" @click="deleteAccount(a)">Delete</button>
                </div>
              </li>
              <li v-if="!browseAccounts.length" class="text-xs text-slate-500">No accounts</li>
            </ul>
          </div>
        </div>
      </aside>

      <main class="min-h-[640px] flex-1">
        <div v-if="!tabs.length" class="panel flex h-full items-center justify-center p-10">
          <div class="max-w-md text-center">
            <p class="text-lg font-semibold text-white">Open an account to start</p>
            <p class="mt-2 text-sm text-slate-400">
              Browse buckets, manage folders, and upload objects once an account is selected.
            </p>
          </div>
        </div>
        <div v-else class="flex h-full flex-col">
          <div class="flex flex-wrap gap-2">
            <button
              v-for="t in tabs"
              :key="t.id"
              type="button"
              class="rounded-lg border border-slate-800 px-4 py-2 text-sm"
              :class="activeTabId === t.id ? 'bg-accent/20 text-white' : 'bg-surface-overlay text-slate-400'"
              @click="activeTabId = t.id"
            >
              {{ t.label }}
              <span class="ml-2 text-slate-500" @click.stop="closeTab(t.id)">x</span>
            </button>
          </div>
          <div class="mt-4 flex-1">
            <AccountTab
              v-for="t in tabs"
              :key="t.id"
              :account-id="t.accountId"
              :account-label="t.label"
              :visible="activeTabId === t.id"
              v-show="activeTabId === t.id"
            />
          </div>
        </div>
      </main>
    </div>

    <div v-if="showAccountForm" class="fixed inset-0 z-40 flex items-center justify-center bg-black/50 p-6">
      <div class="panel w-full max-w-xl p-6">
        <h3 class="text-lg font-semibold text-white">New account</h3>
        <form class="mt-4 space-y-4" @submit.prevent="submitAccount">
          <input v-model="accountForm.label" class="field" placeholder="Label" required />
          <div class="grid gap-3 md:grid-cols-2">
            <input v-model="accountForm.endpoint_url" class="field" placeholder="Endpoint URL" />
            <input v-model="accountForm.region" class="field" placeholder="Region" />
          </div>
          <label class="flex items-center gap-2 text-sm text-slate-300">
            <input v-model="accountForm.force_path_style" type="checkbox" />
            Force path style
          </label>
          <div class="grid gap-3 md:grid-cols-2">
            <input v-model="accountForm.access_key" class="field" placeholder="Access key" required />
            <input v-model="accountForm.secret_key" class="field" placeholder="Secret key" required />
          </div>
          <input v-model="accountForm.session_token" class="field" placeholder="Session token (optional)" />
          <div class="flex justify-end gap-2">
            <button type="button" class="button-secondary" @click="showAccountForm = false">Cancel</button>
            <button type="submit" class="button-primary">Create</button>
          </div>
        </form>
      </div>
    </div>

    <div v-if="showEditAccount" class="fixed inset-0 z-40 flex items-center justify-center bg-black/50 p-6">
      <div class="panel w-full max-w-xl p-6">
        <h3 class="text-lg font-semibold text-white">Edit account</h3>
        <form class="mt-4 space-y-4" @submit.prevent="submitEditAccount">
          <input v-model="editAccountForm.label" class="field" placeholder="Label" required />
          <div class="grid gap-3 md:grid-cols-2">
            <input v-model="editAccountForm.endpoint_url" class="field" placeholder="Endpoint URL" />
            <input v-model="editAccountForm.region" class="field" placeholder="Region" />
          </div>
          <label class="flex items-center gap-2 text-sm text-slate-300">
            <input v-model="editAccountForm.force_path_style" type="checkbox" />
            Force path style
          </label>
          <div>
            <label class="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500">Folder</label>
            <select v-model="editAccountForm.folder_id" class="field">
              <option :value="null">(root)</option>
              <option v-for="f in allFolders" :key="f.id" :value="f.id">
                {{ folderOptionLabel(f.id) }}
              </option>
            </select>
          </div>
          <div class="grid gap-3 md:grid-cols-2">
            <input v-model="editAccountForm.access_key" class="field" placeholder="New access key (optional)" />
            <input v-model="editAccountForm.secret_key" class="field" placeholder="New secret key (optional)" />
          </div>
          <input v-model="editAccountForm.session_token" class="field" placeholder="New session token (optional)" />
          <div class="flex justify-end gap-2">
            <button type="button" class="button-secondary" @click="showEditAccount = false">Cancel</button>
            <button type="submit" class="button-primary">Save</button>
          </div>
        </form>
      </div>
    </div>

    <div v-if="showFolderForm" class="fixed inset-0 z-40 flex items-center justify-center bg-black/50 p-6">
      <div class="panel w-full max-w-md p-6">
        <h3 class="text-lg font-semibold text-white">New folder</h3>
        <form class="mt-4 space-y-4" @submit.prevent="submitFolder">
          <input v-model="newFolderLabel" class="field" placeholder="Folder name" required />
          <div class="flex justify-end gap-2">
            <button type="button" class="button-secondary" @click="showFolderForm = false">Cancel</button>
            <button type="submit" class="button-primary">Create</button>
          </div>
        </form>
      </div>
    </div>

    <div v-if="showEditFolder" class="fixed inset-0 z-40 flex items-center justify-center bg-black/50 p-6">
      <div class="panel w-full max-w-md p-6">
        <h3 class="text-lg font-semibold text-white">Edit folder</h3>
        <form class="mt-4 space-y-4" @submit.prevent="submitEditFolder">
          <input v-model="editFolderForm.label" class="field" placeholder="Folder name" required />
          <div>
            <label class="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500">Parent</label>
            <select v-model="editFolderForm.parent_id" class="field">
              <option :value="null">(root)</option>
              <option v-for="f in allFolders" :key="f.id" :value="f.id">
                {{ folderOptionLabel(f.id) }}
              </option>
            </select>
          </div>
          <div class="flex justify-end gap-2">
            <button type="button" class="button-secondary" @click="showEditFolder = false">Cancel</button>
            <button type="submit" class="button-primary">Save</button>
          </div>
        </form>
      </div>
    </div>
  </div>
</template>
