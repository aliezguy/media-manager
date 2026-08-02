import { defineConfig } from 'vite'
import type { UserConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// https://vitejs.dev/config/
// 使用 UserConfig 显式标注配置类型，获得 IDE 提示与自文档化；
// defineConfig 负责在加载配置时注入 ConfigEnv（mode/command 等）。
const config: UserConfig = {
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
}

export default defineConfig(config)
