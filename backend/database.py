from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
import os

# 1. 动态获取当前文件所在的目录 (backend/)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# 2. 定义数据目录 (backend/data/)
DATA_DIR = os.path.join(BASE_DIR, 'data')

# 3. 如果 data 目录不存在，自动创建 (非常重要！否则报错)
if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)

# 4. 将数据库文件指定到 data 目录中
SQLALCHEMY_DATABASE_URL = f"sqlite:///{os.path.join(DATA_DIR, 'emby_ai.db')}"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()