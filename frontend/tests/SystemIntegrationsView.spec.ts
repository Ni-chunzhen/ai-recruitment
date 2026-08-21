import { mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'

import * as integrationsApi from '../src/api/integrations'
import type { IntegrationsSummary } from '../src/api/integrations'
import AdminLayout from '../src/layouts/AdminLayout.vue'
import { useAuthStore } from '../src/stores/auth'
import ForbiddenView from '../src/views/ForbiddenView.vue'
import HomeView from '../src/views/HomeView.vue'
import SystemIntegrationsView from '../src/views/SystemIntegrationsView.vue'

vi.mock('element-plus', async () => {
  const actual = await vi.importActual<typeof import('element-plus')>('element-plus')
  return {
    ...actual,
    ElMessage: {
      success: vi.fn(),
      error: vi.fn(),
      warning: vi.fn(),
      info: vi.fn(),
    },
  }
})

function makeUser(permissions: string[]) {
  return {
    id: 'u1',
    username: 'admin',
    display_name: 'Admin',
    is_active: true,
    must_change_password: false,
    roles: permissions.includes('integration.manage')
      ? ['system_admin']
      : permissions.includes('recruitment.manage')
        ? ['recruiter_admin']
        : ['interviewer'],
    permissions,
  }
}

function field(value = '', configured = Boolean(value)): integrationsApi.FieldStatus {
  return { value, configured, enabled: true, status: 'ok' }
}

function secretField(configured: boolean): integrationsApi.FieldStatus {
  return { configured, enabled: true, status: 'ok' }
}

function makeSummary(overrides: Partial<IntegrationsSummary> = {}): IntegrationsSummary {
  return {
    dify: {
      api_base_url: field('https://example.invalid/v1', true),
      api_key: secretField(true),
      jd_parse_api_key: secretField(false),
      score_dimension_api_key: secretField(false),
      jd_parse_workflow_id: field('wf-jd'),
      score_dimension_workflow_id: field(''),
      resume_parse_api_key: secretField(false),
      resume_score_api_key: secretField(false),
      resume_parse_workflow_id: field(''),
      resume_score_workflow_id: field(''),
      interview_question_generate_api_key: secretField(false),
      interview_question_generate_workflow_id: field(''),
      ai_provider: field('mock', true),
      live_enabled_env: false,
    },
    minio: {
      endpoint: field('127.0.0.1:9000', true),
      access_key: secretField(true),
      secret_key: secretField(true),
      bucket: field('resumes', true),
      secure: field('false', true),
      presign_seconds: field('600', true),
    },
    mail: {
      delivery_provider: 'console',
      queue_name: 'mail_outbound',
      smtp_enabled: false,
      note: '一期仅 Console，无 SMTP',
    },
    restart_required: true,
    message_key: 'integrations.restart_required',
    ...overrides,
  }
}

const globalStubs = {
  'el-button': {
    props: ['disabled', 'loading', 'type'],
    template: '<button :disabled="disabled || loading" :data-type="type"><slot /></button>',
  },
  'el-alert': {
    props: ['title', 'type', 'closable'],
    template: '<div class="el-alert" :data-type="type" data-test="restart-required-hint">{{ title }}</div>',
  },
  'el-input': {
    props: ['modelValue', 'type', 'placeholder', 'showPassword', 'autocomplete'],
    emits: ['update:modelValue'],
    template:
      '<input class="field-input" :type="type || \'text\'" :value="modelValue" :placeholder="placeholder" @input="$emit(\'update:modelValue\', ($event.target).value)" />',
  },
  'el-select': {
    props: ['modelValue'],
    emits: ['update:modelValue'],
    template:
      '<select class="field-select" :value="modelValue" @change="$emit(\'update:modelValue\', ($event.target).value)"><slot /></select>',
  },
  'el-option': {
    props: ['label', 'value'],
    template: '<option :value="value">{{ label }}</option>',
  },
}

function makeRouter(): Router {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      {
        path: '/',
        name: 'home',
        component: HomeView,
        meta: { requiresAuth: true, permission: 'profile.read' },
      },
      {
        path: '/system/integrations',
        name: 'system-integrations',
        component: SystemIntegrationsView,
        meta: { requiresAuth: true, permission: 'integration.manage' },
      },
      {
        path: '/forbidden',
        name: 'forbidden',
        component: ForbiddenView,
        meta: { requiresAuth: true },
      },
    ],
  })
}

async function mountLayout(permissions: string[]) {
  const pinia = createPinia()
  setActivePinia(pinia)
  const store = useAuthStore()
  store.initialized = true
  store.user = makeUser(permissions)
  const router = makeRouter()
  await router.push('/')
  await router.isReady()
  return mount(AdminLayout, {
    global: { plugins: [pinia, router] },
  })
}

async function mountView(
  permissions: string[] = ['integration.manage', 'profile.read'],
): Promise<VueWrapper> {
  const pinia = createPinia()
  setActivePinia(pinia)
  const store = useAuthStore()
  store.initialized = true
  store.user = makeUser(permissions)
  const router = makeRouter()
  await router.push('/system/integrations')
  await router.isReady()
  const wrapper = mount(SystemIntegrationsView, {
    global: {
      plugins: [pinia, router],
      stubs: {
        AdminLayout: { template: '<div class="layout"><slot /></div>' },
        ...globalStubs,
      },
    },
  })
  await nextTick()
  await nextTick()
  return wrapper
}

describe('SystemIntegrationsView', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    vi.spyOn(integrationsApi, 'getIntegrationsSummary').mockResolvedValue(makeSummary())
    vi.spyOn(integrationsApi, 'updateDifyIntegrations').mockResolvedValue(
      makeSummary({ restart_required: true }),
    )
    vi.spyOn(integrationsApi, 'updateMinioIntegrations').mockResolvedValue(
      makeSummary({ restart_required: true }),
    )
    vi.spyOn(integrationsApi, 'testIntegrationProvider').mockResolvedValue({
      ok: true,
      error_code: null,
      latency_ms: 11,
    })
  })

  it('shows integrations menu only with integration.manage', async () => {
    const admin = await mountLayout(['integration.manage', 'profile.read'])
    expect(admin.text()).toContain('第三方集成')
    expect(admin.find('[data-test="nav-integrations"]').exists()).toBe(true)

    const recruiter = await mountLayout(['recruitment.manage', 'profile.read'])
    expect(recruiter.text()).not.toContain('第三方集成')

    const interviewer = await mountLayout(['interview.execute', 'profile.read'])
    expect(interviewer.text()).not.toContain('第三方集成')
  })

  it('rejects recruiter from integrations route via app router', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const store = useAuthStore()
    store.initialized = true
    store.user = makeUser(['recruitment.manage', 'profile.read'])
    const { default: appRouter } = await import('../src/router')
    await appRouter.push('/system/integrations')
    await appRouter.isReady()
    expect(appRouter.currentRoute.value.name).toBe('forbidden')
  })

  it('renders three blocks without SMTP form or live auto-enable copy', async () => {
    const wrapper = await mountView()
    expect(wrapper.find('[data-test="integration-config-page"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="dify-block"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="minio-block"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="mail-block"]').exists()).toBe(true)
    const text = wrapper.text()
    expect(text).toContain('console')
    expect(text).toContain('mail_outbound')
    expect(text.toLowerCase()).not.toContain('smtp_host')
    expect(text).not.toContain('发送测试邮件')
    expect(text).not.toContain('自动开启')
    expect(wrapper.find('[data-test="dify-test-btn"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="minio-test-btn"]').exists()).toBe(true)
  })

  it('keeps secret inputs empty and type=password; empty secrets omitted on save', async () => {
    const wrapper = await mountView()
    const passwordInputs = wrapper
      .findAll('input.field-input')
      .filter((el) => el.attributes('type') === 'password')
    expect(passwordInputs.length).toBeGreaterThanOrEqual(2)
    for (const input of passwordInputs) {
      expect((input.element as HTMLInputElement).value).toBe('')
    }

    const saveBtn = wrapper.findAll('button').find((b) => b.text().includes('保存 Dify'))
    expect(saveBtn).toBeTruthy()
    await saveBtn!.trigger('click')
    await nextTick()
    expect(integrationsApi.updateDifyIntegrations).toHaveBeenCalled()
    const payload = vi.mocked(integrationsApi.updateDifyIntegrations).mock.calls[0][0]
    expect(payload).not.toHaveProperty('api_key')
    expect(JSON.stringify(payload)).not.toContain('enc:v1:')
  })

  it('shows restart hint after successful save', async () => {
    const wrapper = await mountView()
    const saveBtn = wrapper.findAll('button').find((b) => b.text().includes('保存 Dify'))
    await saveBtn!.trigger('click')
    await nextTick()
    await nextTick()
    const hint = wrapper.find('[data-test="restart-required-hint"]')
    expect(hint.exists()).toBe(true)
    expect(hint.text()).toMatch(/重启 API/)
    expect(hint.text()).toMatch(/worker/)
  })

  it('shows connectivity result with only ok/error_code/latency_ms', async () => {
    const wrapper = await mountView()
    await wrapper.find('[data-test="dify-test-btn"]').trigger('click')
    await nextTick()
    await nextTick()
    const result = wrapper.find('[data-test="dify-test-result"]')
    expect(result.exists()).toBe(true)
    expect(result.text()).toContain('ok=true')
    expect(result.text()).toContain('error_code=null')
    expect(result.text()).toContain('latency_ms=11')
    expect(result.text()).not.toContain('Authorization')
    expect(result.text()).not.toContain('body')
  })
})
