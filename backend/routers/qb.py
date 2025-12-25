from fastapi import APIRouter, HTTPException, Body
from typing import List, Optional
from config.settings import load_config, save_config
from services.qb_service import get_qb_data, get_torrents, delete_torrents,get_torrent_files
import uuid

router = APIRouter()

# ===========================
# 1. qBittorrent 配置管理
# ===========================

@router.get("/qb/configs")
async def get_qb_configs():
    cfg = load_config()
    return cfg.get("qb_configs", [])

@router.post("/qb/configs")
async def add_qb_config(config: dict = Body(...)):
    if not config.get("name") or not config.get("host"):
        raise HTTPException(status_code=400, detail="Name and Host are required")
    
    cfg = load_config()
    qb_configs = cfg.get("qb_configs", [])
    
    # 生成 ID
    config["id"] = str(uuid.uuid4())
    if "active" not in config:
        config["active"] = True
        
    qb_configs.append(config)
    save_config({"qb_configs": qb_configs})
    return config

@router.put("/qb/configs/{config_id}")
async def update_qb_config(config_id: str, config: dict = Body(...)):
    cfg = load_config()
    qb_configs = cfg.get("qb_configs", [])
    
    index = -1
    for i, c in enumerate(qb_configs):
        if c.get("id") == config_id:
            index = i
            break
            
    if index == -1:
        raise HTTPException(status_code=404, detail="Config not found")
        
    # 保持 ID 不变
    config["id"] = config_id
    qb_configs[index] = config
    save_config({"qb_configs": qb_configs})
    return config

@router.delete("/qb/configs/{config_id}")
async def delete_qb_config(config_id: str):
    cfg = load_config()
    qb_configs = cfg.get("qb_configs", [])
    
    new_configs = [c for c in qb_configs if c.get("id") != config_id]
    if len(new_configs) == len(qb_configs):
        raise HTTPException(status_code=404, detail="Config not found")
        
    save_config({"qb_configs": new_configs})
    return {"message": "Deleted successfully"}

# ===========================
# 2. qBittorrent 实例数据
# ===========================

@router.get("/qb/data")
async def get_all_qb_data():
    """获取所有已激活 qB 实例的标签和分类"""
    return get_qb_data()

@router.get("/qb/{config_id}/torrents")
async def get_qb_torrents(
    config_id: str, 
    filter: Optional[str] = None, 
    tag: Optional[str] = None, 
    category: Optional[str] = None,
    keyword: Optional[str] = None
):
    return get_torrents(config_id, filter, tag, category,keyword)

@router.post("/qb/{config_id}/torrents/delete")
async def delete_qb_torrents(
    config_id: str, 
    hashes: List[str] = Body(...), 
    delete_files: bool = Body(False)
):
    success = delete_torrents(config_id, hashes, delete_files)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to delete torrents")
    return {"message": "Success"}

@router.get("/qb/{config_id}/torrents/{hash}/files")
def get_files(config_id: str, hash: str):
    return get_torrent_files(config_id, hash)
