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
        // rewrite: (path) => path.replace(/^\/api/, '') // 如果后端不需要 /api 前缀才开这个，你的代码目前是需要的，所以注释掉
      }
    }
  }
})