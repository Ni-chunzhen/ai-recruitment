<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { useAuthStore } from '../stores/auth'

const authStore = useAuthStore()
const router = useRouter()
const route = useRoute()

const canManageJobs = computed(() => authStore.hasPermission('recruitment.manage'))
const canReadAudit = computed(() => authStore.hasPermission('audit.read'))

const mainNavItems = computed(() => {
  const items = [{ name: 'home', label: '首页', path: '/' }]
  if (canManageJobs.value) {
    items.push({ name: 'jobs', label: '岗位管理', path: '/jobs' })
    items.push({ name: 'resumes', label: '简历库', path: '/resumes' })
    items.push({ name: 'candidate-center', label: '候选人中心', path: '/candidate-center' })
  }
  return items
})

function isActive(path: string) {
  if (path === '/') return route.path === '/'
  return route.path === path || route.path.startsWith(`${path}/`)
}

async function handleLogout() {
  await authStore.signOut()
  await router.push({ name: 'login' })
}
</script>

<template>
  <div class="admin-shell">
    <aside class="sidebar">
      <div class="brand">AI 招聘</div>
      <nav>
        <RouterLink
          v-for="item in mainNavItems"
          :key="item.path"
          :to="item.path"
          class="nav-item"
          :class="{ active: isActive(item.path) }"
        >
          {{ item.label }}
        </RouterLink>
        <template v-if="canReadAudit">
          <div class="nav-group">系统管理</div>
          <RouterLink
            to="/system/ai-tasks"
            class="nav-item"
            :class="{ active: isActive('/system/ai-tasks') }"
          >
            AI任务中心
          </RouterLink>
        </template>
      </nav>
      <div class="sidebar-foot">
        <div class="user">{{ authStore.user?.display_name || authStore.user?.username }}</div>
        <button type="button" class="link-btn" @click="handleLogout">退出</button>
      </div>
    </aside>
    <main class="content">
      <slot />
    </main>
  </div>
</template>

<style scoped>
.admin-shell {
  display: flex;
  min-height: 100vh;
  background: var(--bg);
  color: var(--fg);
  text-align: left;
}

.sidebar {
  width: 220px;
  flex-shrink: 0;
  border-right: 1px solid var(--border);
  background: var(--surface);
  display: flex;
  flex-direction: column;
  padding: 20px 12px;
}

.brand {
  font-size: 18px;
  font-weight: 700;
  padding: 8px 12px 20px;
  color: var(--fg);
}

.nav-item {
  display: block;
  padding: 10px 12px;
  border-radius: 8px;
  color: var(--muted);
  text-decoration: none;
  margin-bottom: 4px;
}

.nav-item:hover,
.nav-item.active {
  background: color-mix(in oklab, var(--accent) 12%, transparent);
  color: var(--accent);
}

.nav-group {
  margin: 16px 12px 6px;
  font-size: 12px;
  color: var(--muted);
}

.sidebar-foot {
  margin-top: auto;
  padding: 12px;
  border-top: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.user {
  font-size: 13px;
  color: var(--fg);
}

.link-btn {
  border: none;
  background: transparent;
  color: var(--muted);
  cursor: pointer;
  text-align: left;
  padding: 0;
}

.content {
  flex: 1;
  min-width: 0;
  padding: 24px 28px 48px;
}
</style>
