"""MCP prompts for vDB: onboarding, create/restore/configuration flows, triage.

Everything here is choreography that deliberately does **not** live in a tool
docstring. Per the monorepo convention, a docstring carries the discovery chain
and the guardrails; the long "ask this, then that, confirm, poll" flow belongs
in guidance loaded on demand, or every write tool's description turns into an
essay.

The same content is served two ways. MCP prompts have to be loaded by the user,
and agents very often run promptless -- so each flow is also reachable through
the `get_vdb_guide` tool, which an agent calls on its own initiative.

The guide bodies are written in Vietnamese, matching vks's prompts_handler:
they are end-user-facing conversation guidance, not source code text. Code --
module, class and method docstrings, comments -- stays English per the monorepo
convention.

Every rule below was measured against the live API. Where something is a
platform behaviour that contradicts the OpenAPI document, it is called out as
such, because an agent that trusts the spec over this file will get it wrong.
"""

from __future__ import annotations

from greennode.vdb_mcp_server.tool_annotations import READ
from pydantic import Field
from typing import Literal


GuideTopic = Literal[
    "getting_started",
    "create_instance",
    "restore_backup",
    "configuration_group",
    "backups_and_storage",
    "troubleshooting",
]


_GETTING_STARTED = """\
# GreenNode vDB — Bắt đầu

vDB là dịch vụ database managed của GreenNode. Bạn mô tả nhu cầu bằng ngôn ngữ
tự nhiên; trợ lý tự khám phá tài nguyên sẵn có, đề xuất default an toàn, và xác
nhận trước khi tạo hoặc thay đổi bất cứ thứ gì. Bạn KHÔNG cần biết ID tài
nguyên thô.

## Bốn họ sau một gateway

vDB là bốn API riêng biệt dùng chung một gateway và một token. Tool được đặt
tiền tố theo họ và **không** thay thế cho nhau — cùng một thao tác thường có
path khác nhau, đôi khi cả HTTP method khác nhau, ở mỗi họ:

| Họ | Engine | Tiền tố tool |
|---|---|---|
| Relational | MySQL, MariaDB, PostgreSQL đơn lẻ | `*_relational_*` |
| MemoryStore | Redis | `*_memory_*` |
| PostgreSQL Cluster | PostgreSQL dạng cụm | `*_postgresql_*` |
| Kafka | Kafka | `*_kafka_*` |

Xác định người dùng đang nói về sản phẩm nào TRƯỚC khi đi tìm. "Không thấy" ở
một họ không chứng minh được là không tồn tại ở họ khác.

## ID của instance KHÔNG cho biết nó thuộc họ nào

Tiền tố `db-` được dùng bởi **cả** relational lẫn MemoryStore. Chỉ `pg-`
(PostgreSQL Cluster) là đặc trưng. Vì vậy:

- Không bao giờ suy ra họ từ id.
- `get_memory_instance` kiểm tra chặt theo họ: nó trả về kết quả tức là instance
  đó đúng là Redis. `get_postgresql_cluster` cũng chặt, nhưng là do **tool tự
  kiểm tra tiền tố** chứ không phải do API — endpoint nó gọi là của họ
  relational và nhận mọi loại id.
- `get_relational_instance` thì **không**: nó trả về cả instance Redis lẫn cụm
  `pg-` mà không phàn nàn gì. Hãy đọc `datastore_type` của thứ nhận được trước
  khi coi nó là relational.
- Các endpoint backup, configuration và parameter cũng vậy: bản của họ này giải
  được tài nguyên của họ kia. Đọc field engine, đừng tin path mình vừa gọi.

## Không có region, không có project id

Một gateway phục vụ toàn bộ account và tự xác định project từ token. Không tool
nào có tham số `region`, nên cũng không có gì để nhầm.

## Mặc định chỉ đọc

Thao tác đọc chạy được ngay. Tạo, sửa, restore và xoá yêu cầu server khởi động
với `--allow-write`; nếu một thao tác ghi báo lỗi bị tắt, hãy bảo người dùng
khởi động lại server với cờ đó thay vì tìm đường khác.

## Xác thực

Credential lấy từ `~/.greennode/credentials` và `~/.greennode/config`, dùng
chung với `greennode-cli` (`grn configure` ghi ra hai file này). Biến môi trường
được ưu tiên cao hơn: `GRN_CLIENT_ID`, `GRN_CLIENT_SECRET`, `GRN_PROFILE`,
`GRN_DEFAULT_REGION`, `GRN_PROJECT_ID`.

## Câu hỏi nào → tool nào

- Đang có gì: `list_relational_instances`, `list_memory_instances`,
  `get_*_instance`.
- Nền tảng cung cấp gì: các tool catalog —
  `list_*_datastores` (**bắt đầu từ đây**: cặp engine + version),
  `list_relational_zones`, `list_*_flavors`, `list_*_volume_types`,
  `list_*_subnets`.
- Đã xảy ra chuyện gì: `list_*_instance_histories`. Đây là nơi **duy nhất** ghi
  lại một lỗi bất đồng bộ — xem guide `troubleshooting`.
- Backup: `list_*_backups` (dòng index), `get_*_backup` (chi tiết thật).
- Tham số ghi đè: `list_*_configurations`, `list_*_configuration_params`.
- Quota backup trả phí: `get_*_backup_storage`,
  `list_*_backup_storage_packages`.
- Cụm PostgreSQL: `list_postgresql_clusters` / `get_postgresql_cluster` (suy ra
  từ listing relational — xem bên dưới), `list_postgresql_cluster_secrules`,
  `list_postgresql_cluster_histories`, và các tool backup
  `list_postgresql_restore_points` / `list_postgresql_backup_locations` /
  `list_postgresql_backup_policies`.

## Họ PostgreSQL Cluster không có endpoint list, cũng không có get

Hai tool đọc của nó **phái sinh** từ listing của họ relational rồi lọc theo tiền
tố `pg-`. Hệ quả phải nói rõ với người dùng:

- **Kết quả có lọc không bao giờ chính xác tuyệt đối.** API không áp filter
  `name`/`statuses` lên dòng cụm, nên việc lọc làm tại chỗ, chỉ trong những
  trang đã quét. Trường `filters_are_approximate` báo điều đó.
- **Quét theo trang có thể dừng sớm**: dòng relational dùng chung phân trang, nên
  `more_pages_exist` nghĩa là còn cụm chưa thấy — tăng `max_pages`.

Năm thao tác nữa của cụm cũng không có endpoint riêng và dùng endpoint của họ
relational: security rule (đọc + ghi), history, reboot và **xoá**. Nhưng đừng
suy rộng: `replicas` trả 403 và `backups/insId` trả mảng rỗng cho một cụm.

## Kafka khác mọi họ còn lại — đừng mang quy ước nào sang

- **Bắt đầu bằng `get_kafka_limits`.** Đây là chỗ DUY NHẤT trong vDB mà nền tảng
  tự công bố luật kiểm tra của nó: số broker 3-10 (cần quorum nên sàn là **3**,
  không phải 2 như cụm PostgreSQL), storage 20-5000 GB **mỗi broker**, tối đa 50
  topic và 50 user mỗi cụm, và **năm cổng duy nhất** một security rule được mở:
  9092, 9094, 9096, 9194, 9196. Nó cũng có regex tên — khác nhau theo từng loại
  tài nguyên: tên config group được có dấu cách, tên topic được có dấu chấm, tên
  cụm thì không được cả hai.
- **Một giới hạn KHÔNG nằm ở đó**: replication factor của topic không được vượt
  số broker của cụm. Đọc `broker_count` từ `get_kafka_cluster`.
- **Hai id dễ nhầm nhất**: create nhận `flav-…` (không phải `id` số của flavor)
  và `vtype-…` (không phải `id` số, cũng không phải tên `type` của volume type).
- **Quyền của user là DANH SÁCH hoặc CỜ, không bao giờ cả hai.** Khi cờ `*_all`
  bật thì nền tảng **bỏ qua** danh sách — nên một user hiện `produce_all: true`
  cạnh hai tên topic thực ra ghi được vào MỌI topic. Đọc cờ trước danh sách.
- **Config group của Kafka có version và bất biến.** Cụm gắn vào một
  **version** (`cgroupver-…`), không phải group (`cgroup-…`). Tạo group hay tạo
  version **không đụng gì tới cụm đang chạy**; chỉ
  `update_kafka_cluster_config_group` mới đụng, và nó **restart lần lượt từng
  broker**. Chỉ `get_kafka_config_group_version` trả về settings — group detail
  để trống.
- **Security rule chỉ thấy được trong `get_kafka_cluster`**; không có endpoint
  nào liệt kê chúng, và listing thì bỏ hẳn field này.
- **Credential của user là file ZIP chứa private key và mật khẩu**, không phải
  danh sách chuỗi như spec ghi. `get_kafka_user_credentials` ghi ra file và chỉ
  báo lại đường dẫn + tên các file bên trong; **tuyệt đối không đọc nội dung
  file đó vào khung chat**. Tool này chỉ tồn tại khi server chạy với
  `--allow-sensitive-data-access`.
- **Kafka không có zone và không có backup trong vDB.** Xoá một cụm là mất toàn
  bộ topic, message, user và credential, không có gì để restore.

## Nguyên tắc

- Đọc thì tự do; **mọi** thao tác ghi phải qua MỘT lần xác nhận rõ ràng.
- Không bao giờ tự quyết flavor, dung lượng, chu kỳ tính phí hay mật khẩu thay
  người dùng. Hãy đề xuất, đánh dấu rõ đó là đề xuất, và cho họ sửa.
- Resolve tên → ID bằng discovery tool; đừng bắt người dùng dán ID thô.
- Khi hiển thị danh sách hay chi tiết tài nguyên, đặt `id` và `name` lên **đầu**
  và không bao giờ cắt ngắn id — lệnh gọi tiếp theo cần nó.
- `create_*`, `resize_*` và `restore_*` tạo order **tốn tiền thật**. Mỗi tool có
  một bản `*_dryrun` không gửi gì cả; chạy nó và trình payload trước.
- **HTTP 200 ở API này KHÔNG có nghĩa là thành công.** Xem `troubleshooting`.
- Trả lời bằng ngôn ngữ người dùng đang dùng. Không bao giờ dán mật khẩu ngược
  lại vào khung chat.
"""


_CREATE_INSTANCE = """\
# Tạo instance vDB — luồng có hướng dẫn

Áp dụng cho `create_relational_instance` và `create_memory_instance`. Cả hai đều
tạo **order tốn tiền thật** và cấp phát bất đồng bộ.

## Trước khi hỏi bất cứ điều gì

1. Xác định họ. Relational (MySQL / MariaDB / PostgreSQL) hay MemoryStore
   (Redis)? Hai bên nhận body khác nhau; đừng đoán.
2. `list_*_datastores` → các cặp engine và version thực sự tồn tại.

## Hỏi theo đúng thứ tự này, mỗi câu hỏi MỘT cấu hình

Không gộp hai cấu hình vào một câu hỏi, và không bỏ qua bước nào một cách thầm
lặng. Mọi giá trị bạn đề xuất phải được đánh dấu là đề xuất và cho người dùng
sửa.

1. **Engine và version** — từ `list_*_datastores`.
2. **Zone** — `list_relational_zones` (`HCM03-1A`, `HCM03-1B`, `HCM03-1C`).
   Họ memory không có endpoint zone riêng; zone là chung cho cả account nên dùng
   endpoint của họ relational cho cả hai.
3. **Flavor** — `list_*_flavors` với engine, version **và zone đã chọn**. Hiển
   thị vCPU, RAM và chi phí hằng tháng.
4. **Storage** (chỉ relational) — `list_relational_volume_types` cho đúng zone
   đó; tôn trọng khoảng dung lượng nó báo. **Họ memory không có field volume
   nào cả**: flavor Redis đã kèm sẵn disk, và họ này cũng không có
   `resize-storage`.
5. **Mạng** — `list_*_subnets`. Lệnh create cần id **subnet** (`sub-...`).
   `list_*_networks` trả về network, không phải thứ field này muốn.
6. **Thông tin đăng nhập quản trị.**
   - Relational: tên user và mật khẩu, cộng **ít nhất một database**.
     `databases: []` bị từ chối dù spec ghi là tuỳ chọn.
   - MemoryStore: một master password. Không có khái niệm user và database.
7. **Public access** — mặc định tắt. Nói rõ nó làm gì và KHÔNG làm gì; xem cảnh
   báo bên dưới.
8. **Backup tự động** — mặc định tắt. Nếu bật, `backupDuration` là **số NGÀY giữ
   lại, 2-14** (không phải độ dài cửa sổ backup, dù nó nằm ngay cạnh
   `backupTime`), còn `backupTime` là giờ trong ngày, ví dụ `"02:00"`.
9. **Configuration group** — tuỳ chọn, và có thể bỏ qua với instance đầu tiên.

## Những luật API bắt buộc nhưng giải thích rất tệ

- **`packageId`, `volumeType` và `locateZoneId` phải cùng đến từ MỘT zone.** Id
  của flavor khác nhau theo zone (cùng một tên flavor là id 224 ở 1A, 240 ở 1B,
  256 ở 1C) và volume type có hậu tố zone ở ngoài `HCM03-1A`
  (`Gen2-NVMe2-IOPS3000-HCM03-1B`). Trộn zone là nguyên nhân bị từ chối phổ
  biến nhất.
- **Danh sách flavor lấy mà không kèm zone chỉ mô tả `HCM03-1A`.** Bỏ trống
  `zoneId` nghĩa là zone mặc định, không phải mọi zone.
- **Luật mật khẩu khác nhau theo họ** và hoàn toàn không có trong spec:

  | Họ | Độ dài | Bộ ký tự |
  |---|---|---|
  | Relational | **8-32** | chữ cái, chữ số và `$ ^ _ < >` |
  | MemoryStore | **16-128** | cùng bộ ký tự |

  Cả hai đều phải bắt đầu bằng chữ cái và kết thúc bằng chữ cái hoặc chữ số. Một
  mật khẩu hợp lệ cho MySQL thường quá ngắn cho Redis. Tool kiểm tra tại chỗ nên
  lỗi ở đây sẽ nói đúng vấn đề.
- **MemoryStore: `publicAccess` đòi phải bật master password.** Nếu không, nền
  tảng từ chối tổ hợp này.
- **Field lạ bị bỏ qua chứ không bị từ chối.** Gửi field chỉ có ở relational vào
  lệnh create của memory vẫn trả 200 và cấp phát một instance có kích thước theo
  flavor chứ không theo thứ đã yêu cầu. DTO có kiểu là thứ duy nhất chặn được
  điều này — luôn đi qua tool, đừng bao giờ tự dựng body.

## Cổng xác nhận

Chạy `create_*_instance_dryrun` và trình cho người dùng:

- flavor, storage, zone và subnet chính xác;
- rằng đây là order **tốn tiền thật**;
- rằng **instance mới mặc định mở ra `0.0.0.0/0` trên cổng database**, bất kể
  `publicAccess` đặt thế nào — `publicAccess` quản floating IP, không quản
  firewall. Hãy dự tính thu hẹp nó bằng `update_*_instance_secrules` ngay sau
  khi tạo xong.

Sau đó hỏi xác nhận trong một câu hỏi rõ ràng. Chỉ khi có "đồng ý" tường minh
mới gọi tool thật.

## Sau khi đặt order

- Với luồng mặc định `IAM_USER` (Auto Payment), order trả về `resourceId`, nhưng
  việc cấp phát là bất đồng bộ: poll `get_*_instance` qua `BUILDING` tới
  `ACTIVE`.
- **Đừng** chuyển sang `ROOT_USER` trừ khi người dùng chủ động muốn một order
  cần duyệt. Nó cũng trả 200, và **không tạo gì cả** cho tới khi có người hoàn
  tất order trong console thanh toán.
- `list_*_instance_secrules` → thu hẹp rule `0.0.0.0/0` mặc định. Lưu ý
  `update_*_instance_secrules` **thay thế toàn bộ tập rule**, nên phải gửi lại
  mọi rule cần giữ, mỗi rule kèm `id` của nó.
- Nếu instance rơi vào `ERROR` hoặc `INTERNAL_ERROR` thì đó là lỗi phía nền
  tảng. Báo lại id, zone và payload đã gửi. Đừng retry như thể request sai.
"""


_RESTORE_BACKUP = """\
# Restore backup vDB — luồng có hướng dẫn

## Nói điều này trước tiên, trước mọi thứ khác

**Restore không hoàn tác bất cứ thứ gì.** `restore_relational_backup` và
`restore_memory_backup` dựng ra một **instance thứ hai, hoàn toàn mới** từ
backup. Bản backup và instance gốc của nó không hề bị đụng tới. Nó được tính
tiền như tạo instance mới, không phải như một thao tác sửa chữa.

Nếu điều người dùng thực sự muốn là "đưa dữ liệu cũ trở lại instance đang chạy"
thì câu trả lời là: restore ra một instance mới, kiểm tra nó, rồi mới quyết định
xử lý instance gốc — và việc xoá instance gốc là quyết định của họ, một bước
riêng và tường minh.

## Luồng

1. **Tìm bản backup.** `list_*_backups` (toàn project, có phân trang, **không có
   filter**) hoặc `list_*_instance_backups` (một instance, không phân trang).
2. **Đọc nó bằng `get_*_backup`, đừng đọc từ dòng listing.** Dòng listing chỉ là
   mục index: nó trả về các field kích thước, flavor, mạng và credential đều là
   `null`, và viết thường tên engine. Lập kế hoạch restore từ một dòng listing
   sẽ gửi đi toàn `null` ở đúng những field mà restore cần.
3. **Kiểm tra status là `COMPLETED`.** Restore từ một backup còn ở
   `NEW` / `BUILDING` / `SAVING` là restore từ dữ liệu chưa hoàn chỉnh.
4. **Kiểm tra engine.** Không endpoint `get_*_backup` nào giới hạn theo họ, nên
   có kết quả không chứng minh backup thuộc về họ bạn vừa hỏi. Đọc
   `datastore_type` và dùng tool restore của đúng họ đó — hai body restore khác
   nhau.
5. **Chọn hình dạng cho instance mới**, y như khi tạo mới: zone, flavor, subnet,
   và (chỉ relational) volume type cùng dung lượng. Luật cùng-một-zone vẫn áp
   dụng nguyên vẹn.
   - Relational: `volumeSize` phải ít nhất bằng `storage_size_gb` của bản gốc.
   - MemoryStore: đối chiếu RAM của flavor với `ram_gb` của backup; không có
     field volume nào để đặt.
6. **`network_ids` của backup KHÔNG dùng được ở đây.** Đó là id network
   (`net-...`); lệnh restore cần id subnet (`sub-...`) từ `list_*_subnets`. Ở
   phía API cả hai đều tên là `netIds` — đó chính là cái bẫy.
7. **Chỉ MemoryStore:** `redisPassword` đặt master password cho instance
   **mới**. Nó không được kế thừa từ bản gốc. 16-128 ký tự.
8. **Chạy dry run** — `restore_*_backup_dryrun`, trình payload cùng chi phí, và
   hỏi xác nhận trong một câu hỏi.
9. **Restore**, rồi poll `get_*_instance` qua `BUILDING` tới `ACTIVE`, và thu
   hẹp rule `0.0.0.0/0` của instance mới.
"""


_CONFIGURATION_GROUP = """\
# Configuration group vDB — luồng có hướng dẫn

Configuration group là một tập tham số engine ghi đè, có tên, để các instance
gắn vào. Tạo và sửa nó thì miễn phí; rủi ro nằm hoàn toàn ở chỗ nó tác động thế
nào lên các database đang chạy.

## Luật quan trọng nhất

**Sửa một group là sửa mọi instance đang gắn vào nó.** Trước bất kỳ lần update
nào:

1. `get_*_configuration` → đọc `values` và, trên hết, đọc `instances`. Mọi
   instance trong danh sách đó đều bị ảnh hưởng, kể cả instance production.
2. **Tin `instances`, đừng tin `instanceCount`.** Một instance vừa được gắn đã
   xuất hiện trong danh sách trong khi con số đếm vẫn còn là `0`.
3. Trình danh sách đó cho người dùng và xác nhận trước khi đổi bất cứ thứ gì.

## Ngữ nghĩa restart — chỗ này tốn kém

`list_*_configuration_params` báo `restart_required` cho từng tham số. Truyền
`restart_required=true` sẽ cho thấy chính xác những tham số nào buộc phải reboot.

- Đổi một tham số như vậy sẽ đưa mọi instance đang gắn sang trạng thái
  `RESTART_REQUIRED`, và **instance vẫn tiếp tục phục vụ bằng giá trị CŨ cho tới
  khi có người reboot nó**. Thay đổi trông như đã áp dụng nhưng thực ra chưa.
- **Số tham số cần restart bằng không KHÔNG phải là lời hứa rằng không cần
  reboot.** Đã đo: Redis 7.2 báo không có tham số nào cần restart, vậy mà thao
  tác *gỡ* group ra vẫn đẩy instance sang `RESTART_REQUIRED`. **Trạng thái của
  instance** mới là thứ quyết định, không bao giờ là danh mục tham số.
- Không bao giờ tự ý reboot một database. Hãy nói rằng cần reboot, nói rõ cái
  giá phải trả (mọi kết nối bị ngắt; với Redis thì dữ liệu chưa persist sẽ mất),
  và để người dùng chọn thời điểm.

## Giới hạn của tham số

- Tham số kiểu số báo `minimum` / `maximum` và `allowed_values` **rỗng**. Ở phía
  API, các cận này được lặp lại dưới dạng list, trông như enum nhưng không phải.
- **`maximum` thường không có**, nhất là với Redis. Đừng nói với người dùng rằng
  một tham số có cận trên trừ khi `maximum` thực sự có giá trị.
- Tham số kiểu chuỗi mới báo enum thật trong `allowed_values`.
- Tham số khác nhau rất nhiều theo version — Redis 4.0 có 24, còn 6.2 và 7.2 chỉ
  có 8 — nên luôn truyền đúng version của group đang sửa.
- Một cặp engine/version không nhận diện được sẽ trả về **danh sách rỗng, không
  phải lỗi**. Kết quả khi đó đặt cờ `unknown_engine`; đừng báo lại thành "engine
  này không có tham số nào chỉnh được".

## Tạo và gắn

1. `list_*_datastores` → engine và version. Hai thứ này **cố định suốt đời của
   group**; không thể gắn group vào một instance chạy thứ khác.
2. `create_*_configuration` → group được tạo ra **rỗng** và chưa ghi đè gì cả.
3. `list_*_configuration_params` → quyết định các giá trị.
4. `update_*_configuration` → đặt chúng. Gửi trọn bộ giá trị mà group cần có
   cuối cùng; việc API merge hay replace không được tài liệu hoá.
5. `update_*_instance_config_group` → gắn group. Truyền chuỗi rỗng để gỡ ra.

## Luật về thời điểm gây lỗi âm thầm

- **Mỗi instance chỉ chạy MỘT lần sửa tại một thời điểm.** Lần thứ hai vẫn được
  nhận với HTTP 200 rồi thất bại bất đồng bộ với "Cannot perform action EDIT".
  Chờ `status_kind` về `settled` giữa hai lần ghi.
- **Group vừa tạo không xoá được trong vài giây đầu** — lệnh gọi trả về
  "Resource not found" trong khi group hiển nhiên vẫn được liệt kê và đọc được.
  Đó là cửa sổ thời gian, không phải id sai.
- **`values` của group trễ một nhịp** sau khi update: lần đọc lại vẫn có thể
  hiện tập giá trị cũ. `changed_parameters` trong kết quả mới là thứ đã gửi đi.
- **Đổi giá trị KHÔNG ghi entry nào vào instance history** — chỉ gắn và gỡ mới
  ghi. Nên với một thay đổi tham số, history không phải chỗ để xác nhận.
"""


_BACKUPS_AND_STORAGE = """\
# Backup và backup storage của vDB

Hai thứ khác nhau, rất hay bị lẫn:

- **Backup** là bản sao tại một thời điểm của một instance.
- **Backup storage** là quota trả phí mà backup được ghi vào, nằm trên phần miễn
  phí được cấp.

## Tạo một bản backup

1. `get_*_free_backup_usage` → còn chỗ không?
2. `list_*_instance_backups` → với backup `INCREMENTAL`, chọn `parentId`. Backup
   `FULL` thì không được có parent, và một backup incremental chỉ restore được
   tốt bằng chuỗi parent của nó.
3. **Instance phải đang rảnh.** Mỗi lúc chỉ một thao tác backup chạy được; lần
   thứ hai được nhận với HTTP 200 rồi thất bại.
4. `create_*_backup` → trả về `backupId` **đồng bộ**, nhưng dữ liệu được ghi bất
   đồng bộ. Poll `get_*_backup` tới khi status ổn định ở `COMPLETED` rồi mới
   restore từ nó.

**`description` không được để rỗng**, dù spec ghi là tuỳ chọn. Gửi rỗng hoặc
không gửi thì request vẫn được nhận với HTTP 200 và một id thật — rồi backup
thất bại, biến mất khỏi mọi listing, và id đó không giải ra gì cả. Tool đặt sẵn
default theo đúng cách Portal ghi; đừng đụng vào trừ khi người dùng muốn nội
dung riêng.

Nếu một backup id đang từ giải được chuyển thành không giải được nữa thay vì ổn
định lại, thì backup đã hỏng ở phía nền tảng.
`list_*_instance_histories` là nơi duy nhất ghi lại lý do.

## Xoá backup

- Không thể hoàn tác, và một bản backup có thể là bản sao duy nhất của dữ liệu.
  Trình cho người dùng thấy họ sắp mất gì (tên, instance, dung lượng, ngày) rồi
  mới xác nhận.
- Kiểm tra backup con trước: một backup `INCREMENTAL` bị xoá mất parent sẽ không
  restore được nữa.
- `delete_memory_backups` **ở dạng số nhiều là có chủ ý** — endpoint của nó
  không nhận id trên path, nên mọi id trong danh sách đều bị xoá trong một thao
  tác không hoàn tác được. Xác nhận từng id một.
- HTTP 200 không có nghĩa là đã xoá. Kiểm tra `accepted` và `warning` trong kết
  quả: xoá thật sự thì báo `success: null`, còn thất bại thì báo
  `success: false` kèm mã lỗi.

## Hạn mức miễn phí không phải hằng số

`get_*_free_backup_usage` trông như một quota cố định nhưng không phải: nó là
**tổng phần mà các instance của project cấp cho**. Đã đo: restore thêm một
instance trên flavor cấp 5 GB đã đẩy hạn mức của họ memory từ 100 GB lên 105, và
xoá instance đó đi thì nó tụt về như cũ.

Hai hệ quả: cần thì đọc lại chứ đừng nhớ một con số, và cảnh báo người dùng rằng
**xoá một instance sẽ làm hạn mức co lại**, điều này có thể đẩy những backup
trước đó vốn miễn phí vượt qua lằn ranh.

**Quota trả phí cũng KHÔNG cộng vào hạn mức miễn phí.** Đó là hai bể riêng biệt
— giữ 200 GB quota trả phí không làm con số miễn phí đổi chút nào. Đừng bao giờ
cộng chúng lại khi trả lời câu "còn bao nhiêu chỗ trống".

## Mua backup storage

1. `get_*_free_backup_usage` → có thực sự cần storage trả phí không?
2. `get_*_backup_storage` → đã có sẵn phần nào chưa? Nếu rồi thì công cụ đúng là
   `resize_*_backup_storage`, không phải create.
3. `list_*_backup_storage_packages` → bảng giá. Nó được lặp lại theo từng engine
   group và các bản lặp là giống hệt nhau, nên một `package_id` là không mơ hồ —
   chọn từ `packages`.
4. `create_*_backup_storage_dryrun` → xác nhận. **Đây là khoản phí LẶP LẠI hằng
   tháng**, không phải trả một lần; muốn dừng thì phải trả lại quota. Nói rõ
   điều này.
5. Sau khi mua hoặc resize, xác nhận quota mới bằng `get_*_backup_storage`. Cả
   hai đều ổn định trong vài giây.

Trả lại quota (`delete_*_backup_storage`) khiến các backup chỉ còn phần miễn
phí, nên lần backup kế tiếp có thể thất bại vì thiếu chỗ. Kiểm tra `usage_gb`
trước và nói rõ đang có gì trong đó.
"""


_TROUBLESHOOTING = """\
# Chẩn đoán vDB — khi một lệnh gọi không làm đúng điều nó nói

Sự thật quan trọng nhất về API này: **HTTP 200 không có nghĩa là thao tác đã
thành công.** Phần lớn kiểu hỏng ở đây là một mã 200 không thay đổi gì, hoặc một
mã 200 rồi sau đó thất bại bất đồng bộ và chỉ được ghi lại ở đúng một chỗ.

## `list_*_instance_histories` là bản ghi duy nhất

Một thao tác được-nhận-rồi-hỏng không xuất hiện ở listing nào, chi tiết nào.
Endpoint history là nơi duy nhất nó được ghi lại, và `description` của nó cho
biết nền tảng đã hiểu request đó là gì — đó là cách phân biệt "attach" với
"detach", hay phân biệt một lỗi thật với một vụ đụng độ concurrency.

**Sau bất kỳ thao tác ghi nào mà trông như không có gì xảy ra, hãy đọc history
trước khi kết luận.** Đừng retry, và đừng báo là thành công.

## `Cannot perform action ...` — instance đang bận

Mỗi instance chỉ chạy một thao tác sửa hoặc backup tại một thời điểm. Lần thứ
hai được nhận với HTTP 200 và `status: 202`, rồi thất bại với
`Cannot perform action EDIT, current database action is EDIT` (hoặc
`... status is BUILDING`). Chờ `status_kind` về `settled` rồi thử lại. Đây không
phải là request sai.

## Mảng kết quả rỗng nghĩa là KHÔNG được áp dụng

Các thao tác vòng đời trả về một dòng kết quả cho mỗi instance. Nếu mảng trả về
rỗng (`data: []` ở họ relational, `data: null` ở họ memory) thì thao tác đã bị
bỏ qua một cách âm thầm — khi đó tool đặt `accepted: false` kèm một `warning`.
Hãy báo đó là "không có tác dụng", không bao giờ báo là thành công.

Nguyên nhân thường gặp là một giá trị `action` hoặc loại tài nguyên mà endpoint
không nhận diện được. Tool dùng các hằng số đã được kiểm chứng, nên nếu bạn thấy
lỗi này từ một tool thì hãy báo lại chứ đừng đoán payload.

## `in_valid` — mọi lỗi validate, không kèm tên field

`400 Bad request: in_valid` là câu trả lời cho mật khẩu sai, zone lệch nhau,
thiếu field, và cả body rỗng. Nó không nêu tên gì cả, nên **đừng cố chia đôi
payload để dò**. Hãy kiểm tra theo thứ tự sau:

1. **Luật mật khẩu** — 8-32 cho relational, 16-128 cho MemoryStore, chỉ gồm chữ
   cái, chữ số và `$ ^ _ < >`, bắt đầu bằng chữ cái, kết thúc bằng chữ-số.
2. **Zone phải nhất quán** — `packageId`, `volumeType` và `locateZoneId` phải
   cùng đến từ một zone.
3. **Chỉ relational:** phải có ít nhất một phần tử trong `databases`.
4. **Chỉ MemoryStore:** `redisPasswordEnabled` phải bật nếu `publicAccess` bật.

## `bad_request` khi tạo configuration group

Lệnh create của họ memory cần một `deployType` mà chính schema của nó không khai
báo, và `datastoreType` của nó phân biệt hoa thường (`"Redis"`, không phải
`"redis"`). DTO xử lý cả hai — đây là lý do phải đi qua tool thay vì gửi body
thô.

## Đọc một status

Mọi response của instance và backup đều mang theo `status_kind` và
`status_guidance` bên cạnh `status` thô, bởi giá trị thô không nói lên phải làm
gì:

| `status_kind` | Nghĩa là | Phải làm gì |
|---|---|---|
| `settled` | Không có gì đang chạy | Báo lại được |
| `transitional` | Một thao tác đang chạy | Poll tiếp; đừng khởi động gì khác |
| `failed` | Lỗi phía **nền tảng** | Báo id, zone và request. Đừng retry như thể payload sai |
| `attention` | Ổn định nhưng cần một quyết định | Nói rõ người dùng phải quyết gì; đừng quyết thay họ |

Chữ hoa chữ thường không phải tín hiệu: `BUILDING` và `REBOOT` viết hoa và đang
chạy dở, còn `SHUTDOWN` cũng viết hoa nhưng đã ổn định. Một status lạ sẽ báo là
`unknown`, nghĩa là "không xác định được", không phải "hỏng".

**Một lần poll ngay vài giây sau thao tác bất đồng bộ sẽ trả về status TRƯỚC
ĐÓ.** Nên `settled` ngay sau khi gửi một thao tác không chứng minh được thao tác
đã xong — hãy chờ tới khi thấy trạng thái chuyển.

## `ERROR` / `INTERNAL_ERROR` không phải lỗi của bạn

Hai trạng thái này nghĩa là nền tảng đã thất bại khi cấp phát hoặc vận hành tài
nguyên. Hãy báo id tài nguyên, zone của nó và điều đã yêu cầu, để đội nền tảng
kiểm tra. Đừng gọi lại đúng lệnh đó mà hy vọng kết quả khác, và cũng đừng lặng
lẽ thử một hình dạng payload khác.

## "Not found" mà không phải do lệch họ

Các endpoint backup, configuration và parameter đều giải được tài nguyên của họ
*kia*, nên một kết quả not-found từ chúng nghĩa là id đó thật sự không tồn tại.
Hai nguyên nhân thường gặp: tài nguyên đã được yêu cầu nhưng nền tảng dựng thất
bại (kiểm tra history), hoặc nó đã bị xoá rồi.

Chỗ duy nhất mà họ thực sự quan trọng khi tra cứu: `get_memory_instance` từ chối
mọi thứ không phải Redis, còn `get_relational_instance` thì nhận tất.
"""


_GUIDES: dict[str, str] = {
    "getting_started": _GETTING_STARTED,
    "create_instance": _CREATE_INSTANCE,
    "restore_backup": _RESTORE_BACKUP,
    "configuration_group": _CONFIGURATION_GROUP,
    "backups_and_storage": _BACKUPS_AND_STORAGE,
    "troubleshooting": _TROUBLESHOOTING,
}


class PromptsHandler:
    """Register the vDB guidance prompts, and the tool that serves the same text.

    Both, deliberately. MCP prompts must be loaded by the user, and agents
    routinely run promptless -- `get_vdb_guide` is something an agent calls on
    its own before starting a flow.
    """

    def __init__(self, mcp) -> None:
        self.mcp = mcp

        # One literal name per registration: the monorepo Conventions job reads
        # these names statically, and a loop would hide every tool from it.
        self.mcp.prompt(name="vdb_getting_started")(self.vdb_getting_started)
        self.mcp.prompt(name="vdb_create_instance")(self.vdb_create_instance)
        self.mcp.prompt(name="vdb_restore_backup")(self.vdb_restore_backup)
        self.mcp.prompt(name="vdb_configuration_group")(self.vdb_configuration_group)
        self.mcp.prompt(name="vdb_backups_and_storage")(self.vdb_backups_and_storage)
        self.mcp.prompt(name="vdb_troubleshooting")(self.vdb_troubleshooting)

        self.mcp.tool(name="get_vdb_guide", annotations=READ)(self.get_vdb_guide)

    async def get_vdb_guide(
        self,
        topic: GuideTopic = Field(..., description="Which guide to fetch"),
    ) -> str:
        """Get vDB guidance: question order, platform rules, and confirm-gate protocol.

        Read-only and free -- it calls no API. Fetch the relevant topic **before**
        starting a flow or asking the user anything, and follow it as written.
        The rules in it were measured against the live platform and several of
        them contradict the OpenAPI document. The guide text is Vietnamese, and
        it tells you to reply in whatever language the user writes in.

        Topics:

        - `getting_started` -- what vDB is, the four families, why an instance id
          does not identify its family, tool routing.
        - `create_instance` -- the discovery chain, the same-zone rule, the
          per-family password rules, the confirm gate.
        - `restore_backup` -- why a restore creates a **second** instance rather
          than rolling anything back, and what it needs.
        - `configuration_group` -- what a change does to attached instances,
          `RESTART_REQUIRED`, and why a zero restart-required count is not a
          promise.
        - `backups_and_storage` -- taking and deleting backups, and the free
          allowance that is not a constant.
        - `troubleshooting` -- **read this whenever a call seems to have done
          nothing.** HTTP 200 is not success here, and the instance history is
          the only record of an asynchronous failure.
        """
        return _GUIDES[topic]

    async def vdb_getting_started(self) -> str:
        """Onboarding for vDB: the four families, auth, read-only mode, tool routing."""
        return _GETTING_STARTED

    async def vdb_create_instance(self) -> str:
        """Guided instance creation (discovery chain, zone rules, billing confirm gate)."""
        return _CREATE_INSTANCE

    async def vdb_restore_backup(self) -> str:
        """Guided restore: it builds a NEW instance, and what that needs."""
        return _RESTORE_BACKUP

    async def vdb_configuration_group(self) -> str:
        """Guided configuration-group work, including the restart consequences."""
        return _CONFIGURATION_GROUP

    async def vdb_backups_and_storage(self) -> str:
        """Backups, backup deletion, the free allowance, and paid backup quota."""
        return _BACKUPS_AND_STORAGE

    async def vdb_troubleshooting(self) -> str:
        """Triage for vDB: why HTTP 200 is not success, and where failures are recorded."""
        return _TROUBLESHOOTING
