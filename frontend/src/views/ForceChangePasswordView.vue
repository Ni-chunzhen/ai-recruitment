<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'

import { useAuthStore } from '../stores/auth'

const authStore = useAuthStore()
const router = useRouter()

const currentPassword = ref('')
const newPassword = ref('')
const confirmPassword = ref('')
const errorMessage = ref('')
const submitting = ref(false)

async function handleSubmit() {
  errorMessage.value = ''
  if (newPassword.value !== confirmPassword.value) {
    errorMessage.value = '两次输入的新密码不一致'
    return
  }

  submitting.value = true
  try {
    await authStore.updatePassword(currentPassword.value, newPassword.value)
    await router.push({ name: 'home' })
  } catch {
    errorMessage.value = '修改密码失败，请检查当前密码和新密码规则'
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <main class="page">
    <h1>修改密码</h1>
    <p>首次登录需要修改密码后才能继续使用系统。</p>
    <form @submit.prevent="handleSubmit">
      <label>
        当前密码
        <input v-model="currentPassword" type="password" required />
      </label>
      <label>
        新密码
        <input v-model="newPassword" type="password" required />
      </label>
      <label>
        确认新密码
        <input v-model="confirmPassword" type="password" required />
      </label>
      <p v-if="errorMessage" class="error">{{ errorMessage }}</p>
      <button type="submit" :disabled="submitting">保存新密码</button>
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

.error {
  color: #991b1b;
}
</style>
