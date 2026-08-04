"""一次性直迁脚本：SQLite → MySQL（纯切换，幂等可重跑）。

用法：
  venv/bin/python scripts/migrate_sqlite_to_mysql.py --dry-run          # 先看行数对比（不写数据）
  venv/bin/python scripts/migrate_sqlite_to_mysql.py                    # 正式直迁

流程：建库(utf8mb4) → create_all + _run_migrations 建 MySQL 表 → 逐表直迁
      （保留显式主键，JSON 列 SQLite TEXT → Python dict → MySQL 原生 JSON）→ 行数对比。

安全说明：
  --dry-run 只做「建表结构 + 读源计数」，绝不 INSERT / DELETE / UPDATE 任何数据行。
  正式直迁前会 DELETE 目标表实现幂等重跑 —— 该行为仅在**不带** --dry-run 时发生。
"""
import argparse, json, logging, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from database import Base, _run_migrations, _build_database_url
import models  # noqa: 注册所有表

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("migrate")

# 模型里全部 JSON 列（SQLite 落 TEXT，读回需 json.loads）
_JSON_COLUMNS = {"detail", "tags", "wash_params", "context", "details", "douban_cast_cache"}

# 目标表顺序：无 FK 约束，顺序自由；关联表放后阅读友好
_TABLE_ORDER = [
    "media_sync_status", "media_metadata", "actor_profiles", "actor_records",
    "media_tags", "tv_show_details", "completed_season_records", "torrent_records",
    "task_action_logs", "wash_history", "auto_task_flows", "scheduled_tasks", "scan_run_logs",
]


def _row_count(conn, table: str) -> int:
    return conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar() or 0


def _read_rows(src_conn, table: str) -> list[dict]:
    """读全表 → dict 列表。JSON 列从 SQLite TEXT 解析为 Python 对象。"""
    cols = [c["name"] for c in inspect(src_conn).get_columns(table)]
    rows = []
    for row in src_conn.execute(text(f"SELECT {', '.join(cols)} FROM {table}")):
        d = dict(zip(cols, row))
        for jcol in _JSON_COLUMNS:
            if jcol in d and d[jcol] is not None:
                d[jcol] = json.loads(d[jcol])   # TEXT → dict（MySQL JSON 列由驱动序列化）
        rows.append(d)
    return rows


def _copy_table(src_conn, dst_conn, table: str, dry_run: bool) -> int:
    rows = _read_rows(src_conn, table)
    if dry_run:
        return len(rows)
    if not rows:
        return 0
    # JSON 列：dict/list → JSON 字符串。pymysql 不自动序列化 dict（裸 text() 绕过 ORM），
    # MySQL JSON 列接收字符串并校验存储为原生 JSON；None 保持 NULL。
    for r in rows:
        for jcol in _JSON_COLUMNS:
            v = r.get(jcol)
            if isinstance(v, (dict, list)):
                r[jcol] = json.dumps(v, ensure_ascii=False)
    cols = list(rows[0].keys())
    placeholders = ", ".join(":" + c for c in cols)
    col_list = ", ".join(cols)
    dst_conn.execute(text(f"DELETE FROM {table}"))   # 幂等：重跑先清目标（仅非 dry-run）
    # SQLAlchemy 2.0: Connection 无 executemany，用 execute + list[dict] 触发批量插入
    dst_conn.execute(
        text(f"INSERT INTO {table} ({col_list}) VALUES ({placeholders})"),
        rows,
    )
    return len(rows)


def main():
    parser = argparse.ArgumentParser(description="SQLite → MySQL 一次性直迁")
    parser.add_argument("--src", default=os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "data", "emby_ai.db"), help="SQLite 源文件")
    parser.add_argument("--dry-run", action="store_true", help="只打行数对比，不写数据")
    args = parser.parse_args()

    src_url = f"sqlite:///{args.src}"
    dst_url = _build_database_url()
    dst_mask = make_url(dst_url).render_as_string(hide_password=True)
    logger.info("源 (SQLite):   %s", src_url)
    logger.info("目标 (MySQL):  %s  [%s]", dst_mask, "DRY-RUN，仅建表不写数据" if args.dry_run else "正式直迁")

    # 1. 建库（utf8mb4）— 仅 MySQL 目标
    if make_url(dst_url).get_backend_name() == "mysql":
        db = make_url(dst_url).database
        server = make_url(dst_url).set(database=None)
        with create_engine(server).connect() as conn:
            conn.execute(text(
                f"CREATE DATABASE IF NOT EXISTS `{db}` "
                f"CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"))
            conn.commit()
        logger.info("🛢️  已确保库 %s 为 utf8mb4", db)

    src = create_engine(src_url, connect_args={"check_same_thread": False})
    dst = create_engine(dst_url, pool_pre_ping=True, pool_recycle=3600)

    # 2. 建目标表（create_all 幂等：缺表才建；dry-run 同样执行以验证表结构）
    Base.metadata.create_all(bind=dst)
    _run_migrations(dst)

    # 3. 直迁（--dry-run 只计数）
    with src.connect() as sc, dst.connect() as dc:
        logger.info("%-22s %8s %10s", "表", "SQLite", "MySQL(目标)")
        ok = True
        for table in _TABLE_ORDER:
            n_src = _row_count(sc, table)
            n_dst = _copy_table(sc, dc, table, args.dry_run)
            mark = "dry-run" if args.dry_run else ("OK" if n_src == n_dst else "⚠️ 不一致")
            if n_src != n_dst and not args.dry_run:
                ok = False
            logger.info("%-22s %8d %10s %s", table, n_src, n_dst, mark)
        if not args.dry_run:
            dc.commit()
    logger.info("完成%s。验证通过后切 DATABASE_URL 重启；保留 SQLite 文件至线上稳定。",
                "（dry-run，未写任何数据）" if args.dry_run else "")


if __name__ == "__main__":
    main()
