<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'

import { useAuthStore } from '../stores/auth'

const authStore = useAuthStore()
const router = useRouter()

const username = ref('')
const password = ref('')
const errorMessage = ref('')
const submitting = ref(false)

async function handleSubmit() {
  errorMessage.value = ''
  submitting.value = true
  try {
    const user = await authStore.signIn(username.value, password.value)
    if (user.must_change_password) {
      await router.push({ name: 'force-change-password' })
      return
    }
    await router.push({ name: 'home' })
  } catch {
    errorMessage.value = '用户名或密码错误'
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <main class="page">
    <h1>登录</h1>
    <form @submit.prevent="handleSubmit">
      <label>
        用户名
        <input v-model="username" autocomplete="username" required />
      </label>
      <label>
        密码
        <input
          v-model="password"
          type="password"
          autocomplete="current-password"
          required
        />
      </label>
      <p v-if="errorMessage" class="error">{{ errorMessage }}</p>
      <button type="submit" :disabled="submitting">
        {{ submitting ? '登录中...' : '登录' }}
      </button>
    </form>
  </main>
</template>

<style scoped>
.page {
  max-width: 420px;
  margin: 2rem auto;
  padding: 1rem;
}

form {
  display: grid;
  gap: 1rem;
}

label {
  display: grid;
  gap: 0.5rem;
}

input {
  padding: 0.5rem;
}

.error {
  color: #991b1b;
}

button {
  padding: 0.6rem 1rem;
}
</style>
