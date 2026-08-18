import { createRouter, createWebHistory } from 'vue-router'

import { useAuthStore } from '../stores/auth'
import ForceChangePasswordView from '../views/ForceChangePasswordView.vue'
import ForbiddenView from '../views/ForbiddenView.vue'
import HomeView from '../views/HomeView.vue'
import JobCloseView from '../views/JobCloseView.vue'
import JobDetailView from '../views/JobDetailView.vue'
import JobEditView from '../views/JobEditView.vue'
import JobsListView from '../views/JobsListView.vue'
import LoginView from '../views/LoginView.vue'
import ResumeReviewView from '../views/ResumeReviewView.vue'
import ResumesListView from '../views/ResumesListView.vue'
import ScoreReportView from '../views/ScoreReportView.vue'
import InterviewTimelineView from '../views/InterviewTimelineView.vue'
import InterviewTranscriptView from '../views/InterviewTranscriptView.vue'
import AiTasksView from '../views/AiTasksView.vue'
import CandidateCenterListView from '../views/CandidateCenterListView.vue'
import CandidateCenterDetailView from '../views/CandidateCenterDetailView.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/login',
      name: 'login',
      component: LoginView,
      meta: { public: true },
    },
    {
      path: '/force-change-password',
      name: 'force-change-password',
      component: ForceChangePasswordView,
      meta: { requiresAuth: true },
    },
    {
      path: '/forbidden',
      name: 'forbidden',
      component: ForbiddenView,
      meta: { requiresAuth: true },
    },
    {
      path: '/',
      name: 'home',
      component: HomeView,
      meta: { requiresAuth: true, permission: 'profile.read' },
    },
    {
      path: '/jobs',
      name: 'jobs',
      component: JobsListView,
      meta: { requiresAuth: true, permission: 'recruitment.manage' },
    },
    {
      path: '/jobs/new',
      name: 'job-create',
      component: JobEditView,
      meta: { requiresAuth: true, permission: 'recruitment.manage' },
    },
    {
      path: '/jobs/:id/close',
      name: 'job-close',
      component: JobCloseView,
      meta: { requiresAuth: true, permission: 'recruitment.manage' },
    },
    {
      path: '/jobs/:id',
      name: 'job-detail',
      component: JobDetailView,
      meta: { requiresAuth: true, permission: 'recruitment.manage' },
    },
    {
      path: '/jobs/:id/edit',
      name: 'job-edit',
      component: JobEditView,
      meta: { requiresAuth: true, permission: 'recruitment.manage' },
    },
    {
      path: '/resumes',
      name: 'resumes',
      component: ResumesListView,
      meta: { requiresAuth: true, permission: 'recruitment.manage' },
    },
    {
      path: '/resumes/:versionId/review',
      name: 'resume-review',
      component: ResumeReviewView,
      meta: { requiresAuth: true, permission: 'recruitment.manage' },
    },
    {
      path: '/candidate-center',
      name: 'candidate-center',
      component: CandidateCenterListView,
      meta: { requiresAuth: true, permission: 'recruitment.manage' },
    },
    {
      path: '/candidate-center/candidates/:candidateId/applications/:applicationId',
      name: 'candidate-center-detail',
      component: CandidateCenterDetailView,
      meta: { requiresAuth: true, permission: 'recruitment.manage' },
    },
    {
      path: '/applications/:applicationId/score-report',
      name: 'score-report',
      component: ScoreReportView,
      meta: { requiresAuth: true, permission: 'recruitment.manage' },
    },
    {
      path: '/applications/:applicationId/interviews',
      name: 'application-interviews',
      component: InterviewTimelineView,
      meta: {
        requiresAuth: true,
        anyPermission: ['recruitment.manage', 'interview.execute'],
      },
    },
    {
      path: '/interview-rounds/:roundId/transcript',
      name: 'interview-transcript',
      component: InterviewTranscriptView,
      meta: {
        requiresAuth: true,
        anyPermission: ['recruitment.manage', 'interview.execute'],
      },
    },
    {
      path: '/system/ai-tasks',
      name: 'admin-ai-tasks',
      component: AiTasksView,
      meta: { requiresAuth: true, permission: 'audit.read' },
    },
  ],
})

router.beforeEach(async (to) => {
  const authStore = useAuthStore()
  if (!authStore.initialized) {
    await authStore.bootstrap()
  }

  if (to.meta.public) {
    if (authStore.isAuthenticated && to.name === 'login') {
      return authStore.mustChangePassword
        ? { name: 'force-change-password' }
        : { name: 'home' }
    }
    return true
  }

  if (!authStore.isAuthenticated) {
    return { name: 'login', query: { redirect: to.fullPath } }
  }

  if (authStore.mustChangePassword && to.name !== 'force-change-password') {
    return { name: 'force-change-password' }
  }

  const permission = to.meta.permission as string | undefined
  if (permission && !authStore.hasPermission(permission)) {
    return { name: 'forbidden' }
  }

  const anyPermission = to.meta.anyPermission as string[] | undefined
  if (
    anyPermission?.length &&
    !anyPermission.some((code) => authStore.hasPermission(code))
  ) {
    return { name: 'forbidden' }
  }

  return true
})

export default router
