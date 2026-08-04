from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, declarative_base
import os
import logging

logger = logging.getLogger("uvicorn")

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


def _run_migrations():
    """对现有表执行增量迁移。

    SQLite 不支持 ALTER TABLE DROP COLUMN，因此移除字段的策略是：
      1. 检查旧列是否存在于 PRAGMA table_info
      2. 如表无数据 → DROP TABLE + create_all 重建
      3. 如有数据 → 创建新表 → 复制数据 → 删旧表 → 重命名
    """
    try:
        with engine.connect() as conn:
            # ---- media_sync_status: 补齐 tmdb_id / imdb_id / douban_id ----
            existing = [
                row[1] for row in
                conn.execute(text("PRAGMA table_info(media_sync_status)")).fetchall()
            ]
            for col_name in ("tmdb_id", "imdb_id", "douban_id"):
                if col_name not in existing:
                    conn.execute(text(
                        f"ALTER TABLE media_sync_status ADD COLUMN {col_name} VARCHAR"
                    ))
                    logger.info("📦 [Migration] media_sync_status 添加字段: %s", col_name)

            # ★ P3-3b: douban_cast_cache（JSON 缓存列，SQLite 落为 TEXT，与 create_all 一致）
            if "douban_cast_cache" not in existing:
                conn.execute(text(
                    "ALTER TABLE media_sync_status ADD COLUMN douban_cast_cache TEXT"
                ))
                logger.info("📦 [Migration] media_sync_status 添加字段: douban_cast_cache")

            # ---- actor_records: 移除废弃的 image_url 列 ----
            # SQLite 不支持 DROP COLUMN，采用 DROP + 重建策略
            try:
                ar_cols = [
                    row[1] for row in
                    conn.execute(text("PRAGMA table_info(actor_records)")).fetchall()
                ]
            except Exception:
                ar_cols = []  # 表不存在 — create_all 会处理

            if "image_url" in ar_cols:
                # 检查表是否为空（安全起见）
                row_count = conn.execute(
                    text("SELECT COUNT(*) FROM actor_records")
                ).scalar() or 0

                conn.commit()  # 结束当前事务，DDL 需要独立执行

                if row_count == 0:
                    # 无数据 — 直接 DROP + 重建
                    with engine.connect() as ddl_conn:
                        ddl_conn.execute(text("DROP TABLE IF EXISTS actor_records"))
                        ddl_conn.commit()
                    from models import ActorRecord
                    ActorRecord.__table__.create(bind=engine, checkfirst=True)
                    logger.info(
                        "📦 [Migration] actor_records 已重建（移除废弃 image_url 列，0 行数据）"
                    )
                else:
                    # 有数据 — 安全重建（创建新表 → 复制 → 删旧 → 重命名）
                    logger.info(
                        "📦 [Migration] actor_records 含 %d 行数据，执行安全重建...",
                        row_count,
                    )
                    with engine.connect() as ddl_conn:
                        ddl_conn.execute(text("""
                            CREATE TABLE IF NOT EXISTS actor_records_new (
                                id INTEGER PRIMARY KEY AUTOINCREMENT,
                                emby_item_id VARCHAR NOT NULL,
                                name VARCHAR NOT NULL,
                                role VARCHAR,
                                type VARCHAR NOT NULL DEFAULT 'Actor',
                                sort_order INTEGER DEFAULT 0,
                                update_time DATETIME
                            )
                        """))
                        ddl_conn.execute(text("""
                            INSERT INTO actor_records_new
                                (id, emby_item_id, name, role, type, sort_order, update_time)
                            SELECT id, emby_item_id, name, role, type, sort_order, update_time
                            FROM actor_records
                        """))
                        ddl_conn.execute(text("DROP TABLE actor_records"))
                        ddl_conn.execute(text(
                            "ALTER TABLE actor_records_new RENAME TO actor_records"
                        ))
                        ddl_conn.commit()
                    logger.info(
                        "📦 [Migration] actor_records 已安全重建（移除废弃 image_url 列）"
                    )

            # ---- actor_profiles: douban_id → douban_celebrity_id ----
            try:
                ap_cols = [
                    row[1] for row in
                    conn.execute(text("PRAGMA table_info(actor_profiles)")).fetchall()
                ]
            except Exception:
                ap_cols = []

            if "douban_id" in ap_cols and "douban_celebrity_id" not in ap_cols:
                conn.execute(text(
                    "ALTER TABLE actor_profiles RENAME COLUMN douban_id TO douban_celebrity_id"
                ))
                conn.commit()
                logger.info(
                    "📦 [Migration] actor_profiles: douban_id → douban_celebrity_id"
                )

            # ---- actor_profiles / actor_records: 新增置信度与译名来源列 ----
            for table_name in ("actor_profiles", "actor_records"):
                try:
                    cols = [
                        row[1] for row in
                        conn.execute(text(f"PRAGMA table_info({table_name})")).fetchall()
                    ]
                except Exception:
                    continue  # 表尚未创建 — create_all 会处理

                for col_name, col_type in (
                    ("confidence_level", "INTEGER DEFAULT 0"),
                    ("translation_source", "VARCHAR DEFAULT ''"),
                ):
                    if col_name not in cols:
                        conn.execute(text(
                            f"ALTER TABLE {table_name} ADD COLUMN {col_name} {col_type}"
                        ))
                        logger.info("📦 [Migration] %s 添加字段: %s", table_name, col_name)

            conn.commit()
    except Exception as e:
        logger.debug("   [Migration] 跳过（表尚未创建或已是最新）: %s", e)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()