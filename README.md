# 作业提交系统 (Assignment Submission System)

一个基于 Django 的作业提交与管理系统，为教师和学生提供便捷的作业提交、审查和管理功能。

## 项目概述

该系统是一个功能完整的在线作业管理平台，支持学生提交作业、教师后台管理、批量下载、代提交等功能。

## 核心功能

### 学生功能
- **作业浏览** — 查看所有可用的作业项目及提交状态
- **文件提交** — 支持拖拽上传，自动重命名
- **申诉机制** — 提交时附带申诉说明
- **截止时间提醒** — 项目截止状态实时显示
- **账号管理** — 修改姓名、学号、密码

### 管理员功能
- **项目管理** — 创建、编辑、删除作业项目
- **用户管理** — 管理学生和管理员账户
- **提交审查** — 查看、筛选、下载所有学生提交
- **代提交作业** — 在提交记录中替学生上传文件
- **批量下载** — 多选提交记录，一键打包下载 ZIP
- **自动重命名** — 灵活的文件重命名规则配置
- **数据统计** — 各项目提交率统计
- **数据导出** — 导出提交记录为 CSV
- **系统设置** — 配置存储后端、备份策略等

## 项目结构

```
assignment_submission/
├── accounts/              # 用户认证与管理
│   ├── models.py         # 自定义 User 模型
│   ├── views.py          # 登录、注册、账号管理
│   ├── forms.py          # 认证表单
│   └── urls.py           # 用户相关路由
├── assignments/          # 作业核心功能
│   ├── models.py         # Project、Submission 模型
│   ├── views.py          # 作业提交逻辑
│   └── management/       # 自定义管理命令
├── admin_panel/          # 管理员面板
│   ├── views.py          # 仪表盘、设置、统计、代提交、批量下载
│   └── urls.py           # 管理面板路由
├── config/               # Django 项目配置
│   ├── settings.py       # 项目设置
│   ├── urls.py           # URL 主路由
│   └── wsgi.py           # WSGI 配置
├── templates/            # HTML 模板
├── static/               # 静态资源
├── media/                # 用户上传的文件
└── manage.py             # Django 管理脚本
```

## 数据模型

### User 用户模型
| 字段 | 说明 |
|------|------|
| student_id | 学号 (unique) |
| name | 姓名 |
| is_active | 是否激活 |
| is_staff | 是否为教师/管理员 |
| is_admin | 是否为超级管理员 |
| created_at | 注册时间 |

### Project 项目模型
| 字段 | 说明 |
|------|------|
| name | 项目名称 |
| slug | URL 标识符 (unique) |
| description | 项目描述 |
| is_active | 是否启用 |
| auto_rename | 是否启用自动重命名 |
| rename_pattern | 重命名规则模板 |
| storage_backend | 存储后端 (local/webdav/s3/smb) |
| deadline | 截止时间 |
| max_file_size | 最大文件大小 (MB) |
| allowed_extensions | 允许的文件类型 |
| created_at | 创建时间 |

### Submission 提交模型
| 字段 | 说明 |
|------|------|
| project | 关联项目 |
| student | 关联学生 |
| file | 上传文件 |
| original_filename | 原始文件名 |
| renamed_filename | 重命名后文件名 |
| file_size | 文件大小 (bytes) |
| auto_renamed | 是否自动重命名 |
| appeal | 申诉内容 |
| appeal_submitted | 是否已提交申诉 |
| submitted_at | 提交时间 |

## 环境配置

### 系统要求
- Python 3.8+
- Django 6.0+
- SQLite 3

### 依赖安装

```bash
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # Linux/Mac
pip install -r requirements.txt
```

### 依赖包
```
Django==6.0.7
django-crispy-forms==2.6
crispy-bootstrap5==2026.3
```

## 快速开始

```bash
cd assignment_submission
python manage.py migrate
python manage.py createsuperuser
python manage.py collectstatic --noinput
python manage.py runserver
```

访问地址：
- 学生页面：http://localhost:8000/
- Django 后台：http://localhost:8000/admin/
- 管理面板：http://localhost:8000/admin-panel/

## 用户指南

### 学生流程
1. **登录** — 使用学号和密码登录（默认密码 `123456`）
2. **浏览项目** — 首页查看所有可提交的作业项目
3. **提交作业** — 点击项目进入提交页，拖拽或选择文件上传
4. **查看状态** — 已提交的项目显示绿色标记
5. **账号管理** — 导航栏"账号管理"可修改姓名、学号、密码

### 管理员流程
1. **登录** — 使用管理员账户登录
2. **管理面板** — 导航栏进入管理后台
3. **创建项目** — 在"作业项目管理"中创建新项目
4. **提交记录** — 查看所有学生提交，支持筛选、下载、代上传
5. **批量下载** — 勾选多条记录，点击"下载所选项"打包为 ZIP
6. **数据统计** — 查看各项目提交率
7. **导出 CSV** — 一键导出所有提交记录

## 文件重命名模板变量

```
{student_id}    - 学号
{name}          - 学生姓名
{project}       - 项目名称
{timestamp}     - 提交时间戳 (YYYYMMDD_HHMMSS)
{original_name} - 原始文件名（不含扩展名）
{ext}           - 文件扩展名
```

示例：`{student_id}_{name}_{timestamp}{ext}` → `2024210853_姜冠宇_20250727_143025.pdf`

## 管理命令

```bash
# 导入花名册
python manage.py import_roster

# 备份和清理
python manage.py backup_and_cleanup
```

## 安全性说明

- `DEBUG = True` 仅用于开发，生产环境应设置为 `False`
- `SECRET_KEY` 应更换为强密钥
- `ALLOWED_HOSTS = ["*"]` 在生产环境中应限制为特定域名
