<script setup lang="ts">
import { ref } from "vue";
import { api } from "@/api";

const emit = defineEmits<{ loggedIn: [] }>();

const username = ref("");
const password = ref("");
const err = ref("");
const busy = ref(false);

async function submit() {
  err.value = "";
  busy.value = true;
  try {
    await api.login(username.value.trim(), password.value);
    emit("loggedIn");
  } catch (e) {
    err.value = e instanceof Error ? e.message : "Login failed";
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <div class="flex min-h-screen items-center justify-center px-6 py-10">
    <div class="panel w-full max-w-md p-8">
      <h1 class="font-sans text-2xl font-semibold tracking-tight text-white">
        S3 Client
      </h1>
      <p class="mt-2 text-sm text-slate-400">
        Sign in to manage accounts, buckets, and objects.
      </p>
      <form class="mt-6 space-y-4" @submit.prevent="submit">
        <div>
          <label class="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500">
            Username
          </label>
          <input
            v-model="username"
            type="text"
            autocomplete="username"
            class="field"
            required
          />
        </div>
        <div>
          <label class="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500">
            Password
          </label>
          <input
            v-model="password"
            type="password"
            autocomplete="current-password"
            class="field"
            required
          />
        </div>
        <p v-if="err" class="text-sm text-red-400">{{ err }}</p>
        <button type="submit" :disabled="busy" class="button-primary w-full">
          {{ busy ? "Signing in..." : "Sign in" }}
        </button>
      </form>
    </div>
  </div>
</template>
