from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import desc
from database import get_db
from models import WashHistory

router = APIRouter()

@router.get("/history")
def get_wash_history(limit: int = 50, db: Session = Depends(get_db)):
    """获取最近的洗版记录"""
    records = db.query(WashHistory).order_by(desc(WashHistory.created_at)).limit(limit).all()
    return records

@router.delete("/history")
def clear_history(db: Session = Depends(get_db)):
    """清空历史"""
    db.query(WashHistory).delete()
    db.commit()
    return {"status": "success"}