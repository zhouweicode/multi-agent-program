"""Create missing Excel-defined gkx tables and top all 51 tables up to 1,000 rows.

The command reads the workbook directly (without modifying it), uses the real schema for
tables that already exist, derives DDL from the workbook for missing tables, and never
deletes or overwrites existing rows.  It defaults to a read-only preview; applying changes
requires an exact database confirmation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable
from zipfile import ZipFile

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from models.settings import Settings

WORKBOOK = PROJECT_ROOT / "数据表" / "版权人才、企业机构、政策数据汇总表.xlsx"
SOURCE = "gkx_excel51_seed_v1_20260827"
EXPECTED_TABLES = 51
DEFAULT_TARGET = 1_000

NS = {
    "m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "p": "http://schemas.openxmlformats.org/package/2006/relationships",
}
IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

DOMESTIC_ORG_TABLES = {
    "dwd_org_annual_financial_info", "dwd_org_bankruptcy_public_cases",
    "dwd_org_bankruptcy_public_cases_list", "dwd_org_base_info",
    "dwd_org_changerecord_info", "dwd_org_company_abnormal",
    "dwd_org_company_illegal", "dwd_org_company_punish", "dwd_org_executive_info",
    "dwd_org_financing_info", "dwd_org_heis_info", "dwd_org_important_news_info",
    "dwd_org_invest_info", "dwd_org_merger_acquisition_info",
    "dwd_org_opt_judicial_case", "dwd_org_org_product_info", "dwd_org_recruit_info",
    "dwd_org_risk_shixin", "dwd_org_risk_tax_punish", "dwd_org_risk_zhixing",
    "dwd_org_shareholder_info", "dwd_org_stock_base", "dwd_org_stock_finance_info",
    "dwd_org_subsidiary_info", "dwd_org_tech_tag", "dwd_research_institute_base_info",
    "dwd_special_aomen_company", "dwd_special_hongkong_company",
    "dwd_special_taiwan_company",
}
FOREIGN_ORG_TABLES = {
    "dwd_agg_subsidiary_info", "dwd_foreign_org_annual_financial_info",
    "dwd_forg_base_info", "dwd_forg_executive_info", "dwd_forg_product_info",
    "dwd_forg_research_org_info", "dwd_forg_shareholder_info",
    "dwd_forg_university_org_info",
}

FIELDS = ("人工智能", "知识图谱", "新能源", "生物医药", "新材料", "机器人", "量子信息", "集成电路")
CITIES = ("北京", "上海", "深圳", "广州", "杭州", "南京", "武汉", "西安", "成都", "合肥")
PROVINCES = ("北京市", "上海市", "广东省", "广东省", "浙江省", "江苏省", "湖北省", "陕西省", "四川省", "安徽省")
COUNTRIES = (("US", "United States"), ("DE", "Germany"), ("GB", "United Kingdom"),
             ("FR", "France"), ("JP", "Japan"), ("SG", "Singapore"))


@dataclass(frozen=True)
class FieldSpec:
    table: str
    table_cn: str
    name: str
    name_cn: str
    excel_type: str
    length: str
    description: str
    sample: str
    nullable: bool
    primary: bool
    indexed: bool
    sheet: str


@dataclass(frozen=True)
class Column:
    name: str
    column_type: str
    data_type: str
    nullable: bool
    default: Any
    extra: str = ""
    character_length: int | None = None
    numeric_precision: int | None = None
    numeric_scale: int | None = None
    primary: bool = False


def _text(node: ET.Element | None) -> str:
    return "" if node is None else "".join(part.text or "" for part in node.iter() if part.tag.endswith("}t"))


def _column_index(ref: str) -> int:
    match = re.match(r"[A-Z]+", ref)
    if match is None:
        raise ValueError(f"无效的 Excel 单元格引用: {ref}")
    value = 0
    for letter in match.group(0):
        value = value * 26 + ord(letter) - 64
    return value - 1


def _sheet_rows(archive: ZipFile, member: str, shared: list[str]) -> list[list[str]]:
    root = ET.fromstring(archive.read(member))
    result: list[list[str]] = []
    for row in root.findall("m:sheetData/m:row", NS):
        values: dict[int, str] = {}
        for cell in row.findall("m:c", NS):
            kind = cell.attrib.get("t")
            value_node = cell.find("m:v", NS)
            if kind == "inlineStr":
                value = _text(cell.find("m:is", NS))
            elif value_node is None:
                value = ""
            elif kind == "s":
                value = shared[int(value_node.text or 0)]
            elif kind == "b":
                value = "1" if value_node.text == "1" else "0"
            else:
                value = value_node.text or ""
            values[_column_index(cell.attrib.get("r", "A1"))] = str(value).strip()
        result.append([values.get(index, "") for index in range(max(values, default=-1) + 1)])
    return result


def parse_workbook(path: Path, expected_tables: int | None = EXPECTED_TABLES) -> dict[str, list[FieldSpec]]:
    """Parse the five schema sheets by their header meanings, not fixed positions."""
    if not path.is_file():
        raise FileNotFoundError(path)
    with ZipFile(path) as archive:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in archive.namelist():
            shared_root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            shared = [_text(item) for item in shared_root.findall("m:si", NS)]
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        rels = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        targets = {rel.attrib["Id"]: rel.attrib["Target"] for rel in rels.findall("p:Relationship", NS)}
        tables: dict[str, list[FieldSpec]] = {}
        for sheet in workbook.findall("m:sheets/m:sheet", NS):
            sheet_name = sheet.attrib["name"]
            target = targets[sheet.attrib[f"{{{NS['r']}}}id"]]
            member = target.lstrip("/") if target.startswith("/") else "xl/" + target
            rows = _sheet_rows(archive, member, shared)
            header_at = next((i for i, row in enumerate(rows) if "英文表名" in row and "字段英文名称" in row), None)
            if header_at is None:
                continue
            header = rows[header_at]
            positions = {value: index for index, value in enumerate(header) if value}

            def locate(*labels: str) -> int | None:
                return next((positions[label] for label in labels if label in positions), None)

            table_i = locate("英文表名")
            field_i = locate("字段英文名称")
            if table_i is None or field_i is None:
                continue
            table_cn_i = locate("中文表名")
            name_cn_i = locate("字段中文名称")
            type_i = locate("数据类型", "类型")
            length_i = locate("数据长度（精度）", "数据长度(精度)", "数据长度")
            desc_i = locate("字段描述")
            sample_i = locate("数据样例")
            nested_i = locate("字段英文名称（json解析）")
            null_i = locate("是否有空值", "是否为空")
            pk_i = locate("是否主键", "是否为主键")
            index_i = locate("是否索引")

            def get(row: list[str], index: int | None) -> str:
                return row[index].strip() if index is not None and index < len(row) else ""

            for row in rows[header_at + 1:]:
                table = get(row, table_i)
                name = get(row, field_i)
                if not table or not name:
                    continue
                if not IDENTIFIER.fullmatch(table):
                    # Some workbooks reuse the English-table column for section labels.
                    # Ignore labels that cannot be SQL identifiers; the expected table-count
                    # assertion below still protects against silently losing a real table.
                    continue
                # The workbook has one visibly truncated field label ("arch development am")
                # whose Chinese name and neighboring tables identify it as R&D investment.
                # Normalize that source typo instead of ever interpolating an unsafe name.
                if not IDENTIFIER.fullmatch(name):
                    if get(row, name_cn_i) == "研发投入金额":
                        name = "research_development_amount"
                    else:
                        normalized = re.sub(r"[^A-Za-z0-9_]+", "_", name).strip("_")
                        if not IDENTIFIER.fullmatch(normalized):
                            raise ValueError(f"Excel 中存在无法安全归一化的字段名: {table}.{name}")
                        name = normalized
                nested_name = get(row, nested_i)
                is_nested_json = bool(nested_name and nested_name != "/")
                spec = FieldSpec(
                    table=table, table_cn=get(row, table_cn_i), name=name,
                    name_cn=get(row, name_cn_i),
                    excel_type="json" if is_nested_json else get(row, type_i),
                    length=get(row, length_i), description=get(row, desc_i),
                    sample=get(row, sample_i), nullable=get(row, null_i) in {"是", "有", "Y", "yes"},
                    primary=get(row, pk_i) in {"是", "Y", "yes"},
                    indexed=get(row, index_i) in {"是", "Y", "yes"}, sheet=sheet_name,
                )
                fields = tables.setdefault(table, [])
                existing_at = next((i for i, item in enumerate(fields)
                                    if item.name.lower() == spec.name.lower()), None)
                if existing_at is None:
                    fields.append(spec)
                else:
                    # JSON object/array members are documented on repeated rows under one
                    # top-level database column. Some sheets also repeat a key row verbatim.
                    previous = fields[existing_at]
                    fields[existing_at] = replace(
                        previous,
                        excel_type="json" if is_nested_json or previous.excel_type == "json"
                                   else previous.excel_type,
                        nullable=previous.nullable and spec.nullable,
                        primary=previous.primary or spec.primary,
                        indexed=previous.indexed or spec.indexed,
                    )
    if expected_tables is not None and len(tables) != expected_tables:
        raise ValueError(f"Excel 应定义 {expected_tables} 张表，实际解析到 {len(tables)} 张")
    duplicates = {table: sorted({item.name for item in fields if sum(x.name == item.name for x in fields) > 1})
                  for table, fields in tables.items()}
    duplicates = {table: names for table, names in duplicates.items() if names}
    if duplicates:
        raise ValueError(f"Excel 存在重复字段: {duplicates}")
    return tables


def _length_numbers(value: str) -> tuple[int, ...]:
    return tuple(int(item) for item in re.findall(r"\d+", value or ""))


def _sql_type(spec: FieldSpec) -> str:
    raw = spec.excel_type.strip().lower()
    numbers = _length_numbers(spec.length)
    if raw in {"字符", "字符型"}:
        return f"VARCHAR({min(max(numbers[0] if numbers else 255, 1), 16383)})"
    if raw == "string":
        default_length = 128 if (spec.primary or spec.name.lower().endswith(("_id", "_code", "_period"))
                                 or spec.name.lower().startswith("dim_")) else 255
        return f"VARCHAR({min(max(numbers[0] if numbers else default_length, 1), 16383)})"
    if raw in {"文本", "文本型"}:
        return "TEXT" if not numbers or numbers[0] <= 65_535 else "LONGTEXT"
    if raw in {"日期", "日期型"}:
        return "DATE"
    if raw in {"日期时间", "时间日期"}:
        return "DATETIME"
    if raw == "数值":
        precision = min(numbers[0] if numbers else 20, 65)
        scale = min(numbers[1] if len(numbers) > 1 else 2, precision)
        return f"DECIMAL({precision},{scale})"
    if raw.startswith("json"):
        return "JSON"
    if raw.startswith("varchar"):
        return f"VARCHAR({min(max(numbers[0] if numbers else 255, 1), 16383)})"
    if raw.startswith("bigint"):
        return "BIGINT UNSIGNED" if "unsigned" in raw else "BIGINT"
    if raw.startswith("tinyint"):
        return f"TINYINT({numbers[0]})" if numbers else "TINYINT"
    if raw.startswith("int"):
        return "INT UNSIGNED" if "unsigned" in raw else "INT"
    if raw.startswith("decimal"):
        precision = min(numbers[0] if numbers else 20, 65)
        scale = min(numbers[1] if len(numbers) > 1 else 2, precision)
        return f"DECIMAL({precision},{scale})"
    if raw in {"datetime", "timestamp", "date", "json", "text", "tinytext", "longtext", "mediumtext",
               "double", "float", "real"}:
        return raw.upper()
    raise ValueError(f"无法映射 Excel 类型 {spec.table}.{spec.name}: {spec.excel_type!r}")


def create_table_sql(table: str, fields: list[FieldSpec]) -> str:
    primary = [field.name for field in fields if field.primary]
    definitions: list[str] = []
    for field in fields:
        auto_increment = (field.name == "id" and "自增" in (field.name_cn + field.description)
                          and _sql_type(field).startswith(("BIGINT", "INT")))
        nullable = "NOT NULL" if field.primary or not field.nullable else "NULL"
        definitions.append(f"  `{field.name}` {_sql_type(field)} {nullable}"
                           + (" AUTO_INCREMENT" if auto_increment else ""))
    if primary:
        definitions.append("  PRIMARY KEY (" + ", ".join(f"`{name}`" for name in primary) + ")")
    for field in fields:
        if not field.indexed or field.primary:
            continue
        sql_type = _sql_type(field)
        column = f"`{field.name}`(191)" if "TEXT" in sql_type else f"`{field.name}`"
        definitions.append(f"  KEY `idx_{field.name}` ({column})")
    # Match gkx's default/majority collation so new relation tables can JOIN existing ones
    # without requiring explicit COLLATE clauses.
    return (f"CREATE TABLE IF NOT EXISTS `{table}` (\n" + ",\n".join(definitions)
            + "\n) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci")


def _columns_from_specs(fields: list[FieldSpec]) -> list[Column]:
    result = []
    for field in fields:
        column_type = _sql_type(field).lower()
        data_type = column_type.split("(", 1)[0].split()[0]
        numbers = _length_numbers(column_type)
        char_length = numbers[0] if data_type in {"varchar", "char"} and numbers else None
        precision = numbers[0] if data_type in {"decimal", "numeric"} and numbers else None
        scale = numbers[1] if data_type in {"decimal", "numeric"} and len(numbers) > 1 else None
        auto_increment = (field.name == "id" and "自增" in (field.name_cn + field.description)
                          and data_type in {"bigint", "int"})
        result.append(Column(field.name, column_type, data_type, field.nullable and not field.primary,
                             None, "auto_increment" if auto_increment else "", char_length,
                             precision, scale, field.primary))
    return result


def _columns_from_database(rows: Iterable[dict[str, Any]]) -> list[Column]:
    return [Column(
        name=row["COLUMN_NAME"], column_type=row["COLUMN_TYPE"], data_type=row["DATA_TYPE"],
        nullable=row["IS_NULLABLE"] == "YES", default=row["COLUMN_DEFAULT"],
        extra=row["EXTRA"] or "", character_length=row["CHARACTER_MAXIMUM_LENGTH"],
        numeric_precision=row["NUMERIC_PRECISION"], numeric_scale=row["NUMERIC_SCALE"],
        primary=row["COLUMN_KEY"] == "PRI",
    ) for row in rows]


def _domestic_org_id(index: int) -> str:
    return f"GKXREQORG{index:06d}"


def _foreign_org_id(index: int) -> str:
    return f"GKX51FORG{index:06d}"


def _scholar_id(index: int) -> str:
    return f"GKXREQSCH{index:06d}"


def _domestic_name(index: int) -> str:
    return f"{CITIES[(index - 1) % len(CITIES)]}{FIELDS[(index - 1) % len(FIELDS)]}科技有限公司{index:04d}"


def _foreign_name(index: int) -> str:
    return f"Global {FIELDS[(index - 1) % len(FIELDS)]} Research Corporation {index:04d}"


def _stable_id(table: str, prefix: str, index: int) -> str:
    token = hashlib.sha1(table.encode()).hexdigest()[:8].upper()
    return f"GKX51{prefix}{token}{index:06d}"


def _time(index: int, offset: int = 0) -> datetime:
    # Keep offsets outside the cycle so publish < implementation < expiry always holds.
    return datetime(2020, 1, 1, 9) + timedelta(days=(index * 7) % 1_500 + offset)


def _date(index: int, offset: int = 0) -> date:
    return _time(index, offset).date()


def _sample_text(spec: FieldSpec | None) -> str:
    if spec is None:
        return ""
    value = spec.sample.strip()
    if value in {"", "/", "-", "—", "null", "NULL"}:
        return ""
    if re.fullmatch(r"\d+(?:\.\d+)?", value):
        return ""
    return value


def _semantic_value(table: str, name: str, index: int, spec: FieldSpec | None) -> Any:
    lower = name.lower()
    foreign = table in FOREIGN_ORG_TABLES
    org_id = _foreign_org_id(index) if foreign else _domestic_org_id(index)
    org_name = _foreign_name(index) if foreign else _domestic_name(index)
    country_code, country = COUNTRIES[(index - 1) % len(COUNTRIES)]
    city_index = (index - 1) % len(CITIES)
    field = FIELDS[(index - 1) % len(FIELDS)]

    if lower in {"created_time", "create_time", "updated_time", "update_time"}:
        return datetime(2026, 8, 27, 12)
    if lower in {"publish_date", "public_date", "release_date", "filing_date", "listed_date",
                 "completion_date", "incorporation_date", "issue_date", "dm_birthdate",
                 "penalty_date", "abn_date", "remove_date", "procedure_date", "cancel_date"}:
        return _date(index)
    if lower in {"implementation_date"}:
        return _date(index, 30)
    if lower in {"expire_date", "end_date", "closure_date_end"}:
        return _date(index, 730)
    if lower in {"start_date", "closure_date_begin", "information_date"}:
        return _date(index).isoformat()
    if lower in {"start_time"}:
        return f"{2010 + index % 10:04d}.09"
    if lower in {"end_time"}:
        return f"{2018 + index % 8:04d}.06"
    if lower in {"publish_year", "year", "incorporation_year", "est_year", "founded_year"}:
        return 2018 + index % 9
    if lower == "occur_period":
        return f"{2018 + index % 9:04d}{index % 12 + 1:02d}"

    if lower == "scholar_id":
        return _scholar_id(index)
    if lower in {"org_id", "admin_org_id"}:
        return org_id
    if lower in {"sub_org_id", "inv_org_id", "acquired_org_id"}:
        return _domestic_org_id(index % 1_000 + 1)
    if lower == "acquiring_org_id":
        return _domestic_org_id(index)
    if lower == "eid":
        return f"{800_000_000 + index:09d}"
    if lower == "sub_eid":
        return f"{900_000_000 + index:09d}"
    if lower == "u_id":
        return f"GKX51BID{index:08d}"
    if lower == "id":
        return _stable_id(table, "ID", index)
    if lower.endswith("_id") or lower in {"abnormal_id", "sv_id", "penalty_id", "dishonest_id",
                                         "tax_vio_id", "exec_person_id", "web_id"}:
        return _stable_id(table, re.sub(r"[^A-Z]", "", lower.upper())[:6] or "ID", index)

    if lower in {"name_cn", "company_name", "taxpayer_name", "admin_org", "inv_name",
                 "acquiring_name", "acquired_name", "org_loc_name", "traditional_name",
                 "n_company_name", "qymc", "sub_name_cn"}:
        return _domestic_name(index if lower != "sub_name_cn" else index % 1_000 + 1)
    if lower in {"name_en", "name_std", "ename", "sub_name"}:
        return _foreign_name(index)
    if lower in {"executives_name", "owners_name", "legal_name", "finance_name", "legal_person",
                 "lerep", "related_person_name", "project_host"}:
        return f"示例人员{index:04d}"
    if lower in {"org_name_en"}:
        return f"Research University {index:04d}"
    if lower in {"org_name_zh"}:
        return f"示范研究大学{index:04d}"
    if lower in {"department_name_en"}:
        return f"School of {field}"
    if lower in {"department_name_zh"}:
        return f"{field}学院"
    if lower in {"position_en"}:
        return "Professor"
    if lower in {"position_zh", "executives_position"}:
        return "教授" if "scholar" in table else "技术总监"
    if lower in {"degree_en"}:
        return f"Ph.D. in {field}"
    if lower in {"degree_zh"}:
        return f"{field}博士"
    if lower in {"major_en"}:
        return field
    if lower in {"major_zh"}:
        return field

    if lower in {"external_id", "inv_external_id", "acquiring_external_id", "acquired_external_id",
                 "credit_no", "taxpayer_id", "org_code", "company_code", "br_code"}:
        return f"91310100MA{index:08d}"
    if lower == "school_code":
        return f"SCH{index:08d}"
    if lower == "stock_code":
        return f"{600000 + index:06d}"
    if lower in {"project_number", "plan_number", "project_id"}:
        return f"GKX-2026-{index:06d}"
    if lower in {"case_no", "reg_no", "decision_no", "exec_basis_no", "report_number"}:
        return f"（2026）GKX{index:06d}号"
    if lower in {"publish_code"}:
        return f"科创发〔2026〕{index:04d}号"

    if lower in {"title"}:
        return (f"关于推进{field}创新发展的示范政策{index:04d}" if "policy" in table
                else f"{field}项目公告{index:04d}")
    if lower in {"project_name", "bid_item_name", "target_item_name", "standard_product_name"}:
        return f"{field}技术服务项目{index:04d}"
    if lower in {"job_title"}:
        return f"{field}高级研发工程师"
    if lower in {"main_products", "main_prod", "brand", "model"}:
        return f"{field}平台-{index:04d}"
    if lower in {"org_tag"}:
        return ("国家级高新技术企业", "专精特新企业", "科技型中小企业", "创新型企业")[index % 4]

    if lower in {"province", "project_region_province"}:
        return PROVINCES[city_index]
    if lower in {"city", "project_region_city", "work_place"}:
        return CITIES[city_index]
    if lower in {"area", "district", "project_region_district"}:
        return "高新技术产业开发区"
    if lower in {"country_code", "owners_country_code", "sub_country_code"}:
        return country_code
    if lower in {"country", "owners_country", "sub_country", "region", "geo_region"}:
        return country
    if lower == "language":
        return "English" if foreign or "global" in table else "Chinese"
    if lower in {"address", "reg_address", "company_address", "construction_service_location"}:
        return f"{CITIES[city_index]}市科技大道{index}号"
    if lower in {"website", "web_link", "link", "original_link", "original_textlink"}:
        return f"https://example.invalid/{table}/{index:04d}"
    if lower == "email":
        return f"contact{index:04d}@example.invalid"
    if lower == "phone":
        return f"+86-10-{60000000 + index:08d}"
    if lower == "postal_code":
        return f"{100000 + index:06d}"[-6:]
    if lower in {"lat", "addr_lat"}:
        return Decimal("39.900000") + Decimal(index % 100) / Decimal(1000)
    if lower in {"lng", "addr_lng"}:
        return Decimal("116.300000") + Decimal(index % 100) / Decimal(1000)

    if lower in {"status", "listed_status", "company_status", "reg_status", "exec_status"}:
        return "有效" if "policy" in table else "存续"
    if lower in {"is_current", "is_hidden", "is_history", "allow_joint_bid"}:
        return index % 2
    if lower in {"seq_no", "ranking", "quantity", "employees_number", "rd_staff", "person_num"}:
        return index % 50 + 1
    if lower in {"ownership_percentage", "investment_ratio"}:
        return Decimal(10 + index % 80)
    if any(token in lower for token in ("amount", "assets", "liabilities", "revenue", "profit",
                                         "equity", "capital_num", "target", "price", "fee")):
        return Decimal(1_000_000 + index * 10_000)
    if lower.endswith("_code") or lower in {"relate_type", "party_type", "party_role_type",
                                            "dishonest_type", "exec_person_type", "bidding_document_sub_style"}:
        return index % 5 + 1

    if lower in {"abstract", "content", "description", "bio", "dm_biography", "project_content",
                 "service_content", "job_description", "case_title", "case_cause", "case_role",
                 "main_activities", "business_scope", "illegal_fact", "punish_basis",
                 "violation_fact", "penalty_content", "legal_obligation", "dishonest_behavior"}:
        return f"本条为{field}领域的千级测试数据，用于验证检索、关联分析与统计流程，记录序号{index:04d}。"
    if lower in {"keywords", "theme", "topic", "keypoints"}:
        return f"{field},科技创新,成果转化"
    if lower in {"data_source", "source_table"}:
        return SOURCE
    if lower == "source":
        return "工业和信息化部" if table == "dwd_policy_base" else "EUR-Lex"
    if lower in {"currency_code", "capital_currency", "funding_currency_code",
                 "registered_capital_currency_code", "project_budget_amount_unit", "total_amount_unit",
                 "amount_unit", "tender_document_price_unit", "registration_fee_unit",
                 "bidding_security_unit", "ca_payment_unit", "tender_agent_service_fee_unit",
                 "performance_security_unit"}:
        return "CNY"
    if lower in {"appendix_name", "quote_policy"}:
        return json.dumps([f"示例附件{index:04d}.pdf"], ensure_ascii=False)
    if lower == "appendix_link":
        return json.dumps([f"https://example.invalid/policy/{index:04d}.pdf"], ensure_ascii=False)
    if lower == "policy_support":
        return json.dumps([{"support_object": "企业", "support_behavior": ["研发补助"],
                            "condition": [f"符合{field}产业方向"]}], ensure_ascii=False)

    sample = _sample_text(spec)
    return sample or f"{spec.name_cn if spec and spec.name_cn else name}-{index:04d}"


def _decimal_value(value: Any, column: Column, name: str, index: int) -> Decimal:
    try:
        result = Decimal(str(value))
    except Exception:
        result = Decimal(index)
    precision = int(column.numeric_precision or 20)
    scale = int(column.numeric_scale or 0)
    integer_digits = max(1, precision - scale)
    maximum = Decimal(10) ** integer_digits - (Decimal(10) ** (-scale) if scale else 1)
    if "unsigned" in column.column_type and result < 0:
        result = abs(result)
    if abs(result) > maximum:
        result = (abs(result) % maximum) * (-1 if result < 0 else 1)
    if scale:
        result = result.quantize(Decimal(1).scaleb(-scale))
    else:
        result = result.quantize(Decimal(1))
    return result


def coerce_value(column: Column, value: Any, index: int) -> Any:
    """Coerce a semantic source value into the target MySQL column type."""
    data_type = column.data_type.lower()
    if data_type in {"datetime", "timestamp"}:
        if isinstance(value, date) and not isinstance(value, datetime):
            return datetime.combine(value, datetime.min.time())
        return value if isinstance(value, datetime) else _time(index)
    if data_type == "date":
        return value.date() if isinstance(value, datetime) else (value if isinstance(value, date) else _date(index))
    if data_type in {"decimal", "numeric"}:
        return _decimal_value(value, column, column.name, index)
    if data_type in {"tinyint", "smallint", "mediumint", "int", "bigint"}:
        if column.name in {"status", "seq_no"}:
            value = 1
        if column.name == "id" and not isinstance(value, int):
            value = 9_510_000_000 + index
        try:
            integer = int(value)
        except (TypeError, ValueError):
            integer = index
        limits = {
            "tinyint": (0, 255) if "unsigned" in column.column_type else (-128, 127),
            "smallint": (0, 65_535) if "unsigned" in column.column_type else (-32_768, 32_767),
            "mediumint": (0, 16_777_215) if "unsigned" in column.column_type else (-8_388_608, 8_388_607),
            "int": (0, 4_294_967_295) if "unsigned" in column.column_type else (-2_147_483_648, 2_147_483_647),
            "bigint": (0, 18_446_744_073_709_551_615) if "unsigned" in column.column_type
                      else (-9_223_372_036_854_775_808, 9_223_372_036_854_775_807),
        }
        minimum, maximum = limits[data_type]
        if integer < minimum or integer > maximum:
            integer = minimum + abs(integer) % (maximum - minimum + 1)
        return integer
    if data_type == "json":
        if isinstance(value, str):
            try:
                json.loads(value)
                return value
            except json.JSONDecodeError:
                pass
        payload = value if isinstance(value, (dict, list, int, float, bool)) else {"value": value, "index": index}
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if data_type in {"char", "varchar", "text", "tinytext", "mediumtext", "longtext", "enum", "set"}:
        text = str(value)
        if column.character_length is not None:
            text = text[:int(column.character_length)]
        return text
    if data_type in {"float", "double", "real"}:
        return float(value) if isinstance(value, (int, float, Decimal)) else float(index)
    if data_type in {"binary", "varbinary", "blob", "tinyblob", "mediumblob", "longblob"}:
        return str(value).encode()
    return value


def value_for(table: str, column: Column, index: int, spec: FieldSpec | None) -> Any:
    return coerce_value(column, _semantic_value(table, column.name, index, spec), index)


def generate_rows(table: str, fields: list[FieldSpec], columns: list[Column], start: int,
                  count: int) -> list[dict[str, Any]]:
    specs = {field.name: field for field in fields}
    writable = [column for column in columns
                if "auto_increment" not in column.extra.lower() and "generated" not in column.extra.lower()]
    return [{column.name: value_for(table, column, index, specs.get(column.name)) for column in writable}
            for index in range(start + 1, start + count + 1)]


def validate_rows(table: str, rows: list[dict[str, Any]], columns: list[Column]) -> list[str]:
    errors: list[str] = []
    writable = [column for column in columns
                if "auto_increment" not in column.extra.lower() and "generated" not in column.extra.lower()]
    expected = {column.name for column in writable}
    primary = [column.name for column in writable if column.primary]
    seen_primary: set[tuple[Any, ...]] = set()
    for row_number, row in enumerate(rows, 1):
        if set(row) != expected:
            errors.append(f"{table} 第 {row_number} 行字段集合不匹配")
            break
        for column in writable:
            value = row[column.name]
            if value is None and not column.nullable and column.default is None:
                errors.append(f"{table}.{column.name} 第 {row_number} 行不能为空")
            if column.character_length is not None and value is not None and len(str(value)) > column.character_length:
                errors.append(f"{table}.{column.name} 第 {row_number} 行超过长度 {column.character_length}")
            if column.data_type.lower() == "json" and value is not None:
                try:
                    json.loads(value)
                except (TypeError, json.JSONDecodeError):
                    errors.append(f"{table}.{column.name} 第 {row_number} 行不是合法 JSON")
        if primary:
            key = tuple(row[name] for name in primary if name in row)
            if key and key in seen_primary:
                errors.append(f"{table} 生成了重复主键 {key}")
            seen_primary.add(key)
    if table == "dwd_policy_base":
        for row_number, row in enumerate(rows, 1):
            publish = row.get("publish_date")
            implementation = row.get("implementation_date")
            expiry = row.get("expire_date")
            if publish and implementation and expiry and not (publish <= implementation <= expiry):
                errors.append(f"{table} 第 {row_number} 行日期顺序错误")
    return errors


def _connect(database: str, autocommit: bool = False):
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
        cursorclass=pymysql.cursors.DictCursor, autocommit=autocommit,
        read_timeout=120, write_timeout=120,
    )


def inspect_database(connection: Any, tables: Iterable[str]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    with connection.cursor() as cursor:
        for table in sorted(tables):
            cursor.execute(
                """SELECT COLUMN_NAME, COLUMN_TYPE, DATA_TYPE, CHARACTER_MAXIMUM_LENGTH,
                          NUMERIC_PRECISION, NUMERIC_SCALE, IS_NULLABLE, COLUMN_DEFAULT,
                          EXTRA, COLUMN_KEY, ORDINAL_POSITION
                     FROM information_schema.columns
                    WHERE table_schema = DATABASE() AND table_name = %s
                    ORDER BY ORDINAL_POSITION""", (table,))
            columns = cursor.fetchall()
            if not columns:
                result[table] = {"exists": False, "count": 0, "columns": []}
                continue
            cursor.execute(f"SELECT COUNT(*) AS total FROM `{table}`")
            result[table] = {"exists": True, "count": int(cursor.fetchone()["total"]),
                             "columns": _columns_from_database(columns)}
    return result


def build_plan(specs: dict[str, list[FieldSpec]], database_state: dict[str, dict[str, Any]],
               target: int) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]], list[str]]:
    plan: dict[str, Any] = {}
    generated: dict[str, list[dict[str, Any]]] = {}
    errors: list[str] = []
    for table, fields in sorted(specs.items()):
        state = database_state[table]
        current = int(state["count"])
        needed = max(0, target - current)
        columns = state["columns"] if state["exists"] else _columns_from_specs(fields)
        excel_names = {field.name for field in fields}
        actual_names = {column.name for column in columns}
        missing_excel_fields = sorted(excel_names - actual_names) if state["exists"] else []
        rows = generate_rows(table, fields, columns, current, needed)
        errors.extend(validate_rows(table, rows, columns))
        generated[table] = rows
        plan[table] = {
            "sheet": fields[0].sheet, "exists_before": state["exists"], "current_rows": current,
            "target_rows": target, "planned_inserts": needed, "excel_fields": len(fields),
            "actual_fields": len(columns), "excel_fields_missing_in_existing_table": missing_excel_fields,
        }
    return plan, generated, errors


def apply_plan(connection: Any, specs: dict[str, list[FieldSpec]], plan: dict[str, Any],
               generated: dict[str, list[dict[str, Any]]], batch_size: int, target: int) -> dict[str, Any]:
    created: list[str] = []
    with connection.cursor() as cursor:
        for table, item in plan.items():
            if not item["exists_before"]:
                cursor.execute(create_table_sql(table, specs[table]))
                created.append(table)
    # MySQL DDL commits implicitly.  All data inserts after this point share one transaction.
    inserted: dict[str, int] = {}
    try:
        with connection.cursor() as cursor:
            for table, rows in generated.items():
                if not rows:
                    inserted[table] = 0
                    continue
                columns = tuple(rows[0])
                statement = (f"INSERT INTO `{table}` (" + ", ".join(f"`{name}`" for name in columns)
                             + ") VALUES (" + ", ".join(["%s"] * len(columns)) + ")")
                for offset in range(0, len(rows), max(1, batch_size)):
                    batch = rows[offset:offset + max(1, batch_size)]
                    cursor.executemany(statement, [tuple(row[name] for name in columns) for row in batch])
                inserted[table] = len(rows)
            for table in specs:
                cursor.execute(f"SELECT COUNT(*) AS total FROM `{table}`")
                total = int(cursor.fetchone()["total"])
                if total < target:
                    raise ValueError(f"事务内复核失败: {table} 只有 {total} 行，目标至少 {target}")
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    return {"created_tables": created, "inserted": inserted}


def validate_in_mysql(connection: Any, specs: dict[str, list[FieldSpec]], plan: dict[str, Any],
                      generated: dict[str, list[dict[str, Any]]], batch_size: int) -> dict[str, Any]:
    """Exercise the exact DDL and inserts in connection-scoped temporary tables."""
    checked: dict[str, int] = {}
    with connection.cursor() as cursor:
        for table, rows in generated.items():
            if not rows:
                continue
            temporary = "_tmp_gkx51_" + hashlib.sha1(table.encode()).hexdigest()[:12]
            cursor.execute(f"DROP TEMPORARY TABLE IF EXISTS `{temporary}`")
            if plan[table]["exists_before"]:
                cursor.execute(f"CREATE TEMPORARY TABLE `{temporary}` LIKE `{table}`")
            else:
                ddl = create_table_sql(table, specs[table])
                prefix = f"CREATE TABLE IF NOT EXISTS `{table}`"
                ddl = ddl.replace(prefix, f"CREATE TEMPORARY TABLE `{temporary}`", 1)
                cursor.execute(ddl)
            columns = tuple(rows[0])
            statement = (f"INSERT INTO `{temporary}` (" + ", ".join(f"`{name}`" for name in columns)
                         + ") VALUES (" + ", ".join(["%s"] * len(columns)) + ")")
            for offset in range(0, len(rows), max(1, batch_size)):
                batch = rows[offset:offset + max(1, batch_size)]
                cursor.executemany(statement, [tuple(row[name] for name in columns) for row in batch])
            cursor.execute(f"SELECT COUNT(*) AS total FROM `{temporary}`")
            actual = int(cursor.fetchone()["total"])
            if actual != len(rows):
                raise ValueError(f"MySQL 临时表校验失败: {table} expected={len(rows)}, actual={actual}")
            checked[table] = actual
            cursor.execute(f"DROP TEMPORARY TABLE `{temporary}`")
    connection.rollback()
    return {"valid": True, "tables_checked": len(checked), "rows_checked": sum(checked.values())}


def audit_after(connection: Any, tables: Iterable[str], target: int) -> dict[str, Any]:
    state = inspect_database(connection, tables)
    counts = {table: item["count"] for table, item in state.items()}
    below = {table: count for table, count in counts.items() if count < target}
    return {
        "table_count": len(counts), "tables_with_data": sum(count > 0 for count in counts.values()),
        "tables_without_data": sum(count == 0 for count in counts.values()),
        "tables_at_or_above_target": sum(count >= target for count in counts.values()),
        "tables_below_target": below, "total_rows_in_51_tables": sum(counts.values()),
        "counts": counts,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="按 Excel 定义补齐 gkx 的 51 张表，并将每表补到千级")
    parser.add_argument("--workbook", type=Path, default=WORKBOOK)
    parser.add_argument("--database", default="gkx")
    parser.add_argument("--target-per-table", type=int, default=DEFAULT_TARGET)
    parser.add_argument("--batch-size", type=int, default=200)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm-database")
    args = parser.parse_args()
    if args.target_per_table < 1:
        raise SystemExit("--target-per-table 必须大于 0")
    if args.database != "gkx":
        raise SystemExit("安全限制：此脚本只允许目标数据库 gkx")

    specs = parse_workbook(args.workbook.resolve())
    connection = _connect(args.database)
    try:
        before = inspect_database(connection, specs)
        plan, generated, errors = build_plan(specs, before, args.target_per_table)
        mysql_validation = (validate_in_mysql(connection, specs, plan, generated, args.batch_size)
                            if not errors else {"valid": False, "tables_checked": 0, "rows_checked": 0})
        summary = {
            "database": args.database, "workbook": str(args.workbook.resolve()),
            "source_marker": SOURCE, "dry_run": not args.apply,
            "excel_table_count": len(specs),
            "existing_tables": sum(item["exists"] for item in before.values()),
            "missing_tables_to_create": [table for table, item in before.items() if not item["exists"]],
            "planned_insert_rows": sum(item["planned_inserts"] for item in plan.values()),
            "tables_needing_inserts": sum(item["planned_inserts"] > 0 for item in plan.values()),
            "validation": {"valid": not errors, "errors": errors[:100]},
            "mysql_temporary_table_validation": mysql_validation, "plan": plan,
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
        if errors:
            raise SystemExit(1)
        if not args.apply:
            return
        if args.confirm_database != args.database:
            raise SystemExit("拒绝写入：必须同时使用 --database gkx --confirm-database gkx")
        applied = apply_plan(connection, specs, plan, generated, args.batch_size, args.target_per_table)
        after = audit_after(connection, specs, args.target_per_table)
        print(json.dumps({"applied": True, **applied, "post_audit": after},
                         ensure_ascii=False, indent=2, default=str))
    finally:
        connection.close()


if __name__ == "__main__":
    main()
