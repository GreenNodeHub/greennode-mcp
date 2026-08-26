"""MCP prompts for vMonitor: portable onboarding + feature-flow guidance.

A vMonitor "feature" here means a capability that is built by *composing several
endpoints/tools* — e.g. creating an alarm means picking a metric, resolving a
notification channel, then submitting the alarm. The step-by-step choreography of
such a composite feature (which tools to call, in what order, the guardrails and
confirm gates) lives here, not in tool docstrings. Docstrings keep the per-tool
contract; these prompts/guides carry the multi-endpoint flow.

Each guide is served BOTH as an MCP prompt (`vmonitor_<name>`, loaded by the
user) and via the `get_feature_guide` tool (agents call it themselves) from a
single source of truth.
"""

from __future__ import annotations

from greennode.vmonitor_mcp_server.tool_annotations import READ
from pydantic import Field
from typing import Literal


_GETTING_STARTED = """\
# vMonitor (GreenNode Observability) — Bắt đầu

vMonitor là dịch vụ giám sát/quan sát của GreenNode. Bạn mô tả nhu cầu bằng ngôn
ngữ tự nhiên; trợ lý tự khám phá tài nguyên, chọn default an toàn, và xác nhận
trước khi thực thi. vMonitor là dịch vụ **toàn cục — KHÔNG có region**.

## Khái niệm
- Dashboard: bảng widget hiển thị metric. Mỗi *host* hạ tầng tự sinh một dashboard
  mặc định (read-only, đặt theo tên host); dashboard do người dùng tạo hoặc clone
  mới sửa được.
- Infrastructure host: tài nguyên đẩy metric về vMonitor — máy chủ cài Metric Agent
  (`list_hosts`) hoặc tài nguyên sản phẩm GreenNode (vServer/vStorage/vDB/vLB/
  vBackup/vBandwidth/VAS, vDB-Kafka) được giám sát như host.
- Metric information: catalogue của từng metric — đơn vị hiển thị (unit) và các
  dimension (nhãn scope dữ liệu như host, cpu, instance). Khác với *host metrics*
  (giá trị hiện tại của một host).
- Alarm: quy tắc cảnh báo trên metric / log / thay đổi (change-method), bắn thông
  báo qua kênh Notification khi vượt ngưỡng.
- Synthetic: phép thử uptime chủ động (probe) tới endpoint từ nhiều location.

## Chuẩn bị
1. MCP server đã cấu hình trong client. Thao tác đọc chạy mặc định; tạo/sửa/xoá
   cần chạy server với `--allow-write` (write lỗi vì read-only → báo người dùng
   khởi động lại với `--allow-write`).
2. Xác thực qua `~/.greennode/` (GreenNode IAM bearer token) hoặc env
   (`GRN_CLIENT_ID`, `GRN_CLIENT_SECRET`, `GRN_PROFILE`, `GRN_PROJECT_ID`).

## Các tính năng tổng hợp → guide tương ứng
Mỗi tính năng dưới đây được tạo nên bằng cách kết hợp NHIỀU tool/endpoint. Lấy
choreography bằng `get_feature_guide feature=<tên>` (hoặc mở prompt
`vmonitor_<tên>`):
- `build_dashboard` — Dựng dashboard: kết hợp tool quản lý dashboard + biến
  (variable) + view + widget.
- `query_metrics` — Truy vấn số liệu: kết hợp tool tra metric catalogue + lấy
  time-series, và tìm & export log.
- `create_metric_alarm` — Tạo cảnh báo: kết hợp tool chọn nguồn (metric/log/
  change-method) + kênh thông báo + tạo alarm.
- `monitor_infrastructure` — Giám sát hạ tầng: kết hợp tool liệt kê host + xem số
  liệu + bật/tắt giám sát + log mapping.
- `edit_metric_unit` — Đổi/đặt lại đơn vị hiển thị metric: kết hợp tool tra mapping
  + unit rồi tạo/xoá override.
- `manage_log_projects` — Quản lý log: kết hợp tool project + pipeline/processor +
  archive + refill.
- `manage_integrations` — Tích hợp: kết hợp tool app tích hợp + API key + chứng chỉ.
- `create_notification_channel` — Tạo kênh thông báo: kết hợp tool loại kênh +
  luồng OTP (gửi/validate) + tạo kênh.
- `view_quota_usage` — Xem quota/usage + báo giá gói: kết hợp tool đọc usage +
  catalogue gói + báo giá (read-only).
- `create_uptime_monitor` — Tạo uptime monitor: kết hợp tool location + validate
  probe + tạo monitor.

## Nguyên tắc
- Đọc thì tự do; MỌI thao tác ghi phải qua MỘT lần xác nhận rõ ràng (hard gate).
- Khi hiển thị danh sách/chi tiết: `id` và `name` là HAI CỘT ĐẦU của bảng, không
  cắt ngắn id — thao tác tiếp theo cần nó.
- Resolve tên → id qua tool list tương ứng; không bắt người dùng dán id thô.
- Trả lời bằng ngôn ngữ người dùng dùng. Không bao giờ dán secret vào chat.
"""


def _edit_metric_unit_guidance() -> str:
    return """\
# Đổi / đặt lại đơn vị hiển thị của một Metric

Tính năng: đổi đơn vị hiển thị (display unit) của một metric, hoặc đặt lại về mặc
định. Được tạo nên bằng cách kết hợp các tool tra cứu mapping + danh sách unit rồi
tạo/xoá một override.

## Quy trình
1. Tìm metric: `list_metric_unit_mappings name=<từ khoá>` (bảng có metricName,
   unit hiện tại, description). Lấy từ dòng người dùng chọn: `metric_name`, và
   `metric_unit_mapping_user_id` (dùng cho reset — chỉ có giá trị khi metric đã
   được override; rỗng = đang dùng mặc định hệ thống).
2. (Tuỳ chọn, để có ngữ cảnh) `get_metric_dimensions name=<metric_name>` — xem các
   dimension và giá trị của metric.
3. Chọn đơn vị mới: `list_metric_units` → trình cho người dùng các `name` unit
   hợp lệ (danh sách unit có thể trùng tên, đã gộp theo tên). `unit` truyền vào
   create PHẢI là một trong các `name` này.
4. Trình plan (metric_name, unit mới, description) NGAY TRONG cùng tin nhắn với
   câu hỏi xác nhận. HARD GATE: chờ xác nhận rõ ràng.
5. Đặt đơn vị: `create_metric_unit_mapping` với body
   `{metricName: <metric_name>, unit: <unit đã chọn>, description?: <mô tả>}` —
   tạo một user unit-mapping đè lên mặc định.
6. Đặt lại về mặc định: `delete_metric_unit_mapping mapping_user_id=<metric_unit_mapping_user_id>`
   — xoá override, metric trở lại đơn vị mặc định. Chỉ làm được khi dòng đó ĐÃ có
   `metric_unit_mapping_user_id` (đang là override); nếu rỗng thì vốn đã là mặc
   định, không cần reset.

## Lưu ý / guardrail
- **Cần quota/billing metric đang bật** để đổi đơn vị: nếu tài khoản chưa mua
  quota metric, API sẽ từ chối. Gặp lỗi này → báo người dùng mua quota metric
  trước, đừng thử lại.
- `metricName` lấy từ dòng đã chọn ở bước 1, KHÔNG tự bịa tên metric.
- `delete_metric_unit_mapping` là thao tác reset (DESTRUCTIVE ở nghĩa xoá override,
  không xoá metric) — vẫn qua confirm gate như mọi thao tác ghi.
- Server read-only (không `--allow-write`) → create/delete sẽ lỗi; báo người dùng
  khởi động lại với `--allow-write`.
"""


def _build_dashboard_guidance() -> str:
    return """\
# Dựng Dashboard (dashboard + variable + view + widget)

Tính năng: tạo (hoặc clone) một dashboard, khai báo biến để lọc động, thêm các
widget biểu đồ, sắp xếp layout, và lưu lại các "view" trạng thái. Được tạo nên
bằng cách kết hợp các tool quản lý dashboard, variable, view và widget.

## Quy trình
1. Xem/định vị dashboard: `list_dashboards`, `get_dashboard id=<id>` hoặc
   `get_dashboard_by_name name=<tên>`; `list_widgets dashboard_id=<id>` để xem nó
   đang vẽ những truy vấn metric nào (kể cả để sao chép sang dashboard mới). Lưu ý
   dashboard mặc định của mỗi host là READ-ONLY — muốn tuỳ biến thì
   `create_dashboard_clone` từ nó rồi sửa bản clone.
2. Tạo mới: `create_dashboard` (đặt tên, mô tả). Đổi tên sau bằng
   `update_dashboard_name`; ghim yêu thích bằng `update_dashboard_favorite`.
3. (Tuỳ chọn) Biến động: đọc `list_dashboard_variables id=<id>` trước, sửa danh
   sách, rồi `update_dashboard_variables` — đây là **REPLACE cả danh sách**, phải
   gửi ĐỦ mọi biến muốn giữ (biến bị bỏ ra = bị xoá).
4. Thêm widget: trước tiên xác định truy vấn sẽ vẽ (xem guide `query_metrics`:
   `get_metric_names` → `get_metric_dimensions`). Sau đó `create_widget` với
   `{name, typeChart (LINE/BAR/NUMBER/TABLE...), type "Metric",
   graphs: {a: <GraphRequest>, b: ...}}` — mỗi key a/b/c là một truy vấn.
   Với biểu đồ metric, `GraphRequest = {type:"METRIC_GRAPH", data:{name (metric),
   statistic (avg/max/...), alias, groupBy, color (#hex), filter, enabled:true}}`.
   **KHÔNG cần truyền `layout`** — widget sẽ tự được xếp vào ô trống trên lưới
   10 cột (mặc định `cols:5, rows:2`, hai cái một hàng) nên không đè lên nhau và
   trông gọn trên web. Chỉ truyền `layout` `"cols:C, rows:R, x:X, y:Y"` khi muốn
   đặt vị trí cụ thể.
5. Sắp xếp (tuỳ chọn): `update_widget_layout` để đổi vị trí/kích thước lưới; sửa
   nội dung widget bằng `update_widget_v2`; xoá bằng `delete_widget`.
6. Lưu trạng thái khám phá: `create_dashboard_view id=<id>` với `{name,
   variables?, filters?, query?, timeRange?}` — tái áp dụng bằng
   `get_dashboard_view`; quản lý bằng `list/update/delete_dashboard_view`.
7. Trình plan (dashboard nào, widget/biến sẽ thêm) NGAY cùng câu hỏi xác nhận
   trước mỗi thao tác ghi. HARD GATE.

## Lưu ý / guardrail
- Dashboard mặc định theo host KHÔNG sửa được → luôn clone trước khi tuỳ biến.
- `update_dashboard_variables` và bản chất "view" là whole-list/whole-state:
  đọc hiện trạng trước, đừng gửi thiếu.
- `delete_dashboard` / `delete_widget` là DESTRUCTIVE — qua confirm gate.
- Server read-only → mọi create/update/delete sẽ lỗi; báo bật `--allow-write`.
"""


def _query_metrics_guidance() -> str:
    return """\
# Truy vấn metric & vẽ số liệu, tìm & export log

Tính năng: chọn một metric, lọc theo dimension rồi lấy time-series; hoặc chạy
truy vấn log, tổng hợp và export. Được tạo nên bằng cách kết hợp các tool
catalogue metric + lấy số liệu + tìm/export log. Đây là nhóm CHỈ ĐỌC.

## ĐƯỜNG TẮT: lấy metric từ dashboard (ưu tiên khi người dùng nói theo TÀI NGUYÊN)
Khi câu hỏi là "xem metric của server/LB/DB X", ĐỪNG dò catalogue và ĐỪNG bảo
người dùng bật detail monitoring. Mỗi tài nguyên đã có sẵn một **dashboard mặc
định (system)** và các widget của nó lưu sẵn đúng truy vấn mà console vẽ.
1. `list_dashboards searching_text=<tên tài nguyên>` (hoặc `get_dashboard_by_name`)
   → lấy `id` dashboard. Dashboard mặc định thường tên dạng `vServer-<tên>-<hash>`.
2. `list_widgets dashboard_id=<id>` → mỗi widget trả về `metric_queries`, mỗi
   phần tử đã có ĐỦ `metric_name`, `statistic`, `dimensions` (đã chứa
   `resource_id:...,product:...`), `group_by`, và `period` ở cấp widget.
3. Ghép thẳng vào `get_statistics_v2` (xem mẫu ở bước 3 mục dưới): `metric_name`
   → `name`, `statistic` → `statistics`, `dimensions` → `dimensions`, `group_by`
   → `group_by`, `period` widget → `period`. KHÔNG cần `get_metric_dimensions`.
   Dùng đúng `period` của widget thì số liệu khớp với biểu đồ trên web.
- Cách này áp dụng cho MỌI dashboard (infra hay tự tạo), không phụ thuộc detail
  monitoring. Chỉ quay lại quy trình catalogue bên dưới khi cần một metric mà
  dashboard chưa có widget nào vẽ.
- Widget có `log_graph_count > 0` là biểu đồ LOG → truy vấn bằng `search_logs`.

## Truy vấn METRIC (khi phải tự chọn metric từ catalogue)
1. Tìm metric: `get_metric_names` — LUÔN truyền cửa sổ thời gian (start/end), vì
   catalogue rất lớn; lọc theo từ khoá nếu có.
2. Hiểu cách lọc/group: `get_metric_dimensions name=<metric>` (các dimension và
   giá trị — ở đây lấy `resource_id` của tài nguyên cần xem), hoặc
   `list_metric_dimension_names` / `list_metric_dimension_values`.
3. Lấy số liệu — DÙNG `get_statistics_v2` (POST, type=SIMPLE) làm cách CHÍNH (đây
   là cách vMonitor tự query cho cả infra lẫn người dùng):
   ```
   {"type":"SIMPLE","data":{"graph":{
       "name":"vserver.cpu.utilization_norm_perc",
       "dimensions":"resource_id:ins-...,product:vserver",
       "statistics":"avg","group_by":"none",
       "offset":0,"limit":"","rollup":"","rate":0},
     "start_time":<epoch_ms>,"end_time":<epoch_ms>,"period":60,"alarm":false}}
   ```
   - `dimensions` là chuỗi các cặp `key:value` nối bằng dấu phẩy (dùng dấu HAI
     CHẤM giữa key và value, KHÔNG dùng `=`).
   - `statistics` là STRING (`avg`/`max`/`sum`...), `start_time`/`end_time` là
     epoch MILLIS. Sai KIỂU sẽ khiến backend trả 500.
   - `get_statistics` (GET, tham số phẳng) là bản rút gọn cho 1 metric; metric
     synthetic (single-stat) dùng `get_statistics_synthetic`.

## Truy vấn LOG
1. Chọn project: `list_projects` → lấy `project_id`.
2. (Tuỳ chọn) kiểm tra có dữ liệu: `get_project_log_data_exists`.
3. Tìm: `search_logs project_id=<id>` với body LogSearchDto
   `{query, sorts?, aggregations?, size?, from?}`.
   - `query` là một mệnh đề `{type, value}` (KHÔNG phải Elasticsearch thô). Các
     `type` hợp lệ: `match` (value `{field, value}`), `range` (value
     `{field, gte/lte/gt/lt}`), `exists` (value `{field}`), `bool` (value có
     `filter`/`must`/`should`/`mustNot`). Bỏ trống `query` để khớp mọi log
     (`match_all` KHÔNG hợp lệ ở đây). Cú pháp ES quen thuộc
     (`{"match": {field: value}}`, `bool` có `must_not`) sẽ được tự dịch.
   - LUÔN giới hạn cửa sổ thời gian: đặt một mệnh đề `range` trên `@timestamp`
     (trong `bool.filter`), ví dụ
     `{"type":"range","value":{"field":"@timestamp","gte":"<ISO>","lte":"<ISO>"}}`.
     Tìm không giới hạn thời gian trên project nhiều dữ liệu có thể chậm/timeout →
     đây chính là nguyên nhân "lúc được lúc không".
   - `sorts` dạng `{field, order}` (order `asc`/`desc`, mặc định mới nhất trước),
     ví dụ `[{"field": "@timestamp", "order": "desc"}]`.
   - `search_logs_default` là biến thể mặc định (user) — chỉ nhận
     `match`/`range`/`bool` (không có `exists`).
4. Export: `create_log_export` để tạo job export, `get_log_export` để tải kết quả.

## Lưu ý / guardrail
- Nhóm này read-only, không cần `--allow-write`.
- Metric catalogue lớn → luôn giới hạn thời gian + từ khoá, đừng liệt kê tất cả.
- Kết quả `search_logs` là JSON thô — tóm tắt cho người dùng, giữ nguyên field
  quan trọng; đây cũng là bước dựng truy vấn trước khi tạo log alarm.
"""


def _create_metric_alarm_guidance() -> str:
    return """\
# Tạo Alarm (metric / log / change-method)

Tính năng: định nghĩa một cảnh báo — chọn nguồn (metric, log, hoặc
change-method), đặt ngưỡng và chọn kênh thông báo khi kích hoạt. Được tạo nên
bằng cách kết hợp các tool chọn nguồn + kênh thông báo + tạo alarm.

## Chuẩn bị chung
- Kênh thông báo phải có TRƯỚC: `list_notifications` để lấy kênh cho các trường
  `inAlarm` / `ok` / `undetermined`. QUAN TRỌNG: dùng `metric_mapping_id` của kênh
  (KHÔNG phải `id`) — truyền `id` thô khiến API trả 500 gây hiểu nhầm. Server có
  tự đổi `id` → `metric_mapping_id` nên giá trị nào cũng chạy, nhưng hãy ưu tiên
  `metric_mapping_id`. Chưa có kênh → xem guide `create_notification_channel`.

## Alarm METRIC (`create_metric_alarm`)
1. Chọn metric (xem guide `query_metrics`): `get_metric_names` →
   `get_metric_dimensions` để có `metricName` và `resource_id` cần giám sát.
   ĐƯỜNG TẮT: nếu cảnh báo cho một tài nguyên cụ thể, `list_widgets` trên
   dashboard mặc định của nó cho luôn `metric_name` (→ `metricName`) và
   `dimensions` (tách `resource_id:...` ra thành `metricFilter`).
2. Trình plan (metric, ngưỡng, kênh) NGAY cùng câu hỏi xác nhận. HARD GATE.
3. Tạo: `create_metric_alarm` `{name, metricName, metricStatistic (avg/max/sum),
   condition (gt/lt/gte/lte), severity (LOW/MEDIUM/HIGH), thresholdValue,
   metricFilter ({"resource_id":"ins-..."} để giới hạn 1 tài nguyên),
   metricPeriod, inAlarm/ok/undetermined (id kênh)}`.
   - Các trường evaluator (`formula="a"`, `thresholdMethod="static"`,
     `metricProduct=""`, `metricGroupBy="none"`, `metricFilter={}`, `timeshift` =
     `-metricPeriod`) được điền sẵn nếu bỏ trống → payload hợp lý đầu tiên tạo ra
     alarm CHẠY ĐƯỢC, không phải mò nhiều lần.
   - `severity` là `LOW`/`MEDIUM`/`HIGH` (product này KHÔNG có `CRITICAL`),
     `condition` lower-case — cả hai tự chuẩn hoá.
4. Xem/sửa: `list_alarms`, `get_alarm`, `get_metric_alarm_definition`,
   `list_metric_alarm_histories`; `update_metric_alarm` (LƯU Ý: KHÔNG đổi được
   phần chọn metric — chỉ ngưỡng/timing/kênh; đổi metric thì xoá rồi tạo lại).
   Xoá: `delete_metric_alarm` / `delete_metric_sub_alarm` (DESTRUCTIVE).

## Alarm LOG (`create_log_alarm`)
1. Dựng & thử truy vấn log trước bằng `search_logs` (guide `query_metrics`).
2. Tạo: `create_log_alarm` `{name, logProjectId, queryString, groupByField?,
   metricAggType (count/avg...), metricAggKey?, timeFrame, condition,
   thresholdValue, inAlarm/ok}`. Xem `get_log_alarm_status`,
   `list_log_alarm_histories`; sửa `update_log_alarm`; xoá `delete_log_alarm`.

## Alarm CHANGE-METHOD (`create_change_alarm`)
- Cảnh báo theo biến động/thay đổi: `create_change_alarm`, xem
  `get_change_alarm` / `list_change_alarm_histories`, sửa `update_change_alarm`,
  xoá `delete_change_alarm` / `delete_change_alarm_history`.

## Lưu ý / guardrail
- Không có kênh thông báo hợp lệ thì alarm bắn mà không tới ai — tạo kênh trước.
- Mọi create/update/delete qua confirm gate; delete là DESTRUCTIVE.
- Server read-only → thao tác ghi sẽ lỗi; báo bật `--allow-write`.
"""


def _monitor_infrastructure_guidance() -> str:
    return """\
# Giám sát hạ tầng: khám phá host, bật/tắt giám sát, log mapping

Tính năng: xem các host đang đẩy metric (máy cài Metric Agent hoặc tài nguyên sản
phẩm GreenNode), bật/tắt giám sát, và bật thu log cho các tài nguyên hỗ trợ. Được
tạo nên bằng cách kết hợp các tool liệt kê host + xem số liệu + bật/tắt + log
mapping.

## Quy trình
1. Khám phá host: `list_hosts` (agent), hoặc theo sản phẩm
   `list_<type>_hosts` với type ∈ vserver/vstorage/vdb/vdb_kafka/vlb/vbackup/
   vbandwidth/vas. Lấy id host từ dòng người dùng chọn.
2. Xem trạng thái: `get_host`, `get_host_metrics`, hoặc
   `get_<type>_host_metrics` (số liệu hiện tại của host — chỉ là ảnh chụp một
   thời điểm).
2b. Xem metric theo THỜI GIAN mà KHÔNG cần bật detail monitoring: mỗi host có sẵn
   một dashboard mặc định (system). `list_dashboards searching_text=<tên host>`
   → `list_widgets dashboard_id=<id>` → mỗi `metric_queries` đã có đủ
   `metric_name` / `statistic` / `dimensions` / `group_by` để đưa thẳng vào
   `get_statistics_v2`. Đây là cách nhanh nhất trả lời "CPU/network của host này
   thế nào" (chi tiết ở guide `query_metrics`).
3. Bật/tắt giám sát: `update_host_enabled` / `update_host_disabled`; cấu hình host
   sản phẩm bằng `update_<type>_host`.
4. Gỡ host: `delete_host` / `delete_<type>_host` — IRREVERSIBLE.
5. Log mapping (bật thu log cho tài nguyên): liệt kê bằng
   `list_<vcdn|vdb|vlb|vstorage>_log_mappings` (vStorage còn theo region/bucket),
   rồi bật/tắt `update_<type>_log_mapping_enabled` / `..._disabled` hoặc cấu hình
   `update_<type>_log_mapping`.
6. Trình plan NGAY cùng câu hỏi xác nhận trước mỗi thao tác ghi. HARD GATE.

## Lưu ý / guardrail
- Chọn đúng bộ tool theo loại: host agent chung (`*_host`) vs host sản phẩm
  (`*_<type>_host`); dùng list tương ứng để resolve id, đừng bịa.
- `delete_host*` là IRREVERSIBLE → confirm gate rõ ràng, nêu hậu quả (mất chuỗi
  metric của host).
- Server read-only → thao tác ghi sẽ lỗi; báo bật `--allow-write`.
"""


def _manage_log_projects_guidance() -> str:
    return """\
# Quản lý Log: project, pipeline/processor, archive, refill

Tính năng: quản lý log project, dựng pipeline xử lý (processor group + processor,
gồm parser Grok), lưu trữ (archive) ra object storage, và nạp lại (refill) dữ
liệu. Được tạo nên bằng cách kết hợp các tool project + pipeline/processor +
archive + refill.

## Project
- `list_projects`, `get_project`, `get_project_mappings`; cấu hình bằng
  `update_project`, `update_project_mappings`.
- Vòng đời project nằm ở **billing**: `create_log_project` (mua quota = tạo
  project), `resize_log_project`, `delete_log_project` — TỐN TIỀN / KHÔNG hoàn
  tác. Xem `get_feature_guide feature=view_quota_usage` để có đủ quy trình
  class → retention → packageId → quantity → báo giá → xác nhận.

## Pipeline + Processor
1. Tạo pipeline: `create_pipeline {name, description?}`.
2. Thêm nhóm xử lý: `create_processor_group` (tham khảo thư viện mẫu
   `list_processor_group_libraries`, tạo mẫu dùng lại `create_processor_group_library`).
3. Thêm processor: nếu là Grok, `validate_grok_parser` TRƯỚC để chắc pattern
   khớp (tham khảo `list_date_formats` cho định dạng thời gian), rồi
   `create_processor`. Sắp thứ tự bằng `update_processor_order`.
4. Sửa/xoá: `update_pipeline`/`update_processor_group`/`update_processor`;
   `delete_pipeline`/`delete_processor_group`/`delete_processor` (DESTRUCTIVE).

## Archive (lưu trữ ra storage)
1. `validate_archive_connection` để kiểm tra kết nối storage TRƯỚC.
2. `create_archive`; xem `list_archives`/`get_archive`; sửa `update_archive`;
   xoá `delete_archive`.

## Refill (nạp lại dữ liệu)
- `validate_refill_connection` trước; `create_refill` hoặc
  `create_refill_from_archive` (nạp lại từ một archive đã có); xem
  `list_refills`/`get_refill`; xoá `delete_refill`.

## Lưu ý / guardrail
- Luôn `validate_*` (grok, archive/refill connection) TRƯỚC khi tạo, để không lưu
  cấu hình sai.
- Mọi create/update/delete qua confirm gate; delete là DESTRUCTIVE.
- Server read-only → thao tác ghi sẽ lỗi; báo bật `--allow-write`.
"""


def _manage_integrations_guidance() -> str:
    return """\
# Tích hợp: cài app, API key metric, chứng chỉ log

Tính năng: cài các app tích hợp (nguồn metric), phát hành API key để client ngoài
đẩy custom metric, và tạo chứng chỉ TLS cho log project. Được tạo nên bằng cách
kết hợp các tool app tích hợp + API key + chứng chỉ.

## App tích hợp
- Duyệt: `list_integrations`, `get_integration`.
- Cài: `update_integration_installed id=<id>` (kèm `logProjectId` nếu app thu log).
- Gỡ (không xoá): `update_integration_uninstalled`; xoá hẳn: `delete_integration`.

## API key metric
1. `list_metric_api_keys` xem key hiện có.
2. `create_metric_api_key {name, description?}` — **giá trị key chỉ hiện MỘT LẦN
   tại đây**, hướng dẫn người dùng lưu an toàn; đừng lặp lại key trong chat sau đó.
3. Thu hồi: `delete_metric_api_key key=<value>` — IRREVERSIBLE.

## Chứng chỉ log (mTLS đẩy log)
- Tạo: `create_project_certificate` cho một project; tải: `get_project_certificate_download`;
  xoá: `delete_project_certificate`. Private key là **bí mật** — không dán vào chat.

## Lưu ý / guardrail
- API key và private key chứng chỉ là secret: hiển thị tối thiểu, không log, không
  lặp lại; nhắc người dùng tự lưu.
- create/delete qua confirm gate; delete/thu hồi là IRREVERSIBLE.
- Server read-only → thao tác ghi sẽ lỗi; báo bật `--allow-write`.
"""


def _create_notification_channel_guidance() -> str:
    return """\
# Tạo kênh thông báo (luồng OTP)

Tính năng: thêm một kênh nhận cảnh báo (Email/SMS/Slack/Telegram/Webhook). Kênh
cần xác minh qua OTP trước khi lưu (trừ Webhook). Được tạo nên bằng cách kết hợp
các tool loại kênh + gửi/validate OTP + tạo kênh.

## Quy trình
1. Chọn loại kênh: `list_notification_types`; xem kênh đã có: `list_notifications`.
2. Kênh cần OTP (Email/SMS/Slack/Telegram):
   a. `create_notification_otp {type, address}` — **gửi OTP thật** tới địa chỉ đó
      (email/SMS được phát đi), trả về `ref`. XÁC NHẬN với người dùng trước khi gửi.
   b. Người dùng nhập mã nhận được → `validate_notification_otp {otp, address, ref}`
      → trả về `code` đã xác thực.
   c. `create_notification {name, address, type, otpCode: <code đã xác thực>}`.
3. Kênh Webhook: KHÔNG cần OTP → gọi thẳng `create_notification {name, address,
   type: "Webhook", header?}`.
4. Sửa: `update_notification` (nếu ĐỔI address phải kèm `otpCode` mới — lặp lại
   bước OTP cho address mới). Xoá: `delete_notification` (DESTRUCTIVE).

## Lưu ý / guardrail
- `create_notification_otp` phát OTP thật (tốn SMS/email) → hỏi xác nhận trước,
  đừng gọi lặp; nếu người dùng chưa nhận, chờ chứ đừng gửi lại ngay.
- `get_notification_otp_info` để xem cấu hình/khả dụng OTP nếu cần.
- Webhook bỏ qua OTP; các kênh khác BẮT BUỘC otpCode đã validate.
- Server read-only → create/update/delete sẽ lỗi; báo bật `--allow-write`.
"""


def _view_quota_usage_guidance() -> str:
    return """\
# Quota & Usage: xem mức dùng, báo giá, và ĐẶT LỆNH mua/resize/xoá

Tính năng: xem mức sử dụng theo từng loại quota (metric/log/synthetic/SMS/email),
duyệt gói (quota class → retention → package), báo giá, và đặt lệnh mua/resize/xoá.
**Nhóm đặt lệnh TIÊU TIỀN THẬT và không hoàn tác được** — luôn báo giá rồi hỏi xác
nhận trước.

## Xem usage & quota
- `get_quota_usage`, `get_composite_usage`, `get_current_quota` — mức dùng/quota
  hiện tại; log riêng: `get_log_usage`, `list_log_quotas`, `get_log_quota`.
- Chi tiết & thiết lập: `get_quota_detail` (trả `resourceId`, `packageId`,
  `classId`, số lượng hiện tại — bước bắt buộc trước mọi resize),
  `get_billing_settings`, `list_trash_quotas`, `get_convert_result`.

## Duyệt gói (catalogue)
- **v2 (dùng cho mọi lệnh mua/resize metric & log)**: `list_quota_classes
  {category}` → mỗi class có `config.retentions[]`, mỗi retention mang
  `packageId` + giới hạn `minSize`/`maxSize`/`step` (log) hoặc
  `minResource`/`maxResource`/`step` (metric). `packageId` PHẢI lấy từ đây.
  `list_quota_class_packages` để xem chi tiết gói trong class.
- v1 (phẳng, SMS/email chỉ có ở đây): `list_packages` / `get_package` /
  `get_package_detail` / `get_package_description(_detail)`; `list_tiers` /
  `get_tier` / `get_tier_description`.

## Báo giá (quote — KHÔNG tạo đơn)
- Mua mới: `get_creation_price {category, package_id, quantity?, month_period?,
  buy_with?}`.
- Nâng/hạ: `get_resize_price {category, package_id, resource_id?, quantity?}`.
- Khôi phục từ trash: `get_recovery_price`. Gia hạn: `get_renewal_price`.
- **Truyền `quantity` để dùng bản báo giá v2** — nó gửi ĐÚNG body mà tool đặt
  lệnh sẽ gửi, nên là bước tiền kiểm (pre-flight) an toàn: payload nào báo giá từ
  chối thì đặt lệnh cũng hỏng.

## Đặt lệnh (WRITE, cần `--allow-write`, TỐN TIỀN)
Quy tắc chung 3 bước: **đọc quota hiện tại → chọn class/retention → báo giá → hỏi
xác nhận → đặt lệnh**.
- `resize_metric_quota {body: {packageId, quantity, pay?}}` —
  `quantity` = số host. Tài khoản chỉ có 1 metric quota nên không cần id.
- `create_log_project {body: {projectName, packageId, quantity,
  projectDescription?, monthPeriod?, buyWith?, pay?}}` — mua log quota CHÍNH LÀ
  tạo log project.
  `quantity` = GB-day = (GB/ngày) × (số ngày retention). Class `Basic` là gói
  miễn phí (1 ngày, cố định 10 GB/ngày).
- `resize_log_project {resource_id, body: {packageId, quantity, ...}}` — dùng cả
  khi nâng Basic → Pro. `resource_id` = id trong `list_log_quotas` (trùng id
  trong `list_projects`).
- `delete_log_project {resource_id}` — XOÁ CẢ project và log đã lưu, KHÔNG khôi
  phục được.
- `resize_sms_quota` / `resize_email_quota {body: {packageId, ...}}` — gói SMS/email
  là bundle cố định nên chỉ đổi gói, không có `quantity`.

## Lưu ý / guardrail
- Trước mỗi lệnh tốn tiền: báo giá bằng `get_*_price` (kèm `quantity`), đọc to số
  tiền + tên tài nguyên chính xác, rồi CHỜ người dùng xác nhận.
- Quota chỉ tăng: `quantity` mới phải lớn hơn mức đang dùng, và tuân thủ
  min/max/step của retention đã chọn — sai là backend từ chối.
- `pay=false` (mặc định) tạo đơn và trả `payment_url`: đơn còn treo cho tới khi
  người dùng mở link thanh toán. `pay=true` trừ tiền ngay.
- **ĐỪNG truyền `redirectUrl`** — tool tự điền trang quota của console vMonitor.
  Upstream allow-list trường này (chỉ ở lệnh đặt hàng; báo giá thì nhận chuỗi
  rỗng): thiếu → "redirectUrl: must not be null", chuỗi trắng → "redirect URL is
  invalid", URL hợp lệ nhưng ngoài danh sách → "redirect URL is incorrect".
- `month_period` chỉ áp dụng gói prepaid; `resource_id` bắt buộc với category
  `log` ở resize/recovery/renewal.
- Log project `projectType=required` là project hệ thống — TUYỆT ĐỐI không xoá.
- Nhóm đọc + báo giá không cần `--allow-write`; nhóm đặt lệnh thì có.
"""


def _create_uptime_monitor_guidance() -> str:
    return """\
# Tạo một Synthetic Uptime Monitor (API test)

Tính năng: khai báo một phép thử định kỳ (probe) tới một URL/endpoint từ một hoặc
nhiều location, đặt điều kiện đạt/không đạt (assertions), lịch chạy, và kênh thông
báo. Được tạo nên bằng cách kết hợp các tool location + validate probe + tạo
monitor (host uptime-manager, field snake_case).

## Quy trình
1. Chọn location: `list_locations` → lấy các `id` location để chạy probe (PUBLIC
   do nền tảng cấp; PRIVATE là worker người dùng tự chạy).
2. Dựng `config` (probe): `{assertions: [{type, operator, target}], request:
   {url, method, headers, query, body, timeout, verified_ssl}}` — ví dụ assertion
   `{type:"status_code", operator:"does_not_match_regex", target:"[4-5][0-9][0-9]"}`.
3. (Tuỳ chọn) `options` = `{test_frequency, tests, failed_locations}` (lịch chạy +
   ngưỡng coi là fail); `notifications` = `{"In-alarm":[...], "Up":[...],
   "Undetermined":[...]}` với id kênh lấy từ `list_notifications`.
4. Xem trước: `validate_uptime` với `{subtype, config, locations}` — chạy thử 1 lần
   KHÔNG lưu, để kiểm tra probe đạt trước khi tạo.
5. Trình plan (name, subtype, url/target, locations, lịch) NGAY cùng câu hỏi xác
   nhận. HARD GATE: chờ xác nhận rõ ràng.
6. Tạo: `create_uptime` với body `{name, type:"API", subtype, config, locations,
   options?, notifications?}`.
7. Sau đó: `list_uptimes` / `get_uptime` để xem; `update_uptime` sửa;
   `update_uptime_status` bật/tắt; `delete_uptime` xoá (IRREVERSIBLE).

## Lưu ý / guardrail
- `locations` là danh sách ID location (từ bước 1), không phải tên.
- `subtype` là giao thức probe (HTTP, PING, TCP, SSL...); `type` hiện là "API".
- Luôn `validate_uptime` trước khi tạo/sửa để tránh tạo monitor sai.
- Server read-only → create/update/delete sẽ lỗi; báo người dùng khởi động lại
  với `--allow-write`.
"""


_FEATURE_GUIDES = {
    "build_dashboard": _build_dashboard_guidance,
    "query_metrics": _query_metrics_guidance,
    "create_metric_alarm": _create_metric_alarm_guidance,
    "monitor_infrastructure": _monitor_infrastructure_guidance,
    "edit_metric_unit": _edit_metric_unit_guidance,
    "manage_log_projects": _manage_log_projects_guidance,
    "manage_integrations": _manage_integrations_guidance,
    "create_notification_channel": _create_notification_channel_guidance,
    "view_quota_usage": _view_quota_usage_guidance,
    "create_uptime_monitor": _create_uptime_monitor_guidance,
}

Feature = Literal[
    "build_dashboard",
    "query_metrics",
    "create_metric_alarm",
    "monitor_infrastructure",
    "edit_metric_unit",
    "manage_log_projects",
    "manage_integrations",
    "create_notification_channel",
    "view_quota_usage",
    "create_uptime_monitor",
]


class PromptsHandler:
    """Register portable vMonitor guidance prompts + the get_feature_guide tool.

    Each guide covers one composite capability — a feature built by combining
    several endpoints/tools. Guides are served BOTH as MCP prompts (loaded by the
    user) and as the get_feature_guide tool (agents call it on their own) from
    one source of truth.
    """

    def __init__(self, mcp) -> None:
        self.mcp = mcp
        self.mcp.prompt(name="vmonitor_getting_started")(self.vmonitor_getting_started)
        self.mcp.prompt(name="vmonitor_build_dashboard")(self.vmonitor_build_dashboard)
        self.mcp.prompt(name="vmonitor_query_metrics")(self.vmonitor_query_metrics)
        self.mcp.prompt(name="vmonitor_create_metric_alarm")(self.vmonitor_create_metric_alarm)
        self.mcp.prompt(name="vmonitor_monitor_infrastructure")(
            self.vmonitor_monitor_infrastructure
        )
        self.mcp.prompt(name="vmonitor_edit_metric_unit")(self.vmonitor_edit_metric_unit)
        self.mcp.prompt(name="vmonitor_manage_log_projects")(self.vmonitor_manage_log_projects)
        self.mcp.prompt(name="vmonitor_manage_integrations")(self.vmonitor_manage_integrations)
        self.mcp.prompt(name="vmonitor_create_notification_channel")(
            self.vmonitor_create_notification_channel
        )
        self.mcp.prompt(name="vmonitor_view_quota_usage")(self.vmonitor_view_quota_usage)
        self.mcp.prompt(name="vmonitor_create_uptime_monitor")(self.vmonitor_create_uptime_monitor)
        self.mcp.tool(name="get_feature_guide", annotations=READ)(self.get_feature_guide)

    async def get_feature_guide(
        self,
        feature: Feature = Field(
            ...,
            description="Which composite vMonitor capability to get the step-by-step guide for",
        ),
    ) -> str:
        """Get the step-by-step guide for a composite vMonitor capability.

        A capability is a feature built by combining several endpoints/tools.
        The guide returns the tool order, ask-the-user steps, guardrails, and
        confirm-gate protocol. Call this FIRST, before starting a multi-step
        feature (building a dashboard, creating an alarm, setting up a
        notification channel, editing a metric unit, creating an uptime monitor,
        ...), and follow it exactly.
        """
        return _FEATURE_GUIDES[feature]()

    async def vmonitor_getting_started(self) -> str:
        """Onboarding for vMonitor: what it is, auth, the composite capabilities, and their guides."""
        return _GETTING_STARTED

    async def vmonitor_build_dashboard(self) -> str:
        """Guided flow to build a dashboard: create/clone, variables, views, widgets."""
        return _build_dashboard_guidance()

    async def vmonitor_query_metrics(self) -> str:
        """Guided flow to query metrics (catalogue -> statistics) and search/export logs."""
        return _query_metrics_guidance()

    async def vmonitor_create_metric_alarm(self) -> str:
        """Guided flow to create a metric / log / change-method alarm."""
        return _create_metric_alarm_guidance()

    async def vmonitor_monitor_infrastructure(self) -> str:
        """Guided flow to discover hosts, toggle monitoring, and set up log mappings."""
        return _monitor_infrastructure_guidance()

    async def vmonitor_edit_metric_unit(self) -> str:
        """Guided flow to edit or reset a metric's display unit."""
        return _edit_metric_unit_guidance()

    async def vmonitor_manage_log_projects(self) -> str:
        """Guided flow for log projects: pipelines/processors, archive, and refill."""
        return _manage_log_projects_guidance()

    async def vmonitor_manage_integrations(self) -> str:
        """Guided flow to install integrations, issue metric API keys, and manage log certs."""
        return _manage_integrations_guidance()

    async def vmonitor_create_notification_channel(self) -> str:
        """Guided flow to create a notification channel (OTP verification flow)."""
        return _create_notification_channel_guidance()

    async def vmonitor_view_quota_usage(self) -> str:
        """Guided flow to view quota/usage and quote package prices (read-only)."""
        return _view_quota_usage_guidance()

    async def vmonitor_create_uptime_monitor(self) -> str:
        """Guided flow to create a synthetic uptime monitor (API test)."""
        return _create_uptime_monitor_guidance()
