from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import os

router = APIRouter()

# 🔥 动态计算绝对路径
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__)) # /app/backend/routers
BACKEND_DIR = os.path.dirname(CURRENT_DIR)               # /app/backend
CATEGORY_PATH = os.path.join(BACKEND_DIR, 'data', 'category.yaml')

ALLOWED_FILES = {
    "category_yaml": CATEGORY_PATH,  # 使用计算好的绝对路径
    # "config": os.path.join(BACKEND_DIR, '..', 'config', 'config.yaml')
}

class FileContent(BaseModel):
    content: str

@router.get("/editor/{file_key}")
def get_file_content(file_key: str):
    if file_key not in ALLOWED_FILES:
        raise HTTPException(status_code=400, detail="不支持的文件类型")
    
    file_path = ALLOWED_FILES[file_key]
    
    # 如果文件不存在，返回一个默认模板
    if not os.path.exists(file_path):
        default_content = """# 默认分类策略
movie:
  动画电影:
    genre_ids: '16'
tv:
  国漫:
    genre_ids: '16'
    origin_country: CN
"""
        return {"content": default_content, "path": file_path}
    
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return {"content": f.read(), "path": file_path}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/editor/{file_key}")
def save_file_content(file_key: str, payload: FileContent):
    if file_key not in ALLOWED_FILES:
        raise HTTPException(status_code=400, detail="不支持的文件类型")
    
    file_path = ALLOWED_FILES[file_key]
    try:
        # 确保 data 目录存在
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(payload.content)
        return {"status": "success", "message": "配置已保存，下次分类时自动生效"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))