# backend/models.py
from database import Base
from sqlalchemy import Column, Integer, String, JSON, DateTime
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
    name = Column(String, index=True)
    season = Column(Integer)
    tmdb_id = Column(Integer)
    status = Column(String)
    message = Column(String)
    wash_params = Column(JSON)
    # 🔥 新增字段，默认值为 'complete'
    wash_type = Column(String, default="complete") 
    created_at = Column(DateTime, default=datetime.now)