"""MySQL 只读仓储：首批接入学者、论文和共同论文查询。"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator
import json
import re

from models.settings import Settings


class MySQLRepository:
    """通过参数化 SQL 读取 gkx，不执行任何写操作。"""
    backend = "mysql"

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or Settings.from_env()
        if not self.settings.mysql_password:
            raise ValueError("MYSQL_PASSWORD 未配置")

    def health(self) -> dict:
        """执行最小只读探针，不返回连接凭据。"""
        with self._cursor() as cursor:
            cursor.execute("SELECT 1 AS ok")
            ready = cursor.fetchone()["ok"] == 1
        return {"backend": "mysql", "ready": ready, "database": self.settings.mysql_database}

    @contextmanager
    def _cursor(self) -> Iterator[object]:
        try:
            import pymysql
        except ImportError as exc:
            raise RuntimeError("请安装 PyMySQL") from exc
        connection = pymysql.connect(
            host=self.settings.mysql_host,
            port=self.settings.mysql_port,
            user=self.settings.mysql_user,
            password=self.settings.mysql_password,
            database=self.settings.mysql_database,
            charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor,
            read_timeout=10,
            write_timeout=10,
            autocommit=False,
        )
        try:
            with connection.cursor() as cursor:
                # 双重保护：本连接只允许执行只读事务。
                cursor.execute("SET SESSION TRANSACTION READ ONLY")
                yield cursor
        finally:
            connection.rollback()
            connection.close()

    @staticmethod
    def _repair_text(value: str | None) -> str:
        """兼容库中少量 UTF-8 被按 latin1 写入形成的历史乱码。"""
        if not value:
            return ""
        try:
            raw = bytearray()
            for char in value:
                raw.extend(bytes([ord(char)]) if ord(char) <= 255 else char.encode("cp1252"))
            repaired = bytes(raw).decode("utf-8")
            return repaired if any("\u4e00" <= char <= "\u9fff" for char in repaired) else value
        except (UnicodeEncodeError, UnicodeDecodeError, ValueError):
            return value

    @staticmethod
    def _entity(row: dict) -> dict:
        return {
            "entity_id": row["scholar_id"],
            "name": MySQLRepository._repair_text(row.get("name_zh") or row.get("name_en")),
            "organization": MySQLRepository._repair_text(row.get("scholar_org_name_zh") or row.get("scholar_org_name_en")),
            "title": MySQLRepository._repair_text(row.get("work_experience_position_zh") or row.get("work_experience_position_en")),
        }

    def search_scholars(self, mention: str, limit: int = 10) -> list[dict]:
        sql = """
            SELECT scholar_id, name_zh, name_en, scholar_org_name_zh, scholar_org_name_en,
                   work_experience_position_zh, work_experience_position_en
            FROM dwd_scholar
            WHERE status = 1 AND (name_zh = %s OR name_en = %s)
            ORDER BY scholar_id
            LIMIT %s
        """
        with self._cursor() as cursor:
            cursor.execute(sql, (mention, mention, max(1, min(limit, 50))))
            return [self._entity(row) for row in cursor.fetchall()]

    def get_scholar(self, scholar_id: str) -> dict | None:
        sql = """
            SELECT scholar_id, name_zh, name_en, scholar_org_name_zh, scholar_org_name_en,
                   work_experience_position_zh, work_experience_position_en
            FROM dwd_scholar WHERE scholar_id = %s AND status = 1 LIMIT 1
        """
        with self._cursor() as cursor:
            cursor.execute(sql, (scholar_id,))
            row = cursor.fetchone()
            return self._entity(row) if row else None

    def list_scholars(self, limit: int = 10000, offset: int = 0) -> list[dict]:
        """供 Milvus 离线建索引使用，使用分页避免一次读取全表。"""
        sql = """
            SELECT scholar_id, name_zh, name_en, scholar_org_name_zh, scholar_org_name_en,
                   work_experience_position_zh, work_experience_position_en
            FROM dwd_scholar WHERE status = 1
            ORDER BY scholar_id LIMIT %s OFFSET %s
        """
        with self._cursor() as cursor:
            cursor.execute(sql, (max(1, min(limit, 10000)), max(0, offset)))
            return [self._entity(row) for row in cursor.fetchall()]

    @staticmethod
    def _paper(row: dict, author_ids: list[str]) -> dict:
        year = row.get("year")
        return {
            "paper_id": str(row["paper_id"]),
            "title": MySQLRepository._repair_text(row.get("zh_name") or row.get("en_name")),
            "year": int(year) if year is not None else None,
            "authors": author_ids,
            "doi": row.get("doi"),
            "evidence_id": f"mysql_paper_{row['paper_id']}",
            "source": "mysql:gkx.dwd_scholar_papers",
        }

    def get_author_papers(self, scholar_id: str, limit: int = 100) -> list[dict]:
        sql = """
            SELECT p.id AS paper_id, p.zh_name, p.en_name, p.doi,
                   COALESCE(r.year, YEAR(r.publish_time), YEAR(p.cover_date_start)) AS year
            FROM dwd_scholar_paper_relation r
            JOIN dwd_scholar_papers p ON p.id = r.related_paper_id
            WHERE r.scholar_id = %s AND r.status = 1 AND p.status = 1
            ORDER BY year DESC, p.id
            LIMIT %s
        """
        with self._cursor() as cursor:
            cursor.execute(sql, (scholar_id, max(1, min(limit, 500))))
            return [self._paper(row, [scholar_id]) for row in cursor.fetchall()]

    def get_common_papers(self, scholar_ids: list[str], limit: int = 100) -> list[dict]:
        ids = list(dict.fromkeys(scholar_ids))
        if len(ids) < 2:
            return self.get_author_papers(ids[0], limit) if ids else []
        placeholders = ", ".join(["%s"] * len(ids))
        sql = f"""
            SELECT p.id AS paper_id, p.zh_name, p.en_name, p.doi,
                   COALESCE(MAX(r.year), YEAR(MAX(r.publish_time)), YEAR(p.cover_date_start)) AS year
            FROM dwd_scholar_paper_relation r
            JOIN dwd_scholar_papers p ON p.id = r.related_paper_id
            WHERE r.scholar_id IN ({placeholders}) AND r.status = 1 AND p.status = 1
            GROUP BY p.id, p.zh_name, p.en_name, p.doi, p.cover_date_start
            HAVING COUNT(DISTINCT r.scholar_id) = %s
            ORDER BY year DESC, p.id
            LIMIT %s
        """
        params = (*ids, len(ids), max(1, min(limit, 500)))
        with self._cursor() as cursor:
            cursor.execute(sql, params)
            return [self._paper(row, ids) for row in cursor.fetchall()]

    @staticmethod
    def _years(value: str | None) -> tuple[int | None, int | None]:
        years = [int(item) for item in re.findall(r"(?:19|20)\d{2}", value or "")]
        return (years[0] if years else None, years[-1] if len(years) > 1 else None)

    def get_employment_history(self, scholar_id: str) -> list[dict]:
        sql = """
            SELECT work_experience_date, work_experience_institution_zh, work_experience_institution_en,
                   work_experience_department_zh, work_experience_department_en,
                   work_experience_position_zh, work_experience_position_en
            FROM dwd_scholar WHERE scholar_id = %s AND status = 1 LIMIT 1
        """
        with self._cursor() as cursor:
            cursor.execute(sql, (scholar_id,))
            row = cursor.fetchone()
        if not row:
            return []
        start_year, end_year = self._years(row.get("work_experience_date"))
        organization = self._repair_text(row.get("work_experience_institution_zh") or row.get("work_experience_institution_en"))
        role = self._repair_text(row.get("work_experience_position_zh") or row.get("work_experience_position_en"))
        if not organization and not role:
            return []
        return [{"entity_id": scholar_id, "organization": organization,
                 "department": self._repair_text(row.get("work_experience_department_zh") or row.get("work_experience_department_en")),
                 "role": role, "start_year": start_year, "end_year": end_year,
                 "evidence_id": f"mysql_employment_{scholar_id}", "source": "mysql:gkx.dwd_scholar"}]

    def get_education_history(self, scholar_id: str) -> list[dict]:
        sql = """
            SELECT education_background_date, education_background_institution_zh,
                   education_background_institution_en, education_background_degree_zh,
                   education_background_degree_en
            FROM dwd_scholar WHERE scholar_id = %s AND status = 1 LIMIT 1
        """
        with self._cursor() as cursor:
            cursor.execute(sql, (scholar_id,))
            row = cursor.fetchone()
        if not row:
            return []
        start_year, end_year = self._years(row.get("education_background_date"))
        institution = self._repair_text(row.get("education_background_institution_zh") or row.get("education_background_institution_en"))
        degree = self._repair_text(row.get("education_background_degree_zh") or row.get("education_background_degree_en"))
        if not institution and not degree:
            return []
        return [{"entity_id": scholar_id, "institution": institution, "degree": degree,
                 "start_year": start_year, "end_year": end_year,
                 "evidence_id": f"mysql_education_{scholar_id}", "source": "mysql:gkx.dwd_scholar"}]

    def _scholar_names(self, scholar_ids: list[str]) -> list[str]:
        return [row["name"] for scholar_id in scholar_ids
                if (row := self.get_scholar(scholar_id)) and row.get("name")]

    def get_common_projects(self, scholar_ids: list[str], limit: int = 100) -> list[dict]:
        names = self._scholar_names(scholar_ids)
        if not names:
            return []
        conditions = " AND ".join(["(project_host = %s OR JSON_CONTAINS(participants, JSON_QUOTE(%s)))"] * len(names))
        sql = f"""
            SELECT id, title, approval_year, research_period, project_host, participants
            FROM dwd_zh_project WHERE {conditions}
            ORDER BY approval_year DESC, id LIMIT %s
        """
        params = tuple(value for name in names for value in (name, name)) + (max(1, min(limit, 500)),)
        with self._cursor() as cursor:
            cursor.execute(sql, params)
            rows = cursor.fetchall()
        return [{"project_id": str(row["id"]), "name": row.get("title") or "",
                 "start_year": int(row["approval_year"]) if row.get("approval_year") else 0,
                 "end_year": int(row["approval_year"]) if row.get("approval_year") else 0,
                 "participant_ids": scholar_ids, "participants": json.loads(row["participants"] or "[]"),
                 "evidence_id": f"mysql_project_{row['id']}", "source": "mysql:gkx.dwd_zh_project"}
                for row in rows]

    def get_common_patents(self, scholar_ids: list[str], limit: int = 100) -> list[dict]:
        names = self._scholar_names(scholar_ids)
        if not names:
            return []
        conditions = " AND ".join(["JSON_SEARCH(p.inventors, 'one', %s, NULL, '$[*].name') IS NOT NULL"] * len(names))
        sql = f"""
            SELECT p.patent_id, p.publication_number, p.inventors,
                   COALESCE(t.title_zh, t.title_localized, p.publication_number) AS title
            FROM dwd_patent p LEFT JOIN dwd_patent_title t ON t.patent_id = p.patent_id
            WHERE {conditions} ORDER BY p.patent_id LIMIT %s
        """
        with self._cursor() as cursor:
            cursor.execute(sql, (*names, max(1, min(limit, 500))))
            rows = cursor.fetchall()
        return [{"patent_id": row["patent_id"], "publication_number": row["publication_number"],
                 "title": self._repair_text(row.get("title")), "inventor_ids": scholar_ids,
                 "inventors": json.loads(row["inventors"] or "[]"),
                 "evidence_id": f"mysql_patent_{row['patent_id']}", "source": "mysql:gkx.dwd_patent"}
                for row in rows]
