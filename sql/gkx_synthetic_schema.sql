-- Schema for generated data only. The importer refuses to target the production gkx database.
CREATE TABLE IF NOT EXISTS organizations (
  org_id VARCHAR(32) PRIMARY KEY,
  name_zh VARCHAR(255) NOT NULL,
  name_en VARCHAR(255),
  org_type VARCHAR(64) NOT NULL,
  city VARCHAR(64),
  status TINYINT NOT NULL DEFAULT 1,
  updated_at DATE NOT NULL,
  UNIQUE KEY uk_organization_name_zh (name_zh)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS departments (
  dept_id VARCHAR(32) PRIMARY KEY,
  org_id VARCHAR(32) NOT NULL,
  name_zh VARCHAR(255) NOT NULL,
  name_en VARCHAR(255),
  status TINYINT NOT NULL DEFAULT 1,
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  KEY idx_department_org (org_id),
  CONSTRAINT fk_department_org FOREIGN KEY (org_id) REFERENCES organizations(org_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS enterprises (
  enterprise_id VARCHAR(32) PRIMARY KEY,
  name_zh VARCHAR(255) NOT NULL,
  name_en VARCHAR(255),
  credit_code VARCHAR(32) NOT NULL,
  city VARCHAR(64),
  status TINYINT NOT NULL DEFAULT 1,
  updated_at DATE NOT NULL,
  UNIQUE KEY uk_enterprise_credit_code (credit_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS industry_segments (
  segment_id VARCHAR(32) PRIMARY KEY,
  name_zh VARCHAR(255) NOT NULL,
  level INT NOT NULL,
  parent_segment_id VARCHAR(32),
  status TINYINT NOT NULL DEFAULT 1,
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  KEY idx_segment_parent (parent_segment_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS dwd_scholar (
  scholar_id VARCHAR(32) PRIMARY KEY,
  name_zh VARCHAR(128) NOT NULL,
  name_en VARCHAR(255),
  org_id VARCHAR(32) NOT NULL,
  dept_id VARCHAR(32) NOT NULL,
  scholar_org_name_zh VARCHAR(255),
  scholar_org_name_en VARCHAR(255),
  work_experience_date VARCHAR(64),
  work_experience_institution_zh VARCHAR(255),
  work_experience_institution_en VARCHAR(255),
  work_experience_department_zh VARCHAR(255),
  work_experience_department_en VARCHAR(255),
  work_experience_position_zh VARCHAR(128),
  work_experience_position_en VARCHAR(128),
  education_background_date VARCHAR(64),
  education_background_institution_zh VARCHAR(255),
  education_background_institution_en VARCHAR(255),
  education_background_degree_zh VARCHAR(64),
  education_background_degree_en VARCHAR(64),
  orcid VARCHAR(32) NOT NULL,
  email_hash CHAR(64) NOT NULL,
  research_field VARCHAR(128),
  status TINYINT NOT NULL DEFAULT 1,
  updated_at DATE NOT NULL,
  UNIQUE KEY uk_scholar_orcid (orcid),
  KEY idx_scholar_name (name_zh),
  KEY idx_scholar_org (org_id),
  CONSTRAINT fk_scholar_org FOREIGN KEY (org_id) REFERENCES organizations(org_id),
  CONSTRAINT fk_scholar_dept FOREIGN KEY (dept_id) REFERENCES departments(dept_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS dwd_scholar_papers (
  id VARCHAR(32) PRIMARY KEY,
  zh_name VARCHAR(500) NOT NULL,
  en_name VARCHAR(500),
  doi VARCHAR(128) NOT NULL,
  cover_date_start DATE,
  venue VARCHAR(255),
  status TINYINT NOT NULL DEFAULT 1,
  updated_at DATE NOT NULL,
  UNIQUE KEY uk_paper_doi (doi)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS dwd_scholar_paper_relation (
  id VARCHAR(40) PRIMARY KEY,
  scholar_id VARCHAR(32) NOT NULL,
  related_paper_id VARCHAR(32) NOT NULL,
  author_order INT NOT NULL,
  year INT,
  publish_time DATE,
  status TINYINT NOT NULL DEFAULT 1,
  evidence_id VARCHAR(128) NOT NULL,
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  UNIQUE KEY uk_paper_author (scholar_id, related_paper_id),
  KEY idx_authorship_paper (related_paper_id),
  CONSTRAINT fk_authorship_scholar FOREIGN KEY (scholar_id) REFERENCES dwd_scholar(scholar_id),
  CONSTRAINT fk_authorship_paper FOREIGN KEY (related_paper_id) REFERENCES dwd_scholar_papers(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS dwd_zh_project (
  id VARCHAR(32) PRIMARY KEY,
  title VARCHAR(500) NOT NULL,
  approval_year INT,
  research_period VARCHAR(64),
  project_host VARCHAR(128),
  participants JSON NOT NULL,
  status TINYINT NOT NULL DEFAULT 1,
  updated_at DATE NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS scholar_project_relation (
  id VARCHAR(40) PRIMARY KEY,
  project_id VARCHAR(32) NOT NULL,
  scholar_id VARCHAR(32) NOT NULL,
  role VARCHAR(32) NOT NULL,
  status TINYINT NOT NULL DEFAULT 1,
  evidence_id VARCHAR(128) NOT NULL,
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  UNIQUE KEY uk_project_scholar (project_id, scholar_id),
  CONSTRAINT fk_project_relation_project FOREIGN KEY (project_id) REFERENCES dwd_zh_project(id),
  CONSTRAINT fk_project_relation_scholar FOREIGN KEY (scholar_id) REFERENCES dwd_scholar(scholar_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS dwd_patent (
  patent_id VARCHAR(32) PRIMARY KEY,
  publication_number VARCHAR(64) NOT NULL,
  inventors JSON NOT NULL,
  assignee_enterprise_id VARCHAR(32) NOT NULL,
  application_date DATE,
  status TINYINT NOT NULL DEFAULT 1,
  updated_at DATE NOT NULL,
  UNIQUE KEY uk_patent_publication_number (publication_number),
  CONSTRAINT fk_patent_enterprise FOREIGN KEY (assignee_enterprise_id) REFERENCES enterprises(enterprise_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS dwd_patent_title (
  patent_id VARCHAR(32) PRIMARY KEY,
  title_zh VARCHAR(500) NOT NULL,
  title_localized VARCHAR(500),
  status TINYINT NOT NULL DEFAULT 1,
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  CONSTRAINT fk_patent_title_patent FOREIGN KEY (patent_id) REFERENCES dwd_patent(patent_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS scholar_patent_relation (
  id VARCHAR(40) PRIMARY KEY,
  patent_id VARCHAR(32) NOT NULL,
  scholar_id VARCHAR(32) NOT NULL,
  inventor_order INT NOT NULL,
  status TINYINT NOT NULL DEFAULT 1,
  evidence_id VARCHAR(128) NOT NULL,
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  UNIQUE KEY uk_patent_scholar (patent_id, scholar_id),
  CONSTRAINT fk_patent_relation_patent FOREIGN KEY (patent_id) REFERENCES dwd_patent(patent_id),
  CONSTRAINT fk_patent_relation_scholar FOREIGN KEY (scholar_id) REFERENCES dwd_scholar(scholar_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS scholar_enterprise_relation (
  id VARCHAR(32) PRIMARY KEY,
  scholar_id VARCHAR(32) NOT NULL,
  enterprise_id VARCHAR(32) NOT NULL,
  role VARCHAR(64) NOT NULL,
  start_year INT,
  status TINYINT NOT NULL DEFAULT 1,
  evidence_id VARCHAR(128) NOT NULL,
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  UNIQUE KEY uk_scholar_enterprise_role (scholar_id, enterprise_id, role),
  CONSTRAINT fk_scholar_enterprise_scholar FOREIGN KEY (scholar_id) REFERENCES dwd_scholar(scholar_id),
  CONSTRAINT fk_scholar_enterprise_enterprise FOREIGN KEY (enterprise_id) REFERENCES enterprises(enterprise_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS enterprise_industry_relation (
  id VARCHAR(32) PRIMARY KEY,
  enterprise_id VARCHAR(32) NOT NULL,
  segment_id VARCHAR(32) NOT NULL,
  relation_type VARCHAR(64) NOT NULL,
  status TINYINT NOT NULL DEFAULT 1,
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  UNIQUE KEY uk_enterprise_segment (enterprise_id, segment_id),
  CONSTRAINT fk_enterprise_industry_enterprise FOREIGN KEY (enterprise_id) REFERENCES enterprises(enterprise_id),
  CONSTRAINT fk_enterprise_industry_segment FOREIGN KEY (segment_id) REFERENCES industry_segments(segment_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS industry_events (
  event_id VARCHAR(32) PRIMARY KEY,
  segment_id VARCHAR(32) NOT NULL,
  title VARCHAR(500) NOT NULL,
  event_date DATE,
  importance DECIMAL(4,2),
  status TINYINT NOT NULL DEFAULT 1,
  evidence_id VARCHAR(128) NOT NULL,
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  CONSTRAINT fk_industry_event_segment FOREIGN KEY (segment_id) REFERENCES industry_segments(segment_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
