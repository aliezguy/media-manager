/// <reference types="vite/client" />

// 项目自定义环境变量类型声明区（在 .env 文件中定义后，import.meta.env 即获得类型提示）
// 示例：
//   VITE_API_BASE_URL=https://api.example.com
// 声明：
//   interface ImportMetaEnv {
//     readonly VITE_API_BASE_URL?: string
//   }
// 注：VITE_ 开头的变量才暴露给前端；TS 中通过 interface 合并机制与 vite/client 类型自动合并。

interface ImportMetaEnv {
  // readonly VITE_API_BASE_URL?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
