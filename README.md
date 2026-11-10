# Muggle Word Cards — 后端

拍照 → OCR → 大模型提取六级生词 → 确认入库 → 间隔复习。
Python 3.11+ / FastAPI / SQLite，不需要装数据库、不需要服务器也能在本地跑。

## 目录

```
backend/
├── app/
│   ├── main.py       接口（auth / extract / import / groups / review / due / wanted / health）
│   ├── auth.py       密码 bcrypt 哈希 + JWT 签发/校验（HS256，30 天）
│   ├── db.py         SQLite 表结构 + 增删改查（标准库 sqlite3）
│   ├── ocr.py        本地 Tesseract OCR + 云端 OCR 预留桩
│   ├── llm.py        DeepSeek 客户端（Key 只从环境变量读）
│   ├── prompts.py    提取用的 system prompt（产品护城河，先改这里再测）
│   └── ratelimit.py  每 IP 每天限流，防刷 API 账单
├── requirements.txt
├── .env.example      复制成 .env 后填 Key
├── run.sh            本地一键启动
└── data/             SQLite 文件（自动生成，不用手动建）
```

## 本地运行

```bash
cd backend
cp .env.example .env          # 然后把 DEEPSEEK_API_KEY 填进去
./run.sh                      # 或手动：pip install -r requirements.txt && uvicorn app.main:app --reload --port 8000
```

打开 http://127.0.0.1:8000/docs 能看到交互式接口文档，直接在网页里试每个接口。

### DeepSeek Key 怎么拿

1. 去 https://platform.deepseek.com 注册账号；
2. 充值几块钱（一次提取约 3 分钱，10 块钱能测 300 多次）；
3. 左侧「API Keys」新建一个 Key，粘贴到 `.env` 的 `DEEPSEEK_API_KEY=` 后面。
4. 注意：Key 只放在你自己电脑/服务器的 `.env` 里，绝不写进代码、绝不发给别人、绝不让前端传。

### OCR 需要另外装 Tesseract（二选一）

- macOS：`brew install tesseract`
- Ubuntu/Debian：`sudo apt install tesseract-ocr`

不装也能启动后端，但 `/api/extract` 会明确报错。以后想换识别率更高的云端 OCR，
去 `app/ocr.py` 实现 `ocr_with_cloud()` 即可，主流程不用改。

## 接口速查

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/health` | 健康检查（公开） |
| POST | `/api/auth/register` | 注册：`{"email","password","name?"}` → 201 `{"token","user"}`；邮箱已存在 409，邮箱格式错/密码 < 6 位 400 |
| POST | `/api/auth/login` | 登录：`{"email","password"}` → `{"token","user"}`；凭证错误 401 |
| GET | `/api/auth/me` | 当前登录用户（需 Bearer token） |
| POST | `/api/extract` | 传 1–3 张图片（multipart），返回草稿（不保存）。每天每 IP 限 20 次 |
| POST | `/api/import` | 传确认过的草稿 JSON，存成新分组；已在库里的词自动跳过 |
| GET | `/api/groups` | 分组列表（含每组词数） |
| GET | `/api/groups/{id}/words` | 某分组下的全部单词 |
| POST | `/api/review/{word_id}` | `{"known": true/false, "pass_number": 1/2/3（可选）}`，按阶梯排期 |
| GET | `/api/review/due` | 今天到期的词 |
| GET | `/api/wanted` | 通缉名单（连续答错 ≥ 2 次） |

> 除 `/health` 外，全部 `/api/*` 接口都要在请求头带 `Authorization: Bearer <token>`，
> 没带或 token 无效/过期返回 401。数据按登录用户隔离：每个用户只能看到自己的
> 分组和单词，读别人的分组/单词返回 404。

## 账号系统

- 注册：邮箱唯一（重复 409），密码 bcrypt 哈希入库，明文绝不保存；
  注册成功直接返回 token，前端存下来（localStorage）即可，后续请求都带上它。
- 登录：邮箱或密码错都返回同一个 401，不透露是哪一步错了。
- Token：JWT（HS256），有效期 30 天，过期重新登录。payload 里只有用户 id。
- 密钥 `JWT_SECRET`：签发 token 用的。本地开发不填也能跑（启动时随机生成，
  但重启后旧 token 全部失效）；**Render 上必须配固定值**，否则每次重新部署
  都会把所有用户踢下线。生成一个：
  ```bash
  python -c "import secrets; print(secrets.token_urlsafe(32))"
  ```
  本地：写进 `.env` 的 `JWT_SECRET=`；Render：服务面板左侧 Environment 里添加
  `JWT_SECRET`，保存后服务自动重启。
- 老库迁移：账号系统上线前建的数据库，启动时自动给 users 表补 `email` /
  `password_hash` 两列，已有数据不用删。老默认用户（id=1）没有邮箱密码，
  不能登录，只能用新注册的账号走接口；它名下的旧数据不受影响。

### 复习规则（和主版本一致）

- 阶梯：+1 / +2 / +4 / +7 / +15 / +30 天。
- 日常复习：答对 → 阶梯进一位，连错清零；答错 → 连错 +1、阶梯归零、明天再来。
- 三遍筛选（新词当晚）：第 1 遍认识 → +4 天；第 2/3 遍救回来 → +2 天；三遍都失败 → 明天。
- 连错 ≥ 2 次的词进通缉名单。

## 部署上线（Render 免费版，2026-11-10 已配好）

仓库里已有 `Dockerfile` + `render.yaml`，按下面走：

1. **传代码到 GitHub**：新建一个仓库（名字随意，如 `muggle-word-cards-api`），
   把 `backend/` 文件夹**里面**的文件传上去（Dockerfile、render.yaml、requirements.txt、
   app/ 等直接放在仓库根目录，不要套一层 backend/ 文件夹）。
   不用命令行也行：GitHub 网页里点 Add file → Upload files，直接把文件夹里的文件拖进去。
   注意：`.env` 里有你的 Key，**绝不能传**（`.gitignore` 和 `.dockerignore` 已经排除了它，传之前再检查一遍）。
2. **Render 注册**：去 https://render.com ，用 GitHub 账号直接登录（不用另外注册）。
3. **新建服务**：New → Web Service → 选刚传的仓库 → Render 会自动读到 `render.yaml`，
   确认计划是 Free，点 Create Web Service，等它构建（第一次几分钟）。
4. **填 Key**：服务建好后，在 Render 面板左侧 Environment 里加两条：
   `DEEPSEEK_API_KEY`（值就是你的 DeepSeek Key）和 `JWT_SECRET`（账号系统的
   签名密钥，生成方法见上面「账号系统」一节；不配的话每次部署都会把所有用户
   踢下线）。保存后服务会自动重启。
5. **验证**：打开 `https://<你的服务名>.onrender.com/health`，看到 `{"status":"ok"}` 就是上线了。
   拿到地址后把地址发给 Vivian，她把试用版的拍照按钮接到这个接口上，拍照提取就真跑起来了。

### 免费版的两个限制（先知道，免得到时候懵）

- **15 分钟没人访问会休眠**，下一次访问要等 30–60 秒唤醒；平时自己用没感觉，别人第一次点可能会慢一次。
- **数据库是临时的**：免费版没有持久磁盘，服务重启/重新部署后 SQLite 里的数据会清空。
  所以部署版先只用来跑拍照提取演示，真正的 331 张卡还留在主版本里，别往部署版里导正式数据。
  以后想数据永久保存，再加 Render 持久磁盘（付费）或换 Postgres。

### 域名以后再接

现在先用 Render 给的免费地址（`xxx.onrender.com`）跑通全流程；等正式上线了，
再买自己的域名，在 Render 面板里 Custom Domain 一栏绑定过去就行，不用重部署。

## 还没做的事（有意的）

- **找回密码/改密码**：注册登录已做，忘密码只能在数据库里手动处理，以后再加邮箱验证。
- **前端页面**：这个仓库只有后端 API。前端（试用版静态页在 `muggle-word-cards-shi-yong-ban`）
  调这些接口即可，部署时把前端静态文件和后端放同一个域名下。
- **云端 OCR**：`app/ocr.py` 里留了桩，没实现。
- **Redis 限流**：现在限流是内存版，重启清零；用户量大了再换 Redis。
