import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [vue()],
  server: {
    host: '0.0.0.0',
    port: 5173,
    // 🔥 关键配置：开发环境代理
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000', // 转发给后端
        changeOrigin: true,
      },
      // ★ 本地演员头像代理 — 将 /people 请求转发到 FastAPI 静态挂载
      '/people': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
      '/static_actors': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      }
    }
  }
})