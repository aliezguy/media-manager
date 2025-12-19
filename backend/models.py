# backend/models.py
from database import Base
from sqlalchemy import Column, String, JSON, Integer, DateTime
from datetime import datetime

class MediaTag(Base):
    __tablename__ = "media_tags"

    # item_id 是主键，对应 Emby 的 ID
    item_id = Column(String, primary_key=True, index=True)
    name = Column(String)
    # 使用 JSON 类型直接存列表 ['古装', '悬疑']
    tags = Column(JSON)

class WashHistory(Base):
    __tablename__ = "wash_history"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String)           # 剧名
    season = Column(Integer)        # 季度
    tmdb_id = Column(String)        # TMDB ID
    status = Column(String)         # 状态: "success" 或 "failed"
    message = Column(String)        # 详细信息 (如订阅ID或错误原因)
    
    # 记录当时的参数快照 (因为全局配置可能会变)
    wash_params = Column(JSON)      
    
    created_at = Column(DateTime, default=datetime.now) # 时间