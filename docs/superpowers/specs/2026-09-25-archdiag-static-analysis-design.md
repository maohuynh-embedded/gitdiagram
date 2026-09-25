# archdiag — Dự án B: Phân tích tĩnh, mô hình chính xác, phân tích theo tầng

- Ngày: 2026-09-25
- Trạng thái: thiết kế đã duyệt, chờ duyệt spec
- Phạm vi: dự án con **B** trong lộ trình A → B → C
- Phụ thuộc: spec A (`2026-09-25-archdiag-local-cli-design.md`). Mọi ràng buộc của A vẫn giữ nguyên: chạy local, không trả phí, không gửi code ra ngoài máy, và quy tắc bảo mật cho project tham chiếu.

## 1. Mục tiêu

A để LLM tự dựng kiến trúc từ một phần nhỏ của repo, nên kết quả không sát project và không làm được với repo lớn. B thay cách làm đó bằng:

- **Cấu trúc 100% từ phân tích tĩnh, tất định:** component, layer, quan hệ, message protocol, class/interface.
- **Mọi quan hệ truy vết được đến `file:line`**, kèm nguồn và độ tin cậy. Đây là nền tảng cho C (ISO 26262 / ASPICE).
- **Phân tích theo tầng** (hệ thống → layer → component → class), nên repo lớn không bị giới hạn context.
- **So sánh kiến trúc dự định (khai báo) với thực tế (code)** và báo vi phạm.
- **LLM chỉ chú thích:** đặt tên, mô tả, gắn nhãn cạnh, viết giải thích. Tool chạy đầy đủ khi không có LLM (`--no-llm`).

Các loại quan hệ nằm trong phạm vi B:

1. phụ thuộc module/lớp
2. giao tiếp thiết bị/protocol
3. cấu trúc class/interface

### Ngoài phạm vi của B

- Wiring runtime của Qt (signal/slot, việc đưa C++ ra QML, thread): để phiên bản sau.
- ARXML và AUTOSAR: thuộc C.
- Ký pháp theo chuẩn (C4, ArchiMate, SysML): thuộc C.
- Ngôn ngữ khác C/C++: dùng luồng LLM của A làm phương án dự phòng.
- Call graph đầy đủ.

## 2. Quyết định chính

| Quyết định | Lựa chọn | Lý do |
|---|---|---|
| Parser | **Lai:** tree-sitter luôn chạy, libclang khi có `compile_commands.json` | Luôn chạy được với toolchain nhúng của hãng (IAR, Keil, TASKING...), và chính xác khi có compile DB |
| Kho dữ liệu | Facts trong SQLite (`.archdiag/facts.db`), cache theo file | Phân tích tăng dần, không cần server |
| Component | Tự suy, cộng khai báo tuỳ chọn trong `archdiag.toml` | Chạy được ngay, và kiểm tra được kiến trúc dự định khi có khai báo |
| LLM | Chỉ chú thích, map-reduce theo component | Tất định, không giới hạn bởi context, không bịa quan hệ |
| Công cụ ngoài | Không bắt buộc. Output JSON của clang-uml có thể thêm làm nguồn tuỳ chọn ở phiên bản sau | Không phụ thuộc cứng vào binary ngoài |

Thư viện (đã kiểm tra chạy được trên Python 3.14 / M2 Pro): `tree-sitter` 0.26, `tree-sitter-c` 0.24, `tree-sitter-cpp` 0.23, `libclang` 18.1 (wheel pip có kèm `libclang.dylib`).

## 3. Kiến trúc

```
src/archdiag/
  facts/
    model.py            # dataclass cho từng loại fact, Confidence, Source
    store.py            # SQLite: bảng files, facts; cache theo (path, sha256, extractor_version, compile_flags_hash)
    extract_ts.py       # tree-sitter C/C++
    extract_clang.py    # libclang theo compile_commands.json
    extract_build.py    # CMake, qmake (đọc tĩnh)
    extract_protocol.py # DBC, EDS/DCF, mẫu trong code
    resolve.py          # phân giải #include
    runner.py           # chạy song song, chọn extractor, gộp kết quả
  arch/
    components.py       # tự suy component / áp khai báo
    layers.py           # gán layer
    aggregate.py        # facts → cạnh giữa component (weight, evidence, confidence)
    rules.py            # vi phạm layer / deny / vòng phụ thuộc
    views.py            # sinh view, thu gọn khi quá 60 node
  annotate/
    map.py              # LLM chú thích từng component
    reduce.py           # LLM viết giải thích hệ thống
  model/schema.py       # ArchModel v2 (mở rộng từ v1 của A)
  render/mermaid.py     # mở rộng: một .mmd cho mỗi view, style theo confidence và vi phạm
```

### Luồng dữ liệu

```
RepoSnapshot (A)
  → facts/runner    → facts.db  (chỉ phân tích lại file đã đổi)
  → arch/components → component, layer
  → arch/aggregate  → cạnh có weight, evidence, confidence
  → arch/rules      → violations
  → arch/views      → views (≤ 60 node mỗi view)
  → annotate        → label, description, nhãn cạnh, explanation  (bỏ qua khi --no-llm)
  → ArchModel v2    → render → .archdiag/{model.json, views/*.mmd, explanation.md, run.json}
```

Pipeline chọn chế độ:
- Có C/C++ trong repo: dùng luồng B.
- Không có: dùng luồng LLM của A. `run.json` ghi rõ `mode`.

## 4. Facts và bộ trích xuất

Mỗi fact có: `file`, `line`, `source` (`treesitter`, `clang`, `build` hoặc `protocol-file`), `confidence` (`high`, `medium` hoặc `low`).

| Fact | Trường | Dùng cho |
|---|---|---|
| `include` | `target_file` hoặc `unresolved: <tên>` | phụ thuộc |
| `symbol` | `kind` (class, struct, enum, function), `qualified_name` | class/interface |
| `inherits` | `child`, `base`, `access` | class/interface |
| `interface` | `class` có ít nhất một hàm ảo thuần | port/adapter |
| `field_type` | `owner`, `type` | composition |
| `build_target` | `name`, `kind` (exe, lib), `sources`, `links`, `include_dirs` | component, phân giải include |
| `message` | `protocol`, `id`, `name` | protocol |
| `message_io` | `function`, `direction` (send/recv), `message` | protocol |

### 4.1 tree-sitter (luôn chạy)

- **Loại file:** `.c .h .cc .cpp .cxx .hh .hpp .hxx .inl`.
- **Trích:**
  - `#include "…"` và `#include <…>`
  - khai báo class/struct/enum/hàm tự do (bao gồm namespace)
  - base class
  - hàm `virtual … = 0`
  - kiểu của field
- **Phân giải `#include`** (`resolve.py`), theo thứ tự:
  1. cùng thư mục với file: `medium`
  2. include dir lấy từ build facts: `medium`
  3. file **duy nhất** trong repo có cùng đường dẫn đuôi (`a/b.h` khớp `…/a/b.h`): `low`
  4. không tìm thấy: `unresolved`, gom thành thư viện ngoài theo tiền tố (`Q*` → Qt, `<std>` → C++ std, hoặc theo tên thư mục)
- `#ifdef`: tree-sitter đọc **cả hai nhánh**. Include nằm trong nhánh điều kiện bị hạ một mức confidence.

### 4.2 libclang (khi có compile DB)

- **Tìm `compile_commands.json`:**
  - thứ tự: `--compile-db PATH` → thư mục gốc repo → `build*/`, `cmake-build-*/`
  - thư mục build nằm trong danh sách bị scan loại trừ, nhưng vẫn được **đọc riêng** file này
- **Mỗi translation unit (TU):**
  - `clang.cindex` parse với đúng tham số trong compile DB
  - lấy các include đã phân giải, class, base, field kèm kiểu đã phân giải
  - facts có confidence `high`, **thay thế** facts tree-sitter của các file mà TU đó bao phủ
- **Khi lỗi:**
  - TU có lỗi mức `fatal` thì file đó quay về dùng tree-sitter, kèm cảnh báo
  - không tải được libclang thì bỏ qua cả bước này, kèm ghi chú

### 4.3 Build (đọc tĩnh, không chạy cmake/qmake)

- **CMake:**
  - lệnh: `add_executable`, `add_library`, `target_link_libraries`, `target_include_directories`, `include_directories`, `add_subdirectory`
  - biến `set(X …)` đơn giản được thay thế
  - generator expression và biến phức tạp bị bỏ qua, kèm cảnh báo
- **qmake:**
  - `TEMPLATE`, `TARGET`, `SOURCES`, `HEADERS`, `INCLUDEPATH`, `SUBDIRS`, `include(*.pri)`
  - biến `$$PWD` và `$$VAR` đơn giản

### 4.4 Protocol

- **Đọc file mô tả (confidence `high`):**
  - `.dbc`: `BO_` (message: id, tên, node gửi), `SG_` (signal, node nhận)
  - `.eds/.dcf`: các section object `[XXXX]` và `[XXXXsubY]`, lấy `ParameterName`
- **Trích từ code (confidence `medium` hoặc `low`, ghi rõ là suy luận theo mẫu)**, cấu hình trong `archdiag.toml`, có sẵn preset `can` và `canopen`:

  ```toml
  [[protocol.code]]
  name = "canopen"
  message_name_patterns = ["*_COB_ID", "OD_*", "SDO_*"]  # const / enum / #define có giá trị số → message
  send_calls = ["send*", "write*Sdo*", "transmit*"]     # lời gọi hàm → message_io send
  recv_calls = ["on*Frame*", "handle*", "dispatch*"]    # định nghĩa/gọi hàm → message_io recv
  ```

  Tham số là literal số hoặc định danh khớp `message_name_patterns` thì liên kết với `message`. Nếu không, ghi message là `unknown` và confidence `low`.

### 4.5 Cache và chạy song song

- **Khoá cache:** `(path, sha256 nội dung, extractor_version, hash tham số compile)`. Khi file đổi thì xoá facts cũ của file đó.
- **Chạy song song:** tree-sitter và libclang chạy bằng `ProcessPoolExecutor`, số worker bằng số CPU. Chỉ tiến trình chính được ghi vào SQLite.
- **Cờ `--rebuild-facts`** xoá cache.

## 5. Component, layer, vi phạm

### 5.1 Bốn tầng

| Tầng | Nội dung |
|---|---|
| L0: Hệ thống | build target (exe/lib), actor thiết bị (từ protocol), thư viện ngoài (gom từ include `unresolved`) |
| L1: Layer / subsystem | khai báo `layers`, hoặc thư mục cấp 1 |
| L2: Component | khai báo `components`, hoặc tự suy |
| L3: Class / interface | symbol bên trong một component |

### 5.2 Tự suy component

1. Nếu build facts có nhiều target, mỗi thư mục chứa source của một target (trừ target chính) là một ứng viên ranh giới.
2. Tách thư mục đệ quy: thư mục có hơn `max_component_files` file (mặc định 40) **và** có thư mục con chứa source thì tách xuống cấp con.
3. Thư mục dưới 2 file được gộp vào thư mục cha. File nằm ở thư mục gốc gom vào component `root`.
4. Tên component là đường dẫn, còn id là đường dẫn chuyển sang snake_case (duy nhất, đúng mẫu id của A).
5. Thư mục `test/`, `tests/`, `*_test*`, `UT/`, `IT/` được gán `kind = test`.

### 5.3 Khai báo kiến trúc dự định (tuỳ chọn)

```toml
[architecture]
layers = ["presentation", "application", "domain", "infrastructure"]  # trên → dưới
allow_upward = false            # mặc định chỉ được phụ thuộc xuống
max_component_files = 40

[[architecture.components]]
name  = "can_driver"
layer = "infrastructure"
paths = ["drivers/can/**"]

[[architecture.rules]]
from = "domain"
deny = ["infrastructure", "presentation"]
```

- Có `components`: file khớp nhiều component thì khai báo **đầu tiên** thắng. File không khớp component nào gom vào `unassigned`, kèm cảnh báo.
- Có `layers` nhưng không có `components`: component tự suy được gán layer theo thư mục cấp 1 trùng tên layer. Không trùng thì gán `unlayered`.
- Khai báo sai (layer không tồn tại, glob rỗng, tên trùng) trả mã thoát `2`.

### 5.4 Vi phạm

| Rule | Điều kiện |
|---|---|
| `layer-order` | cạnh từ layer dưới lên layer trên khi `allow_upward = false` |
| `deny` | cạnh khớp một luật `deny` |
| `cycle` | component thuộc một SCC có kích thước lớn hơn 1 (Tarjan) |
| `unassigned` | có file trong `unassigned` |

Mỗi vi phạm có `evidence[]`: tối đa 20 mẫu `file:line`, kèm tổng số. Component có `kind = test` không tạo vi phạm và mặc định ẩn khỏi view phụ thuộc, nhưng cạnh test → component vẫn được giữ để truy vết.

## 6. ArchModel v2

`schema_version = 2` mở rộng v1 (v1 vẫn đọc được):

- **`nodes[]`:** thêm `level` (`system`, `layer`, `component`, `class`, `external`, `actor`), `layer`, `origin` (`inferred`, `declared`), `metrics` (`files`, `lines`), `responsibilities[]`.
- **`edges[]`:**
  - `kind`: `include`, `inherits`, `implements`, `composes`, `message`, `test`
  - `weight`: số fact
  - `confidence`: mức cao nhất trong các fact
  - `evidence[]`: `{file, line, fact, source, confidence}`, tối đa 20
  - `evidence_total`
- **`messages[]`:** `{id, protocol, message_id, name, senders[], receivers[], confidence}`.
- **`violations[]`:** `{rule, from, to, evidence[], evidence_total}`.
- **`views[]`:** `{id, title, level, node_ids[], edge_ids[], collapsed_from?}`. Cạnh có `id` ổn định.

Giới hạn: tối đa 60 node mỗi view (không phải toàn mô hình). Toàn mô hình tối đa 2.000 node và 10.000 cạnh; vượt thì L3 chỉ giữ các class quan trọng nhất.

## 7. View

| View | Nội dung | Style |
|---|---|---|
| `dependencies` | L1 + L2, cạnh include tổng hợp (có weight) | Cạnh vi phạm màu đỏ. Confidence `low` vẽ nét đứt. Độ dày theo weight (3 mức) |
| `interfaces` | component có `message_io`, actor thiết bị, message | Nhãn cạnh là tên hoặc id message. Gom lại khi quá nhiều |
| `classes/<component>` | L3 của một component: tối đa 25 class nhiều tham chiếu nhất cùng các interface | kế thừa, implements, composes |

**Thu gọn:** view có hơn 60 node thì gộp component lên layer (L1). Web cho phép bấm vào layer để mở view con `dependencies/<layer>`. Mỗi view ghi ra `.archdiag/views/<id>.mmd`. Web có bộ chọn view. Bấm vào cạnh sẽ hiện danh sách evidence và mở được `file:line` trong VS Code (`vscode://file/<abs>:<line>`).

## 8. LLM chú thích (map-reduce)

- **Bước map (từng component, song song 1 luồng vì chạy local):**
  - Đầu vào:
    - id, đường dẫn
    - 30 symbol chính
    - danh sách interface
    - tóm tắt cạnh vào/ra (id đích, loại, weight)
    - message gửi/nhận
    - 2–3 đoạn header ngắn (≤ 2.000 ký tự mỗi đoạn)
  - Đầu ra (JSON schema): `{label, description, responsibilities[], edge_labels: {edge_id: label}}`.
  - Kiểm tra: `edge_id` phải thuộc cạnh ra của component, id lạ bị bỏ kèm cảnh báo, không cho thêm hay bớt node/cạnh. Lỗi thì thử lại tối đa 2 lần, sau đó dùng nhãn mặc định.
- **Bước reduce:** đầu vào là các chú thích, danh sách layer và vi phạm. Đầu ra là `explanation` (150–600 từ, theo `language`).
- **Cache:** theo `(hash facts của component, model, prompt_version)`.
- **`--no-llm`:** label là tên thư mục, không có description hay explanation, sơ đồ vẫn đầy đủ.

## 9. CLI và web (bổ sung vào A)

```
archdiag analyze <repo> [--no-llm] [--compile-db PATH] [--rebuild-facts] [--mode auto|static|llm]
archdiag check   <repo>     # chỉ phân tích tĩnh + luật; mã thoát 3 nếu có vi phạm (dùng cho CI)
```

Web thêm:
- bộ chọn view
- drill-down từ layer
- bảng vi phạm
- xem evidence của cạnh, mở `file:line`

## 10. Hiệu năng

Đo trên project tham chiếu (vài trăm file C++):
- Phân tích tĩnh lần đầu, không LLM: **dưới 30 giây**.
- Chạy lại khi sửa 1 file: **dưới 5 giây**.
- Chú thích bằng LLM tỉ lệ với số component. Có cache, và chạy lại khi không đổi thì không gọi LLM.

## 11. Xử lý lỗi

| Tình huống | Xử lý |
|---|---|
| Parse lỗi một file | Cảnh báo, dùng bộ trích xuất dự phòng hoặc bỏ file đó |
| Không có compile DB hoặc libclang | Chỉ dùng tree-sitter, ghi thông tin vào `run.json` |
| Build file hoặc protocol file hỏng | Cảnh báo và bỏ qua phần hỏng |
| Khai báo `[architecture]` hoặc `[protocol]` sai | Mã thoát `2` |
| LLM lỗi hoặc không có | Xuất sơ đồ không có chú thích, kèm cảnh báo |
| SQLite hỏng | Xoá và dựng lại cache, kèm cảnh báo |

## 12. Kiểm thử

1. **Unit test từng bộ trích xuất** (fixture nhỏ trong `tests/fixtures/`):
   - include: cùng thư mục, qua include dir, trùng tên (`low`), `unresolved`, nằm trong `#ifdef`
   - class: kế thừa, ảo thuần, namespace, field
   - build: CMake (target, include dir, biến đơn giản, generator expression bị bỏ qua), qmake (`SUBDIRS`, `.pri`, `$$PWD`)
   - protocol: DBC, EDS, mẫu trong code (literal, định danh, `unknown`)
2. **Test libclang:** fixture có `compile_commands.json` sinh sẵn (đường dẫn tương đối, được thay khi chạy test). Dùng `pytest.importorskip` / skip nếu không tải được libclang.
3. **Test phần gộp:** tự suy component, áp khai báo, `unassigned`, `layer-order`, `deny`, `cycle`, thu gọn view, giới hạn node.
4. **Test cache:** chỉ phân tích lại file đã đổi, và `--rebuild-facts` hoạt động.
5. **Test annotate** với LLM giả: id lạ bị bỏ, dùng nhãn mặc định khi lỗi, cache hoạt động.
6. **Golden test:** fixture `mini-ecu` mở rộng (có layer, một vi phạm cố ý, một vòng phụ thuộc, DBC nhỏ, mẫu CAN trong code). Kiểm tra `model.json` ổn định và các view `.mmd`.
7. **Bench:**
   - File đáp án có thêm `violations` và `messages`.
   - `archdiag bench --compare a,b` in bảng so sánh A với B.
   - Project tham chiếu vẫn theo quy tắc bảo mật của A: đáp án nằm trong `benchmarks/private/`, chỉ ghi lại con số tổng hợp.

## 13. Tiêu chí hoàn thành

- [ ] `archdiag analyze --no-llm <project tham chiếu>` chạy dưới 30 giây, sinh các view `dependencies`, `interfaces`, `classes/*`, và không có node từ `build/` hay `3rdparty/`.
- [ ] Chạy lại khi sửa 1 file mất dưới 5 giây.
- [ ] Mọi cạnh có `evidence` trỏ tới `file:line` có thật.
- [ ] Khai báo `[architecture]` phát hiện đúng vi phạm cố ý trong fixture. `archdiag check` trả mã thoát 3.
- [ ] Có libclang và compile DB thì facts có confidence `high`. Không có thì vẫn chạy bằng tree-sitter.
- [ ] Bench cho thấy B có recall thành phần và quan hệ **cao hơn** baseline của A trên project tham chiếu.
- [ ] Unit, golden và pipeline test pass. `ruff` và `mypy --strict` sạch.
