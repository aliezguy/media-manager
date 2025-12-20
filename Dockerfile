# ===============================
# 阶段 1: 构建前端 (Vue)
# ===============================
FROM node:20-alpine as frontend-build

WORKDIR /app/frontend

# 复制依赖定义并安装
COPY frontend/package*.json ./
RUN npm install

# 复制源代码并编译打包
COPY frontend/ .
RUN npm run build

# ===============================
# 阶段 2: 构建后端 (Python)
# ===============================
FROM python:3.11-slim

WORKDIR /app

# 设置时区为上海
RUN ln -sf /usr/share/zoneinfo/Asia/Shanghai /etc/localtime

# 安装依赖
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制后端代码
COPY backend/ ./backend/

# 把第一阶段编译好的前端文件，复制到后端的 static 目录
# 假设 vite 默认打包到 frontend/dist，我们需要把它放到 backend/static
COPY --from=frontend-build /app/frontend/dist ./backend/static

# 创建数据目录挂载点
VOLUME /app/backend/data

# 暴露端口
EXPOSE 8000

# 启动命令
CMD ["python", "backend/main.py"]