import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath, URL } from 'node:url'

// 构建产物输出到上级 static/，FastAPI 直接 serve
export default defineConfig(({ mode }) => ({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  // base：开发用 '/'（vite dev server 根），构建用 '/static/'（FastAPI 把 Vue 挂在 /static 下）
  // 这样 build 后 index.html 里的 asset 引用是 /static/assets/...，能被 FastAPI 正确 serve
  base: mode === 'production' ? '/static/' : '/',
  build: {
    outDir: '../static',
    emptyOutDir: true,   // 构建前清空 static/
  },
  server: {
    port: 5173,
    // 开发代理：把 /api 转发到后端 FastAPI，避免跨域
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
}))
