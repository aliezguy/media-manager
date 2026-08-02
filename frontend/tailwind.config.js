/** @type {import('tailwindcss').Config} */
export default {
  content: [
    './index.html',
    './src/**/*.{vue,js,ts,jsx,tsx}',
  ],
  theme: {
    extend: {
      colors: {
        // 星际战舰控制台 — 设计令牌（与 style.css CSS 变量一致）
        space: {
          950: '#0B1120',
          900: '#0F172A',
          800: '#121212',
          700: '#1E293B',
        },
        // 核心状态色
        electric: '#3B82F6',   // 电光蓝 Primary
        neon: '#10B981',       // 霓虹绿 Success
        warn: '#F59E0B',       // 警示黄 Warning
        danger: '#EF4444',     // 霓虹红 Danger
      },
      fontFamily: {
        hud: ['JetBrains Mono', 'SF Mono', 'Menlo', 'Consolas', 'monospace'],
      },
      boxShadow: {
        'glow-blue': '0 0 15px rgba(59,130,246,0.25)',
        'glow-green': '0 0 15px rgba(16,185,129,0.25)',
        'glow-yellow': '0 0 15px rgba(245,158,11,0.25)',
        'glow-red': '0 0 15px rgba(239,68,68,0.25)',
      },
      dropShadow: {
        // 电光蓝箭头 / 文字发光
        glow: '0 0 5px rgba(59, 130, 246, 0.5)',
      },
    },
  },
  plugins: [],
}
