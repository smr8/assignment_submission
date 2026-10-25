# 作业提交系统 (Assignment Submission System)

一个基于 Django 的作业提交与管理系统，为教师和学生提供便捷的作业提交、审查和管理功能。

## 项目概述

该系统是一个功能完整的在线作业管理平台，支持学生提交作业、教师后台管理、批量下载、代提交等功能。

## 核心功能

### 学生功能

-   **作业浏览** — 查看所有可用的作业项目及提交状态
-   **项目资料** — 查看教师上传的作业图片（带描述，可放大预览）与附件下载
-   **文件提交** — 支持拖拽上传，自动重命名；大文件自动分片上传并显示进度条
-   **申诉机制** — 提交时附带申诉说明
-   **截止时间提醒** — 项目截止状态实时显示
-   **账号管理** — 修改姓名、学号、密码

### 管理员功能

-   **项目管理** — 创建、编辑、删除作业项目
-   **项目归档** — 结课项目一键归档（或批量归档已过截止时间的项目）：学生端不再显示、停止接收提交，提交记录与文件完整保留，可随时恢复；支持「进行中 / 已归档 / 全部」视图切换
-   **作业资料** — 为项目上传图片（每张可填描述、可预览）与附件（学生可下载）
-   **用户管理** — 管理学生和管理员账户
-   **提交审查** — 查看、筛选、下载所有学生提交
-   **代提交作业** — 在提交记录中替学生上传文件（同样支持分片上传）
-   **批量下载** — 多选提交记录，一键打包下载 ZIP
-   **自动重命名** — 灵活的文件重命名规则配置
-   **数据统计** — 各项目提交率统计，点击卡片进入明细，查看已交 / 未交名单（学号 + 姓名），可导出未交名单 CSV
-   **数据导出** — 导出提交记录为 CSV
-   **备份历史** — 记录每次备份的来源后端、状态、文件数、大小与错误详情，支持按状态筛选
-   **系统设置** — 配置存储后端、备份策略等

## 项目结构

text

Copy

```
assignment_submission/├── accounts/              # 用户认证与管理│   ├── models.py         # 自定义 User 模型│   ├── views.py          # 登录、注册、账号管理│   ├── forms.py          # 认证表单│   └── urls.py           # 用户相关路由├── assignments/          # 作业核心功能│   ├── models.py         # Project、Submission 模型│   ├── views.py          # 作业提交逻辑│   └── management/       # 自定义管理命令├── admin_panel/          # 管理员面板│   ├── views.py          # 仪表盘、设置、统计、代提交、批量下载│   └── urls.py           # 管理面板路由├── config/               # Django 项目配置│   ├── settings.py       # 项目设置│   ├── urls.py           # URL 主路由│   └── wsgi.py           # WSGI 配置├── templates/            # HTML 模板├── static/               # 静态资源├── media/                # 用户上传的文件└── manage.py             # Django 管理脚本
```

## 数据模型

### User 用户模型

| 字段 | 说明 |
| --- | --- |
| student\_id | 学号 (unique) |
| name | 姓名 |
| is\_active | 是否激活 |
| is\_staff | 是否为教师/管理员 |
| is\_admin | 是否为超级管理员 |
| created\_at | 注册时间 |

### Project 项目模型

| 字段 | 说明 |
| --- | --- |
| name | 项目名称 |
| slug | URL 标识符 (unique) |
| description | 项目描述 |
| is\_active | 是否启用 |
| auto\_rename | 是否启用自动重命名 |
| rename\_pattern | 重命名规则模板 |
| storage\_backend | 存储后端 (local/webdav/s3/smb) |
| deadline | 截止时间 |
| max\_file\_size | 最大文件大小 (MB) |
| allowed\_extensions | 允许的文件类型 |
| archived\_at | 归档时间（为空表示未归档；归档后学生端隐藏且不可提交） |
| created\_at | 创建时间 |

### Submission 提交模型

| 字段 | 说明 |
| --- | --- |
| project | 关联项目 |
| student | 关联学生 |
| file | 上传文件 |
| original\_filename | 原始文件名 |
| renamed\_filename | 重命名后文件名 |
| file\_size | 文件大小 (bytes) |
| auto\_renamed | 是否自动重命名 |
| appeal | 申诉内容 |
| appeal\_submitted | 是否已提交申诉 |
| submitted\_at | 提交时间 |

### ProjectImage 作业图片模型

| 字段 | 说明 |
| --- | --- |
| project | 关联项目 |
| image | 图片文件 |
| caption | 描述（显示在学生提交页） |
| sort\_order | 排序 |
| created\_at | 上传时间 |

### ProjectAttachment 作业附件模型

| 字段 | 说明 |
| --- | --- |
| project | 关联项目 |
| file | 附件文件 |
| name | 显示名称 |
| file\_size | 文件大小 (bytes) |
| created\_at | 上传时间 |

### BackupRecord 备份记录模型

| 字段 | 说明 |
| --- | --- |
| backup\_type | 备份类型（all/incremental） |
| backend\_type | 使用的存储后端 |
| status | 状态（success/partial/failed） |
| backup\_prefix | 远程备份路径前缀 |
| file\_count | 备份文件数 |
| total\_size | 备份总大小 (bytes) |
| database\_backed\_up | 是否含数据库 |
| duration\_seconds | 耗时 (秒) |
| error\_message | 错误信息 |
| created\_at | 创建时间 |

## 大文件上传（分片上传）

大文件不再整包上传：前端用 `File.slice()` 把文件切成固定大小的分片，逐个 POST 到
`/upload/chunk/`，服务端把分片落到 `media/_chunk_uploads/<upload_id>/`，收到最后一片时
按序合并、校验大小与扩展名，再写入 `Submission`。上传过程中显示进度条，单个分片失败
会自动重试（最多 2 次）。

相关配置在 `config/settings.py`：

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `CHUNK_UPLOAD_SIZE` | 4 MB | 前端切片大小 |
| `DATA_UPLOAD_MAX_MEMORY_SIZE` | 64 MB | 原始请求体上限 |
| `FILE_UPLOAD_MAX_MEMORY_SIZE` | 8 MB | 超过则落临时文件而非内存 |
| `CHUNK_UPLOAD_TMP_DIR` | `_chunk_uploads` | 分片临时目录（相对 MEDIA_ROOT） |
| `PROJECT_IMAGE_EXTENSIONS` | jpg/png/gif/webp/bmp | 允许作为作业图片的格式 |

> **注意（实测结论）**：Django 的 `DATA_UPLOAD_MAX_MEMORY_SIZE` 只在读取原始请求体
> (`request.body`) 时校验，`multipart/form-data` 的文件上传走解析器、不经过该校验；
> `FILE_UPLOAD_MAX_MEMORY_SIZE` 只决定落盘还是进内存，并不是大小上限。
> 实际部署中最容易卡住大文件的是反向代理，例如 nginx 默认
> `client_max_body_size 1m`。若部署在 nginx 后面，请将其调到不小于
> `CHUNK_UPLOAD_SIZE`，否则单个分片都会被拦截。

上传失败或中断时，分片目录会在 24 小时后被自动清理，不会无限堆积。

## 环境配置

### 系统要求

-   Python 3.8+
-   Django 6.0+
-   SQLite 3

### 依赖安装

bash

Copy

```bash
python -m venv venvvenv\Scripts\activate        # Windows# source venv/bin/activate # Linux/Macpip install -r requirements.txt
```

### 依赖包

text

Copy

```
Django==6.0.7
django-crispy-forms==2.6
crispy-bootstrap5==2026.3
tzdata==2026.3
```

存储后端依赖按需安装（`requirements.txt` 中已注释列出）：

-   S3 / 兼容对象存储：`boto3`，**同时需要 `python-dateutil`**（botocore 依赖，缺失会导致连接报错）
-   WebDAV：`webdavclient3`
-   SMB 共享：`pysmb`

## 快速开始

bash

Copy

```bash
cd assignment_submissionpython manage.py migratepython manage.py createsuperuserpython manage.py collectstatic --noinputpython manage.py runserver
```

访问地址：

-   学生页面：[http://localhost:8000/](http://localhost:8000/)
-   Django 后台：[http://localhost:8000/admin/](http://localhost:8000/admin/)
-   管理面板：[http://localhost:8000/admin-panel/](http://localhost:8000/admin-panel/)

## 用户指南

### 学生流程

1.  **登录** — 使用学号和密码登录（默认密码 `123456`）
2.  **浏览项目** — 首页查看所有可提交的作业项目（卡片上显示图片 / 附件数量）
3.  **查看资料** — 提交页可看到教师上传的图片（带描述，点击放大）与可下载的附件
4.  **提交作业** — 拖拽或选择文件上传；大文件自动分片并显示进度
5.  **查看状态** — 已提交的项目显示绿色标记
6.  **账号管理** — 导航栏"账号管理"可修改姓名、学号、密码

### 管理员流程

1.  **登录** — 使用管理员账户登录
2.  **管理面板** — 导航栏进入管理后台
3.  **创建项目** — 在"作业项目管理"中创建新项目，可同时上传作业图片（每张填描述）与附件
4.  **提交记录** — 查看所有学生提交，支持筛选、下载、代上传（同样支持大文件分片上传）
5.  **批量下载** — 勾选多条记录，点击"下载所选项"打包为 ZIP
6.  **数据统计** — 查看各项目提交率；点击项目卡片进入明细页，查看未交学生的姓名 / 学号并可直接代提交、导出未交名单 CSV
7.  **导出 CSV** — 一键导出所有提交记录
8.  **备份历史** — 查看每次备份的后端、状态、文件数、大小；失败备份也会留下记录并可查看错误详情

## 文件重命名模板变量

text

Copy

```
{student_id}    - 学号{name}          - 学生姓名{project}       - 项目名称{timestamp}     - 提交时间戳 (YYYYMMDD_HHMMSS){original_name} - 原始文件名（不含扩展名）{ext}           - 文件扩展名
```

示例：`{student_id}_{name}_{timestamp}{ext}` → `2024210853_张三_20250727_143025.pdf`

## 管理命令

bash

Copy

```bash
# 导入花名册（默认密码 123456）
python manage.py import_roster                                   # 使用命令内置名单
python manage.py import_roster --file 班级名单.et                 # 从文件导入
python manage.py import_roster --file 名单.xlsx --dry-run         # 只预览，不写库
python manage.py import_roster --file 名单.csv --update-names     # 已存在用户也按表更新姓名

# 备份和清理
python manage.py backup_and_cleanup
```

**花名册文件要求**

- 支持 `.csv` / `.tsv` / `.xls` / `.et`（WPS 表格）/ `.xlsx`，取第一个工作表
- 表头需含「学号」「姓名」两列（列顺序不限）；没有表头时按内容自动识别列
- 读取 `.xls` / `.et` 需要 `xlrd`，读取 `.xlsx` 需要 `openpyxl`（见 `requirements.txt`）
- 学号在表格里被存成数字（如 `2411001332.0`）也能正确转换，不会带小数点
- 已存在的学号默认跳过，不会覆盖；加 `--update-names` 才会用表里的姓名更新

## 测试

```bash
python manage.py test assignments
```

覆盖：分片上传（含超限校验、管理员代提交权限）、项目图片 / 附件上传与下载、
统计明细页与未交名单导出、备份失败记录与备份历史筛选、各页面渲染。

## 安全性说明

-   `DEBUG = True` 仅用于开发，生产环境应设置为 `False`
-   `SECRET_KEY` 应更换为强密钥
-   `ALLOWED_HOSTS = ["*"]` 在生产环境中应限制为特定域名