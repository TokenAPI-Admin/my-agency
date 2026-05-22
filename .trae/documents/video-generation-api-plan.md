# 短视频生成 API 服务 - 实施计划

## 项目目标

基于 ComfyUI workflow JSON，构建一个 API 服务。用户通过 API 传入「人物」、「衣服」、「动作」三个参数，服务自动修改 ComfyUI workflow 中的对应节点参数，调用 ComfyUI 生成视频，最终返回 MP4 视频文件。

---

## 技术栈

| 层级 | 技术选型 | 理由 |
|------|---------|------|
| Web 框架 | **Python FastAPI** | 异步高性能、自动生成 OpenAPI 文档、Pydantic 校验、AI 生态成熟 |
| ASGI 服务器 | **Uvicorn** | FastAPI 官方推荐 |
| HTTP 客户端 | **httpx** | 异步支持、连接池、ComfyUI API 调用 |
| 配置管理 | **pydantic-settings** | 环境变量 + .env 文件 |
| 包管理 | **uv / pip** | 现代 Python 包管理 |

---

## 项目结构

```
my-agency/
├── app/
│   ├── __init__.py
│   ├── main.py              # FastAPI 应用入口
│   ├── config.py             # 配置管理（ComfyUI 地址等）
│   ├── models/
│   │   ├── __init__.py
│   │   └── request.py        # Pydantic 请求/响应模型
│   ├── services/
│   │   ├── __init__.py
│   │   ├── workflow.py       # Workflow JSON 加载与参数替换
│   │   └── comfyui.py        # ComfyUI API 客户端
│   └── routers/
│       ├── __init__.py
│       └── video.py          # 视频生成路由
├── workflows/                # 存放 ComfyUI workflow JSON 文件
│   └── default.json          # 用户的 workflow（待提供）
├── outputs/                  # 生成的视频输出目录
├── requirements.txt
├── pyproject.toml
├── .env.example              # 环境变量示例
└── .gitignore
```

---

## 实施步骤

### 步骤 1：项目初始化

1. 创建项目目录结构
2. 编写 `pyproject.toml`，定义项目元信息和依赖
3. 编写 `requirements.txt`（FastAPI, uvicorn, httpx, pydantic-settings, python-multipart）
4. 编写 `.env.example`（COMFYUI_BASE_URL、输出目录等配置）
5. 编写 `.gitignore`

### 步骤 2：配置管理（`app/config.py`）

- 使用 `pydantic-settings` 的 `BaseSettings`
- 读取环境变量: `COMFYUI_BASE_URL`（ComfyUI 服务地址）、`WORKFLOW_DIR`（workflow JSON 目录）、`OUTPUT_DIR`（视频输出目录）、`POLL_INTERVAL`（轮询间隔）、`POLL_TIMEOUT`（超时时间）

### 步骤 3：请求/响应模型（`app/models/request.py`）

- `VideoGenerateRequest`: `person`(str)、`clothing`(str)、`action`(str)
- `VideoGenerateResponse`: `status`、`message`、`video_url`（或文件路径）
- `HealthResponse`: ComfyUI 连接状态

### 步骤 4：Workflow 服务（`app/services/workflow.py`）

核心模块，负责：
- 加载 workflow JSON 文件
- 查找并替换 workflow 中与「人物」、「衣服」、「动作」对应的节点参数
- 由于用户的 workflow 结构尚未确定，采用**灵活的参数映射机制**：
  - 支持通过配置文件定义「参数名 → workflow 节点 ID + 字段路径」的映射
  - 例如：`person` 映射到 `node_5.inputs.text`，`clothing` 映射到 `node_3.inputs.prompt` 等
- 返回修改后的 workflow JSON（可直接提交给 ComfyUI）

函数设计：
- `load_workflow(name: str) -> dict`
- `inject_parameters(workflow: dict, params: dict, mapping: dict) -> dict`
- `list_workflows() -> list[str]`

### 步骤 5：ComfyUI 客户端（`app/services/comfyui.py`）

封装与 ComfyUI API 的交互：

- `check_health() -> bool`：检查 ComfyUI 服务是否可达
- `submit_workflow(workflow_json: dict) -> str`：提交 workflow，返回 prompt_id
- `wait_for_completion(prompt_id: str, poll_interval: int, timeout: int) -> dict`：轮询等待任务完成，返回输出节点信息
- `download_output(output_info: dict, output_dir: str) -> str`：从 ComfyUI 下载生成的视频文件到本地
- `generate_video(workflow_json: dict) -> str`：组合上述步骤，同步完成全流程

### 步骤 6：路由层（`app/routers/video.py`）

API 端点设计：

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/health` | 健康检查，返回 ComfyUI 连接状态 |
| `POST` | `/api/v1/generate` | 生成视频（同步阻塞） |

`POST /api/v1/generate` 详细流程：
1. 校验请求参数
2. 加载指定的 workflow JSON
3. 将 `person`、`clothing`、`action` 注入 workflow
4. 提交到 ComfyUI 并等待完成
5. 下载生成的视频
6. 返回 `FileResponse`（MP4 视频文件）

### 步骤 7：FastAPI 应用入口（`app/main.py`）

- 创建 FastAPI 实例
- 注册路由
- 配置 CORS（允许跨域）
- 添加启动事件（检查 ComfyUI 连接）
- 自动生成 API 文档（Swagger UI 在 `/docs`）

---

## 工作流参数映射机制

由于用户的工作流 JSON 尚未提供，需要设计一套灵活的参数映射方案。在 `workflows/` 目录下为每个 workflow 放置一个同名的 `.mapping.json` 文件：

```json
{
  "workflow_file": "default.json",
  "parameter_mapping": {
    "person": {
      "node_id": "6",
      "field_path": "inputs.text",
      "template": "a {person} is wearing a {clothing}, doing {action}"
    },
    "clothing": {
      "node_id": "6",
      "field_path": "inputs.text",
      "template": "a {person} is wearing a {clothing}, doing {action}"
    },
    "action": {
      "node_id": "6",
      "field_path": "inputs.text",
      "template": "a {person} is wearing a {clothing}, doing {action}"
    }
  }
}
```

- 当多个参数映射到同一节点时，使用 `template` 字段组合所有参数
- 当参数映射到不同节点时，各自独立替换
- 用户提供 workflow 后，只需编写对应的 `.mapping.json` 即可

---

## 依赖项

```
fastapi>=0.110.0
uvicorn[standard]>=0.27.0
httpx>=0.27.0
pydantic>=2.6.0
pydantic-settings>=2.1.0
python-multipart>=0.0.9
```

---

## 待用户提供

1. **ComfyUI workflow JSON 文件** → 放到 `workflows/` 目录
2. **Workflow 中哪些节点对应「人物」、「衣服」、「动作」参数** → 用于编写 `.mapping.json`
3. **ComfyUI 服务地址** → 写入 `.env` 文件