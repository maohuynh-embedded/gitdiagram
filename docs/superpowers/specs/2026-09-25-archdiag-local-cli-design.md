# archdiag — Dự án A: CLI + web chạy local, không trả phí

- Ngày: 2026-09-25
- Trạng thái: thiết kế đã duyệt từng phần, chờ duyệt spec
- Phạm vi: dự án con **A** trong lộ trình A → B → C

## 1. Bối cảnh và mục tiêu

GitDiagram (repo này) sinh sơ đồ kiến trúc từ repo GitHub, nhưng:

1. Phụ thuộc dịch vụ trả phí (OpenAI/OpenRouter, Cloudflare R2, Upstash Redis) và chỉ đọc repo qua GitHub API.
2. Phân tích chưa sát project: chỉ dùng cây file, README và tối đa 12 file bị cắt còn 48k ký tự, nên LLM phải đoán kiến trúc.
3. Repo phức tạp không phân tích được.
4. Sơ đồ không theo chuẩn nào (ISO/IEC/IEEE 42010, C4, ArchiMate, automotive).

Lộ trình gồm ba dự án con, mỗi dự án có spec và kế hoạch riêng:

| Dự án | Nội dung | Phụ thuộc |
|---|---|---|
| **A** (spec này) | Khung CLI và web chạy local, dùng LLM local, đọc repo từ thư mục | — |
| B | Phân tích tĩnh (tree-sitter, libclang/clang-uml, include graph, CMake/qmake) để dựng mô hình chính xác, phân tích theo tầng cho repo lớn | A |
| C | Mô hình và ký pháp theo chuẩn: 42010 viewpoint, C4, ArchiMate, SysML v2, AUTOSAR / ISO 26262 | B |

Đối tượng chính: project **embedded / automotive C/C++**. Bộ chuẩn tham chiếu là một **project nội bộ, cần giữ kín** (gọi là *project tham chiếu*): C++/QML, có kiến trúc phân lớp rõ ràng, build bằng CMake và qmake, vài trăm file mã nguồn. Tên, đường dẫn và cấu trúc chi tiết của project này **không được ghi vào repo**.

### Mục tiêu của A

- Chạy hoàn toàn trên máy (M2 Pro, 32 GB): không cloud, không API key trả phí, không telemetry.
- Đọc repo từ **thư mục local**.
- Có **CLI** (`analyze`, `serve`, `doctor`, `bench`) và **web UI local**.
- Tạo `ArchModel`, một hợp đồng trung tâm ổn định để B làm cho mô hình chính xác hơn và C xuất ra nhiều ký pháp.
- Có con số **baseline** về độ chính xác trên project tham chiếu.

### Ngoài phạm vi của A

- Phân tích tĩnh sâu (thuộc B).
- Ký pháp theo chuẩn (thuộc C).
- Đọc repo qua GitHub API.
- Tính năng video, admin, quota, sponsor của GitDiagram.
- Sửa hay xoá app Next.js hiện có: app này được giữ nguyên, không đụng tới.

## 2. Quyết định chính

| Quyết định | Lựa chọn | Lý do |
|---|---|---|
| Ngôn ngữ | **Python** (≥ 3.12, máy hiện có 3.14) | Máy không có Node/Bun. Hệ sinh thái tree-sitter, libclang, graph tốt nhất cho B. Kỹ sư embedded quen dùng |
| Vị trí | Thư mục mới `archdiag/` trong repo này | Tách biệt khỏi app Next.js |
| Tái sử dụng | Viết lại ý tưởng graph AST, validator và compiler Mermaid của GitDiagram (`src/features/diagram/graph.ts`, `src/server/generate/graph.ts`) | Đây là phần giá trị nhất, khoảng 640 dòng, dễ viết lại |
| LLM | Ollama, qua API tương thích OpenAI | Miễn phí, chạy local, đổi runtime chỉ bằng cấu hình |
| Web | FastAPI + trang tĩnh, JS đóng gói sẵn | Một runtime duy nhất, chạy được khi không có mạng |
| Tham khảo | clang-uml, includegraph, ArchLintCpp, graphify (xem §10) | Dùng cho B, không dùng trong A |

## 3. Kiến trúc

```
archdiag/
  pyproject.toml               # pip install -e archdiag  → lệnh `archdiag`
  src/archdiag/
    cli.py                     # analyze | serve | doctor | bench
    config.py                  # archdiag.toml + tham số CLI
    scan/
      walker.py                # duyệt thư mục, .gitignore, lọc nhiễu và file nhị phân
      snapshot.py              # RepoSnapshot
    context/
      select.py                # RepoSnapshot → Evidence (B sẽ thay phần này)
    llm/
      client.py                # client tương thích OpenAI, structured output
      prompts.py
    model/
      schema.py                # ArchModel (Pydantic)
      validate.py              # kiểm tra, repair path
    render/
      mermaid.py               # ArchModel → Mermaid, tất định
    pipeline.py                # ghép các bước, thử lại, cache
    bench.py                   # so với file đáp án
    web/
      server.py                # FastAPI, chỉ lắng nghe 127.0.0.1
      static/                  # index.html, app.js, vendor/{mermaid, elk, dompurify}
  tests/
    unit/ pipeline/ live/
    fixtures/mini-repo/        # repo C/C++ nhỏ, tự tạo cho test
```

### Luồng dữ liệu

```
thư mục repo
  → scan    → RepoSnapshot   (cây file, ngôn ngữ, kích thước, README, file build)
  → context → Evidence       (các đoạn trích file tiêu biểu, trong giới hạn token)
  → llm     → JSON thô
  → model   → ArchModel      (đã kiểm tra)
  → render  → Mermaid
  → ghi ra  <out>/{model.json, diagram.mmd, explanation.md, run.json, debug/}
web chỉ đọc <out>/, còn nút "Phân tích lại" gọi lại pipeline.py
```

Mỗi bước là một hàm thuần (dữ liệu vào, dữ liệu ra), test được độc lập. Chỉ `pipeline.py` và `cli.py` được đọc/ghi file hoặc gọi mạng.

### Hợp đồng dữ liệu

- **RepoSnapshot:** `root` (đường dẫn tuyệt đối), `files[]` (path tương đối, kích thước, ngôn ngữ, `is_binary`), `dirs[]`, `readme`, `build_files[]` (CMakeLists.txt, *.pro, *.pri, Makefile, CMakePresets.json), `content_hash`.
- **Evidence:** `tree_summary` (cây thư mục đã lọc), `excerpts[]` (path, text), `budget` (số token đã dùng / giới hạn), `truncated` (bool).
- **ArchModel** (`schema_version = 1`):
  - `groups[]` (`id`, `label`, `parent?`)
  - `nodes[]` (`id`, `label`, `kind`, `group?`, `paths[]`, `description`)
  - `edges[]` (`from`, `to`, `label?`, `kind`)
  - `explanation`
  - `kind` là chuỗi tự do trong A. C sẽ chuẩn hoá thành bộ từ vựng theo chuẩn.
- **Giới hạn:** tối đa 60 node, 120 cạnh, 20 group. id theo mẫu `^[a-z][a-z0-9_]{0,47}$`. Mọi path phải tồn tại trong `RepoSnapshot`.

### Lọc nhiễu mặc định (walker)

- Tôn trọng `.gitignore`.
- Bỏ qua mặc định:
  - các thư mục: `.git/`, `build*/`, `cmake-build-*/`, `out/`, `node_modules/`, `3rdparty/`, `third_party/`, `external/`, `vendor/`
  - file build sinh ra: `*.o`, `*.obj`, `*.a`, `*.so`, `*.dylib`, `*.dll`, `*.exe`, `*.hex`, `*.dfu`, `*.bin`, `*.elf`, `moc_*`, `*.moc`, `qrc_*`, `ui_*.h`
  - ảnh, font, file nén
- Nhận diện file nhị phân bằng cách tìm byte NUL trong 8 KB đầu.
- Người dùng ghi đè bằng `include` / `exclude` trong `archdiag.toml`.

## 4. LLM local

- Mặc định `endpoint = "http://127.0.0.1:11434/v1"` và `model = "qwen3-coder:30b"`, khoảng 19 GB ở Q4_K_M. Cả hai đều cấu hình được. Khi bắt đầu làm sẽ kiểm tra lại model đang có trên Ollama.
- Context thực dùng mặc định **32K token**. `num_ctx` gửi qua tùy chọn của Ollama.
- **Structured output:** truyền JSON Schema của `ArchModel` qua tham số `format` của Ollama. Với runtime không hỗ trợ, dùng `response_format` kiểu `json_schema`, rồi đến chế độ JSON thường.
- **Thử lại:**
  - Lỗi cấu trúc (JSON sai, id trùng, cạnh trỏ tới node không tồn tại, vượt giới hạn): gửi lại kèm lỗi cụ thể, tối đa **3** lần.
  - Nếu lỗi **duy nhất** là path không tồn tại: bỏ các path đó, ghi cảnh báo, không thử lại.
  - Timeout hoặc lỗi mạng: thử lại 1 lần.
- **Thất bại:** hết số lần thử thì mã thoát `1`. Phản hồi thô và danh sách lỗi được lưu vào `<out>/debug/`. Không bao giờ ghi ra sơ đồ chưa qua kiểm tra.
- Nhiệt độ thấp (0.2) để kết quả ổn định. Seed cố định khi runtime hỗ trợ.

## 5. CLI

```
archdiag analyze <repo> [--out DIR] [--model NAME] [--endpoint URL] [--no-cache] [--allow-remote-llm]
archdiag serve   <repo> [--out DIR] [--port 8765]
archdiag doctor  [--endpoint URL] [--model NAME]
archdiag bench   <repo> --expected FILE [--out DIR]
```

- Mặc định `--out` là `<repo>/.archdiag/`.
- **Cấu hình** được đọc theo thứ tự ưu tiên: tham số CLI > `<repo>/archdiag.toml` > `~/.config/archdiag/config.toml` > giá trị mặc định.
- **Mã thoát:** `0` thành công, `1` lỗi phân tích, `2` lỗi cấu hình hoặc môi trường.
- **Cache:** khoá gồm `content_hash`, tên model, phiên bản prompt và `schema_version`. Nếu khoá không đổi thì dùng lại `model.json` cũ, trừ khi có `--no-cache`.
- **`doctor`:**
  - kiểm tra endpoint có phản hồi không
  - kiểm tra model đã pull chưa
  - kiểm tra RAM trống so với dung lượng model
  - in lệnh gợi ý sửa cho từng mục chưa đạt
- **`run.json`:** model, endpoint, thời gian từng bước, số lần thử lại, cảnh báo (bao gồm `truncated`), `content_hash`, phiên bản archdiag.

## 6. Web (`archdiag serve`)

- **Server:** FastAPI + uvicorn, **chỉ bind `127.0.0.1`**.
- **API:**
  - `GET /api/model`: nội dung `model.json`
  - `GET /api/explanation`: nội dung `explanation.md`
  - `GET /api/file?path=...`: xem nhanh nội dung file
  - `POST /api/analyze`: chạy lại pipeline, trả tiến độ qua SSE
- **Chặn truy cập file ngoài repo:** `path` được resolve (bao gồm symlink) và bắt buộc nằm trong `root`. Nếu không thì trả `403`. Giới hạn kích thước file xem nhanh.
- **CSRF:** `POST /api/analyze` yêu cầu header `Origin` trùng với chính server.
- **Giao diện:**
  - sơ đồ ở giữa (zoom, pan), phần giải thích ở bên cạnh
  - bấm node để mở menu: mở trong VS Code (`vscode://file/<abs path>`) hoặc xem nhanh
  - xuất PNG, SVG, copy Mermaid
  - sáng/tối theo `prefers-color-scheme`
- **Vendor JS:** mermaid, @mermaid-js/layout-elk và DOMPurify được đóng gói vào `static/vendor/`, phiên bản ghim cố định. Không tải từ CDN.
- **Bảo mật render:**
  - Compiler escape toàn bộ text.
  - Mermaid chạy với `securityLevel: "antiscript"` và `htmlLabels: false`.
  - SVG được lọc qua DOMPurify.
  - Sau khi lọc, chỉ giữ link dạng `vscode://file/` trỏ tới path nằm trong repo. Các link khác bị xoá.

## 7. Xử lý lỗi

| Bước | Lỗi | Xử lý |
|---|---|---|
| scan | Path không tồn tại, không có quyền đọc, repo rỗng | Mã thoát `2`, thông báo rõ nguyên nhân |
| context | Vượt ngân sách token | Cắt bớt, đặt `truncated=true`, cảnh báo trong CLI và `run.json` |
| llm | Endpoint không phản hồi, model chưa pull | Mã thoát `2`, gợi ý lệnh như `doctor` |
| llm | Timeout, hết bộ nhớ | Thử lại 1 lần, sau đó gợi ý model nhỏ hơn |
| model | Lỗi cấu trúc | Thử lại kèm lỗi cụ thể, tối đa 3 lần, sau đó mã thoát `1` và lưu `debug/` |
| model | Chỉ sai path | Bỏ path sai, ghi cảnh báo |
| output | Không ghi được thư mục output | Mã thoát `2` |

## 8. Kiểm thử

1. **Unit** (`tests/unit`, không cần Ollama):
   - walker: lọc nhiễu, `.gitignore`, file nhị phân
   - schema và giới hạn
   - validate: id, cạnh, path, repair
   - compiler Mermaid: escape ký tự đặc biệt, nhãn độc hại, kết quả tất định
   - server: path traversal, symlink, kiểm tra `Origin`
2. **Pipeline** (`tests/pipeline`): dùng client LLM giả với phản hồi ghi sẵn (đúng, JSON sai, cạnh sai, chỉ sai path, timeout) để kiểm tra thử lại, repair, cache, mã thoát và các file output.
3. **Live** (`tests/live`, đánh dấu `live`, không chạy mặc định): chạy pipeline thật với Ollama trên `fixtures/mini-repo`.
4. **Bench** (`archdiag bench`):
   - File đáp án `benchmarks/<tên>.expected.toml` gồm:
     - `components`: tên thành phần và glob path
     - `relations`: một số quan hệ quan trọng giữa các thành phần
     - `forbidden`: các glob không được xuất hiện, ví dụ `build/**`, `3rdparty/**`
   - **Chỉ số:**
     - recall thành phần
     - recall quan hệ
     - tỉ lệ path hợp lệ (mục tiêu 100%)
     - số node rác (mục tiêu 0)
   - Kết quả ghi vào `<out>/bench.json` và in ra dạng bảng.
   - Project tham chiếu là code nội bộ, cần giữ kín:
     - Không chép code, tên module hay cấu trúc của nó vào repo.
     - Đường dẫn chỉ truyền qua tham số `<repo>` hoặc biến `ARCHDIAG_BENCH_REPO`.
     - File đáp án của nó nằm ở `benchmarks/private/`, thư mục này nằm trong `.gitignore`.
     - Output của nó (`.archdiag/`, `bench.json`) chỉ nằm trong project đó, không bao giờ ghi vào repo này.
     - Chỉ commit `benchmarks/mini-repo.expected.toml` của fixture tự tạo.
   - Pipeline không gửi nội dung repo ra ngoài máy. Endpoint LLM mặc định là `127.0.0.1`. Nếu cấu hình một endpoint không phải loopback, `analyze` sẽ cảnh báo và yêu cầu thêm `--allow-remote-llm`.

Chạy test: `pytest` (unit + pipeline), `pytest -m live`, `ruff check`, `mypy --strict src/`.

## 9. Tiêu chí hoàn thành

- [ ] `pip install -e archdiag` cài được lệnh `archdiag` trên máy M2 Pro.
- [ ] `archdiag doctor` báo đúng trạng thái Ollama và model.
- [ ] `archdiag analyze <project tham chiếu>` sinh `model.json`, `diagram.mmd`, `explanation.md` và `run.json` hợp lệ, không có node từ `build/` hay `3rdparty/`.
- [ ] `archdiag serve` hiển thị sơ đồ, mở được file trong VS Code, xuất được PNG/SVG, hoạt động khi không có mạng.
- [ ] Unit và pipeline test pass. `ruff` và `mypy` sạch.
- [ ] `archdiag bench` trên project tham chiếu cho ra chỉ số **baseline**. Chỉ các con số tổng hợp được ghi lại để làm mốc so sánh cho B, không kèm tên module.

## 10. Tham khảo

- clang-uml: https://github.com/bkryza/clang-uml
- clang-include-graph: https://github.com/bkryza/clang-include-graph
- includegraph: https://github.com/Notgnoshi/includegraph
- ArchLintCpp: https://github.com/mao2009/ArchLintCpp
- clangd-graph-rag: https://github.com/2015xli/clangd-graph-rag
- graphify: https://github.com/Graphify-Labs/graphify
- AST-derived graphs vs LLM-extracted KGs: https://arxiv.org/pdf/2601.08773
- Structurizr (C4): https://structurizr.com/ · archicode: https://github.com/tmorin/archicode
