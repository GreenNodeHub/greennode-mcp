"""MCP prompts for VKS: portable onboarding + cluster & node-group guidance."""

from __future__ import annotations

from greennode.vks_mcp_server.tool_annotations import READ
from pydantic import Field
from typing import Literal


_GETTING_STARTED = """\
# VKS (GreenNode Kubernetes Service) — Bắt đầu

VKS là Kubernetes managed của GreenNode. Bạn mô tả nhu cầu bằng
ngôn ngữ tự nhiên; trợ lý tự khám phá tài nguyên, chọn default an toàn, và
xác nhận trước khi thực thi. Bạn KHÔNG cần biết ID tài nguyên thô.

## Khái niệm
- Cluster: control plane K8s managed. `create_cluster` chỉ tạo control plane;
  thêm worker sau bằng `create_nodegroup`.
- Node group: nhóm VM worker giống nhau (flavor/disk/os). Workload cần ít nhất 1.
- VPC/subnet: mạng riêng của node (vServer). Flavor: cỡ VM (vCPU/RAM/GPU).
- SSH key / security group: truy cập node & tường lửa.

## Chuẩn bị
1. MCP server `greennode-mcp` đã cấu hình trong client. Thao tác đọc chạy mặc định;
   tạo/sửa/xoá/scale cần chạy server với `--allow-write` (nếu write lỗi vì read-only,
   báo người dùng khởi động lại với `--allow-write`).
2. Xác thực qua `~/.greenode/` (GreenNode IAM bearer token). Cấu hình bằng
   `grn configure` (ghi `~/.greenode/credentials` + `config`). Thứ tự ưu tiên:
   env (`GRN_CLIENT_ID`, `GRN_CLIENT_SECRET`, `GRN_PROJECT_ID`, `GRN_DEFAULT_REGION`)
   → file profile (`GRN_PROFILE`, mặc định `default`). `project_id` cần cho discovery;
   `grn configure` tự dò. Kiểm tra xác thực bằng tool `get_access_token`.

## Region
- `HCM-3` (mặc định), `HAN` (Hà Nội). Override qua tham số `region` hoặc `GRN_DEFAULT_REGION`.

## Quy tắc tên & mạng
- Tên cluster: 5–20 ký tự (thường + số + gạch nối, đầu/cuối là chữ-số).
- Tên node group: 5–15 ký tự, cùng quy tắc.
- Network type (3 loại): `CILIUM_OVERLAY` và `TIGERA` cần `cidr` (vd `10.96.0.0/16`).
  Default an toàn cho người mới: `CILIUM_OVERLAY` + cidr.
- `secondarySubnets` KHÔNG set ở cluster — chỉ dùng cho node group của cụm
  `CILIUM_NATIVE_ROUTING`, tự set khi tạo: copy nguyên field `secondary_subnets`
  (CIDR, không phải id) của subnet đã chọn; cụm networkType khác không dùng
  field này (bỏ qua, không truyền).

## Tác vụ nào → tool nào
- Tạo cluster: xem prompt `vks_create_cluster` (discovery + `validate_cluster_create`
  + `create_cluster`, poll `get_cluster`). Có thể tạo control-plane-only.
- Discovery (resolve tên → ID): `list_vpcs`, `list_subnets`, `list_flavors`,
  `list_ssh_keys`, `list_security_groups`, `list_volume_types` (ID → `diskType`),
  `list_placement_groups` (ID → `placementGroupId`).
  Quota còn lại: `get_quota` (check trước khi tạo).
- Xem trạng thái: `list_clusters/get`, `list_nodegroups/get`, `list_nodes`,
  `get_cluster_events`, `list_cluster_versions`.
- Node group (tạo/scale/sửa/xoá/nâng version): xem prompt `vks_create_nodegroup` và
  các tool `nodegroup_*`. Labels/tags/taints: dùng `update_nodegroup_metadata`.
- Sửa cluster: `update_cluster` chỉ đổi **version + whitelistNodeCIDRs** (+ bật/tắt
  LB/CSI plugin) — KHÔNG đổi được tên/description. Lịch tự nâng cấp:
  `configure_auto_upgrade`; tự phục hồi node: `configure_auto_healing`.
- Kết nối cluster: cluster mới cần `generate_kubeconfig` một lần (async, cần
  `--allow-write`; hỏi user số ngày hết hạn, 1–365), rồi `get_cluster_kubeconfig`
  (kubeconfig = credential admin — cần `--allow-sensitive-data-access`).
  Xác thực: `get_access_token`.

## Nguyên tắc
- Đọc thì tự do; MỌI thao tác ghi phải qua MỘT lần xác nhận rõ ràng (hard gate).
- Không tự quyết tham số người dùng quan tâm — chọn default an toàn, đánh dấu `[auto]`, cho sửa.
- Resolve tên → ID qua discovery tools; không bắt người dùng dán ID thô.
- Khi hiển thị danh sách/chi tiết tài nguyên: `id` và `name` là HAI CỘT ĐẦU TIÊN
  của bảng, id không được cắt ngắn — thao tác tiếp theo cần nó, không lược bỏ.
- Trả lời bằng ngôn ngữ người dùng dùng. Không bao giờ dán secret vào chat.
"""


def _create_cluster_guidance() -> str:
    return """\
# Tạo Cluster VKS (luồng hybrid)

## Quy trình
1. Xác thực (`get_access_token`); lỗi auth → hướng dẫn `grn configure`.
2. Check quota: `get_quota` — nếu `num_clusters` đã chạm `max_clusters`, dừng và
   báo người dùng (tránh create fail giữa chừng).
3. Hỏi user THEO ĐÚNG THỨ TỰ dưới đây — mỗi câu hỏi CHỈ MỘT cấu hình. KHÔNG gộp
   hai cấu hình vào một câu; KHÔNG gộp nhiều bước tuỳ chọn vào một câu chọn-nhiều
   — mỗi bước là một câu hỏi riêng ("Bạn có muốn cấu hình X không?"). Không bỏ
   qua bước nào thầm lặng:
   a. Tên cluster (5–20 ký tự: thường + số + gạch nối, đầu/cuối chữ-số);
      `description` nhập hoặc bỏ trống ngay trong bước này — KHÔNG hỏi riêng
      "có muốn thêm mô tả không?". LƯU Ý: description chỉ nhận ASCII không dấu
      (chữ/số/khoảng trắng/`-_.@`, ≤255 ký tự) — tiếng Việt có dấu sẽ bị API
      từ chối.
   b. Public/private: `enablePrivateCluster` (mặc định false → API server có
      endpoint public).
   c. Chỉ khi private (`enablePrivateCluster=true`): hỏi
      `enabledServiceEndpoint` (mặc định BẬT — true). Cluster public → BỎ QUA
      bước này, không hỏi và không set field.
   d. `list_cluster_versions` → user chọn `version` (ưu tiên bản recommended);
      `releaseChannel` mặc định `STABLE`.
   e. azStrategy: `SINGLE` (mặc định) / `MULTI` (HA).
   f. `list_vpcs` → user chọn → `vpcId`. Nếu azStrategy=`MULTI`: CHỈ trình các VPC
      có `enabled_dns=true` (MULTI bắt buộc VPC đã bật vDNS); không có VPC nào đạt
      → dừng, hướng dẫn bật vDNS cho VPC ở console trước.
   g. `list_subnets vpc_id=<vpcId>` → SINGLE: chọn 1 → `subnetId`;
      MULTI: chọn nhiều → `listSubnetIds`.
   h. networkType: `CILIUM_OVERLAY` + `cidr: 10.96.0.0/16` (mặc định — đổi cidr
      nếu trùng dải mạng hiện có); `TIGERA` cũng cần `cidr`;
      `CILIUM_NATIVE_ROUTING` → hỏi `nodeNetmaskSize` (KHÔNG hỏi
      `secondarySubnets` — cluster không nhận field này; node group tự set khi tạo).
   i. Tuỳ chọn: plugins — `enabledLoadBalancerPlugin`,
      `enabledBlockStoreCsiPlugin` (mặc định bật cả hai).
   j. Tuỳ chọn: `autoUpgradeConfig` (weekdays + time).
   k. Tuỳ chọn: `autoHealingConfig` (enableAutoHealing, maxUnhealthy,
      unhealthyRange, timeoutUnhealthy 5–180 phút).
4. Chạy `validate_cluster_create` với body; có lỗi → sửa rồi validate lại.
5. Trình plan ĐẦY ĐỦ — bảng mọi field + giá trị, đánh dấu `[auto]`/`[bạn chọn]`
   (gồm cả `poc=false`, `autoRenewal=true`); cho sửa field. Bảng phải nằm NGAY
   TRONG cùng tin nhắn với câu hỏi xác nhận, ngay phía trên nó — KHÔNG được hỏi
   "Xác nhận tạo?" mà chỉ tham chiếu "cấu hình trên" từ tin nhắn trước.
6. HARD GATE: chờ xác nhận rõ ràng (`ok`/`confirm`/`proceed`/...). Input khác =
   điều chỉnh, trình lại plan.
7. Gọi `create_cluster` — chỉ tạo control plane; thêm worker sau bằng
   `create_nodegroup`. Poll `get_cluster` tới `ACTIVE` (~15–20 phút, báo mỗi lần
   đổi trạng thái). Timeout/`ERROR` → xem `get_cluster_events` và báo nguyên nhân.
8. Sau khi ACTIVE: tiếp tục luồng `vks_create_nodegroup` để thêm worker; lấy
   kubeconfig: gọi `generate_kubeconfig` (hỏi user số ngày hết hạn, 1–365;
   async), rồi poll `get_cluster_kubeconfig` tới khi trả YAML.

## Lưu ý
- Tên cluster 5–20 ký tự (thường + số + gạch nối, đầu/cuối chữ-số).
- Sau khi tạo, KHÔNG đổi được tên/description qua MCP; `update_cluster` chỉ đổi
  version + whitelistNodeCIDRs + plugin toggle.
- Server read-only (không `--allow-write`) → tạo sẽ lỗi; báo người dùng khởi động
  lại với `--allow-write`.
"""


def _create_nodegroup_guidance(cluster_id: str | None) -> str:
    target = (
        f"Cluster mục tiêu: `{cluster_id}`."
        if cluster_id
        else "Chưa có cluster_id: hỏi tên cluster rồi `list_clusters` để resolve (đúng 1 khớp → dùng; nhiều → liệt kê hỏi)."
    )
    return f"""\
# Tạo Node Group cho VKS (luồng hybrid)

{target}

## Quy trình
1. Xác thực (`get_access_token`); lỗi auth → hướng dẫn `grn configure`.
2. Resolve cluster (nếu chưa có cluster_id) qua `list_clusters`. Check `get_quota`
   nếu nghi ngờ chạm giới hạn (max node groups/cluster, max nodes/node group).
3. `get_cluster` → lấy `vpcId` và region của cluster (mọi discovery chạy ở region đó).
4. Hỏi user THEO ĐÚNG THỨ TỰ dưới đây — mỗi câu hỏi CHỈ MỘT cấu hình. KHÔNG gộp
   hai cấu hình vào một câu (vd. không hỏi public/private chung với os); KHÔNG gộp
   nhiều bước tuỳ chọn vào một câu chọn-nhiều — mỗi bước là một câu hỏi riêng
   ("Bạn có muốn cấu hình X không?"). Không bỏ qua bước nào thầm lặng. KHÔNG tự
   chọn thầm subnet/flavor/diskType/sshKey khi có nhiều lựa chọn:
   a. Tên node group (5–15 ký tự: thường + số + gạch nối, đầu/cuối chữ-số):
      đề xuất `<cluster>-ng`; không hợp lệ → `default-ng`.
      numNodes: gợi ý `1` (0–10; prod/HA gợi ý 3).
   b. Public/private: `enablePrivateNodes` (mặc định false → node có IP public).
   c. os: `ubuntu` (mặc định; hoặc `linux`, `rocky`).
   d. `list_subnets vpc_id=<vpcId>` → cluster `CILIUM_NATIVE_ROUTING` (xem
      networkType từ get_cluster ở bước 3): CHỈ trình các subnet ACTIVE CÓ
      `secondary_subnets` (không subnet nào đạt → dừng, hướng dẫn user thêm
      secondary subnet cho subnet trên console rồi `list_subnets refresh=true`);
      networkType khác: mọi subnet ACTIVE đều hợp lệ.
      KHÔNG cần trùng subnet/zone mà cluster đang dùng (kể cả MULTI-AZ). Đúng 1 subnet
      đạt → `[auto]`; nhiều hơn → bắt buộc hỏi → `subnetId` (zone của subnet
      quyết định flavor và volume type — hai tool dưới tự suy ra).
      `secondarySubnets` `[auto]`, KHÔNG hỏi: cluster `CILIUM_NATIVE_ROUTING` →
      copy nguyên field `secondary_subnets` (CIDR) của subnet vừa chọn, hiển thị
      giá trị trong plan xác nhận; networkType khác → BỎ QUA field này (không
      truyền, không đưa vào plan).
   e. Tuỳ chọn: `securityGroups` (id từ `list_security_groups`).
   f. `list_flavors cluster_id=<id> subnet_id=<subnetId>` (lọc `need` nếu rõ nhu cầu)
      → user chọn → `flavorId`; gợi ý flavor nhỏ nhất theo vCPU/RAM (dev/test).
   g. Volume: `list_volume_types cluster_id=<id> subnet_id=<subnetId>` → user chọn bậc
      IOPS → `id` là `diskType` (**ID volume type**, không phải chuỗi tên loại đĩa).
      Loại đĩa: NVME mặc định; zone không có NVME → tool tự chuyển SSD (field
      `type_name` trong kết quả cho biết loại); user chủ động cần SSD → truyền
      `type_name=SSD`. Gợi ý bậc IOPS thấp nhất. diskSize: `100` GB (20–5000).
      Hỏi `enabledEncryptionVolume` (mặc định false → đĩa không mã hoá).
   h. `list_ssh_keys` → user chọn → `sshKeyId` (VKS dùng 1 key).
   i. Tuỳ chọn: `autoScaleConfig` (minSize/maxSize — liên quan numNodes).
   j. Tuỳ chọn: `upgradeConfig` (chiến lược nâng cấp; mặc định SURGE 1/0).
   k. Tuỳ chọn: `placementGroupConfigDto` (type=NEW + tên, hoặc type=EXISTING
      + id từ `list_placement_groups`).
   l. Tuỳ chọn: `labels` / `taints` / `tags`.
   Truyền `refresh: true` cho discovery nếu người dùng vừa tạo tài nguyên ở console.
5. Chạy `validate_nodegroup_create` với cluster_id + body; có lỗi → sửa rồi
   validate lại.
6. Trình plan ĐẦY ĐỦ — bảng mọi field + giá trị, đánh dấu `[auto]`/`[bạn chọn]`;
   cho sửa field. Bảng phải nằm NGAY TRONG cùng tin nhắn với câu hỏi xác nhận,
   ngay phía trên nó — KHÔNG được hỏi "Xác nhận tạo?" mà chỉ tham chiếu "cấu
   hình trên" từ tin nhắn trước.
7. HARD GATE: chờ xác nhận rõ ràng (`ok`/`confirm`/`proceed`/...). Input khác = điều chỉnh, trình lại plan.
8. Gọi `create_nodegroup` với body dạng:
   `{{"name","numNodes","flavorId","diskSize","diskType":"<id từ list_volume_types>","subnetId":"<subnet đã chọn>","os":"ubuntu","enablePrivateNodes":false,"securityGroups":[...],"sshKeyId":...}}`
   cộng các mục user đã chọn ở bước 4.
9. Poll `get_nodegroup` tới `ACTIVE` (~10 phút, báo mỗi lần đổi trạng thái). Timeout/`ERROR` → kiểm tra và báo nguyên nhân.

## Lưu ý
- Không có SSH key (`list_ssh_keys` rỗng) → dừng, hướng dẫn tạo key ở GreenNode console (vServer → SSH Keys), rồi resume với `list_ssh_keys refresh=true`.
- Scale/sửa: `update_nodegroup` nhận body **partial** — chỉ `numNodes`,
  `securityGroups`, `autoScaleConfig`, `upgradeConfig`; body rỗng bị từ chối.
  Đổi labels/tags/taints: dùng `update_nodegroup_metadata` (endpoint riêng).
  Cảnh báo scale-down làm gián đoạn workload; nếu có autoScaleConfig, numNodes
  thủ công có thể bị autoscaler ghi đè.
- Server read-only (không `--allow-write`) → tạo/scale sẽ lỗi; báo người dùng khởi động lại với `--allow-write`.
- Không bắt dán secret vào chat.
"""


class PromptsHandler:
    """Register portable VKS guidance prompts on the MCP server.

    The same choreography is also served as a tool (get_creation_guide):
    MCP prompts must be loaded by the user, and field tests showed agents
    run promptless — a tool is something every agent calls on its own.
    """

    def __init__(self, mcp) -> None:
        self.mcp = mcp
        self.mcp.prompt(name="vks_getting_started")(self.vks_getting_started)
        self.mcp.prompt(name="vks_create_cluster")(self.vks_create_cluster)
        self.mcp.prompt(name="vks_create_nodegroup")(self.vks_create_nodegroup)
        self.mcp.tool(name="get_creation_guide", annotations=READ)(self.get_creation_guide)

    async def get_creation_guide(
        self,
        resource: Literal["cluster", "nodegroup"] = Field(
            ..., description="Which creation flow to get the guide for"
        ),
        cluster_id: str | None = Field(
            None,
            description=(
                "For resource='nodegroup': the target cluster id, if already "
                "known — the guide is then anchored to it."
            ),
        ),
    ) -> str:
        """Get the step-by-step guide for creating a VKS cluster or node group.

        Returns the question order, the per-question rules, defaults, and the
        confirm-gate protocol. Call this FIRST, before asking the user
        anything in a create flow, and conduct the conversation exactly as it
        says.
        """
        if resource == "cluster":
            return _create_cluster_guidance()
        return _create_nodegroup_guidance(cluster_id)

    async def vks_getting_started(self) -> str:
        """VKS onboarding: what it is, auth setup, regions, naming, tool routing."""
        return _GETTING_STARTED

    async def vks_create_cluster(self) -> str:
        """Guided cluster creation flow (discovery, defaults, validate, confirm gate)."""
        return _create_cluster_guidance()

    async def vks_create_nodegroup(self, cluster_id: str | None = None) -> str:
        """Guided node-group creation flow (discovery, smart defaults, confirm gate)."""
        return _create_nodegroup_guidance(cluster_id)
