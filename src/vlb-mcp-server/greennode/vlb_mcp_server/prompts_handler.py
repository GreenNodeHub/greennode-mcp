"""MCP prompts for vLB: portable onboarding + feature-flow guidance.

A vLB "feature" here means a capability built by *composing several
endpoints/tools* — putting a load balancer in front of two web servers means
resolving a package, a subnet, a pool, its members, a listener and possibly a
certificate before traffic flows. The step-by-step choreography of such a
composite feature (which tools to call, in what order, the guardrails and the
confirm gates) lives here, not in tool docstrings. Docstrings keep the per-tool
contract; these prompts/guides carry the multi-endpoint flow.

Each guide is served BOTH as an MCP prompt (`vlb_<name>`, loaded by the user)
and via the `get_feature_guide` tool (agents call it themselves) from a single
source of truth. Guide text is Vietnamese, matching the other GreenNode MCP
servers; code, docstrings and parameter descriptions stay English.
"""

from __future__ import annotations

from greennode.vlb_mcp_server.tool_annotations import READ
from pydantic import Field
from typing import Literal


_GETTING_STARTED = """\
# vLB (GreenNode Load Balancer) — Bắt đầu

vLB là dịch vụ cân bằng tải của GreenNode. Bạn mô tả nhu cầu bằng ngôn ngữ tự
nhiên; trợ lý tự khám phá tài nguyên, chọn default an toàn, và xác nhận trước
khi thực thi. Bạn KHÔNG cần biết ID tài nguyên thô.

## Khái niệm
- Load balancer: đối tượng gốc, nhận traffic tại một địa chỉ. Đặt trên MỘT
  subnet của vServer, và subnet đó CHỐT zone.
  - `scheme`: `Internet` (có IP public) hoặc `Internal` (chỉ trong VPC).
  - `type`: `Layer 4` (TCP/UDP) hoặc `Layer 7` (HTTP/HTTPS, có L7 policy).
    Lưu ý hai giá trị này CÓ dấu cách. Không đổi được sau khi tạo.
- Listener: cổng vào — protocol + port. Quyết định TLS và timeout.
- Pool: nhóm backend + thuật toán chia tải + health monitor.
- Member: một backend, định danh bằng IP **private** của server và port.
- Health monitor: cách kiểm tra backend còn sống. Tạo CÙNG pool, và
  protocol của nó KHÔNG đổi được về sau.
- L7 policy: luật định tuyến theo path/host trên một listener Layer 7.
- Certificate (`cas`): chứng chỉ TLS cho listener HTTPS.

## Chuẩn bị
1. MCP server đã cấu hình trong client. Thao tác đọc chạy mặc định; tạo/sửa/xoá
   cần chạy server với `--allow-write` (write lỗi vì read-only → báo người dùng
   khởi động lại với `--allow-write`, đừng tìm cách lách).
2. Xác thực qua `~/.greennode/` (GreenNode IAM) hoặc env (`GRN_CLIENT_ID`,
   `GRN_CLIENT_SECRET`, `GRN_PROFILE`, `GRN_PROJECT_ID`, `GRN_DEFAULT_REGION`).
   Kiểm tra bằng tool `get_access_token`.

## Region & zone
- Region: `HCM-3` (mặc định) hoặc `HAN`. Tài nguyên hai region KHÔNG thấy nhau.
  Không tìm thấy thứ người dùng nhắc → thử region còn lại TRƯỚC khi kết luận.
- Zone: dạng `HCM03-1A`. Subnet người dùng chọn chốt zone cho load balancer.
- Project id tự resolve theo region — không bao giờ hỏi người dùng.

## Hai điều phải nhớ ở vLB
1. **Mọi lệnh ghi đều bất đồng bộ.** API trả về trước khi việc xong, và gateway
   TỪ CHỐI lệnh ghi thứ hai khi cái trước còn đang chạy. Sau MỖI lệnh ghi, gọi
   `get_load_balancer_status` cho tới khi `settled = true`.
2. **`update_pool_members` thay TOÀN BỘ danh sách member.** Muốn thêm/bớt một
   backend thì dùng `attach_pool_members` / `detach_pool_members`. Tự dựng danh
   sách đầy đủ từ một yêu cầu "thêm 1 server" là cách nhanh nhất để gây sự cố.

## Định hướng, theo thứ tự
1. `list_load_balancers` — đang có sẵn những gì.
2. `list_load_balancer_packages` — gói nào mua được, cho `Layer 4` hay `Layer 7`.
3. `list_vpcs` → `list_subnets` — đặt cái mới ở đâu.
4. `list_servers` — backend nào sẽ nhận traffic (lấy `private_ip`).

## Quy tắc phiên làm việc
- Render danh sách: LUÔN để `id` và `name` là hai cột đầu.
- KHÔNG tự chọn thay người dùng khi có nhiều lựa chọn (package, subnet, scheme,
  protocol, chứng chỉ, ngưỡng health check). Trình ra rồi hỏi.
- Trước mọi lệnh ghi: tóm tắt plan và xin xác nhận rõ ràng. Với DESTRUCTIVE
  (xoá, thay danh sách member, gỡ member) phải nêu đúng cái gì mất và mất
  vĩnh viễn.
- Tên load balancer, listener, pool, policy đều KHÔNG đổi được — chốt tên với
  người dùng trước khi tạo.

## Tính năng tổng hợp → guide tương ứng
Lấy choreography bằng `get_feature_guide feature=<tên>` (hoặc mở prompt
`vlb_<tên>`):
- `create_load_balancer` — Dựng load balancer đầu tiên, từ package tới traffic.
- `manage_members` — Thêm/bớt backend an toàn, và sửa health check.
- `route_http_traffic` — Định tuyến theo path/host bằng L7 policy.
- `terminate_tls` — Import chứng chỉ và bật HTTPS.
- `high_availability` — Multi-AZ, autoscale, resize, clone.
"""


def _create_load_balancer_guidance() -> str:
    """The end-to-end creation chain, from package discovery to live traffic."""
    return """\
Mục tiêu: dựng một load balancer nhận traffic và chia về các backend.

## Thứ tự bắt buộc
Pool phải có TRƯỚC listener (listener tham chiếu `defaultPoolId`). Có hai cách:

- **Một lệnh**: `create_load_balancer` với `listener` + `pool` lồng bên trong.
  Đây là cách DUY NHẤT có được load balancer chạy được trong một lần gọi.
- **Từng bước**: `create_load_balancer` → `create_pool` → `create_listener`.
  Dễ kiểm soát hơn, và bắt buộc nếu cần nhiều pool/listener.

## Các bước
1. `list_load_balancer_packages` — cho người dùng chọn. Đối chiếu `lb_type` với
   `type` định tạo; gói `Layer 4` không dùng được cho `Layer 7`.
2. `list_vpcs` → `list_subnets` — chọn subnet. Đọc `zone_id` TỪ subnet, đừng
   hỏi zone riêng.
3. Hỏi người dùng:
   - `scheme`: `Internet` hay `Internal`?
   - `type`: `Layer 4` hay `Layer 7`? (dịch vụ HTTP/HTTPS → `Layer 7`)
   - Tên (5-50 ký tự) — nói rõ tên này VĨNH VIỄN, không có API đổi tên.
4. `list_servers` — lấy `private_ip` của các backend. IP public sẽ cho ra pool
   không bao giờ healthy.
5. `validate_load_balancer_create` với đúng body định gửi. Sửa hết `errors`,
   đọc `warnings` cho người dùng nghe.
6. Tóm tắt plan (package + giá, subnet, scheme, type, listener, pool, member)
   và XIN XÁC NHẬN.
7. `create_load_balancer`.
8. `get_load_balancer_status` lặp lại tới khi `settled = true`.
9. `get_load_balancer` — đọc `address` để đưa cho người dùng.

## Bẫy hay gặp
- `id` trả về rỗng KHÔNG có nghĩa là thất bại: API không luôn nói id mới ở key
  nào. Đọc `hint`, kiểm tra bằng `list_load_balancers`, TUYỆT ĐỐI không tạo lại.
- `healthMonitor` là BẮT BUỘC khi tạo pool, và không có endpoint thêm sau. Pool
  tạo thiếu nó chỉ sửa được bằng cách tạo lại pool.
- Protocol của health monitor cũng không đổi được. Chọn đúng ngay từ đầu:
  `TCP` cho Layer 4, `HTTP`/`HTTPS` cho web có endpoint healthz.
"""


def _manage_members_guidance() -> str:
    """Adding and removing backends without taking the others out of service."""
    return """\
Mục tiêu: thay đổi tập backend của một pool mà không làm gián đoạn phần còn lại.

## Vì sao phải cẩn thận
Endpoint gốc `PUT .../members` THAY TOÀN BỘ danh sách. Nhận yêu cầu "thêm 1
server" rồi gửi danh sách 1 phần tử là xoá sạch mọi backend khác — mất dịch vụ
ngay lập tức.

## Thêm backend
1. `list_servers` — lấy `private_ip` và port.
2. `list_pool_members` — cho người dùng thấy đang có gì.
3. `attach_pool_members` với CHỈ các member mới. Tool tự đọc danh sách hiện tại
   và ghi lại bản đã gộp.
   - Member trùng `(ipAddress, port)` sẽ THAY THẾ member cũ — đó là cách đổi
     `weight` hoặc bật/tắt `backup`.
4. `get_load_balancer_status` tới khi `settled`.
5. `list_pool_members` — xác nhận `display_status` của member mới.

## Gỡ backend
1. `list_pool_members` — xác định đúng `(ip_address, port)`.
2. Nói rõ với người dùng: backend này NGỪNG nhận traffic ngay. Xin xác nhận.
3. `detach_pool_members` với `targets`.
   - Target không khớp gì → tool báo lỗi, không im lặng bỏ qua.
   - Gỡ hết member cần `allow_empty=true`; pool rỗng không phục vụ gì.

## Chỉ dùng `update_pool_members` khi
Người dùng thực sự muốn ghi đè toàn bộ tập member (ví dụ đồng bộ từ danh sách
bên ngoài). Khi đó phải đọc `list_pool_members` trước và đưa vào ĐỦ mọi member
muốn giữ.

## Sửa health check
- `get_pool_health_monitor` — xem cấu hình hiện tại.
- `update_pool_health_monitor` — chỉ truyền field muốn đổi; tool tự mang theo
  algorithm, stickiness, TLS và các ngưỡng còn lại (API bắt buộc chúng, gửi
  thiếu là reset).
- KHÔNG đổi được protocol của health check. Muốn đổi phải xoá và tạo lại pool —
  nói thẳng điều này với người dùng thay vì thử.

## Lưu ý đồng thời
`attach_pool_members` và `detach_pool_members` là đọc-rồi-ghi, không nguyên tử. Đừng chạy hai thao tác member
song song trên cùng một pool.
"""


def _route_http_traffic_guidance() -> str:
    """Path/host based routing with L7 policies."""
    return """\
Mục tiêu: đưa `/api` sang một nhóm backend, phần còn lại sang nhóm khác.

## Điều kiện
Load balancer phải là `Layer 7` và listener phải là HTTP hoặc HTTPS. L7 policy
không tồn tại trên Layer 4.

## Các bước
1. `create_pool` cho từng nhóm backend đích (xem guide `manage_members`).
2. `list_listeners` — chọn listener sẽ mang luật.
3. `create_l7_policy` cho mỗi nhánh:
   - `action = REDIRECT_TO_POOL` + `redirectPoolId`, hoặc
   - `action = REDIRECT_TO_URL` + `redirectUrl` (+ `redirectHttpCode` 301/302).
   - `rules`: TẤT CẢ rule phải khớp thì policy mới chạy.
     `ruleType` là `PATH` hoặc `HOST_NAME`; `compareType` là `STARTS_WITH`,
     `EQUAL_TO`, `CONTAINS`, `ENDS_WITH` hoặc `REGEX`.
   - Policy KHÔNG có rule nào sẽ khớp MỌI request và nuốt hết traffic của
     default pool. Đừng bao giờ tạo như vậy.
4. `list_l7_policies` — đọc `position`.
5. `update_l7_policy_order` nếu cần: luật cụ thể phải đứng TRƯỚC luật rộng, nếu
   không luật cụ thể không bao giờ được xét.
   - Giữ nguyên hệ đánh số mà `list_l7_policies` đang báo. Tài liệu API tự mâu
     thuẫn về 0-based hay 1-based, nên bám theo giá trị thực tế.
6. `get_load_balancer_status` tới khi `settled`, rồi `list_l7_policies` xác nhận.

## Traffic không khớp luật nào
Đi về `default_pool_id` của listener; nếu listener không có thì áp
`defaultAction` (`accept`/`drop`). Trước khi xoá một policy, nói rõ traffic đó
sẽ rơi về đâu.

## Sửa policy
`update_l7_policy` thay TOÀN BỘ, kể cả `rules`. Đọc `get_l7_policy` trước và
gửi lại các rule muốn giữ — bỏ sót là policy thành khớp mọi thứ.
"""


def _terminate_tls_guidance() -> str:
    """Importing a certificate and turning on HTTPS."""
    return """\
Mục tiêu: listener phục vụ HTTPS bằng chứng chỉ của người dùng.

## Các bước
1. `list_certificates` — có sẵn chứng chỉ nào chưa, `not_after` còn hạn không.
2. Nếu cần import: hỏi người dùng nội dung PEM. `import_certificate` với
   - `type = TLS/SSL` cho chứng chỉ server (`CA` là cho certificate authority),
   - `name` 5-50 ký tự, chỉ chữ/số/`.`/`-` — chỗ DUY NHẤT trong API này KHÔNG
     cho dấu gạch dưới,
   - `certificate`, `privateKey`, và `certificateChain` nếu có intermediate.
   - `privateKey` là bí mật: gửi một lần, không bao giờ đọc lại, không in ra
     cho người dùng, không đưa sang tool khác. Đừng tự đi đọc file khoá.
3. Gắn vào listener:
   - Listener mới: `create_listener` với `listenerProtocol = HTTPS`,
     `defaultCertificateAuthority = <id>`.
   - Listener sẵn có: `get_listener` → `update_listener` với TOÀN BỘ cấu hình
     hiện tại cộng thêm chứng chỉ. Đây là thay thế toàn phần: thiếu
     `certificateAuthorities` hay `insertHeaders` là có thể mất chúng.
4. Tuỳ chọn: `certificateAuthorities` cho SNI nhiều domain, `clientCertificate`
   cho mTLS, `tlsSecurityPolicy` (`COMPATIBLE`/`MODERN`/`STRICT`),
   `alpnProtocols` (`h2` để bật HTTP/2).
5. `get_load_balancer_status` tới khi `settled`, rồi `get_listener` xác nhận.

## Trước khi xoá chứng chỉ
`get_certificate` xem `in_use`. Xoá chứng chỉ đang được listener phục vụ là làm
hỏng TLS của listener đó, và khoá riêng không khôi phục được — phải import lại
từ PEM gốc.
"""


def _high_availability_guidance() -> str:
    """Multi-AZ, autoscaling, resizing and cloning."""
    return """\
Mục tiêu: load balancer chịu được mất một zone, và co giãn theo tải.

## Multi-AZ + autoscale
1. Load balancer phải được tạo với `autoScalable = true`. Không có API bật lại
   về sau — nếu đang tắt, phải tạo mới (cân nhắc `create_load_balancer_clone`).
2. `get_load_balancer` — đọc `subnet_id` hiện tại (subnet chính).
3. `list_subnets` — chọn thêm subnet ở zone khác.
4. `configure_load_balancer_scaling`:
   - `subnets` là THAY THẾ TOÀN BỘ. Phải bao gồm subnet chính, nếu không zone
     đó mất node.
   - `max_size` là trần số node; `min_size` mặc định bằng số subnet.
5. `get_load_balancer_status` tới khi `settled`.
6. `list_load_balancer_scale_history` — xem thực tế đã scale ra sao.

## Đổi dung lượng gói
`resize_load_balancer` với `package_id` từ `list_load_balancer_packages`. Giá
đổi ngay — xác nhận với người dùng. Đối chiếu `lb_type` của gói với `type` của
load balancer.

## Băng thông (chỉ cho `scheme = Internet`)
`update_load_balancer_bandwidth` với id dạng `bandwidth-pack-<uuid>`. Cũng đổi
giá ngay.

## Nhân bản
1. `get_load_balancer_clone_metadata` — API trả về danh sách listener kèm chứng
   chỉ mà bản sao cần được cấp.
2. `create_load_balancer_clone` với `name` mới, `package_id`, và `certificates`
   cho MỌI listener có TLS. Thiếu entry nào thì listener tương ứng của bản sao
   không có chứng chỉ.
3. Đây là tạo thêm MỘT load balancer tính phí, không phải sửa bản gốc. Xác nhận
   rõ với người dùng.
4. `get_load_balancer_status` trên id MỚI tới khi `settled`.
"""


_FEATURE_GUIDES: dict[str, tuple[str, str]] = {
    "getting_started": ("# vLB — Bắt đầu", _GETTING_STARTED),
    "create_load_balancer": (
        "# vLB — Tạo load balancer",
        _create_load_balancer_guidance(),
    ),
    "manage_members": ("# vLB — Quản lý backend member", _manage_members_guidance()),
    "route_http_traffic": (
        "# vLB — Định tuyến HTTP theo path/host",
        _route_http_traffic_guidance(),
    ),
    "terminate_tls": ("# vLB — Chứng chỉ và HTTPS", _terminate_tls_guidance()),
    "high_availability": (
        "# vLB — Multi-AZ, autoscale, resize, clone",
        _high_availability_guidance(),
    ),
}

Feature = Literal[
    "getting_started",
    "create_load_balancer",
    "manage_members",
    "route_http_traffic",
    "terminate_tls",
    "high_availability",
]


class PromptsHandler:
    """Register the vLB guidance tool and the matching MCP prompts."""

    def __init__(self, mcp):
        self.mcp = mcp
        self.mcp.tool(name="get_feature_guide", annotations=READ)(self.get_feature_guide)

        self.mcp.prompt(name="vlb_getting_started")(self.vlb_getting_started)
        self.mcp.prompt(name="vlb_create_load_balancer")(self.vlb_create_load_balancer)
        self.mcp.prompt(name="vlb_manage_members")(self.vlb_manage_members)
        self.mcp.prompt(name="vlb_route_http_traffic")(self.vlb_route_http_traffic)
        self.mcp.prompt(name="vlb_terminate_tls")(self.vlb_terminate_tls)
        self.mcp.prompt(name="vlb_high_availability")(self.vlb_high_availability)

    async def get_feature_guide(
        self,
        feature: Feature = Field(
            ...,
            description=(
                "Which composite capability to load the flow for: "
                "'getting_started' (orientation, regions, the two vLB rules that bite), "
                "'create_load_balancer' (the full creation chain), "
                "'manage_members' (adding/removing backends safely, health checks), "
                "'route_http_traffic' (L7 policies and their ordering), "
                "'terminate_tls' (certificates and HTTPS listeners), "
                "'high_availability' (Multi-AZ, autoscaling, resize, clone)."
            ),
        ),
    ) -> str:
        """Get the step-by-step guide for a multi-endpoint vLB feature.

        Call this BEFORE starting any creation or management flow. The guide
        gives the tool order, which questions to ask the user, and the confirm
        gates — detail the individual tool docstrings deliberately leave out.
        Guide text is Vietnamese.
        """
        title, body = _FEATURE_GUIDES[feature]
        return f"{title}\n\n{body}"

    async def vlb_getting_started(self) -> str:
        """Onboarding: what vLB manages, regions, and the composite guides."""
        return _GETTING_STARTED

    async def vlb_create_load_balancer(self) -> str:
        """Guided flow for creating a load balancer end to end."""
        return _create_load_balancer_guidance()

    async def vlb_manage_members(self) -> str:
        """Guided flow for changing a pool's backends without an outage."""
        return _manage_members_guidance()

    async def vlb_route_http_traffic(self) -> str:
        """Guided flow for path/host routing with L7 policies."""
        return _route_http_traffic_guidance()

    async def vlb_terminate_tls(self) -> str:
        """Guided flow for importing a certificate and serving HTTPS."""
        return _terminate_tls_guidance()

    async def vlb_high_availability(self) -> str:
        """Guided flow for Multi-AZ, autoscaling, resizing and cloning."""
        return _high_availability_guidance()
