"""Add a uniform updated_at protocol to the isolated gkx_synthetic database."""
from __future__ import annotations

import argparse
import json

from data.synthetic_gkx import TABLE_ORDER
from models.settings import Settings


def main() -> None:
    parser = argparse.ArgumentParser(description="为gkx_synthetic启用组合Watermark；默认只预览")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm-database")
    args = parser.parse_args()
    settings = Settings.from_env()
    if not (settings.mysql_database == "gkx_synthetic" or settings.mysql_database.startswith("gkx_synthetic_")):
        raise SystemExit("拒绝迁移：MYSQL_DATABASE必须为gkx_synthetic或gkx_synthetic_*")
    if not args.apply:
        print(json.dumps({"dry_run": True, "database": settings.mysql_database,
                          "tables": list(TABLE_ORDER), "column": "updated_at DATETIME(6) ON UPDATE"},
                         ensure_ascii=False, indent=2))
        return
    if args.confirm_database != settings.mysql_database:
        raise SystemExit("拒绝迁移：--confirm-database必须与当前MYSQL_DATABASE完全一致")
    import pymysql
    connection = pymysql.connect(host=settings.mysql_host, port=settings.mysql_port,
                                 user=settings.mysql_user, password=settings.mysql_password,
                                 database=settings.mysql_database, charset="utf8mb4", autocommit=False)
    changes = {}
    try:
        with connection.cursor() as cursor:
            for table in TABLE_ORDER:
                cursor.execute("""SELECT COUNT(*) FROM information_schema.COLUMNS
                    WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s AND COLUMN_NAME='updated_at'""",
                               (settings.mysql_database, table))
                exists = cursor.fetchone()[0] == 1
                operation = "MODIFY COLUMN" if exists else "ADD COLUMN"
                cursor.execute(f"ALTER TABLE {table} {operation} updated_at DATETIME(6) NOT NULL "
                               "DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6)")
                changes[table] = "modified" if exists else "added"
            cursor.execute("""SELECT COUNT(*) FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA=%s AND TABLE_NAME='dwd_patent_title' AND COLUMN_NAME='status'""",
                           (settings.mysql_database,))
            if cursor.fetchone()[0] == 0:
                cursor.execute("ALTER TABLE dwd_patent_title ADD COLUMN status TINYINT NOT NULL DEFAULT 1")
                changes["dwd_patent_title.status"] = "added"
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    print(json.dumps({"dry_run": False, "database": settings.mysql_database,
                      "changes": changes}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
