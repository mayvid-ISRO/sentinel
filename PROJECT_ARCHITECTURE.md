# IRIS - Intelligent Reasoning Infrastructure System
## Project Overview & Architecture Document

---

## Executive Summary

IRIS is an **air-gapped, multi-modal intelligent agent system** that automates complex desktop tasks through AI-driven orchestration. The system combines LLM-based reasoning with browser automation, system-level control, and NetApp ONTAP storage management capabilities.

### Key Capabilities
- **Web-based UI** with real-time task monitoring and history management
- **Multi-step agent loop** with plan generation and execution
- **Air-gapped operation** - all processing occurs locally with no external data transmission
- **Cross-platform tools** for browser automation (Playwright), system control (PyAutoGUI), and enterprise storage (NetApp ONTAP SDK)

---

## High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                              USER INTERFACE                                     │
│  ┌─────────────────────────────────────────────────────────────────────────┐   │
│  │  Browser (Chrome)                                                       │   │
│  │  - Single-page application (index.html)                                 │   │
│  │  - Glassmorphism UI with CSS animations                                 │   │
│  │  - WebSocket client for real-time updates                               │   │
│  └─────────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────────┘
                                        │
                                        ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                           FASTAPI BACKEND                                       │
│  ┌─────────────────────────────────────────────────────────────────────────┐   │
│  │  REST API Endpoints (Port 8000)                                         │   │
│  │  - POST /api/tasks   - Submit new task                                  │   │
│  │  - GET  /api/tasks   - List all tasks                                   │   │
│  │  - PATCH /api/tasks/{id} - Update task (pin, tags)                      │   │
│  │  - DELETE /api/tasks/{id} - Delete task                                 │   │
│  │  - GET  /api/tools   - List available tools                             │   │
│  │  - GET  /api/health  - System health check                              │   │
│  │  - WS   /api/ws/{id} - Real-time WebSocket streaming                    │   │
│  │                                                                         │   │
│  │  SQLite Database (logs/iris_tasks.db)                                   │   │
│  │  - Task persistence with WAL mode                                       │   │
│  │  - Fields: id, task, status, steps_json, pinned, tags                   │   │
│  └─────────────────────────────────────────────────────────────────────────┘   │
│                                        │                                       │
│  ┌─────────────────────────────────────▼───────────────────────────────────┐   │
│  │  Background Task Runner                                                 │   │
│  │  - Spawns async run_task() for each submission                          │   │
│  │  - Streams steps via WebSocket                                          │   │
│  │  - Updates task status on completion                                    │   │
│  └─────────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────────┘
                                        │
                                        ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                            AGENT SYSTEM                                         │
│  ┌─────────────────────────────────────────────────────────────────────────┐   │
│  │  1. Pre-Prompt Generator (pre_prompt.py)                               │   │
│  │     - Breaks complex task into sub-tasks                                │   │
│  │     - Returns structured task plan                                       │   │
│  └─────────────────────────────────────────────────────────────────────────┘   │
│                                        │                                       │
│  ┌─────────────────────────────────────▼───────────────────────────────────┐   │
│  │  2. Agent Loop (agent.py)                                               │   │
│  │     ┌─────────────────────────────────────────────────────────────────┐  │   │
│  │     │  Step 1: Generate plan using LLM                              │  │   │
│  │     │  Step 2: Build prompt with tools schema                       │  │   │
│  │     │  Step 3: Call LLM for next action                             │  │   │
│  │     │  Step 4: Parse JSON response                                  │  │   │
│  │     │  Step 5: Validate action structure                            │  │   │
│  │     │  Step 6: Dispatch to appropriate tool                         │  │   │
│  │     │  Step 7: Record observation and repeat                        │  │   │
│  │     └─────────────────────────────────────────────────────────────────┘  │   │
│  │     - Stops on "done" tool or error/max steps                          │   │
│  └─────────────────────────────────────────────────────────────────────────┘   │
│                                        │                                       │
│  ┌─────────────────────────────────────▼───────────────────────────────────┐   │
│  │  3. LLM Interface (llm.py)                                              │   │
│  │     - Ollama-compatible API endpoint                                   │   │
│  │     - Configurable model (currently: gpt-oss:20b)                       │   │
│  │     - Prompt + completion format                                       │   │
│  └─────────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────────┘
                                        │
                                        ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                              TOOLS SYSTEM                                       │
│  ┌─────────────────────────────────────────────────────────────────────────┐   │
│  │  Input Tools (tools/input/)                                             │   │
│  │  - click_tool: Mouse click at screen coordinates (PyAutoGUI)            │   │
│  │  - type_tool: Keyboard input (PyAutoGUI)                                │   │
│  │                                                                         │   │
│  │  System Tools (tools/system/)                                           │   │
│  │  - open_app_tool: Launch Windows applications (subprocess)              │   │
│  │  - run_terminal_command_tool: Execute shell commands (safety-checked)   │   │
│  │  - ssh_connect_tool: SSH to remote servers                              │   │
│  │                                                                         │   │
│  │  Browser Tools (tools/browser/)                                         │   │
│  │  - browser_open: Open URLs in Chrome (Playwright)                       │   │
│  │  - browser_click: Click elements on page                                │   │
│  │  - browser_type: Input text to web forms                                │   │
│  │  - browser_scroll: Scroll page vertically                               │   │
│  │  - browser_extract: Extract DOM content                                 │   │
│  │  - browser_close: Close browser session                                 │   │
│  │                                                                         │   │
│  │  Vision Tools (tools/vision/)                                           │   │
│  │  - take_screenshot_tool: Capture screen (PyAutoGUI + PIL)               │   │
│  │                                                                         │   │
│  │  NetApp Tools (tools/netapp/)                                           │   │
│  │  - list_volumes: List NetApp volumes                                    │   │
│  │  - create_volume: Provision new volume                                  │   │
│  │  - delete_volume: Remove volume                                         │   │
│  │  - patch_volume: Update volume properties                               │   │
│  │  - list_qtrees: List qtrees                                             │   │
│  │  - create_qtree: Create qtree structure                                 │   │
│  │  - delete_qtree: Remove qtree                                           │   │
│  │  - create_quota: Set storage quotas                                     │   │
│  │  - create_cifs_share: Create SMB shares                                 │   │
│  └─────────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## Component Details

### 1. Frontend (frontend/index.html)

**Technology Stack:**
- Pure HTML5, CSS3, vanilla JavaScript
- No external dependencies (air-gapped requirement)
- Google Fonts: 'Share Tech Mono' and 'Syne'

**Key Features:**
- **Glassmorphism UI**: Backdrop-filter blur effects with semi-transparent backgrounds
- **Animated Background**: CSS grid pattern with parallax pulse and conic rotation
- **Real-time Updates**: WebSocket client for streaming task progress
- **Task Management**:
  - Filter by status (ALL, RUNNING, DONE, FAILED)
  - Pin/favorite important tasks
  - Search tasks by keyword
  - Edit and delete tasks
- **Visual Feedback**:
  - Confetti celebration on task completion
  - Toast notifications system
  - Status LEDs (color-coded: blue=pending, green=done, red=failed)
- **Timeline Visualization**: Gantt-style step tracking

**CSS Architecture:**
```css
Variables: --iris (cyan), --amber, --green, --red, --purple
Animations: gridPulse, conicSpin, itemShimmer, hologram
Components: glass-btn, cyber-border, task-item, toast-container
```

### 2. Backend (backend/main.py)

**Technology Stack:**
- FastAPI (Python 3.10+)
- SQLite with WAL mode
- Uvicorn ASGI server

**Database Schema:**
```sql
CREATE TABLE tasks (
    id TEXT PRIMARY KEY,
    task TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    max_steps INTEGER DEFAULT 10,
    created_at TEXT NOT NULL,
    finished_at TEXT,
    error TEXT,
    steps_json TEXT,
    pinned INTEGER DEFAULT 0,
    tags TEXT
)
```

**WebSocket Protocol:**
```json
// Status updates
{"type": "status", "status": "pending|running|done|failed"}

// Step execution
{"type": "step", "step": 1, "tool": "tool_name", "args": {}, "result": "output", "status": "ok"}

// Task completion
{"type": "status", "status": "done"}
{"type": "error", "message": "error description"}
```

**API Endpoints:**
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | /api/tasks | Submit new task |
| GET | /api/tasks | List tasks (with status filter) |
| GET | /api/tasks/{id} | Get task details |
| PATCH | /api/tasks/{id} | Update task (task, pinned, tags) |
| DELETE | /api/tasks/{id} | Delete task |
| GET | /api/tools | List available tools |
| GET | /api/health | Health check |
| WS | /api/ws/{id} | Real-time streaming |

### 3. Agent System

**Agent Loop (agent/agent.py):**

```
┌───────────────────────────────────────────────────────────────────────────────┐
│                         AGENT EXECUTION LOOP                                  │
├───────────────────────────────────────────────────────────────────────────────┤
│                                                                               │
│  1. PRE-PROMPT PHASE                                                          │
│     Input: User task description                                              │
│     Output: Structured task plan (array of sub-tasks)                         │
│     LLM Call: build_pre_prompt() → call_llm()                                │
│                                                                               │
│  2. EXECUTION LOOP (max_steps iterations)                                     │
│     ┌─────────────────────────────────────────────────────────────────────┐   │
│     │  a. Build Prompt                                                    │   │
│     │     - User task                                                     │   │
│     │     - Execution plan                                                │   │
│     │     - Tool schema (from tools/__init__.py)                         │   │
│     │     - Previous observations                                         │   │
│     │                                                                     │   │
│     │  b. Call LLM                                                        │   │
│     │     - prompt.py builds JSON schema prompt                          │   │
│     │     - call_llm() → raw response                                    │   │
│     │                                                                     │   │
│     │  c. Parse Response                                                  │   │
│     │     - Extract JSON from markdown blocks or raw string              │   │
│     │     - Validate tool name and args                                  │   │
│     │                                                                     │   │
│     │  d. Execute Tool                                                    │   │
│     │     - dispatch() maps tool name → Tool.func()                      │   │
│     │     - Returns result or raises exception                           │   │
│     │                                                                     │   │
│     │  e. Record & Repeat                                                 │   │
│     │     - Store step in steps_list                                     │   │
│     │     - Push to WebSocket clients                                    │   │
│     │     - Break on "done" tool or error                                │   │
│     └─────────────────────────────────────────────────────────────────────┘   │
│                                                                               │
│  3. FINALIZATION                                                              │
│     - Determine final status (done/failed)                                    │
│     - Update database with steps_json                                         │
│     - Close WebSocket connection                                              │
└───────────────────────────────────────────────────────────────────────────────┘
```

**Tool Registration (tools/__init__.py):**
```python
TOOLS = {
    "click_tool": click_tool,
    "type_tool": type_tool,
    "open_app_tool": open_app_tool,
    # ... all registered tools
}
```

### 4. LLM Interface (agent/llm.py)

**Configuration:**
```python
url = "http://10.61.241.249:11434/api/generate"
model = "gpt-oss:20b"  # Configurable
```

**Payload Format:**
```json
{
  "model": "gpt-oss:20b",
  "prompt": "<full prompt text>",
  "stream": false
}
```

### 5. Tools System

**Base Tool Class (tools/base.py):**
```python
class Tool:
    def __init__(self, name, description, args_schema, func):
        self.name = name
        self.description = description
        self.args_schema = args_schema  # JSON schema
        self.func = func
    
    def run(self, args):
        return self.func(**args)
```

**Safety Checker (tools/safety.py):**
- Block patterns: `rm -rf /`, `format C:`, shutdown commands
- Allowed paths: C:\Users, C:\Temp, /tmp, /home
- Forbidden paths: C:\Windows\System32, /etc, /bin

**Browser Session Management (tools/browser/session.py):**
- Playwright-based Chrome automation
- Non-headless mode for air-gapped environment
- Singleton SESSION pattern for reusability

**NetApp Integration (tools/netapp/netapp.py):**
- Uses `netapp_ontap` SDK
- Auto-resolution for SVM/aggregate selection
- Connection helper with authentication
- CRUD operations for volumes, qtrees, quotas, CIFS shares

---

## Data Flow

### Task Submission Flow
```
User submits task
    │
    ├─→ POST /api/tasks
    │   ├─→ Generate UUID (task_id)
    │   ├─→ INSERT INTO tasks (id, task, status='pending')
    │   ├─→ BackgroundTasks.add_task(run_task)
    │   └─→ Return {"id": task_id, "status": "pending"}
    │
    ├─→ run_task() in background
    │   ├─→ Set status='running'
    │   ├─→ Agent loop executes
    │   │   ├─→ Pre-prompt generation
    │   │   ├─→ Step-by-step execution
    │   │   └─→ Push WebSocket updates
    │   ├─→ Update status='done' or 'failed'
    │   └─→ Store steps_json
    │
    └─→ WebSocket clients receive real-time updates
        ├─→ UI updates step cards
        ├─→ Shows tool name, args, result
        └─→ Final status toast + confetti
```

### Task History Flow
```
GET /api/tasks
    │
    ├─→ SELECT * FROM tasks ORDER BY created_at DESC
    │
    ├─→ For each row:
    │   ├─→ Convert to dict via task_to_dict()
    │   ├─→ Parse steps_json with json.loads()
    │   ├─→ Convert pinned INTEGER to bool
    │   └─→ Convert tags TEXT to list
    │
    └─→ Return JSON array
```

---

## Technology Stack Summary

| Layer | Technology | Purpose |
|-------|------------|---------|
| Frontend | HTML5, CSS3, JavaScript | Single-page application |
| Backend | FastAPI, Uvicorn | REST + WebSocket server |
| Database | SQLite (WAL mode) | Task persistence |
| LLM | Ollama-compatible API | AI reasoning |
| Browser Automation | Playwright (Chromium) | Web interaction |
| System Control | PyAutoGUI | Mouse/keyboard simulation |
| Storage | NetApp ONTAP SDK | Enterprise storage management |
| Safety | Python re module | Command validation |

---

## Deployment Architecture

### Air-Gapped Environment
- All components run locally
- No external HTTP requests except to configured LLM endpoint
- No third-party CDNs (fonts loaded via preconnect)

### Typical Deployment
```
┌───────────────────────────────────────────────────────────────────────────────┐
│                          WORKSTATION / SERVER                                 │
│                                                                               │
│  ┌──────────────────┐  ┌──────────────────┐  ┌─────────────────────────┐     │
│  │  Frontend        │  │  Backend         │  │  LLM Endpoint           │     │
│  │  python -m http  │  │  uvicorn         │  │  Ollama API             │     │
│  │  port 3000       │──│  port 8000       │──│  (configured in llm.py) │     │
│  └──────────────────┘  └──────────────────┘  └─────────────────────────┘     │
│         │                       │                                             │
│         │                       ▼                                             │
│         │              ┌──────────────────┐                                   │
│         │              │  SQLite DB       │                                   │
│         │              │  logs/iris.db    │                                   │
│         │              └──────────────────┘                                   │
│         │                                                                       │
│         ▼                                                                       │
│  ┌──────────────────┐                                                         │
│  │  User Browser    │                                                         │
│  │  http://localhost│                                                         │
│  └──────────────────┘                                                         │
└───────────────────────────────────────────────────────────────────────────────┘
```

---

## Key Design Decisions

### 1. Air-Gapped Architecture
- **Rationale**: Security requirements for sensitive data
- **Implementation**: All processing in local Python process

### 2. WebSocket for Real-Time Updates
- **Alternative**: Polling every X seconds
- **Why WebSocket**: Lower latency, reduced HTTP overhead

### 3. SQLite with WAL Mode
- **Why not PostgreSQL/MySQL**: Simpler deployment, single file
- **WAL mode**: Allows concurrent reads during writes

### 4. Agent Loop with Plan Generation
- **Alternative**: Direct execution without pre-planning
- **Why pre-prompt**: Better task decomposition, handles complex multi-step tasks

### 5. Tool-Based Architecture
- **Why**: Extensible, testable, reusable components
- **Pattern**: Each tool is a self-contained function with schema

### 6. JSON-Only LLM Communication
- **Why**: Structured parsing, type safety
- **Enforced**: parser validates JSON structure before dispatch

---

## Future Enhancements

### Planned Features (from codebase)
- [ ] Drag-to-reorder tasks
- [ ] Task progress bars
- [ ] Status LED indicators
- [ ] Activity feed with timestamps
- [ ] Tags management
- [ ] Search functionality

### Suggested Enhancements
1. **Task Templates**: Reusable task templates for common operations
2. **Role-Based Access**: User authentication and permissions
3. **Task History Analytics**: Success rate, average duration by tool
4. **Export Options**: Export task history as JSON/PDF
5. **Configuration UI**: Web-based tool configuration
6. **Scheduled Tasks**: Cron-based task scheduling

---

## Conclusion

IRIS represents a sophisticated, air-gapped agent system that combines modern web technologies with powerful automation capabilities. The modular architecture ensures extensibility while the air-gapped design meets strict security requirements. The system is production-ready with comprehensive error handling, real-time feedback, and persistent storage.

---

## Detailed Component Architecture

### 1. Agent Core (agent/)

#### Agent Loop Architecture
```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        AGENT EXECUTION FLOW                                 │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│ Phase 1: Pre-Prompt Generation (Task Decomposition)                         │
│  ├─ Input: User task description                                            │
│  ├─ LLM Call: build_pre_prompt() → call_llm()                               │ 
│  ├─ Output: Array of sub-tasks (structured plan)                            │
│  └─ Storage: Plan persisted in session_state                                │
│                                                                             │
│ Phase 2: Execution Loop (Step-by-Step)                                      │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │  For each step (max_steps limit):                                     │  │
│  │  ┌─ Build Prompt                                                      │  │
│  │  │   - User task + execution plan                                     │  │
│  │  │   - Tool schema (TOOLS dictionary)                                 │  │
│  │  │   - Previous observations/history                                  │  │
│  │  └─ Call LLM → JSON response                                          │  │
│  │                                                                       │  │
│  │  ┌─ Parse & Validate                                                  │  │
│  │  │   - Extract JSON from response                                     │  │
│  │  │   - Validate tool name exists in TOOLS                             │  │
│  │  │   - Validate args match tool schema                                │  │
│  │  └─ Execute Tool                                                      │  │
│  │        - dispatch() → Tool.func(**args)                               │  │
│  │        - Return result or exception                                   │  │
│  │                                                                       │  │
│  │  ┌─ Record & Feedforward                                              │  │
│  │  │   - Append observation to history                                  │  │
│  │  │   - Push to WebSocket clients                                      │  │
│  │  │   - Break on "done" tool or error                                  │  │
│  │  └─ Increment step counter                                            │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
│  Phase 3: Finalization                                                      │
│  ├─ Determine final status (done/failed)                                    │
│  ├─ Store steps_json in database                                            │
│  └─ Close WebSocket connection                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

#### State Management
- **Session State**: In-memory dictionary storing:
  - `netapp`: Connection credentials (cluster, api_user, api_pass, svm_name)
  - `selected_volume`: User selection from interactive prompts
  - `execution_plan`: Current task decomposition
  - `history`: Previous observations for context

- **Persistence**: SQLite database with WAL mode for:
  - Task metadata (id, task, status, created_at, finished_at)
  - Step results (steps_json)
  - Task tags and pinned status

### 2. LLM Interface Layer (agent/llm.py)

#### Design Patterns
- **Thin Wrapper Pattern**: Minimal abstraction over Ollama-compatible API
- **Configuration Flexibility**: Environment variable or direct config override

#### Error Handling Strategy
| Error Type | Recovery Strategy |
|------------|-------------------|
| Network timeout | Retry with exponential backoff |
| JSON parse failure | Fallback parsing (markdown blocks, raw extraction) |
| Tool not found | Return error observation, continue loop |
| Tool execution exception | Catch, wrap in error message, continue |

### 3. Tools System

#### Base Tool Interface
```python
class Tool:
    name: str                    # Unique identifier
    description: str            # LLM context for tool selection
    args_schema: Dict           # JSON schema for validation
    func: Callable              # Implementation function
    + run(args: Dict) → Any     # Execute with validation
```

#### Tool Registration Pattern
```python
# Step 1: Implement function with optional _session parameter
def my_tool_func(arg1: str, arg2: int, _session: Optional[Dict] = None) → Dict:
    if _session:
        # Use persisted session data
        cred = _session.get("credential")
    # ... implementation ...

# Step 2: Wrap in Tool instance
my_tool = Tool(
    name="my_tool_name",
    description="What this tool does",
    args_schema={"arg1": "string", "arg2": "integer", "_session": "dict (optional)"},
    func=my_tool_func
)

# Step 3: Export in __init__.py
TOOLS = {..., "my_tool_name": my_tool}
```

#### Tools Namespace Organization
| Namespace | Purpose | Examples |
|-----------|---------|----------|
| `input/` | Direct user interaction | click, type, mouse_move |
| `system/` | OS-level operations | open_app, run_command, ssh |
| `browser/` | Web automation | browser_open, browser_click, browser_extract |
| `vision/` | Image processing | take_screenshot, OCR integration |
| `netapp/` | Enterprise storage | volumes, qtrees, quotas, CIFS shares |

### 4. Backend Layer (backend/main.py)

#### FastAPI Extensions
- **CORSMiddleware**: Configured for local development
- **BackgroundTasks**: For async task execution
- **WebSocket endpoint**: Real-time step streaming

#### Database Access Layer
```python
# Task CRUD operations
- create_task(): INSERT with UUID generation
- get_tasks(): SELECT with status filter
- get_task_by_id(): SELECT by primary key
- update_task(): UPDATE with partial fields
- delete_task(): DELETE by ID
```

### 5. Frontend Layer (frontend/index.html)

#### Component Architecture
```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           UI COMPONENT TREE                                │
├─────────────────────────────────────────────────────────────────────────────┤
│  App (root)                                                                │
│  ├─ Header (title, status indicator)                                       │
│  ├─ Task Input Section                                                     │
│  │  ├─ Task description textarea                                           │
│  │  └─ Submit button                                                       │
│  ├─ Task List Container                                                    │
│  │  ├─ Filter controls (status, tags)                                      │
│  │  └─ Task List                                                           │
│  │     ├─ Task Item (per task)                                             │
│  │     │  ├─ Task metadata (id, created_at, status)                       │
│  │     │  ├─ Task description                                              │
│  │     │  ├─ Timeline visualization                                        │
│  │     │  └─ Action buttons (pin, edit, delete, retry)                    │
│  │     └─ Loading skeleton (when running)                                 │
│  └─ Toast Container                                                        │
│     └─ Notification (success/error/info)                                   │
└─────────────────────────────────────────────────────────────────────────────┘
```

#### State Management (Client-Side)
- **Task List**: Array of task objects
- **Active Task**: Currently viewed task details
- **Connection Status**: WebSocket connection state
- **Filters**: Applied filters for status/tag search

---

## RAG (Retrieval-Augmented Generation) Integration

### Future Architecture Vision

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                        RAG-INTEGRATED AGENT ARCHITECTURE                     │
├──────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌─── User Query ──────────────────────────────────────┐                    │
│  │                                                     │                    │
│  │  ┌───────────┐    ┌──────────────┐    ┌──────────┐ │                    │
│  │  │  Query    │    │  Retrieval   │    │  Prompt  │ │                    │
│  │  │  Encoder  │───▶│  Component   │───▶│  Builder │ │                    │
│  │  └───────────┘    └──────────────┘    └──────────┘ │                    │
│  │         │              │                   │        │                    │
│  │         ▼              ▼                   ▼        │                    │
│  │  ┌───────────┐    ┌──────────────┐    ┌──────────┐ │                    │
│  │  │  Vector   │    │  Vector DB   │    │  Context │ │                    │
│  │  │  Embedding│    │  (Chroma,    │    │  Augment │ │                    │
│  │  │  Model    │    │  Pinecone,   │    │  Prompt  │ │                    │
│  │  └───────────┘    │  FAISS)      │    └──────────┘ │                    │
│  │                     └──────────────┘               │                    │
│  │                            │                       │                    │
│  │                            ▼                       │                    │
│  │                     ┌──────────────┐               │                    │
│  │                     │  LLM         │◀───────────────┘                    │
│  │                     │  Generation  │                                    │
│  │                     └──────────────┘                                    │
│  │                            │                                            │
│  │                            ▼                                            │
│  │                     ┌──────────────┐                                    │
│  │                     │  Final       │                                    │
│  │                     │  Response    │                                    │
│  │                     └──────────────┘                                    │
│  └──────────────────────────────────────────────────────────────────────────┘
│                                                                              │
└──────────────────────────────────────────────────────────────────────────────┘
```

### RAG Implementation Plan

#### Phase 1: Knowledge Base Setup
1. **Document Ingestion Pipeline**
   ```
   ┌─────────────┐    ┌──────────────┐    ┌─────────────┐    ┌─────────────┐
   │   Source    │───▶│   Parser     │───▶│  Chunker    │───▶│   Vectorize │
   │ (PDF, Doc,  │    │ (Unstructured│    │ (Sliding    │    │  (Embedding │
   │ Web, etc)   │    │  library)    │    │  window)    │    │  model)     │
   └─────────────┘    └──────────────┘    └─────────────┘    └─────────────┘
          │                   │                  │                   │
          ▼                   ▼                  ▼                   ▼
   ┌─────────────┐    ┌──────────────┐    ┌─────────────┐    ┌─────────────┐
   │ Text chunks │    │ Document     │    │ Overlap     │    │ Vector      │
   │ with        │    │ metadata     │    │ chunks      │    │ embeddings  │
   │ metadata    │    │ (source,     │    │             │    │ stored in   │
   │             │    │  page, etc)  │    │             │    │  vector DB  │
   └─────────────┘    └──────────────┘    └─────────────┘    └─────────────┘
   ```

2. **Vector Database Options**
   - **Chroma**: Lightweight, embedded, Python-friendly
   - **Pinecone**: Cloud-hosted, scalable
   - **FAISS**: Facebook AI Similarity Search (local, fast)
   - **Qdrant**: High-performance, Rust-based, filters

#### Phase 2: Retrieval Component
```python
# Pseudocode for retrieval
def retrieve_context(query: str, top_k: int = 5) -> List[RetrievedDoc]:
    # 1. Embed query
    query_vector = embedder.encode(query)
    
    # 2. Search vector DB
    results = vector_db.search(
        query_vector=query_vector,
        top_k=top_k,
        filter={"document_type": "irrelevant_document"}
    )
    
    # 3. Re-rank results (optional: using cross-encoder)
    re_ranked = reranker.rerank(query, results)
    
    return re_ranked
```

#### Phase 3: Prompt Augmentation
```python
def build_rag_prompt(user_query: str, retrieved_docs: List[RetrievedDoc]) -> str:
    context = "\n\n".join([doc.content for doc in retrieved_docs])
    
    prompt = f"""
    You are an AI agent with access to contextual knowledge.
    
    Contextual Information:
    {context}
    
    User Query: {user_query}
    
    Instructions:
    1. Use the provided context to inform your response
    2. Cite sources when applicable: [Source: doc_id]
    3. If context is insufficient, state what information is missing
    4. Do not hallucinate facts outside the context
    """
    return prompt
```

#### Integration Points in IRIS

1. **Pre-Prompt Phase Enhancement**
   - Before generating task plan, retrieve relevant documentation
   - Example: For "list NetApp volumes", retrieve storage API docs

2. **Execution Phase Enhancement**
   - During tool execution, retrieve troubleshooting guides
   - On error, retrieve similar past failures and solutions

3. **Prompt Engineering**
   - Dynamic prompt building with context window management
   - Token-aware chunking for large knowledge bases

### RAG Architecture在IRIS中的应用

#### Knowledge Sources
| Source Type | Format | Use Case |
|-------------|--------|----------|
| API Documentation | Markdown/PDF | Tool usage reference |
| Error Repositories | JSON/DB | Troubleshooting patterns |
| User Manuals | Markdown | Task execution guidance |
| Past Tasks | JSON (history) | Learning from execution history |

#### RAG-Enabled Tools
```python
# Example: Enhanced tool with RAG context
def netapp Troubleshoot_connection(
    cluster: str,
    _session: Optional[Dict] = None
) -> Dict:
    # 1. Build query from failure symptoms
    query = f"NetApp connection error cluster={cluster}"
    
    # 2. Retrieve relevant docs
    docs = rag_retrieve(query, top_k=3)
    
    # 3. Build augmented prompt
    prompt = rag_build_prompt(query, docs)
    
    # 4. LLM analyzes error + context
    analysis = call_llm(prompt)
    
    return {
        "status": "retrieved",
        "docs_referenced": len(docs),
        "analysis": analysis
    }
```

---

## Event-Driven Architecture

### Current State: Intent-Driven

**Characteristics:**
- User explicitly provides task description
- Agent executes based on direct intent
- Pull-based execution model

```
User Input → API → Agent Loop → Execution → Result
```

**Pros:**
- Predictable execution flow
- Easy to debug and trace
- Clear ownership of tasks

**Cons:**
- Requires user awareness of all tasks
- Reactive rather than proactive
- Human-in-the-loop bottleneck

### Future State: Event-Driven

**Characteristics:**
- Events trigger agent actions automatically
- Data center events (logs, metrics, alerts) feed into agent
- Push-based execution model

```
Event Source → Event Queue → Agent Event Handler → Decision → Action
```

**Pros:**
- Proactive problem resolution
- Reduced human intervention
- Real-time response to events

**Cons:**
- More complex debugging
- Event loop handling requirements
- Potential for event storms

### Event-Driven Architecture Design

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                    EVENT-DRIVEN ARCHITECTURE                                 │
├──────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌────────────────────────────────────────────────────────────────────────┐  │
│  │                        EVENT SOURCES                                   │  │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌──────────────┐  │  │
│  │  │ System Logs │  │ Metrics     │  │ Alerts      │  │ Schedule     │  │  │
│  │  │ (Windows)   │  │ (CPU, RAM)  │  │ (SNMP, SMTP)│  │ (Cron)       │  │  │
│  │  └─────────────┘  └─────────────┘  └─────────────┘  └──────────────┘  │  │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌──────────────┐  │  │
│  │  │ NetApp Logs │  │ Network     │  │ File System │  │ External API │  │  │
│  │  │ (ONTAP)     │  │ Events      │  │ Changes     │  │ Webhooks     │  │  │
│  │  └─────────────┘  └─────────────┘  └─────────────┘  └──────────────┘  │  │
│  └────────────────────────────────────────────────────────────────────────┘  │
│                                    │                                          │
│                                    ▼                                          │
│  ┌────────────────────────────────────────────────────────────────────────┐  │
│  │                        EVENT MANAGER                                   │  │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌──────────────┐  │  │
│  │  │ Ingestion   │  │ Routing     │  │ Filtering   │  │ Deduplication│  │  │
│  │  │ Adapters    │  │ Rules Engine│  │ Thresholds  │  │ Algorithms   │  │  │
│  │  └─────────────┘  └─────────────┘  └─────────────┘  └──────────────┘  │  │
│  │                              │                                          │  │
│  │                              ▼                                          │  │
│  │                      ┌─────────────┐                                   │  │
│  │                      │ Event Queue │                                   │  │
│  │                      └─────────────┘                                   │  │
│  └────────────────────────────────────────────────────────────────────────┘  │
│                                    │                                          │
│                                    ▼                                          │
│  ┌────────────────────────────────────────────────────────────────────────┐  │
│  │                        AGENT EVENT HANDLER                             │  │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌──────────────┐  │  │
│  │  │ Event Parser│  │ Context     │  │ Decision    │  │ Action       │  │  │
│  │  │ extractor   │  │ builder     │  │ engine      │  │ dispatcher  │  │  │
│  │  └─────────────┘  └─────────────┘  └─────────────┘  └──────────────┘  │  │
│  │                              │                                          │  │
│  │                              ▼                                          │  │
│  │                      ┌─────────────┐                                   │  │
│  │                      │ Agent Loop  │ (reusing existing execution)     │  │
│  │                      └─────────────┘                                   │  │
│  └────────────────────────────────────────────────────────────────────────┘  │
│                                    │                                          │
│                                    ▼                                          │
│  ┌────────────────────────────────────────────────────────────────────────┐  │
│  │                        ACTION OUTPUT                                   │  │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌──────────────┐  │  │
│  │  │ Execute     │  │ Notify      │  │ Log/Store   │  │ Feedback     │  │  │
│  │  │ Actions     │  │ (Email,     │  │ Events      │  │ Loop         │  │  │
│  │  │ (Tools)     │  │ Slack, etc) │  │             │  │              │  │  │
│  │  └─────────────┘  └─────────────┘  └─────────────┘  └──────────────┘  │  │
│  └────────────────────────────────────────────────────────────────────────┘  │
│                                                                              │
└──────────────────────────────────────────────────────────────────────────────┘
```

### Event Manager Components

#### 1. Event Ingestion Adapters
```python
# Windows Event Log Adapter
class WindowsEventLogAdapter:
    def connect(self):
        # Connect to Windows Event Log API
        pass
    
    def subscribe(self, event_types: List[str], callback):
        # Subscribe to events, call callback on new event
        pass

# NetApp ONTAP Event Adapter
class NetAppEventAdapter:
    def connect(self, cluster: str):
        # Connect to NetApp event logs
        pass
    
    def stream_events(self, callback):
        # Stream events via API or SNMP traps
        pass
```

#### 2. Event Routing Rules
```python
# Rule-based routing configuration
EVENT_ROUTES = [
    {
        "name": "High CPU Usage",
        "condition": lambda e: e["metric"] == "cpu" and e["value"] > 90,
        "action": "cpu_optimization_task"
    },
    {
        "name": "Volume Near Capacity",
        "condition": lambda e: "volume" in e and e["capacity_pct"] > 85,
        "action": "volume_alert_task"
    },
    {
        "name": "NetApp Error",
        "condition": lambda e: e["severity"] == "error",
        "action": "netapp_troubleshoot_task"
    }
]
```

#### 3. Event-to-Task Mapping
```python
# Event → Task Template Mapping
EVENT_TASK_MAP = {
    "high_cpu": {
        "description": "Investigate high CPU usage on server {server_id}",
        "tools": ["run_terminal_command_tool", "open_app_tool"],
        "max_steps": 5
    },
    "volume_capacity": {
        "description": "Check and report on volume {volume_name} capacity",
        "tools": ["netapp_list_volumes", "netapp_create_quota"],
        "max_steps": 3
    }
}
```

### Event Processing Pipeline

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         EVENT PROCESSING FLOW                              │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  1. Event Ingestion                                                        │
│     Event Source → Adapter → Normalized Event                              │
│                                                                             │
│  2. Event Parsing                                                          │
│     ├─ Extract metadata (timestamp, source, severity)                      │
│     ├─ Extract entities (server, volume, user)                             │
│     └─ Extract values (metrics, thresholds)                                │
│                                                                             │
│  3. Event Classification                                                   │
│     ├─ Categorize by type (error, warning, info)                           │
│     ├─ Categorize by domain (system, storage, network)                     │
│     └─ Assign priority (critical, high, medium, low)                      │
│                                                                             │
│  4. Context Enrichment                                                     │
│     ├─ Retrieve historical context (past events for entity)                │
│     ├─ Retrieve current state (current metrics)                            │
│     └─ Retrieve relevant documentation (RAG)                               │
│                                                                             │
│  5. Decision Making                                                        │
│     ├─ Match against event routes                                          │
│     ├─ Evaluate conditions (thresholds, patterns)                          │
│     └─ Generate task template                                              │
│                                                                             │
│  6. Action Dispatch                                                        │
│     ├─ Create task in database                                             │
│     ├─ Start agent execution                                               │
│     └─ Stream results                                                      │
│                                                                             │
│  7. Feedback & Learning                                                    │
│     ├─ Log outcome (success/failure)                                       │
│     ├─ Update event patterns                                               │
│     └─ Adjust routing rules (if configured)                                │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Event-Driven Implementation Plan

#### Phase 1: Event Subscription Foundation
1. **Event Source Connectors**
   - Windows Event Log reader
   - NetApp event log reader
   - File system watcher (for config changes)

2. **Event Normalization**
   - Standardized event schema
   - Common field names across sources

#### Phase 2: Simple Rule Engine
1. **Pattern Matching**
   - Regex-based pattern matching
   - Threshold-based triggering

2. **Task Templates**
   - Pre-defined task templates for common events
   - Variable substitution

#### Phase 3: AI-Augmented Event Processing
1. **Event Understanding**
   - Use LLM to understand event semantics
   - Classify events without explicit rules

2. **Autonomous Actions**
   - Pre-approved actions (with safety checks)
   - Escalation paths for complex decisions

### Safety & Governance in Event-Driven Mode

#### Event Filtering
- **Severity thresholds**: Only process events above threshold
- **Time windows**: Coalesce related events
- **Source validation**: Trust only verified sources

#### Action Safeguards
- **Rate limiting**: Maximum actions per time period
- **Approval workflow**: Critical actions require user confirmation
- **Audit logging**: All autonomous actions logged

#### Fallback Mechanisms
- **Human override**: Emergency stop button
- **Default actions**: Safe fallbacks for unknown events
- **Degradation**: Disable event-driven mode if resources exhausted

---

## Intent-Driven vs Event-Driven: Transition Strategy

### Comparison Matrix

| Aspect | Intent-Driven | Event-Driven |
|--------|---------------|--------------|
| **Trigger** | User input | System events |
| **Initiation** | Pull (user initiates) | Push (system initiates) |
| **Execution Mode** | Sequential task execution | Concurrent event handling |
| **State** | Task-local | Global context |
| **Complexity** | Lower | Higher |
| **Use Cases** | Ad-hoc tasks, planned operations | Monitoring, alerting, automation |

### Unified Architecture

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                    UNIFIED ARCHITECTURE                                     │
├──────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│                         ┌────────────────┐                                  │
│                         │  User Query    │                                  │
│                         └────────┬───────┘                                  │
│                                  │                                          │
│                                  ▼                                          │
│                        ┌────────────────┐         ┌──────────────┐           │
│                        │   Intent       │         │   Event      │           │
│                        │   Interpreter  │         │   Stream     │           │
│                        └────────┬───────┘         └──────┬───────┘           │
│                                 │                       │                   │
│                                 ▼                       ▼                   │
│                        ┌────────────────┐      ┌──────────────────┐         │
│                        │   Task Planner │      │ Event Processor  │         │
│                        └────────┬───────┘      └────────┬─────────┘         │
│                                 │                       │                   │
│                                 ▼                       ▼                   │
│                        ┌──────────────────────────────────────────┐         │
│                        │           Agent Core                     │         │
│                        │  (Unified execution engine)              │         │
│                        └──────────────────────────────────────────┘         │
│                                      │                                      │
│                                      ▼                                      │
│                            ┌──────────────────┐                             │
│                            │   Action Queue   │                             │
│                            └──────────────────┘                             │
│                                      │                                      │
│                   ┌──────────────────┼──────────────────┐                   │
│                   ▼                  ▼                  ▼                   │
│            ┌─────────────┐   ┌─────────────┐   ┌─────────────┐             │
│            │  Intent     │   │   Event     │   │   Scheduled │             │
│            │  Tasks      │   │  Tasks      │   │   Tasks     │             │
│            └─────────────┘   └─────────────┘   └─────────────┘             │
│                                                                              │
└──────────────────────────────────────────────────────────────────────────────┘
```

### Migration Path

#### Phase 1: Existing Intent-Driven (Current State)
- ✅ All user tasks processed through intent flow
- ✅ Agent loop with plan generation
- ✅ Tool execution with error handling

#### Phase 2: Hybrid Mode (Transition)
1. **Background Event Monitoring**
   - Events collected but not auto-triggered
   - Logged for analysis
   - Manual review of patterns

2. **Optional Event Tasks**
   - Events can be manually converted to tasks
   - User confirms event-triggered actions

#### Phase 3: Partial Automation
1. **Whitelist of Safe Events**
   - Pre-approved event-to-action mappings
   - No user confirmation required

2. **Escalation Paths**
   - Complex events → human review
   - Simple events → auto-action

#### Phase 4: Full Event-Driven
1. **AI-Augmented Decision Making**
   - LLM evaluates event context
   - Autonomous action within defined policies

2. **Continuous Learning**
   - Success/failure feedback loop
   - Automatic rule refinement

---

## Future-Ready Design Patterns

### 1. Plugin Architecture

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                         PLUGIN ARCHITECTURE                                 │
├──────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌────────────────────────────────────────────────────────────────────────┐  │
│  │                        CORE SYSTEM                                     │  │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌──────────────┐  │  │
│  │  │ Agent Core  │  │   Tools     │  │  Event Mgr  │  │   RAG Core   │  │  │
│  │  └─────────────┘  └─────────────┘  └─────────────┘  └──────────────┘  │  │
│  └────────────────────────────────────────────────────────────────────────┘  │
│                  │            │            │              │                  │
│                  ▼            ▼            ▼              ▼                  │
│  ┌───────────────┐  ┌─────────────┐  ┌─────────────┐  ┌──────────────┐     │
│  │ Tool Plugins  │  │ Event Sources │  │ Vector DBs  │  │  Knowledge   │     │
│  │ (input,       │  │ (Windows,     │  │ (Chroma,    │  │  Bases       │     │
│  │  system,      │  │  NetApp,      │  │  Pinecone)  │  │  (API docs,  │     │
│  │  browser,     │  │  File, etc)   │  │             │  │   manuals)   │     │
│  │  vision)      │  │             │  │             │  │             │     │
│  └───────────────┘  └─────────────┘  └─────────────┘  └──────────────┘     │
│                                                                              │
└──────────────────────────────────────────────────────────────────────────────┘
```

#### Plugin Interface
```python
# Plugin base class for tools
class ToolPlugin:
    def register_tools(self) -> Dict[str, Tool]:
        """Return dict of tool_name → Tool instances"""
        raise NotImplementedError
    
    def get_metadata(self) -> Dict:
        """Return plugin metadata"""
        return {
            "name": self.__class__.__name__,
            "version": "1.0.0",
            "description": self.__doc__
        }

# Plugin base class for event sources
class EventSourcePlugin:
    def connect(self) -> bool:
        """Connect to event source"""
        raise NotImplementedError
    
    def stream_events(self, callback) -> None:
        """Stream events to callback"""
        raise NotImplementedError
    
    def disconnect(self) -> None:
        """Disconnect from event source"""
        raise NotImplementedError
```

### 2. Context-Aware Processing

#### Context Layers
```
┌──────────────────────────────────────────────────────────────────────────────┐
│                         CONTEXT HIERARCHY                                   │
├──────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  Layer 1: Session Context (short-term)                                      │
│  ├─ Current task                                                           │
│  ├─ Recent observations                                                     │
│  └─ Working state (session_state)                                           │
│                                                                              │
│  Layer 2: User Context (medium-term)                                        │
│  ├─ User preferences                                                        │
│  ├─ History (recent tasks)                                                  │
│  └─ Common patterns                                                         │
│                                                                              │
│  Layer 3: Domain Context (long-term)                                        │
│  ├─ Knowledge base entries                                                  │
│  ├─ RAG embeddings                                                          │
│  └─ Learnings from execution                                                │
│                                                                              │
│  Layer 4: System Context (always available)                                 │
│  ├─ Available tools                                                         │
│  ├─ System capabilities                                                     │
│  └─ Safety policies                                                         │
│                                                                              │
└──────────────────────────────────────────────────────────────────────────────┘
```

### 3. Observability & Monitoring

#### Agent Metrics
- **Execution time** per step
- **Tool success rate**
- **LLM call latency**
- **Memory usage**
- **Error patterns**

#### Logging Strategy
```python
# Structured logging for agent
{
    "timestamp": "2026-07-13T10:30:00Z",
    "level": "INFO",
    "event": "agent_step",
    "task_id": "abc123",
    "step": 3,
    "tool": "netapp_list_volumes",
    "duration_ms": 1500,
    "success": true,
    "metadata": {
        "volume_count": 5
    }
}
```

### 4. Resilience Patterns

#### Retry Strategy
- **Exponential backoff**: 1s, 2s, 4s, 8s, 16s
- **Circuit breaker**: After 5 failures, skip for N minutes
- **Fallback actions**: Try alternative tool on failure

#### Timeout Management
- **Per-tool timeout**: Configurable per tool
- **Total task timeout**: Maximum execution time
- **Heartbeat**: Regular progress reports

---

## Conclusion

IRIS represents a sophisticated, air-gapped agent system that combines modern web technologies with powerful automation capabilities. The modular architecture ensures extensibility while the air-gapped design meets strict security requirements. The system is production-ready with comprehensive error handling, real-time feedback, and persistent storage.

The future-ready design patterns ensure IRIS can evolve to support:

1. **RAG Integration**: Knowledge-aware agents with contextual understanding
2. **Event-Driven Architecture**: Proactive, automated responses to system events
3. **Plugin Architecture**: Easy extension with new tools and event sources
4. **Context-Aware Processing**: Intelligent decision-making with layered context
5. **Resilience Patterns**: Reliable operation with graceful degradation

---

**Document Version**: 2.0  
**Last Updated**: 2026-07-13  
**Project**: IRIS - Intelligent Reasoning Infrastructure System  
**Location**: `D:\IRIS\IRIS test`
