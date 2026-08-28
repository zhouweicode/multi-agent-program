"""Transactionally delete data outside the 51-table Excel whitelist in ``gkx``.

The command keeps every table definition, defaults to a read-only preview, derives the
whitelist from the reference workbook, and refuses to run if a kept table depends on a
table selected for deletion.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from models.settings import Settings
from scripts.seed_gkx_excel51 import EXPECTED_TABLES, WORKBOOK, parse_workbook


def _connect(database: str):
    settings = Settings.from_env()
    if not settings.mysql_password:
        raise ValueError("MYSQL_PASSWORD 未配置")
    try:
        import pymysql
    except ImportError as exc:
        raise RuntimeError("请安装 PyMySQL") from exc
    return pymysql.connect(
        host=settings.mysql_host, port=settings.mysql_port, user=settings.mysql_user,
        password=settings.mysql_password, database=database, charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor, autocommit=False,
        read_timeout=120, write_timeout=120,
    )


def inspect_scope(connection: Any, keep: set[str]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    with connection.cursor() as cursor:
        cursor.execute("""SELECT TABLE_NAME FROM information_schema.tables
                           WHERE table_schema = DATABASE() AND table_type = 'BASE TABLE'
                           ORDER BY TABLE_NAME""")
        tables = [row["TABLE_NAME"] for row in cursor.fetchall()]
        missing_keep = sorted(keep - set(tables))
        if missing_keep:
            raise ValueError(f"白名单表在 gkx 中缺失: {missing_keep}")
        for table in tables:
            cursor.execute(f"SELECT COUNT(*) AS total FROM `{table}`")
            counts[table] = int(cursor.fetchone()["total"])
        cursor.execute("""SELECT TABLE_NAME, REFERENCED_TABLE_NAME, CONSTRAINT_NAME
                            FROM information_schema.key_column_usage
                           WHERE table_schema = DATABASE()
                             AND REFERENCED_TABLE_NAME IS NOT NULL
                           ORDER BY TABLE_NAME, CONSTRAINT_NAME, ORDINAL_POSITION""")
        foreign_keys = cursor.fetchall()
        cursor.execute("""SELECT TRIGGER_NAME, EVENT_OBJECT_TABLE, EVENT_MANIPULATION
                            FROM information_schema.triggers
                           WHERE trigger_schema = DATABASE()
                           ORDER BY EVENT_OBJECT_TABLE, TRIGGER_NAME""")
        triggers = cursor.fetchall()
    targets = {table for table, count in counts.items() if table not in keep and count > 0}
    blocked = [row for row in foreign_keys
               if row["TABLE_NAME"] in keep and row["REFERENCED_TABLE_NAME"] in targets]
    if blocked:
        raise ValueError(f"保留表依赖待清空表，拒绝删除: {blocked}")
    target_triggers = [row for row in triggers if row["EVENT_OBJECT_TABLE"] in targets]
    return {"tables": tables, "counts": counts, "targets": targets,
            "foreign_keys": foreign_keys, "target_triggers": target_triggers}


def deletion_order(targets: set[str], foreign_keys: list[dict[str, Any]]) -> list[str]:
    """Return child-before-parent order for foreign keys wholly inside the target set."""
    outgoing: dict[str, set[str]] = defaultdict(set)
    indegree = {table: 0 for table in targets}
    self_references = []
    for row in foreign_keys:
        child = row["TABLE_NAME"]
        parent = row["REFERENCED_TABLE_NAME"]
        if child not in targets or parent not in targets:
            continue
        if child == parent:
            self_references.append(row)
            continue
        if parent not in outgoing[child]:
            outgoing[child].add(parent)
            indegree[parent] += 1
    if self_references:
        raise ValueError(f"待清空非空表存在自引用外键，拒绝自动处理: {self_references}")
    queue = deque(sorted(table for table, degree in indegree.items() if degree == 0))
    order: list[str] = []
    while queue:
        table = queue.popleft()
        order.append(table)
        for parent in sorted(outgoing[table]):
            indegree[parent] -= 1
            if indegree[parent] == 0:
                queue.append(parent)
    if len(order) != len(targets):
        raise ValueError("待清空表之间存在循环外键，拒绝自动删除")
    return order


def apply_delete(connection: Any, keep: set[str], scope: dict[str, Any], order: list[str]) -> dict[str, Any]:
    before_keep = {table: scope["counts"][table] for table in keep}
    deleted: dict[str, int] = {}
    try:
        with connection.cursor() as cursor:
            for table in order:
                cursor.execute(f"DELETE FROM `{table}`")
                deleted[table] = int(cursor.rowcount)
            for table, expected in before_keep.items():
                cursor.execute(f"SELECT COUNT(*) AS total FROM `{table}`")
                actual = int(cursor.fetchone()["total"])
                if actual != expected:
                    raise ValueError(f"白名单表数据发生变化: {table} expected={expected}, actual={actual}")
            for table in scope["tables"]:
                if table in keep:
                    continue
                cursor.execute(f"SELECT COUNT(*) AS total FROM `{table}`")
                remaining = int(cursor.fetchone()["total"])
                if remaining:
                    raise ValueError(f"非白名单表仍有数据: {table}={remaining}")
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    return deleted


def final_audit(connection: Any, keep: set[str]) -> dict[str, Any]:
    scope = inspect_scope(connection, keep)
    keep_counts = {table: scope["counts"][table] for table in sorted(keep)}
    other_counts = {table: count for table, count in scope["counts"].items() if table not in keep}
    return {
        "all_tables": len(scope["tables"]), "kept_tables": len(keep),
        "kept_tables_with_data": sum(count > 0 for count in keep_counts.values()),
        "kept_rows": sum(keep_counts.values()),
        "other_tables": len(other_counts),
        "other_tables_with_data": sum(count > 0 for count in other_counts.values()),
        "other_rows": sum(other_counts.values()),
        "keep_counts": keep_counts,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="保留 Excel 对应 51 张表，清空 gkx 其余表数据")
    parser.add_argument("--workbook", type=Path, default=WORKBOOK)
    parser.add_argument("--database", default="gkx")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm-database")
    parser.add_argument("--confirm-delete-non-whitelist-data", action="store_true")
    args = parser.parse_args()
    if args.database != "gkx":
        raise SystemExit("安全限制：此脚本只允许目标数据库 gkx")
    specs = parse_workbook(args.workbook.resolve())
    keep = set(specs)
    if len(keep) != EXPECTED_TABLES:
        raise SystemExit(f"白名单必须恰好包含 {EXPECTED_TABLES} 张表")

    connection = _connect(args.database)
    try:
        scope = inspect_scope(connection, keep)
        order = deletion_order(scope["targets"], scope["foreign_keys"])
        preview = {
            "database": args.database, "workbook": str(args.workbook.resolve()),
            "dry_run": not args.apply, "keep_table_count": len(keep),
            "all_table_count": len(scope["tables"]),
            "other_table_count": len(scope["tables"]) - len(keep),
            "nonempty_delete_target_count": len(order),
            "rows_to_delete": sum(scope["counts"][table] for table in order),
            "delete_order": order,
            "delete_counts": {table: scope["counts"][table] for table in order},
            "target_triggers": scope["target_triggers"],
        }
        print(json.dumps(preview, ensure_ascii=False, indent=2))
        if not args.apply:
            return
        if args.confirm_database != args.database or not args.confirm_delete_non_whitelist_data:
            raise SystemExit("拒绝删除：必须同时确认数据库和非白名单数据删除开关")
        deleted = apply_delete(connection, keep, scope, order)
        audit = final_audit(connection, keep)
        print(json.dumps({"applied": True, "deleted": deleted,
                          "deleted_rows": sum(deleted.values()), "post_audit": audit},
                         ensure_ascii=False, indent=2))
    finally:
        connection.close()


if __name__ == "__main__":
    main()
