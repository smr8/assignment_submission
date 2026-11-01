-- ===========================================================================
-- 补齐 assignments 缺失的数据表 / 字段（SQLite）
-- 适用症状：
--   no such table: assignments_projectimage
--   no such table: assignments_backuprecord
--   no such column: assignments_project.archived_at
--
-- 覆盖迁移：0003（备份记录）、0004（作业图片 / 附件）、0005（归档字段）
-- DDL 与 Django 自己生成的语句完全一致，可重复执行。
--
-- 用法一（sqlite3 命令行，一次跑完整个文件）：
--     sqlite3 db.sqlite3 < repair_schema.sql
-- 用法二（Django shell / Python，必须用 executescript，不能用 execute）：
--     from django.db import connection
--     with connection.cursor() as cur:
--         cur.executescript(open('repair_schema.sql', encoding='utf-8').read())
--
-- 若工具报 "You can only execute one statement at a time."，
-- 说明它在用 execute()：去掉 BEGIN/COMMIT，按下面每段注释后的单条语句逐条执行。
-- 注意：第 4 段 ALTER 在该列已存在时会报 duplicate column name: archived_at，忽略即可。
-- ===========================================================================

-- 执行前可先自查，确认到底缺什么（可单独执行）：
--   SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'assignments%';
--   SELECT app, name FROM django_migrations WHERE app='assignments';

BEGIN;

-- 1) 备份记录表（迁移 0003）
CREATE TABLE IF NOT EXISTS "assignments_backuprecord" (
    "id" integer NOT NULL PRIMARY KEY AUTOINCREMENT,
    "backup_type" varchar(16) NOT NULL,
    "backend_type" varchar(32) NOT NULL,
    "status" varchar(16) NOT NULL,
    "backup_prefix" varchar(512) NOT NULL,
    "file_count" integer unsigned NOT NULL CHECK ("file_count" >= 0),
    "total_size" bigint unsigned NOT NULL CHECK ("total_size" >= 0),
    "database_backed_up" bool NOT NULL,
    "duration_seconds" real NOT NULL,
    "error_message" text NOT NULL,
    "created_at" datetime NOT NULL
);

-- 2) 作业图片表（迁移 0004）
CREATE TABLE IF NOT EXISTS "assignments_projectimage" (
    "id" integer NOT NULL PRIMARY KEY AUTOINCREMENT,
    "image" varchar(100) NOT NULL,
    "caption" varchar(255) NOT NULL,
    "sort_order" integer unsigned NOT NULL CHECK ("sort_order" >= 0),
    "created_at" datetime NOT NULL,
    "project_id" bigint NOT NULL REFERENCES "assignments_project" ("id") DEFERRABLE INITIALLY DEFERRED
);
CREATE INDEX IF NOT EXISTS "assignments_projectimage_project_id_8b9d8e0b"
    ON "assignments_projectimage" ("project_id");

-- 3) 作业附件表（迁移 0004）
CREATE TABLE IF NOT EXISTS "assignments_projectattachment" (
    "id" integer NOT NULL PRIMARY KEY AUTOINCREMENT,
    "file" varchar(100) NOT NULL,
    "name" varchar(255) NOT NULL,
    "file_size" integer unsigned NOT NULL CHECK ("file_size" >= 0),
    "created_at" datetime NOT NULL,
    "project_id" bigint NOT NULL REFERENCES "assignments_project" ("id") DEFERRABLE INITIALLY DEFERRED
);
CREATE INDEX IF NOT EXISTS "assignments_projectattachment_project_id_c7188352"
    ON "assignments_projectattachment" ("project_id");

-- 4) 归档字段（迁移 0005）
-- SQLite 的 ADD COLUMN 不支持 IF NOT EXISTS：若该列已存在会报
-- "duplicate column name: archived_at"，属正常，忽略这一条继续执行即可。
ALTER TABLE "assignments_project" ADD COLUMN "archived_at" datetime NULL;
CREATE INDEX IF NOT EXISTS "assignments_project_archived_at_af1b9c06"
    ON "assignments_project" ("archived_at");

-- 5) 补登三条迁移记录
-- 少了这一步，下次 python manage.py migrate 会再建一次表，报 "table already exists"
INSERT INTO "django_migrations" ("app", "name", "applied")
SELECT 'assignments', '0003_backuprecord', CURRENT_TIMESTAMP
WHERE NOT EXISTS (
    SELECT 1 FROM "django_migrations"
    WHERE "app" = 'assignments' AND "name" = '0003_backuprecord'
);

INSERT INTO "django_migrations" ("app", "name", "applied")
SELECT 'assignments', '0004_projectattachment_projectimage', CURRENT_TIMESTAMP
WHERE NOT EXISTS (
    SELECT 1 FROM "django_migrations"
    WHERE "app" = 'assignments' AND "name" = '0004_projectattachment_projectimage'
);

INSERT INTO "django_migrations" ("app", "name", "applied")
SELECT 'assignments', '0005_project_archived_at', CURRENT_TIMESTAMP
WHERE NOT EXISTS (
    SELECT 1 FROM "django_migrations"
    WHERE "app" = 'assignments' AND "name" = '0005_project_archived_at'
);

COMMIT;

-- ===========================================================================
-- 执行后自检（可选）
-- ===========================================================================
-- 应返回 6 行：backuprecord / livesettings / project / projectattachment / projectimage / submission
SELECT name FROM sqlite_master
WHERE type = 'table' AND name LIKE 'assignments%'
ORDER BY name;

-- 应返回 0001 ~ 0005 五条
SELECT name FROM "django_migrations" WHERE "app" = 'assignments' ORDER BY name;

-- 应返回 archived_at 一行
SELECT name FROM pragma_table_info('assignments_project') WHERE name = 'archived_at';
