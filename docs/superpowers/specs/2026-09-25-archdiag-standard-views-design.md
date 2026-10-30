# archdiag — Dự án C: Mô hình và ký pháp kiến trúc theo chuẩn

- Ngày: 2026-09-25
- Trạng thái: thiết kế đã duyệt, chờ duyệt spec
- Phạm vi: dự án con **C** trong lộ trình A → B → C
- Phụ thuộc:
  - spec A (`2026-09-25-archdiag-local-cli-design.md`)
  - spec B (`2026-09-25-archdiag-static-analysis-design.md`)
  - Mọi ràng buộc của A và B vẫn giữ nguyên: chạy local, không trả phí, không gửi code ra ngoài máy, quy tắc bảo mật cho project tham chiếu, LLM chỉ chú thích.

## 1. Mục tiêu

Biến mô hình chính xác của B (ArchModel v2) thành **bản mô tả kiến trúc theo ISO/IEC/IEEE 42010**, được thể hiện bằng các ký pháp chuẩn:

- **Khung ISO/IEC/IEEE 42010** (luôn có): stakeholder, concern, viewpoint, view, correspondence, decision/rationale.
- **C4 model:** Context, Container, Component, Code.
- **ArchiMate** (EA): Open Group Exchange Format, mở được trong Archi, Sparx EA, BiZZdesign.
- **SysML v2** (textual).
- **AUTOSAR:**
  - view kiểu AUTOSAR dẫn xuất cho mọi project
  - import ARXML và đối chiếu với code khi project là AUTOSAR thật
- **ISO 26262 / ASPICE:** ASIL, cảnh báo freedom from interference (FFI), truy vết requirement ↔ component ↔ test.

42010 **không phải một ký pháp**. Nó là khung mà mọi ký pháp phía trên được đặt vào, thông qua viewpoint.

### Giai đoạn (triển khai theo thứ tự, mỗi giai đoạn tự dùng được)

| Giai đoạn | Nội dung |
|---|---|
| **C1** | Metamodel 42010, tài liệu AD, C4 |
| **C2** | ArchiMate |
| **C3** | SysML v2 |
| **C4** | AUTOSAR (dẫn xuất + ARXML) và ISO 26262 / ASPICE (ASIL, FFI, truy vết) |

Truy vết requirement **chưa phải ưu tiên**, nên nằm ở C4. C1–C3 không phụ thuộc vào nó.

### Ngoài phạm vi của C

- Chứng nhận tuân thủ, và qualify tool theo ISO 26262-8 §11. Đầu ra là **bản nháp work product** để kỹ sư duyệt.
- Mô hình hoá ASIL decomposition.
- Sinh ARXML hoặc code từ mô hình (chỉ đọc ARXML, không ghi).
- Mô hình động (sequence, state machine).
- BPMN và lớp Business của ArchiMate.

## 2. Quyết định chính

| Quyết định | Lựa chọn | Lý do |
|---|---|---|
| Kiến trúc | **Metamodel 42010 chung + một exporter cho mỗi ký pháp** | Tuân thủ 42010 thật (có correspondence và rationale), ánh xạ không bị lặp, các ký pháp nhất quán, dễ thêm ký pháp mới |
| Nguồn cấu trúc | Chỉ ArchModel v2 của B và các khai báo trong `archdiag.toml` | Tất định, truy vết được. Không đoán người dùng, quyết định hay ASIL |
| Vai trò LLM | Chỉ viết văn bản mô tả (tổng quan, mô tả view) từ dữ liệu đã có | Theo nguyên tắc của B |
| Kiểm tra | Luật metamodel riêng cho từng ký pháp, XSD cho ArchiMate, validator ngoài là tuỳ chọn | Chạy được offline, không bắt buộc cài thêm công cụ |

## 3. Kiến trúc

```
src/archdiag/ad/
  model.py            # metamodel 42010: AD, Stakeholder, Concern, Viewpoint, View, Element, Relation,
                      # Correspondence, Decision
  build.py            # ArchModel v2 + archdiag.toml → AD
  viewpoints.py       # thư viện viewpoint (mục 5)
  correspondence.py   # luật correspondence và kiểm tra
  document.py         # AD → architecture-description.md
  export/
    c4.py             # Mermaid C4, Structurizr DSL, C4-PlantUML
    archimate.py      # Open Group Exchange Format XML + bố cục
    sysml.py          # SysML v2 textual
    autosar.py        # view AUTOSAR dẫn xuất
    safety.py         # view safety, ma trận truy vết
  validate/
    c4.py  archimate.py  sysml.py  autosar.py
  importers/
    arxml.py          # AUTOSAR 4.x ARXML
    reqif.py  csv.py  # requirement
```

### Luồng dữ liệu

```
ArchModel v2 (B) + archdiag.toml ([c4], [[decisions]], [safety], [autosar], [requirements])
  + docs/adr/*.md + ARXML/ReqIF/CSV (nếu có)
  → ad/build.py        → AD (metamodel 42010)
  → validate/*         → danh sách lỗi và cảnh báo cho từng ký pháp và correspondence
  → export/*           → .archdiag/ad/<notation>/…
  → ad/document.py     → .archdiag/ad/architecture-description.md
```

## 4. Metamodel 42010

| Khái niệm | Trường chính | Nguồn |
|---|---|---|
| `AD` | `system`, `version` (git commit hoặc hash nội dung), `created_at`, `tool_version` | pipeline |
| `Stakeholder` | `id`, `name`, `concern_ids[]` | mặc định, ghi đè được bằng `[[stakeholders]]` |
| `Concern` | `id`, `text` | mặc định, ghi đè được bằng `[[concerns]]` |
| `Viewpoint` | `id`, `name`, `concern_ids[]`, `model_kinds[]`, `notation`, `element_types[]`, `relation_types[]`, `correspondence_rules[]` | thư viện |
| `View` | `id`, `viewpoint_id`, `element_ids[]`, `relation_ids[]`, `diagram_files[]`, `issues[]` | build |
| `Element` | `id`, `name`, `type`, `b_ids[]` (id gốc của B), `properties` (technology, asil, kind, …), `description` | B + khai báo |
| `Relation` | `id`, `type`, `source`, `target`, `b_edge_ids[]`, `description` | B + khai báo |
| `Correspondence` | `rule_id`, `element_ids[]`, `status` (ok hoặc violated) | correspondence.py |
| `Decision` | `id`, `title`, `status`, `rationale`, `affected_element_ids[]`, `source` (toml hoặc ADR) | `[[decisions]]`, `docs/adr/*.md` |

- **Loại element trung lập:** `system`, `container`, `component`, `class`, `interface`, `message`, `actor`, `external`, `layer`, `requirement`, `test`, `device`, `bus`.
- **Loại relation trung lập:** `depends`, `realizes`, `composes`, `specializes`, `flows`, `allocated`, `verifies`, `satisfies`, `contains`.
- **Truy vết:** mọi element và relation giữ `b_ids` / `b_edge_ids`, nên truy ngược được về `evidence` (`file:line`) của B.

### Stakeholder và concern mặc định

| Stakeholder | Concern |
|---|---|
| Developer | cấu trúc module, phụ thuộc giữa các phần, interface cần dùng |
| Architect | phân lớp, vi phạm kiến trúc, quyết định kiến trúc |
| Integrator | build target, thư viện ngoài, giao tiếp với thiết bị và bus |
| Tester | thành phần nào đã có test, ranh giới cần test tích hợp |
| Safety engineer | phân bổ ASIL, freedom from interference, truy vết requirement |

Ghi đè bằng `[[stakeholders]]` / `[[concerns]]` trong `archdiag.toml`.

## 5. Thư viện viewpoint và ánh xạ

| Giai đoạn | Viewpoint | Concern chính | Ký pháp |
|---|---|---|---|
| C1 | `c4-context` | hệ thống, bên ngoài | C4 |
| C1 | `c4-container` | đơn vị build/chạy, công nghệ | C4 |
| C1 | `c4-component` | component trong từng container | C4 |
| C1 | `c4-code` | class/interface của một component | C4 (dùng lại `classes/*` của B) |
| C2 | `archimate-application-cooperation` | component, interface, luồng dữ liệu | ArchiMate |
| C2 | `archimate-technology` | artifact, thiết bị, bus | ArchiMate |
| C3 | `sysml-structure` | part, cấu trúc phân cấp | SysML v2 |
| C3 | `sysml-interface` | port, item, kết nối | SysML v2 |
| C4 | `autosar-swc` | SWC, port, interface, layer AUTOSAR | AUTOSAR-style |
| C4 | `safety-architecture` | ASIL, FFI, truy vết | ISO 26262 / ASPICE |

### 5.1 C4 (C1)

| Nguồn | C4 |
|---|---|
| repo (L0) | Software System |
| actor thiết bị (từ protocol), hệ thống ngoài khai báo `[[c4.external_systems]]` | External Software System |
| người dùng | Person, **chỉ lấy từ khai báo** `[[c4.people]]`, không đoán |
| `build_target` exe/lib | Container, `technology` = ngôn ngữ + framework (từ build facts và include ngoài, ví dụ "C++ / Qt") |
| component của B | Component, gán vào Container theo `sources` của target. Không xác định được thì gán vào container chính |
| thư viện ngoài | ghi trong `technology`, hoặc External System nếu được khai báo |
| cạnh include hoặc message | Relationship, `description` = nhãn cạnh (từ LLM) hoặc loại + weight, `technology` = protocol nếu là message |
| `classes/<component>` | Code view |

**Đầu ra** trong `ad/c4/`:
- `context.mmd`, `container.mmd`, `component-<container>.mmd` (cú pháp `C4Context` / `C4Container` / `C4Component` của Mermaid)
- `workspace.dsl` (Structurizr DSL)
- `*.puml` (C4-PlantUML, `!include` bản cục bộ đi kèm)

### 5.2 ArchiMate (C2)

| Nguồn | ArchiMate |
|---|---|
| component | ApplicationComponent |
| interface ảo thuần | ApplicationInterface, gắn vào component cung cấp bằng **Composition** |
| layer | Grouping (**Aggregation** các component) |
| A phụ thuộc B (include) | **Serving**: B → A |
| message A → B | **Flow**: A → B, nhãn là tên message |
| message (dữ liệu) | DataObject, được **Access** bởi bên gửi và bên nhận (tuỳ chọn, tắt mặc định) |
| exe / lib | Artifact **Realization** → ApplicationComponent |
| thiết bị | Device, bus CAN là CommunicationNetwork. Artifact được **Assignment** tới Node hoặc Device khi có khai báo |

- **Đầu ra:** `ad/archimate/model.xml`, theo Open Group ArchiMate Model Exchange File Format 3.x (namespace `http://www.opengroup.org/xsd/archimate/3.0/`), gồm `elements`, `relationships`, `organizations`, và `views` có toạ độ.
- **Bố cục:** tất định, xếp theo tầng. Mỗi layer là một hàng, các component trong hàng xếp theo tên. Kích thước cố định 120×55, khoảng cách 40.

### 5.3 SysML v2 (C3)

| Nguồn | SysML v2 |
|---|---|
| repo | `package <System>` và `part system : <System>` |
| component | `part def <Component>` và `part <component> : <Component>` trong `system` |
| message | `item def <Message>` (thuộc tính `id` nếu có) |
| component gửi hoặc nhận | `port def <Component>Port` với `out item` / `in item` |
| cặp gửi-nhận | `connect <a>.<port> to <b>.<port>;` |
| include | `dependency from <a> to <b>;` |
| interface ảo thuần | `port def` với các hàm liệt kê trong `doc` |

- **Tên:** tên không hợp lệ được escape bằng `'…'`. Tên được làm duy nhất trong mỗi namespace.
- **Đầu ra:** `ad/sysml/model.sysml`.

### 5.4 AUTOSAR (C4)

**View dẫn xuất** (cho mọi project, luôn ghi nhãn **"AUTOSAR-style (derived), not an AUTOSAR model"**):

| Nguồn | AUTOSAR |
|---|---|
| component `kind = driver/hal` có `message_io` | ComplexDeviceDriver SWC (hoặc SensorActuator nếu được khai báo) |
| component `kind = service` | Service SWC |
| component khác | Application SWC |
| send message | PPort + SenderReceiverInterface (data element = message) |
| recv message | RPort + SenderReceiverInterface |
| interface ảo thuần được cài đặt / được dùng | PPort / RPort + ClientServerInterface (operation = hàm ảo) |
| layer | Application / RTE / BSW (Services, ECU Abstraction, MCAL) / Complex Drivers, theo `[autosar.layers]` hoặc theo `kind` |

**Import ARXML** (AUTOSAR 4.x, namespace `http://autosar.org/schema/r4.0`):
- **Đọc:** `APPLICATION-SW-COMPONENT-TYPE` (và các loại SWC khác), `COMPOSITION-SW-COMPONENT-TYPE`, `P-PORT-PROTOTYPE`, `R-PORT-PROTOTYPE`, `SENDER-RECEIVER-INTERFACE`, `CLIENT-SERVER-INTERFACE`, `ASSEMBLY-SW-CONNECTOR`.
- **Bổ sung cho B:** preset `autosar-rte`. Preset này bắt các lời gọi `Rte_Write_<p>_<d>`, `Rte_Read_…`, `Rte_Send_…`, `Rte_Receive_…`, `Rte_Call_<p>_<op>` thành `message_io`, và file `Rte_<Swc>.h` thành gợi ý ánh xạ SWC ↔ component.
- **Ánh xạ SWC ↔ component:** ưu tiên `[autosar.mapping]`, sau đó đến tên `Rte_<Swc>.h`, cuối cùng là trùng tên.
- **Báo cáo khác biệt (thiết kế trong ARXML so với code):**
  - port khai báo nhưng không có lời gọi RTE
  - lời gọi RTE tới port hoặc data element không có trong ARXML
  - SWC không ánh xạ được tới code
  - connector giữa hai SWC không có luồng tương ứng trong code

**Đầu ra:** `ad/autosar/swc.mmd`, `swc.puml`, `arxml-diff.md` (khi có ARXML).

### 5.5 ISO 26262 / ASPICE (C4)

**Nguồn dữ liệu (dùng cả ba, gộp theo id):**

1. `archdiag.toml`:
   ```toml
   [[safety.components]]
   name = "can_driver"
   asil = "B"                        # QM | A | B | C | D
   requirements = ["SW-REQ-042"]
   safety_goals = ["SG-1"]
   ```
2. Tag trong comment code, do B thu thập kèm `file:line` (thêm fact `tag`):
   - `@req <ID>`, `@asil <QM|A|B|C|D>`, `@safety_goal <ID>`
   - Trong test, `@req` tạo quan hệ `verifies`.
3. Import requirement:
   - **ReqIF** (`SPEC-OBJECT`): tên thuộc tính cho id, text và ASIL cấu hình trong `[requirements.reqif]`
   - **CSV:** cấu hình cột trong `[requirements.csv]`

**Xung đột:** nếu cùng một component có ASIL khác nhau giữa các nguồn thì lấy ASIL **cao nhất**, và ghi cảnh báo.

**Kiểm tra (cảnh báo, không chặn):**

| Kiểm tra | Điều kiện |
|---|---|
| `ffi-dependency` | component ASIL cao phụ thuộc (include, message) component ASIL thấp hơn hoặc QM. Cần lập luận freedom from interference |
| `asil-missing` | đã khai báo ASIL cho một số component nhưng component này chưa có |
| `req-unallocated` | requirement không được gán cho component nào |
| `component-untraced` | component có ASIL khác QM nhưng không có requirement |
| `req-unverified` | requirement không có test nào `verifies` |

**Đầu ra:**
- `ad/safety/safety.mmd`: component tô màu theo ASIL, cạnh FFI được đánh dấu
- `trace-matrix.csv` và `trace-matrix.md`: requirement ↔ component ↔ test, kèm `file:line`
- mục Safety trong tài liệu AD

Đầu ra đi theo gợi ý của ISO 26262-6 §7 (kiến trúc phần mềm: cấu trúc phân cấp, interface, phân bổ ASIL) và ASPICE SWE.2 (mô tả tĩnh, interface, truy vết hai chiều với SWE.1). Đây là **bản nháp để duyệt, không phải bằng chứng đã qualify**.

## 6. Tài liệu AD (`architecture-description.md`)

1. **Định danh và tổng quan:** hệ thống, version, ngày, phiên bản tool, phạm vi, ghi chú giới hạn (bản nháp, tool chưa được qualify).
2. **Stakeholder và concern:** bảng, cộng ma trận concern ↔ viewpoint.
3. **Viewpoint:** mỗi viewpoint có concern, model kind, ký pháp, luật correspondence.
4. **Các view:** mỗi view có sơ đồ Mermaid nhúng sẵn, link tới file ký pháp khác, danh mục element (tên, loại, mô tả, path), và vấn đề của view.
5. **Correspondence:** bảng các luật kèm trạng thái.
6. **Quyết định kiến trúc:** từ `[[decisions]]` và ADR. Không có thì ghi rõ "chưa có quyết định được ghi lại".
7. **Điểm chưa nhất quán và vi phạm:** từ B (`layer-order`, `deny`, `cycle`, `unassigned`), từ các bộ kiểm tra ký pháp, và từ correspondence.
8. **Phụ lục:** bảng truy vết element → `file:line` (tối đa 20 mẫu mỗi element).

LLM chỉ viết đoạn tổng quan và mô tả view từ dữ liệu đã có, theo `language`. `--no-llm` thì các đoạn này chỉ chứa dữ kiện.

## 7. Kiểm tra đúng chuẩn

| Ký pháp | Kiểm tra |
|---|---|
| C4 | Context chỉ chứa Person và Software System. Container chỉ chứa Container và phần tử ngoài. Mọi relationship có `description`. Tên duy nhất trong mỗi view |
| ArchiMate | Mọi relationship nằm trong **bảng quan hệ hợp lệ** của đặc tả ArchiMate 3.x cho các loại phần tử được dùng (bảng nhúng trong code, có test). Kiểm tra theo **XSD** của Open Group khi đã chạy `archdiag fetch-schemas` (cache tại `~/.cache/archdiag/schemas/`), nếu chưa thì bỏ qua kèm ghi chú. Mọi node trong view tham chiếu element có thật |
| SysML v2 | Tên đã escape. Mọi `def` được tham chiếu đều tồn tại. `connect` nối hai port có item tương thích (một `out`, một `in`). Kiểm tra thêm bằng công cụ ngoài nếu cấu hình `sysml_validator = "<lệnh>"` |
| AUTOSAR | PPort và RPort được nối dùng cùng interface. SR và CS không trộn lẫn. Mọi SWC có ít nhất một port hoặc được đánh dấu cô lập |
| Correspondence | Ví dụ: mọi C4 Component có ArchiMate ApplicationComponent tương ứng, mọi part SysML có component của B tương ứng. Vi phạm được ghi vào AD |

Lỗi kiểm tra **không chặn việc xuất file**. Lỗi được ghi vào `run.json` và AD, và `archdiag ad --strict` trả mã thoát `3`.

## 8. CLI và web

```
archdiag ad <repo> [--notation c4,archimate,sysml,autosar,safety] [--viewpoints ID,...] [--strict] [--no-llm]
archdiag fetch-schemas                 # tải XSD ArchiMate một lần (cần mạng, chỉ lần đầu)
```

- Mặc định `--notation` bao gồm các giai đoạn đã triển khai.
- **Đầu ra:**

  ```
  .archdiag/ad/
    architecture-description.md
    c4/  archimate/  sysml/  autosar/  safety/
    validation.json
  ```

- **Web:**
  - trang AD (render markdown đã sanitize)
  - chọn ký pháp cho mỗi view
  - tải từng file xuất
  - bảng kiểm tra và correspondence
  - bấm vào element để mở `file:line`

## 9. Xử lý lỗi

| Tình huống | Xử lý |
|---|---|
| Không có ArchModel v2 (chưa chạy B) | Tự chạy phân tích của B trước |
| Khai báo sai trong `[c4]`, `[safety]`, `[autosar]`, `[requirements]`, `[[decisions]]` | Mã thoát `2`, chỉ rõ khoá bị sai |
| ARXML, ReqIF hoặc CSV hỏng hay không đúng schema | Cảnh báo, bỏ qua nguồn đó, vẫn xuất phần còn lại |
| Chưa có XSD ArchiMate | Bỏ qua kiểm tra XSD, ghi chú gợi ý `archdiag fetch-schemas` |
| Validator SysML ngoài lỗi hoặc không có | Chỉ dùng kiểm tra nội bộ, kèm ghi chú |
| Một exporter lỗi | Các ký pháp khác vẫn xuất. Lỗi được ghi vào `validation.json`, mã thoát `1` |

## 10. Kiểm thử

1. **Unit test cho bảng ánh xạ** của từng ký pháp (element và relation), bao gồm các trường hợp biên: component không thuộc target nào, message `unknown`, tên có ký tự đặc biệt hoặc tiếng Việt.
2. **Golden file** cho mỗi exporter trên fixture `mini-ecu` của B: `.mmd`, `workspace.dsl`, `.puml`, `model.xml`, `model.sysml`, `swc.mmd`, `trace-matrix.csv`, `architecture-description.md`. Chạy lại phải ra kết quả giống hệt.
3. **ArchiMate:** test bảng quan hệ hợp lệ. Test XSD (bỏ qua khi chưa tải schema).
4. **SysML v2:** test escape tên, `def` bị thiếu, `connect` sai hướng.
5. **AUTOSAR:** fixture ARXML 4.x nhỏ tự viết, cộng code có lời gọi `Rte_*`. Kiểm tra từng loại khác biệt.
6. **Safety:** fixture có ASIL lấy từ toml, tag và CSV, có xung đột ASIL. Kiểm tra `ffi-dependency`, `req-unallocated`, `req-unverified`.
7. **ReqIF:** fixture nhỏ tự viết.
8. **Correspondence:** cố ý làm thiếu một ánh xạ và kiểm tra vi phạm được báo.
9. **Kiểm tra thủ công mỗi giai đoạn** (ghi lại trong tài liệu, không tự động):
   - `workspace.dsl` mở được trong Structurizr
   - `model.xml` import được vào Archi
   - `.puml` render được bằng PlantUML

## 11. Tiêu chí hoàn thành

**C1**
- [ ] `archdiag ad --notation c4 <project tham chiếu>` sinh AD và C4 (Context, Container, Component, Code).
- [ ] Các file `.mmd` render được trong web.
- [ ] `workspace.dsl` mở được trong Structurizr.
- [ ] Mọi element trong AD truy ngược được về `file:line`.

**C2**
- [ ] `model.xml` qua được XSD, import được vào Archi, sơ đồ hiển thị theo tầng.

**C3**
- [ ] `model.sysml` qua được bộ kiểm tra nội bộ, và cả validator ngoài nếu có cấu hình.

**C4**
- [ ] View AUTOSAR dẫn xuất chạy được trên project tham chiếu.
- [ ] Báo cáo khác biệt ARXML đúng trên fixture.
- [ ] Ma trận truy vết và cảnh báo FFI đúng trên fixture.

**Tất cả giai đoạn**
- [ ] Unit test và golden test pass. `ruff` và `mypy --strict` sạch.
