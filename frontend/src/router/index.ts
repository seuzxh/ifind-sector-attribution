import { createRouter, createWebHashHistory, type RouteRecordRaw } from 'vue-router'

// 路由表：对应原 ?board= 的 6 个看板。阶段 1 先用占位组件，后续阶段逐个替换。
const routes: RouteRecordRaw[] = [
  {
    path: '/',
    component: () => import('@/layouts/AppLayout.vue'),
    children: [
      { path: '', redirect: '/sector' },
      { path: 'sector', name: 'sector', component: () => import('@/views/DashboardPage.vue'), meta: { title: '板块强度监控', icon: '📊' } },
      { path: 'custom', name: 'custom', component: () => import('@/views/DashboardPage.vue'), meta: { title: '自选分组监控', icon: '⭐' } },
      { path: 'auction', name: 'auction', component: () => import('@/views/AuctionPage.vue'), meta: { title: '集合竞价', icon: '⚡' } },
      { path: 'scan', name: 'scan', component: () => import('@/views/PlaceholderView.vue'), meta: { title: '自选强势归类', icon: '🎯' } },
      { path: 'market_scan', name: 'market_scan', component: () => import('@/views/PlaceholderView.vue'), meta: { title: '全市场强势归类', icon: '🌐' } },
      { path: 'chat', name: 'chat', component: () => import('@/views/PlaceholderView.vue'), meta: { title: 'AI 问答', icon: '🤖' } },
    ],
  },
]

const router = createRouter({
  history: createWebHashHistory(),
  routes,
})

export default router
