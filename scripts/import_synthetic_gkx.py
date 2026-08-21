"""Safely import generated data into a dedicated synthetic MySQL database.

The command is a preview unless --apply is supplied. It never accepts the source database
name ``gkx`` and requires an exact confirmation string before writing.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
from typing import Any

from data.synthetic_gkx import TABLE_ORDER, read_dataset
from data.synthetic_validation import validate_dataset
from models.settings import Settings


SAFE_DATABASE = re.compile(r"^gkx_synthetic(?:_[a-z0-9_]+)?$")


def validate_target(database: str) -> None:
    if database == "gkx" or not SAFE_DATABASE.fullmatch(database):
        raise ValueError("拒绝写入：目标库必须为 gkx_synthetic 或 gkx_synthetic_*，且不能是 gkx")


def _schema_statements(path: Path) -> list[str]:
    lines = [line for line in path.read_text(encoding="utf-8").splitlines()
             if line.strip() and not line.lstrip().startswith("--")]
    return [statement.strip() for statement in "\n".join(lines).split(";") if statement.strip()]


def _convert(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return value


def import_dataset(input_dir: Path, schema_path: Path, database: str, batch_size: int = 1000) -> dict[str, int]:
    validate_target(database)
    settings = Settings.from_env()
    password = os.getenv("SYNTHETIC_MYSQL_PASSWORD") or settings.mysql_password
    if not password:
        raise ValueError("请配置 SYNTHETIC_MYSQL_PASSWORD 或 MYSQL_PASSWORD")
    try:
        import pymysql
    except ImportError as exc:
        raise RuntimeError("请安装 PyMySQL") from exc

    tables = read_dataset(input_dir)
    quality = validate_dataset(tables)
    if not quality["valid"]:
        raise ValueError(f"数据质量校验失败: {quality['errors']}")

    connection = pymysql.connect(
        host=settings.mysql_host, port=settings.mysql_port, user=settings.mysql_user,
        password=password, charset="utf8mb4", autocommit=False,
    )
    inserted: dict[str, int] = {}
    try:
        with connection.cursor() as cursor:
            cursor.execute(f"CREATE DATABASE IF NOT EXISTS `{database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
            cursor.execute(f"USE `{database}`")
            for statement in _schema_statements(schema_path):
                cursor.execute(statement)
            for table_name in TABLE_ORDER:
                rows = tables[table_name]
                if not rows:
                    inserted[table_name] = 0
                    continue
                columns = list(rows[0])
                if any(set(row) != set(columns) for row in rows):
                    raise ValueError(f"{table_name}: inconsistent row columns")
                column_sql = ", ".join(f"`{column}`" for column in columns)
                placeholders = ", ".join(["%s"] * len(columns))
                sql = f"INSERT INTO `{table_name}` ({column_sql}) VALUES ({placeholders})"
                for offset in range(0, len(rows), max(1, batch_size)):
                    batch = rows[offset:offset + max(1, batch_size)]
                    values = [tuple(_convert(row[column]) for column in columns) for row in batch]
                    cursor.executemany(sql, values)
                inserted[table_name] = len(rows)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return inserted


def main() -> None:
    parser = argparse.ArgumentParser(description="安全导入 gkx_synthetic；默认只预览")
    parser.add_argument("--input", type=Path, default=Path(".runtime/synthetic_gkx"))
    parser.add_argument("--schema", type=Path, default=Path("sql/gkx_synthetic_schema.sql"))
    parser.add_argument("--database", default="gkx_synthetic")
    parser.add_argument("--batch-size", type=int, default=1000)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm-database", help="写入时必须与 --database 完全相同")
    args = parser.parse_args()
    validate_target(args.database)
    tables = read_dataset(args.input)
    quality = validate_dataset(tables)
    preview = {"dry_run": not args.apply, "database": args.database, "quality": quality,
               "rows": {table: len(rows) for table, rows in tables.items()}}
    if not args.apply:
        print(json.dumps(preview, ensure_ascii=False, indent=2))
        return
    if args.confirm_database != args.database:
        raise SystemExit("拒绝写入：--confirm-database 必须与 --database 完全一致")
    inserted = import_dataset(args.input, args.schema, args.database, args.batch_size)
    print(json.dumps({"dry_run": False, "database": args.database, "inserted": inserted}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
