# Emby AI Manager — 项目文档

## 技术栈

- **后端**: Python FastAPI (port 8000)
- **前端**: Vue 3 + Element Plus + Vite
- **数据库**: SQLite (via SQLAlchemy)

## 项目结构

```
backend/
  main.py              # FastAPI 入口，注册所有路由
  models.py             # SQLAlchemy 数据模型
  database.py           # 数据库连接
  config/settings.py    # 配置管理 (config.json)
  services/             # 业务逻辑层
    cd2_service.py      # ★ CloudDrive2 gRPC 客户端
    mp_service.py       # MoviePilot 服务
    qb_service.py       # qBittorrent 服务
    emby_service.py     # Emby 服务
    tmdb_service.py     # TMDB 服务
    category_service.py # 分类服务
    clouddrive_pb/      # CD2 Proto 生成的 Python gRPC Stubs
  routers/              # API 路由
    cd2_router.py       # ★ CD2 目录查询接口
    moviepilot.py       # MoviePilot 相关
    qb.py               # qBittorrent 相关
    emby.py             # Emby 相关
    ...
frontend/
  src/components/
    TorrentCleanup.vue  # ★ 种子清理 + CD2 文件浏览页面
    ...
```

## CloudDrive2 (CD2) 集成

### 服务信息
- **地址**: 192.168.31.173:19797
- **协议**: gRPC (insecure channel)
- **Proto 文件**: `clouddrive.proto` (项目根目录)
- **API 文档**: https://www.clouddrive2.com/api/CloudDrive2_gRPC_API_Guide.html

### 核心文件
| 文件 | 用途 |
|------|------|
| `backend/services/cd2_service.py` | gRPC 客户端，封装鉴权、目录查询 |
| `backend/services/clouddrive_pb/` | 从 `clouddrive.proto` 生成的 Python Stubs |
| `backend/routers/cd2_router.py` | REST API: `GET /api/cd2/directories` |
| `clouddrive.proto` | CD2 官方 Proto 定义 (v1.0.11) |

### 关键 RPC 方法
- **鉴权**: `GetToken(GetTokenRequest) → JWTToken` — 用用户名/密码获取 Bearer Token
- **目录列表**: `GetSubFiles(ListSubFileRequest) → stream SubFilesReply` — 按路径列出子文件/目录
- **文件查找**: `FindFileByPath(FindFileByPathRequest) → CloudDriveFile` — 按完整路径查找文件

### 环境变量
```bash
CD2_HOST=192.168.31.173
CD2_PORT=19797
CD2_USERNAME=<你的CD2账号>
CD2_PASSWORD=<你的CD2密码>
CD2_MEDIA_DIR=/80003588/emby库/电视剧/国产剧/
CD2_ORGANIZED_DIR=/80003588/网盘整理/完结整理/电视剧/国产剧
```

### 目标目录
- **目录 A (媒体库/待整理)**: `/80003588/emby库/电视剧/国产剧/`
- **目录 B (已完结/已整理)**: `/80003588/网盘整理/完结整理/电视剧/国产剧`

### 前端页面
- CD2 文件浏览模块集成在 `TorrentCleanup.vue` 页面底部
- 左右两列分别展示"媒体库（待整理）"和"已完结（已整理）"
- 页面加载时自动调用 `GET /api/cd2/directories` 获取数据

### 后续开发方向
- 基于两个目录的文件列表对比，实现自动差异检测
- 标记媒体库中存在但已完结目录中不存在的项目（待整理）
- 标记已完结目录中存在但媒体库中不存在的项目（可清理）
- 提供一键清理/归档操作

## 生成的 Proto Stubs 更新

当 CD2 升级后 Proto 文件有变更时，重新生成 stubs：

```bash
cd backend
source venv/bin/activate
curl https://www.clouddrive2.com/api/clouddrive.proto -o ../clouddrive.proto

python3 -m grpc_tools.protoc \
  -I"../" \
  -I"venv/lib/python3.13/site-packages/grpc_tools/_proto" \
  --python_out=services/clouddrive_pb \
  --grpc_python_out=services/clouddrive_pb \
  ../clouddrive.proto

# 修复 grpc 文件的 import 路径:
# import clouddrive_pb2 → from services.clouddrive_pb import clouddrive_pb2
```
