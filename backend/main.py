from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import uvicorn
import os
import logging
from logging.handlers import RotatingFileHandler
from database import Base, engine
from config.settings import CONFIG_FILE, save_config

# ---------------------------------------------------------------------------
# 日志配置 — 同时输出到控制台和文件
# ---------------------------------------------------------------------------
LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, "app.log")

LOG_FORMAT = "%(asctime)s [%(levelname)-7s] %(name)s: %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# 文件 handler（10 MB × 5 个备份自动轮转）
file_handler = RotatingFileHandler(
    LOG_FILE, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
)
file_handler.setFormatter(logging.Formatter(LOG_FORMAT, DATE_FORMAT))
file_handler.setLevel(logging.DEBUG)

# 控制台 handler
console_handler = logging.StreamHandler()
console_handler.setFormatter(logging.Formatter(LOG_FORMAT, DATE_FORMAT))
console_handler.setLevel(logging.INFO)

# 配置根 logger，所有模块级 logger 会传播到这里
root_logger = logging.getLogger()
root_logger.setLevel(logging.DEBUG)
root_logger.addHandler(file_handler)
root_logger.addHandler(console_handler)

# 降低第三方库日志噪音
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("grpc").setLevel(logging.WARNING)
logging.getLogger("qbittorrentapi").setLevel(logging.WARNING)

logging.getLogger("main").info("=" * 60)
logging.getLogger("main").info("Emby AI Manager 启动 — 日志文件: %s", LOG_FILE)
logging.getLogger("main").info("=" * 60)

# 导入路由
from routers import moviepilot, system, emby, history, qb, file_editor, cd2_router, organize_router, task_flow_router, task_dashboard_router, scheduler_router

# 初始化数据库表
Base.metadata.create_all(bind=engine)

app = FastAPI(title="Emby AI Manager")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(moviepilot.router, prefix="/api", tags=["MoviePilot"])
app.include_router(system.router, prefix="/api", tags=["System"])
app.include_router(emby.router, prefix="/api", tags=["Emby"]) 
app.include_router(history.router, prefix="/api", tags=["History"])
app.include_router(qb.router, prefix="/api", tags=["qBittorrent"])
app.include_router(file_editor.router, prefix="/api", tags=["Editor"])
app.include_router(cd2_router.router, prefix="/api", tags=["CloudDrive2"])
app.include_router(organize_router.router, prefix="/api", tags=["Organize"])
app.include_router(task_flow_router.router, prefix="/api", tags=["TaskFlow"])
app.include_router(task_dashboard_router.router, prefix="/api", tags=["Dashboard"])
app.include_router(scheduler_router.router, prefix="/api", tags=["Scheduler"])
# ---------------------------------------------------------------------------
# APScheduler 生命周期管理
# ---------------------------------------------------------------------------
from services.scheduler_service import scheduler, load_all_tasks


@app.on_event("startup")
async def startup_scheduler():
    """FastAPI 启动时：启动 APScheduler 并加载所有活跃任务。"""
    load_all_tasks()
    scheduler.start()
    logging.getLogger("main").info("[Scheduler] APScheduler 已启动")


@app.on_event("shutdown")
async def shutdown_scheduler():
    """FastAPI 关闭时：优雅关闭 APScheduler。"""
    scheduler.shutdown(wait=False)
    logging.getLogger("main").info("[Scheduler] APScheduler 已关闭")


# 注意：你需要确保前端调用 emby 接口时路径是否匹配，如果前端是 /api/libraries，这里 prefix="/api" 就对了
if os.path.exists("backend/static"):
    app.mount("/", StaticFiles(directory="backend/static", html=True), name="static")
if __name__ == "__main__":
    # 初始化空配置
    if not os.path.exists(CONFIG_FILE):
        save_config({"emby_host": ""})
        
    uvicorn.run(app, host="0.0.0.0", port=8000)
