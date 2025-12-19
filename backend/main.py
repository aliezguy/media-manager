from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import uvicorn
import os
from database import Base, engine
from config.settings import CONFIG_FILE, save_config

# 导入路由
from routers import moviepilot, system, emby, history

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
# 注意：你需要确保前端调用 emby 接口时路径是否匹配，如果前端是 /api/libraries，这里 prefix="/api" 就对了
if os.path.exists("backend/static"):
    app.mount("/", StaticFiles(directory="backend/static", html=True), name="static")
if __name__ == "__main__":
    # 初始化空配置
    if not os.path.exists(CONFIG_FILE):
        save_config({"emby_host": ""})
        
    uvicorn.run(app, host="0.0.0.0", port=8000)