"""MCP prompts for vBackup: portable onboarding + feature-flow guidance.

A vBackup "feature" here means a capability built by *composing several
endpoints/tools* — protecting a server means resolving a backend, a
destination and a policy, and reading the platform limits, before a single
write call is possible. The step-by-step choreography of such a composite
feature (which tools to call, in what order, the guardrails and the confirm
gates) lives here, not in tool docstrings. Docstrings keep the per-tool
contract; these prompts/guides carry the multi-endpoint flow.

Each guide is served BOTH as an MCP prompt (`vbackup_<name>`, loaded by the
user) and via the `get_feature_guide` tool (agents call it themselves) from a
single source of truth. Guide text is Vietnamese, matching the other GreenNode
MCP servers; code, docstrings and parameter descriptions stay English.

Add a guide by appending a `_<name>_guidance()` function, a `_FEATURE_GUIDES`
entry, the `Feature` literal value and a prompt method — and only once the
tools it choreographs actually exist, so a guide never points at a tool the
server does not register.
"""

from __future__ import annotations

from greennode.vbackup_mcp_server.tool_annotations import READ
from pydantic import Field
from typing import Literal


_GETTING_STARTED = """\
# vBackup (GreenNode) — Bắt đầu

vBackup là dịch vụ sao lưu của GreenNode: nó chụp bản sao **mức tệp** các ổ đĩa
của một instance vServer theo lịch, và cất vào một kho lưu trữ (backup
destination). Bạn mô tả nhu cầu bằng ngôn ngữ tự nhiên; trợ lý tự khám phá tài
nguyên và xác nhận trước khi thực thi. Bạn KHÔNG cần biết ID tài nguyên thô.

## Khái niệm
- **Backup server** (id `bk-ins-`): một máy chủ vServer đang được bảo vệ. Nó nối
  ba thứ: `serverId` được bảo vệ, `backupPolicyId` quyết định lịch,
  `backupDestinationId` quyết định nơi cất.
- **Backup policy** (id `bk-pol-`): lịch chạy — bốn mức bật/tắt ĐỘC LẬP (giờ,
  ngày, tuần, tháng), mỗi mức có retention và kiểu backup (FULL / INCREMENTAL)
  riêng. Policy được DÙNG CHUNG: sửa một policy là đổi lịch của mọi backup
  server đang gắn nó.
- **Backup destination** (id `bk-des-`): kho chứa (vault trên vStorage), có
  quota, soft-delete và vault-lock.
- **Backup server point** (id `bk-ins-pt-`): một điểm khôi phục do một lần chạy
  sinh ra. Bên trong là các **backup volume point** (id `bk-vol-pt-`) — phần của
  từng ổ đĩa.
- **Backend**: dịch vụ backup đặt tại một region. Mọi đối tượng đều mang
  `backendId`, và mọi lệnh tạo đều cần nó.

## vBackup KHÁC snapshot của vServer
Snapshot là bản sao **mức block**, nằm trong vServer và do MCP server vServer
quản lý. vBackup là bản sao **mức tệp**, cất ở kho riêng, giữ được cả khi server
nguồn đã bị xoá. Người dùng nói "sao lưu" có thể đang nói về một trong hai —
hỏi lại nếu chưa rõ, đừng đoán.

## Chuẩn bị
1. MCP server đã cấu hình trong client. Thao tác đọc chạy mặc định; tạo/sửa/xoá
   cần chạy server với `--allow-write` (write lỗi vì read-only → báo người dùng
   khởi động lại với `--allow-write`, đừng tìm cách lách).
2. Xác thực qua `~/.greennode/` (GreenNode IAM) hoặc env (`GRN_CLIENT_ID`,
   `GRN_CLIENT_SECRET`, `GRN_PROFILE`, `GRN_PROJECT_ID`, `GRN_DEFAULT_REGION`).
   Kiểm tra bằng tool `get_access_token`.

## Region & backend
- Region: `HCM-3` (mặc định) hoặc `HAN` — chọn bằng tham số `region` trên MỌI
  tool. Không tìm thấy thứ người dùng nhắc → thử region còn lại TRƯỚC khi kết
  luận là không có.
- Trong một region, tài nguyên còn thuộc về một `backendId` và một `projectId`.
  Hai gateway KHÔNG trả về cùng một tập backend, nên đừng suy ra region từ
  `backendId`.

## Ba trạng thái "tắt" khác nhau — đừng nhầm
- `backup_enabled=false` trên backup server: lịch bị **tạm dừng**, điểm khôi
  phục cũ vẫn còn.
- `volumes[].backup_enabled=false`: **ổ đĩa đó bị loại** khỏi mọi lần chạy, dù
  server vẫn đang bật. Sau này khôi phục sẽ KHÔNG có ổ đó.
- `server_deleted=true`: máy nguồn đã bị xoá, nhưng các bản backup **vẫn còn và
  VẪN TÍNH TIỀN**. Luôn nêu ra, đừng lọc bỏ.

## Quy tắc phiên làm việc
- Render danh sách: LUÔN để `id` và `name` là hai cột đầu. Mọi lệnh sau cần id.
- KHÔNG tự chọn thay người dùng (policy, destination, ổ đĩa nào được backup,
  retention). Trình ra rồi hỏi.
- Trước mọi lệnh ghi: tóm tắt plan và xin xác nhận rõ ràng. Với DESTRUCTIVE
  (xoá backup server, xoá policy) phải nêu đúng cái gì mất và mất vĩnh viễn.
- Kích thước API trả về là **byte**; tool đã quy đổi sẵn `*_gb`. Dùng `used_gb`
  khi nói về dung lượng thực sự được chuyển và tính tiền.

## Tính năng tổng hợp → guide tương ứng
Lấy choreography bằng `get_feature_guide feature=<tên>` (hoặc mở prompt
`vbackup_<tên>`):
- `protect_server` — Bảo vệ một máy chủ: chọn backend/destination/policy/ổ đĩa
  rồi tạo backup server.
- `protect_database` — Bảo vệ một database vDB: kiểm tra điều kiện, chọn
  kho/policy rồi tạo backup database.
- `manage_policy` — Tạo và sửa lịch backup, kèm giới hạn nền tảng.
- `check_backups` — Kiểm tra "đêm qua backup có chạy không", đọc lỗi, tìm nguyên
  nhân.
- `inspect_restore_point` — Xem một điểm khôi phục có gì, và vì sao server này
  không tự khôi phục được.
- `reduce_backup_cost` — Rà soát chi phí: backup mồ côi, ổ đĩa thừa, retention.
"""


def _protect_server_guidance() -> str:
    return """\
# Bảo vệ một máy chủ bằng vBackup

Tính năng: biến một instance vServer thành "backup server" có lịch chạy. Được
tạo nên bằng cách kết hợp discovery (backend → destination → policy → ổ đĩa)
rồi mới gọi `create_backup_server`. Mọi ID phải lấy từ tool discovery, TUYỆT
ĐỐI không bịa.

## Kiểm tra trước
1. `list_backup_servers server_id=<ins-...>` — máy này đã được bảo vệ chưa?
   Có kết quả nghĩa là ĐÃ có, đừng tạo trùng; muốn đổi lịch thì dùng
   `update_backup_server_policy`.
   (`list_protected_servers` là cách kiểm tra rẻ hơn nhưng có thể rỗng ngay cả
   khi vẫn có backup — chỉ dùng để loại nhanh, không dùng để kết luận.)
2. `get_configuration` — đọc `allowed_backup_server_status`. Instance ở trạng
   thái khác sẽ bị API từ chối.

## Chuỗi discovery (đúng thứ tự, hỏi người dùng ở mỗi bước)
1. `list_backends` — lấy `backendId`. Thường chỉ có một; nhiều hơn thì hỏi.
2. `list_backup_destinations` — kho chứa. Trình ra tên, `vault.used_gb` so với
   `max_quota_gb`, và `vault_lock`. Ưu tiên cái `is_default=true` nếu người dùng
   không chỉ định. Kho gần đầy sẽ làm CÁC LẦN CHẠY THẤT BẠI chứ không chặn lệnh
   tạo — nói rõ nếu thấy sắp hết.
3. `list_backup_policies` — lịch. Trình ra `schedule.summary` của từng policy
   (đã gộp sẵn các mức đang bật). Không có cái nào hợp → xem guide
   `manage_policy` để tạo mới.
4. Ổ đĩa: lấy `projectId` từ bất kỳ tài nguyên nào trong region (ví dụ một mục
   của `list_backup_servers`). Hỏi người dùng ổ nào cần backup. Mặc định nên
   backup TẤT CẢ; ổ bị loại sẽ không khôi phục được.
5. `list_volume_usage` với các `volumeIds` đã chọn — ước lượng dung lượng sẽ
   chuyển và lưu (`used_gb`, không phải `size_gb`).

## Tạo
`create_backup_server` `{backendId, projectId, serverConfig[{serverId,
volumes[{volumeId, backupEnabled}]}], backupPolicyId, backupDestinationId,
description?, backupEnabled?}`.

- API trả 201 KHÔNG có body. Sau khi tạo phải `list_backup_servers
  server_id=<ins-...>` để lấy `bk-ins-` id và báo lại cho người dùng.
- Lần chạy đầu tiên diễn ra vào **mốc lịch kế tiếp**, không phải ngay lập tức.
  Nói rõ để người dùng không tưởng backup đã có sẵn.

## Xác nhận trước khi ghi
Tóm tắt đúng 5 dòng: máy nào, policy nào (đọc `schedule.summary` ra tiếng
Việt), kho nào, những ổ nào được backup, ước lượng dung lượng. Rồi mới hỏi
"tạo chứ?".
"""


def _manage_policy_guidance() -> str:
    return """\
# Tạo và sửa lịch backup (backup policy)

Tính năng: định nghĩa khi nào backup chạy và giữ lại bao nhiêu bản. Đây là chỗ
dễ gây mất dữ liệu nhất mà không ai để ý — retention ngắn đi nghĩa là các điểm
khôi phục cũ bị dọn.

## Luôn bắt đầu bằng giới hạn nền tảng
`get_configuration` trả về:
- `backup_policy_hourly_intervals` — các khoảng giờ HỢP LỆ cho mức hourly
  (hiện là 4/6/8/12). Đây là giới hạn của BACKUP policy;
  `snapshot_policy_hourly_intervals` là của snapshot vServer, KHÁC nhau, không
  được dùng để kiểm tra backup.
- `backup_policy_retention_limits` — trần retention cho từng mức.
- `backup_policy_hours` — các giờ nền tảng còn mở. Giờ không nằm trong danh
  sách này thì đừng chọn cho policy mới.

Đừng hardcode các giá trị đó vào câu trả lời — đọc lại mỗi lần, chúng đổi được.

## Mô hình lịch: bốn công tắc ĐỘC LẬP
`hourlyEnabled`, `dailyEnabled`, `weeklyEnabled`, `monthlyEnabled` bật/tắt riêng
rẽ; mỗi mức bật phải kèm object config tương ứng.
- Bật mức nào thì phải có config mức đó, thiếu là API từ chối.
- **Không bật mức nào** → policy hợp lệ nhưng KHÔNG BAO GIỜ CHẠY. API vẫn nhận.
  Nếu người dùng đang đi tới trạng thái này, dừng lại và nói thẳng.
- `backupType`: `FULL` copy toàn bộ mỗi lần (tốn kho), `INCREMENTAL` chỉ copy
  phần thay đổi. Hourly thường INCREMENTAL, daily/weekly/monthly thường FULL.
- `hour`/`minute` áp cho daily/weekly/monthly; hourly dùng `interval`.

## Tạo mới
1. `get_configuration`.
2. Hỏi: chu kỳ nào, giữ bao nhiêu bản mỗi chu kỳ, chạy lúc mấy giờ.
   KHÔNG tự chọn retention — đó là khác biệt giữa "khôi phục được" và "mất một
   tuần".
3. `create_backup_policy` `{backendId, projectId, name, config{...}}`.
4. Gắn vào máy: `update_backup_server_policy` hoặc dùng luôn khi
   `create_backup_server`.

## Sửa policy đang dùng
1. `get_backup_policy` — lấy lịch HIỆN TẠI.
2. Xem `backup_server_count`. Lớn hơn 1 → nêu rõ có bao nhiêu máy bị ảnh hưởng
   và xin xác nhận. `is_default=true` là policy nền tảng dùng chung; nên tạo
   policy riêng thay vì sửa nó.
3. **Update là THAY THẾ TOÀN BỘ**: mức nào không gửi sẽ bị tắt, và `name` bắt
   buộc phải gửi lại. Ghép thay đổi của người dùng vào lịch đầy đủ rồi mới gửi.
4. Nếu retention mới NHỎ HƠN cũ: nói rõ những điểm khôi phục vượt quá sẽ bị dọn
   ở lần chạy tới, rồi mới xác nhận.
5. Sau khi update, đọc lại để kiểm tra các mức đáng lẽ vẫn bật thì vẫn bật.

## Xoá policy
`delete_backup_policy` chỉ thành công khi `backup_server_count = 0`. Còn máy
đang gắn thì chuyển chúng sang policy khác trước. Xoá policy KHÔNG xoá các điểm
khôi phục đã có — chúng vẫn tính tiền.
"""


def _check_backups_guidance() -> str:
    return """\
# Kiểm tra backup có chạy không, và vì sao hỏng

Tính năng: trả lời "đêm qua máy X có được backup không?" và "vì sao thất bại?".

## Chuỗi kiểm tra
1. `list_backup_servers server_id=<ins-...>` — máy có được bảo vệ không.
   Rỗng nghĩa là KHÔNG có backup nào cả, đừng đi tiếp.
2. Trên kết quả, đọc ba thứ theo đúng thứ tự:
   - `backup_enabled` — lịch có đang chạy không (false = đã tạm dừng).
   - `policy.schedule` — chuỗi rỗng nghĩa là policy không bật mức nào, tức là
     KHÔNG BAO GIỜ chạy dù `backup_enabled=true`.
   - `server_deleted` — máy nguồn còn không.
3. `list_backup_server_points backup_server_id=<bk-ins-...>` — đã có điểm khôi
   phục nào chưa. Rỗng KHÔNG có nghĩa là chưa từng chạy: có thể đã chạy và hỏng.
4. `list_backup_history backup_server_id=<bk-ins-...> limit=...` — lịch sử chạy
   thật. Đây là nguồn sự thật.
   - `error_message` khác rỗng = lần chạy đó hỏng, và nội dung chính là lý do.
   - `deletion_status` cho biết điểm khôi phục của lần chạy đó đã bị dọn.
   - `policy_name_at_run` là tên policy TẠI THỜI ĐIỂM chạy — dùng nó để giải
     thích một lần chạy cũ, đừng đọc policy hiện tại rồi suy ngược.

## Cái bẫy 180 ngày — đọc trước khi kết luận "chưa từng chạy"
`list_backup_history` KHÔNG mặc định trả về toàn bộ lịch sử: không truyền
`from_date` thì API chỉ nhìn lại **180 ngày**, và không có gì trong kết quả báo
là đã bị cắt. Nên:

- Rỗng hoặc ngắn KHÔNG chứng minh được là backup chưa từng chạy.
- Trước khi nói với người dùng "không có lịch sử", hỏi lại một lần nữa với
  `from_date='1970-01-01'`.
- Khi người dùng hỏi về một mốc cũ hơn nửa năm ("hồi tháng 3 có backup không"),
  bắt buộc phải truyền `from_date`.
- Luôn nói rõ đang xem cửa sổ nào: "180 ngày gần nhất" hay "toàn bộ lịch sử".

## Đúng product, đúng trail
Có ba nhật ký tách rời nhau, gọi nhầm sẽ ra danh sách rỗng chứ không báo lỗi:

- vServer: `list_backup_history` / `list_restore_history`
- vDB: `list_database_backup_history` / `list_database_restore_history`
- Thay đổi cấu hình location: `list_backup_destination_history` (bỏ trống
  `destination_id` để xem toàn account, kể cả location đã bị xoá)

## Các nguyên nhân hay gặp, theo thứ tự nên kiểm tra
- Ổ đĩa bị loại: `list_backup_server_volumes` → `backup_enabled=false` trên ổ.
  Backup "chạy thành công" nhưng thiếu đúng ổ người dùng cần.
- Kho đầy: `list_backup_destinations` → `vault.used_gb` so với `max_quota_gb`.
- Lịch bị tạm dừng: `backup_enabled=false` trên backup server.
- Policy rỗng: `policy.schedule` là chuỗi rỗng.
- Máy nguồn đã xoá: `server_deleted=true` — không có gì để backup nữa, nhưng
  các bản cũ vẫn tính tiền.

## Cách trả lời
Nêu ngày giờ lần chạy thành công gần nhất (`latest_record`), số điểm khôi phục
đang có, và nếu có lỗi thì trích nguyên văn `error_message`. Luôn kèm `limit`
đã dùng để người dùng biết đang xem cửa sổ bao lớn.
"""


def _inspect_restore_point_guidance() -> str:
    return """\
# Xem một điểm khôi phục có gì

Tính năng: trả lời "nếu khôi phục từ điểm này thì được cái gì?".

## QUAN TRỌNG: MCP server này KHÔNG khôi phục được
Gateway vBackup chỉ công bố **lịch sử** khôi phục (`list_restore_history`), không
có endpoint để KÍCH HOẠT một lần khôi phục. Thao tác khôi phục làm trong console
GreenNode. Nói thẳng điều này với người dùng thay vì đi tìm tool khác — và vẫn
giúp họ chuẩn bị bằng cách chỉ ra chính xác điểm khôi phục nào nên dùng.

## Chuỗi
1. `list_backup_server_points backup_server_id=<bk-ins-...>` — các điểm khôi
   phục. Đọc `snapshot_time` (lúc chụp), `size_gb`/`used_gb`, và
   `policy_name_at_run`.
2. `list_vserver_backup_volume_points point_id=<bk-ins-pt-...>` — bên trong điểm
   đó có những ổ nào. Đây là tool DUY NHẤT cho biết ổ nào là ổ khởi động
   (`bootable`, `boot_index=0`), loại đĩa và dung lượng từng ổ.
3. `list_vserver_backup_server_points backup_server_id=<bk-ins-...>` — chỉ nhóm
   này báo `server_info`: image gốc của máy lúc chụp (`image_type`,
   `image_version`). Dùng nó để nói cho người dùng biết điểm khôi phục có còn
   khớp với hệ điều hành họ đang chạy không.

## Cảnh báo cần nêu
- Ổ nào bị loại khỏi lịch (`list_backup_server_volumes` →
  `backup_enabled=false`) sẽ KHÔNG xuất hiện trong điểm khôi phục. Đối chiếu hai
  danh sách và nói rõ ổ nào thiếu.
- `list_restore_history` rỗng là bình thường với tài khoản chưa từng gặp sự cố.

## Nếu bị 403
Ba tool `get_vserver_backup_server`, `get_vserver_backup_server_point`,
`get_vserver_backup_volume_point` phụ thuộc quyền IAM của người gọi. Gặp 403
nghĩa là **thiếu quyền**, KHÔNG phải "không tồn tại" — chuyển sang tool dạng
list tương ứng (chúng trả về cùng dữ liệu) và báo người dùng biết là thiếu grant.
"""


def _reduce_backup_cost_guidance() -> str:
    return """\
# Rà soát chi phí backup

Tính năng: tìm chỗ đang tốn tiền mà không đem lại giá trị. Backup tính tiền theo
dung lượng nằm trong kho, nên mọi câu hỏi về chi phí đều quy về "cái gì đang
chiếm chỗ".

## Chuỗi rà soát
0. `get_backup_metrics` — xem xu hướng 24h/7 ngày trước khi đào chi tiết.
   `vbk.total_usage` tăng trong khi `vbk.total_backup_servers` đứng yên nghĩa là
   backup CŨ đang phình ra (retention quá dài); cả hai cùng tăng nghĩa là vừa
   thêm máy được bảo vệ. Hai câu chuyện đó cần hai cách xử lý khác nhau.
   Muốn soi từng kho thì `get_backup_destination_metrics`.
   Lưu ý: metric `usage` tính bằng **GB** còn `vault.used_gb` tính bằng **GiB**
   — lệch ~7% là do đơn vị, đừng báo là số liệu mâu thuẫn.
1. `list_backup_servers` cho CẢ HAI region (`HCM-3` và `HAN`) — người dùng
   thường quên mất region còn lại.
2. Lọc `server_deleted=true`: máy nguồn đã xoá nhưng backup vẫn còn và **vẫn
   tính tiền**. Đây gần như luôn là khoản lãng phí lớn nhất. Với mỗi cái:
   - `list_backup_server_points` để biết còn bao nhiêu điểm và tổng `used_gb`.
   - Hỏi người dùng có cần giữ không. Muốn bỏ thì `delete_backup_server` —
     DESTRUCTIVE, mất hết điểm khôi phục.
3. Kiểm tra kho: `list_backup_destinations` → `vault.used_gb` so với
   `max_quota_gb`, và `backup_server_count`.
4. Kiểm tra retention: `list_backup_policies` → `schedule.summary`. Giữ 30 bản
   hằng giờ của một máy test là tốn vô ích. Sửa qua guide `manage_policy`.
5. Kiểm tra ổ thừa: `list_backup_server_volumes` — ổ dữ liệu tạm/scratch có
   đang bị backup không. Loại bằng `update_backup_server_volumes`
   (`backupEnabled=false`) — chỉ ảnh hưởng các lần chạy SAU.

## Điều phải nói rõ, đừng để người dùng hiểu nhầm
- `disable_backup_server` chỉ **dừng chạy tiếp**, KHÔNG giải phóng dung lượng
  đã lưu. Muốn giảm chi phí thật thì phải xoá backup server hoặc giảm retention.
- `delete_backup_policy` cũng không xoá điểm khôi phục nào.
- Chỉ `delete_backup_server` mới thực sự bỏ dữ liệu — và không hoàn tác được.

## Trước khi xoá bất cứ thứ gì
Nêu tên máy, số điểm khôi phục, ngày cũ nhất và mới nhất, tổng dung lượng. Xin
xác nhận rõ ràng SAU KHI đã trình những con số đó — không nhận một câu "xoá đi"
nói trước khi người dùng thấy chúng.
"""


def _manage_destination_guidance() -> str:
    return """\
# Quản lý backup location (backup destination)

Tính năng: tạo, sửa và xoá nơi backup được lưu. Console gọi là **Backup
Location**, API gọi là `backup-destinations`, và tag của nó ghi
`BACKUP_LOCATION` — cùng một đối tượng.

Đây là thứ nằm dưới cùng: policy quyết định KHI NÀO chạy, location quyết định
DỮ LIỆU NẰM Ở ĐÂU và tồn tại bao lâu. Xoá location là mất dữ liệu, đổi policy
thì không.

## Trước khi tạo
1. `list_backup_products` — lấy chuỗi `product` (`vServer` hoặc `vDB`), không
   phải id `prd-...`. Product là VĨNH VIỄN, không đổi được sau khi tạo.
2. `list_backup_regions` với đúng product đó — lấy trường **`region_id`**, KHÔNG
   phải `id`. Đây là chỗ sai thường gặp nhất: `id` là `vst-cf...` và bị API từ
   chối.
3. Hỏi người dùng đặt kho ở đâu. Nói rõ đánh đổi: đặt khác vùng với máy chủ thì
   sống sót khi mất cả vùng đó, đặt cùng vùng thì khôi phục nhanh hơn.

## Ba lựa chọn phải hỏi riêng, đừng gộp
- **Max quota** — trần dung lượng tính bằng GB. Chạm trần thì các lần chạy
  **THẤT BẠI**, không phải chạy chậm lại. Đặt trần luôn phải nhìn
  `vault.used_gb` hiện tại trước.
- **Soft delete** — thùng rác. Backup đã xoá vẫn khôi phục được trong
  `retainDays` ngày, và **vẫn bị tính tiền** trong suốt thời gian đó. Nếu người
  dùng đang muốn giảm chi phí thì phải nói thẳng điều này.
- **Location lock (vault lock)** — khoá retention. Sau `changeDuration` ngày kể
  từ lúc bật, cấu hình **VĨNH VIỄN không sửa và không tắt được** — kể cả qua
  console hay support. Không bao giờ tự bật hộ, không bao giờ tự chọn số ngày
  hộ người dùng.

## Sửa: bốn endpoint riêng, không có "update location"
`update_backup_destination_name`, `_max_quota`, `_soft_delete`, `_vault_lock` là
bốn lệnh độc lập. Mỗi lệnh trả về không có body, nên sau mỗi lần sửa phải
`get_backup_destination` đọc lại để xác nhận.

## Xoá: kiểm tra ba thứ trước
1. `list_backup_destination_servers` VÀ `list_backup_destination_databases` —
   còn resource nào thì API từ chối với `backup_location_is_being_used`. Liệt kê
   tên chúng ra cho người dùng, đừng chỉ báo "không xoá được".
2. `get_backup_destination` — `vault_lock` còn hiệu lực thì cũng bị từ chối.
3. `is_default`: nếu đây là location mặc định của product, phải có cái thay thế
   trước, nếu không các lệnh tạo backup sau này không biết ghi vào đâu.

Nói rõ **dữ liệu backup trong đó bị xoá theo và không khôi phục được**, rồi mới
xin xác nhận có nêu đích danh tên location. Sau khi xoá, đọc
`list_backup_destination_history` để xác nhận platform ghi nhận SUCCESS chứ
không phải ERROR.

## Đọc lịch sử thay đổi
`list_backup_destination_history` là nhật ký cấu hình (khác
`list_backup_history` — cái đó là nhật ký các lần CHẠY). Nó giữ cả những lần
thất bại, và `description` ghi lại giá trị đã dùng bằng chính lời của API
("Edit max-quota with {max-quota: 150GB}"), nên đọc được cả lịch sử quota dù
destination chỉ lưu giá trị hiện tại.
"""


def _manage_backup_server_guidance() -> str:
    return """\
# Vận hành backup server

Tính năng: mọi thứ quanh một máy đã được bảo vệ — backup ngay lập tức, đổi nơi
lưu, và xử lý từng restore point.

## Mở đầu bằng bức tranh tổng thể
`get_backup_statistics project_id=<pro-...>` trả lời "tình hình backup thế nào"
trong một lệnh. Đọc ba thứ, đừng đọc vẹt số:

- **Độ phủ**: `total_protected_servers` / `total_servers`. Chênh lệch là số máy
  KHÔNG có backup nào.
- **Lãng phí**: `total_backup_servers` thường LỚN HƠN `total_protected_servers`,
  phần dư là backup server mà máy nguồn đã bị xoá — vẫn giữ dữ liệu và vẫn tính
  tiền. Truy tiếp bằng `list_backup_servers` rồi lọc `server_deleted=true`.
- **Độ tin cậy**: `total_backup_failed` so với `total_backup_completed`.

Thiếu `project_id` thì `total_servers` = 0 và tỉ lệ độ phủ vô nghĩa — đừng trình
bày tỉ lệ dựa trên số 0 đó.

## Gọi tên cái máy, đừng đọc id
`get_vserver_instance` là tool DUY NHẤT ở server này gọi sang sản phẩm vServer.
vBackup chỉ lưu `serverId` trần, nên muốn nói "máy web-01" thay vì
"ins-a1b2c3..." thì phải gọi nó. Dùng thêm để:

- Kiểm tra `status` trước khi `create_backup_server` (đối chiếu
  `get_configuration.allowed_backup_server_status`).
- Đọc `image` khi đánh giá một restore point cũ — point chụp dưới OS version
  khác sẽ khôi phục ra thứ người dùng không ngờ.
- Biết `boot_volume_id` trước khi loại một ổ khỏi backup: loại nhầm ổ boot thì
  các point sau đó không dựng lại được máy khởi động được.

## Backup ngay (Back now)
`start_backup` nhận **id của INSTANCE** (`ins-...`), không phải id backup server
(`bk-ins-...`), còn body cần `backendId` + `projectId`. Lấy cả ba từ chính backup
server qua `list_backup_servers server_id=<ins-...>`.

Ba điều phải nói với người dùng:

1. Lệnh chỉ **được chấp nhận**, chưa xong. Xác nhận bằng `list_backup_history`;
   record đi qua `BACKING_UP` rồi `UPLOADING`, ổ boot 20GB mất vài phút.
2. Không ảnh hưởng lịch — lần chạy theo lịch kế tiếp vẫn diễn ra.
3. Nó ăn quota của destination như mọi lần chạy khác. Kho gần đầy thì chính lần
   chạy thêm này làm tràn và làm HỎNG mọi server đang ghi vào đó.

Một instance có thể có **nhiều hơn một** backup server. Nếu
`list_backup_servers` trả về nhiều cái cho cùng `server_id`, phải hỏi lại người
dùng chứ đừng tự đoán cấu hình nào sẽ chạy.

## Đổi nơi lưu
`update_backup_server_destination` chỉ chuyển **các lần chạy SAU**. Các restore
point đã có Ở LẠI kho cũ, vẫn khôi phục được từ đó, và **vẫn bị tính tiền ở đó**.
Người dùng gần như luôn hiểu nhầm là "đã dời backup sang chỗ mới" — phải nói
thẳng câu này trước khi xác nhận, vì từ lúc đó lịch sử backup nằm rải ở hai kho.

## Tải một restore point về
`get_backup_server_point_download_urls` là đường DUY NHẤT đưa dữ liệu backup ra
khỏi nền tảng, và cũng là thứ gần với "restore" nhất mà server này làm được.

- Mỗi URL là **credential**: ai có link là tải được, không cần đăng nhập lại.
  Đưa cho đúng người đang hỏi, nói rõ tính chất đó, và ĐỪNG dán vào chat chung,
  ticket, log hay nhắc lại trong phần tóm tắt.
- Một ổ lớn bị chia thành NHIỀU link, cần đủ tất cả mới dựng lại được ổ. Báo số
  link theo từng ổ, đừng đưa link đầu tiên như thể đó là "bản tải về".
- Point đang chạy trả về **0 link** mà vẫn thành công. Rỗng nghĩa là "chưa
  xong", không phải "không có dữ liệu" — xem `status` rồi đợi.

## Xoá một restore point
`delete_backup_server_point` xoá đúng một mốc thời gian, không hoàn tác được. Đây
là lựa chọn tinh so với `delete_backup_server` (xoá sạch mọi point).

Trước khi xoá:

1. `list_backup_server_points` — nêu ngày, dung lượng, thuộc máy nào.
2. `get_backup_destination` — nếu kho bật soft delete, point chỉ vào thùng rác và
   **vẫn tính tiền hết `retain_days`**. Nói rõ là dung lượng KHÔNG được giải
   phóng ngay, nhất là khi người dùng đang muốn giảm chi phí.
3. Point đang chạy thì API trả `409 Your resource is being processed.` — đó là
   "đợi đã", không phải lỗi.
4. Xoá point incremental ở giữa có thể làm hỏng chuỗi point sau nó. Ưu tiên xoá
   từ CŨ NHẤT.
"""


def _protect_database_guidance() -> str:
    return """\
# Bảo vệ một database bằng vBackup

Tính năng: biến một instance vDB thành "backup database" có lịch chạy. Song
song với `protect_server` nhưng KHÁC ở bốn chỗ dưới đây — đọc kỹ trước khi áp
dụng thói quen từ phía vServer.

## Bốn khác biệt so với backup server
1. **Không có ổ đĩa để chọn.** Database được chụp nguyên khối, nên không có
   bước chọn volume và cũng không có bẫy "khôi phục thiếu ổ".
2. **Mỗi lần tạo chỉ MỘT database.** Body nhận `databaseId` số ít ở cấp cao
   nhất, không phải danh sách. Người dùng muốn bảo vệ 3 database thì gọi 3 lần.
3. **Phải có `databaseType`**, viết đúng `PostgresCluster` hoặc `RedisCluster`.
   Đây KHÔNG phải tên engine — "PostgreSQL", "Redis", "postgres" đều sai.
4. **Mỗi backup location chỉ chứa được MỘT backup database.** Đây là giới hạn
   hay làm hỏng luồng nhất, xem mục "Chọn kho" bên dưới.

## Kiểm tra trước
`list_databases database_type=<PostgresCluster|RedisCluster>` — một lệnh trả
lời được cả "có database nào" lẫn "cái nào backup được". Với mỗi instance nó
tính sẵn `eligible` và `ineligible_reason`.

Ba lý do khiến `eligible=false`, cách xử lý khác hẳn nhau — phải nói đúng lý do
chứ đừng báo "không tìm thấy":

| Lý do | Nghĩa là | Làm gì |
|---|---|---|
| Already has a backup database | Đã được bảo vệ rồi | Đổi lịch bằng `update_backup_database_policy`, đừng tạo trùng |
| status ≠ ACTIVE | Đang tạo/đang dừng | Đợi instance ACTIVE |
| deployment không phải cluster | PostgreSQL single node | Không backup được, phải dựng lại thành cluster |

Redis không bị ràng buộc topology — cả `sharding` lẫn `non-sharding` đều backup
được (`non-sharding` vẫn là cụm 3 node, không phải máy đơn).

**Bẫy vùng:** gateway vDB không chia theo region nên danh sách instance giống
hệt nhau ở HCM-3 và HAN, nhưng phần kiểm tra "đã được bảo vệ chưa" thì CÓ theo
region. Một database đã backup ở HCM-3 sẽ hiện `eligible=true` khi hỏi HAN. Luôn
kiểm tra region người dùng đang thao tác, đừng kết luận "chưa có backup" từ một
region.

## Chọn kho (chỗ dễ hỏng nhất)
`list_backup_destinations` rồi lọc lấy `product=vDB` — kho tạo cho vServer KHÔNG
chứa được database.

Sau đó, với từng kho ứng viên, gọi `list_backup_destination_databases`. Kho nào
đã có 1 database thì loại. Kho đã dùng mà vẫn đem đi tạo sẽ bị từ chối bằng
`Bad request: The backup destination already contains resources.`

Nếu không còn kho vDB nào trống: nói thẳng là cần tạo backup location mới (xem
guide `manage_destination`), đừng cứ thử lần lượt cho tới khi lỗi.

Khi tạo kho mới cho database, **cân nhắc kỹ vault lock** — xem mục "Xoá" bên
dưới; lock bật lên sẽ khoá luôn khả năng dọn dẹp về sau.

## Chọn lịch
`list_backup_policies` rồi lọc `product=vDB`. Trình ra `schedule.summary` của
từng policy và để người dùng chọn. Không có cái nào hợp → guide `manage_policy`.

## Tạo
`create_backup_database` `{databaseId, databaseType, backupPolicyId,
backupDestinationId, description?, backupEnabled?}`.

Không có `backendId` và `projectId` — gateway tự suy từ token, khác với
`create_backup_server`.

- API trả về KHÔNG có body. Sau khi tạo phải `list_backup_databases` để lấy
  `bk-db-` id và báo lại.
- Lần chạy đầu tiên vào **mốc lịch kế tiếp**. Muốn có bản backup ngay thì gọi
  `start_database_backup`, và nói rõ đó là hai việc khác nhau.
- `description` là ghi chú tự do, tương ứng ô "note" trên console.

## Xác nhận trước khi ghi
Tóm tắt 5 dòng: database nào (tên + id + engine + version), topology, policy nào
(đọc `schedule.summary` ra tiếng Việt), kho nào, note. Rồi mới hỏi "tạo chứ?".

## Backup ngay
`start_database_backup` nhận id backup database (`bk-db-...`) — khác phía
vServer, nơi `start_backup` nhận id instance. Lệnh chỉ được **chấp nhận**, chưa
xong: theo dõi bằng `list_backup_database_points` cho tới khi point mới ACTIVE.
Đó là bản FULL và bị tính tiền như mọi point khác.

## Đọc dung lượng cho đúng
Point trả về hai con số: `size_gb` là dung lượng ĐÃ NÉN, tức thứ được lưu và
tính tiền; `uncompressed_size_gb` là kích thước trước khi nén. Nói về chi phí thì
dùng số thứ nhất — nhầm sang số thứ hai sẽ thổi phồng chi phí lên nhiều lần.

Backup database mới thường rất nhỏ, `size_gb` làm tròn thành `0.0` trong khi
`size_bytes` khác 0. Gặp trường hợp đó thì báo theo byte, đừng nói "backup rỗng".

`backup_name` không phải thời gian dù trông giống (Redis trả về một dãy số,
PostgreSQL trả về tên WAL `base_...`). Thời điểm chạy nằm ở `time`.

## Xoá — và cái bẫy vault lock
Hai mức: `delete_backup_database_point` xoá đúng một mốc,
`delete_backup_database` xoá cả cấu hình lẫn TOÀN BỘ point.

Muốn dừng backup mà giữ lịch sử thì dùng `disable_backup_database` — nó KHÔNG
giải phóng dung lượng, point cũ vẫn nằm đó và vẫn tính tiền.

Khi xoá gặp 409, đọc kỹ câu chữ vì hai câu khác nhau hoàn toàn:

| Thông báo | Nghĩa | Xử lý |
|---|---|---|
| `Your resource is being processed.` | Point đang upload | Đợi rồi thử lại |
| `Your resource is being managed by Vault.` | Kho có **vault lock**, retention còn hiệu lực | Thử lại bao nhiêu lần cũng vô ích |

Với trường hợp thứ hai: gọi `get_backup_destination` đọc
`vault_lock.min_retention_days`, tính ra ngày point mới xoá được, và nói cho
người dùng biết. Cách duy nhất để xoá sớm hơn là gỡ lock bằng
`update_backup_destination_vault_lock` — mà việc đó chỉ làm được trong
`change_duration_days` ngày đầu, và nếu lock được tạo với `changeDuration=0` thì
vĩnh viễn không gỡ được.
"""


_FEATURE_GUIDES: dict[str, tuple[str, str]] = {
    "getting_started": ("vBackup — định hướng", _GETTING_STARTED),
    "protect_server": ("Bảo vệ một máy chủ bằng vBackup", _protect_server_guidance()),
    "protect_database": ("Bảo vệ một database bằng vBackup", _protect_database_guidance()),
    "manage_policy": ("Tạo và sửa lịch backup", _manage_policy_guidance()),
    "check_backups": ("Kiểm tra backup và chẩn đoán lỗi", _check_backups_guidance()),
    "inspect_restore_point": (
        "Xem một điểm khôi phục có gì",
        _inspect_restore_point_guidance(),
    ),
    "manage_backup_server": (
        "Vận hành backup server",
        _manage_backup_server_guidance(),
    ),
    "manage_destination": (
        "Quản lý backup location",
        _manage_destination_guidance(),
    ),
    "reduce_backup_cost": ("Rà soát chi phí backup", _reduce_backup_cost_guidance()),
}

Feature = Literal[
    "getting_started",
    "protect_server",
    "protect_database",
    "manage_policy",
    "check_backups",
    "inspect_restore_point",
    "manage_backup_server",
    "manage_destination",
    "reduce_backup_cost",
]


class PromptsHandler:
    """Register portable vBackup guidance prompts + the get_feature_guide tool.

    Each guide covers one composite capability — a feature built by combining
    several endpoints/tools. Guides are served BOTH as MCP prompts (loaded by
    the user) and as the get_feature_guide tool (agents call it on their own)
    from one source of truth.
    """

    def __init__(self, mcp):
        self.mcp = mcp

        self.mcp.tool(name="get_feature_guide", annotations=READ)(self.get_feature_guide)

        self.mcp.prompt(name="vbackup_getting_started")(self.vbackup_getting_started)
        self.mcp.prompt(name="vbackup_protect_server")(self.vbackup_protect_server)
        self.mcp.prompt(name="vbackup_protect_database")(self.vbackup_protect_database)
        self.mcp.prompt(name="vbackup_manage_policy")(self.vbackup_manage_policy)
        self.mcp.prompt(name="vbackup_check_backups")(self.vbackup_check_backups)
        self.mcp.prompt(name="vbackup_inspect_restore_point")(self.vbackup_inspect_restore_point)
        self.mcp.prompt(name="vbackup_manage_backup_server")(self.vbackup_manage_backup_server)
        self.mcp.prompt(name="vbackup_manage_destination")(self.vbackup_manage_destination)
        self.mcp.prompt(name="vbackup_reduce_backup_cost")(self.vbackup_reduce_backup_cost)

    async def get_feature_guide(
        self,
        feature: Feature = Field(
            ...,
            description=(
                "Which composite capability to load the flow for: "
                "'getting_started' (the object model, regions and backends, and how "
                "vBackup differs from vServer snapshots), "
                "'protect_server' (the full create chain), "
                "'protect_database' (the vDB create chain: eligibility, the "
                "one-database-per-location rule, and the vault-lock delete trap), "
                "'manage_policy' (schedules, retention and the platform limits), "
                "'check_backups' (did it run, and why did it fail), "
                "'inspect_restore_point' (what a restore point holds, and why this "
                "server cannot start a restore), "
                "'manage_backup_server' (coverage statistics, immediate backups, "
                "moving a destination, downloading or deleting a restore point), "
                "'manage_destination' (creating, editing and deleting a backup "
                "location, and the settings that become irreversible), "
                "'reduce_backup_cost' (finding backups that cost money for nothing)."
            ),
        ),
    ) -> str:
        """Get the step-by-step guide for a multi-endpoint vBackup feature.

        Call this BEFORE starting any backup, policy or cost-review flow. The
        guide gives the tool order, which questions to ask the user, and the
        confirm gates — detail the individual tool docstrings deliberately
        leave out. Guide text is Vietnamese.
        """
        title, body = _FEATURE_GUIDES[feature]
        return f"{title}\n\n{body}"

    async def vbackup_getting_started(self) -> str:
        """Onboarding: what vBackup manages, regions and backends, snapshot vs backup."""
        return _GETTING_STARTED

    async def vbackup_protect_server(self) -> str:
        """Guided flow for turning a vServer instance into a protected backup server."""
        return _protect_server_guidance()

    async def vbackup_protect_database(self) -> str:
        """Guided flow for turning a vDB instance into a protected backup database."""
        return _protect_database_guidance()

    async def vbackup_manage_policy(self) -> str:
        """Guided flow for creating and editing a backup schedule."""
        return _manage_policy_guidance()

    async def vbackup_check_backups(self) -> str:
        """Guided flow for checking whether backups ran and diagnosing failures."""
        return _check_backups_guidance()

    async def vbackup_inspect_restore_point(self) -> str:
        """Guided flow for inspecting what a restore point actually contains."""
        return _inspect_restore_point_guidance()

    async def vbackup_manage_backup_server(self) -> str:
        """Guided flow for operating a protected server and its restore points."""
        return _manage_backup_server_guidance()

    async def vbackup_manage_destination(self) -> str:
        """Guided flow for creating, editing and deleting a backup location."""
        return _manage_destination_guidance()

    async def vbackup_reduce_backup_cost(self) -> str:
        """Guided flow for finding and removing backup spend that buys nothing."""
        return _reduce_backup_cost_guidance()
