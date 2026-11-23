CREATE DATABASE IF NOT EXISTS jiatan_activity_agent CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_as_cs;
USE jiatan_activity_agent;

CREATE TABLE IF NOT EXISTS approved_case (
    case_key VARCHAR(80) NOT NULL,
    revision SMALLINT UNSIGNED NOT NULL,
    case_scope VARCHAR(80) NOT NULL,
    source_sha256 CHAR(64) NOT NULL,
    confirmed_summary TEXT NOT NULL,
    recognition_hint TEXT NOT NULL,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    approved_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (case_key, revision),
    KEY active_scope (case_scope, active, approved_at)
);

CREATE TABLE IF NOT EXISTS context_use (
    request_id VARCHAR(200) NOT NULL,
    case_key VARCHAR(80) NOT NULL,
    case_revision SMALLINT UNSIGNED NOT NULL,
    used_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (request_id, case_key, case_revision),
    FOREIGN KEY (case_key, case_revision) REFERENCES approved_case (case_key, revision)
);
