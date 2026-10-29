"""seekdb 连接与建表。"""
import re

import pymysql

from . import config


def connect(with_db: bool = True):
    return pymysql.connect(
        host=config.SEEKDB_HOST,
        port=config.SEEKDB_PORT,
        user=config.SEEKDB_USER,
        password=config.SEEKDB_PASSWORD,
        database=config.SEEKDB_DATABASE if with_db else None,
        charset="utf8mb4",
        autocommit=True,
    )


def _split_statements(sql: str):
    cleaned = "\n".join(
        line for line in sql.splitlines() if not line.strip().startswith("--")
    )
    return [s.strip() for s in re.split(r";\s*\n", cleaned) if s.strip()]


def ensure_schema() -> None:
    sql = config.SCHEMA_FILE.read_text(encoding="utf-8").format(
        db_name=config.SEEKDB_DATABASE, dim=config.EMBED_DIM
    )
    statements = _split_statements(sql)
    with connect(with_db=False) as conn, conn.cursor() as cur:
        for stmt in statements:
            cur.execute(stmt)
            if stmt.upper().startswith("CREATE DATABASE"):
                cur.execute(f"USE `{config.SEEKDB_DATABASE}`")


def vector_literal(vec) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in vec) + "]"
