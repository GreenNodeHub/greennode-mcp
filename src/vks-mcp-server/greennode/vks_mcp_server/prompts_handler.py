"""MCP prompts for VKS: portable onboarding + cluster & node-group guidance."""

from __future__ import annotations


_GETTING_STARTED = """\
# VKS (VNG Kubernetes Service) — Bắt đầu

VKS là Kubernetes managed của GreenNode/VNG Cloud. Bạn mô tả nhu cầu bằng
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
2. Xác thực qua `~/.greenode/` (VNG Cloud IAM bearer token). Cấu hình bằng
   `grn configure` (ghi `~/.greenode/credentials` + `config`). Thứ tự ưu tiên:
   env (`GRN_CLIENT_ID`, `GRN_CLIENT_SECRET`, `GRN_PROJECT_ID`, `GRN_DEFAULT_REGION`)
   → file profile (`GRN_PROFILE`, mặc định `default`). `project_id` cần cho discovery;
   `grn configure` tự dò. Kiểm tra xác thực bằng tool `get_access_token`.

## Region
- `HCM-3` (mặc định), `HAN` (Hà Nội). Override qua tham số `region` hoặc `GRN_DEFAULT_REGION`.

## Quy tắc tên & mạng
- Tên cluster: 5–20 ký tự (thường + số + gạch nối, đầu/cuối là chữ-số).
- Tên node group: 5–15 ký tự, cùng quy tắc.
- Network type (3 loại): `CILIUM_OVERLAY` và `TIGERA` cần `cidr` (vd `10.96.0.0/16`);
  `CILIUM_NATIVE_ROUTING` cần `secondarySubnets` (lấy từ `list_subnets`).
  Default an toàn cho người mới: `CILIUM_OVERLAY` + cidr.

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
- Kết nối cluster: `get_cluster_kubeconfig`. Xác thực: `get_access_token`.

## Nguyên tắc
- Đọc thì tự do; MỌI thao tác ghi phải qua MỘT lần xác nhận rõ ràng (hard gate).
- Không tự quyết tham số người dùng quan tâm — chọn default an toàn, đánh dấu `[auto]`, cho sửa.
- Resolve tên → ID qua discovery tools; không bắt người dùng dán ID thô.
- Trả lời bằng ngôn ngữ người dùng dùng. Không bao giờ dán secret vào chat.
"""


def _create_cluster_guidance() -> str:
    return """\
# Tạo Cluster VKS (luồng hybrid)

## Quy trình
1. Xác thực (`get_access_token`); lỗi auth → hướng dẫn `grn configure`.
2. Check quota: `get_quota` — nếu `num_clusters` đã chạm `max_clusters`, dừng và
   báo người dùng (tránh create fail giữa chừng).
3. Discovery: `list_vpcs` (chọn VPC), `list_cluster_versions` (chọn version — ưu tiên
   bản recommended). Nếu dùng `CILIUM_NATIVE_ROUTING`: thêm `list_subnets` để lấy
   `secondarySubnets` (mỗi subnet trả kèm danh sách `secondary_subnets`).
4. Chọn default an toàn (đánh dấu `[auto]`, cho sửa):
   - networkType: `CILIUM_OVERLAY` + `cidr: 10.96.0.0/16` (đơn giản nhất; đổi cidr
     nếu trùng dải mạng hiện có). TIGERA cũng cần `cidr`;
     `CILIUM_NATIVE_ROUTING` cần `secondarySubnets`.
   - releaseChannel: `STABLE`. azStrategy: `SINGLE` (prod/HA cân nhắc `MULTI`).
   - enablePrivateCluster: `false`. Plugin LB/CSI: bật; serviceEndpoint: tắt.
   - `create_cluster` chỉ tạo control plane; thêm worker sau bằng `create_nodegroup`
     (theo body của prompt `vks_create_nodegroup`).
   - Tuỳ chọn: `autoUpgradeConfig` (weekdays + time), `autoHealingConfig`
     (enableAutoHealing, maxUnhealthy, unhealthyRange, timeoutUnhealthy 5–180 phút).
5. Trình plan đầy đủ (mỗi field + `[auto]`/`[bạn chọn]`); cho sửa field. Nêu rõ
   default liên quan bảo mật để user xác nhận: `enablePrivateCluster=false`
   (API server có endpoint public).
6. Chạy `validate_cluster_create` với body; có lỗi → sửa rồi validate lại.
7. HARD GATE: chờ xác nhận rõ ràng (`ok`/`confirm`/`proceed`/...). Input khác =
   điều chỉnh, trình lại plan.
8. Gọi `create_cluster`. Poll `get_cluster` tới `ACTIVE` (~15–20 phút, báo mỗi lần
   đổi trạng thái). Timeout/`ERROR` → xem `get_cluster_events` và báo nguyên nhân.
9. Sau khi ACTIVE: nếu control-plane-only, tiếp tục luồng `vks_create_nodegroup`
   để thêm worker; lấy kubeconfig bằng `get_cluster_kubeconfig`.

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
3. Discovery theo chuỗi (zone-scoped — chạy mọi discovery ở region của cluster):
   a. `get_cluster` → lấy `vpcId` và region của cluster.
   b. `list_subnets vpc_id=<vpcId>` → trình danh sách cho user chọn → `subnetId`
      (zone của subnet quyết định flavor và volume type — hai tool dưới tự suy ra).
   c. `list_flavors cluster_id=<id> subnet_id=<subnetId>` (lọc `need` nếu rõ nhu cầu)
      → user chọn → `flavorId`.
   d. `list_volume_types cluster_id=<id> subnet_id=<subnetId>` → user chọn bậc IOPS
      → `id` là `diskType` (**ID volume type**, không phải chuỗi "SSD"; NVME cố định).
   e. `list_ssh_keys`; tuỳ chọn `list_security_groups`, `list_placement_groups`.
   Truyền `refresh: true` nếu người dùng vừa tạo tài nguyên ở console.
4. Chọn default an toàn (đánh dấu `[auto]`, cho sửa) — KHÔNG tự chọn thầm
   subnet/flavor/diskType/sshKey khi có nhiều lựa chọn, phải hỏi:
   - Subnet: nếu VPC chỉ có đúng 1 subnet ACTIVE → `[auto]`; nhiều hơn → bắt buộc hỏi.
   - Flavor: gợi ý flavor nhỏ nhất theo vCPU/RAM trong zone đã chọn (dev/test).
   - Disk: gợi ý bậc IOPS thấp nhất từ `list_volume_types`; `100` GB (20–5000).
     numNodes: `1` (0–10; prod/HA gợi ý 3).
   - os: `ubuntu` (hoặc `linux`, `rocky`). SSH key: key đang có (VKS dùng 1 key).
   - Tên node group: đề xuất `<cluster>-ng`; nếu quá 15 ký tự/không hợp lệ → `default-ng`.
5. Trình plan đầy đủ (mỗi field + `[auto]`/`[bạn chọn]`); cho sửa field. Nêu rõ các
   default liên quan bảo mật để user xác nhận: `enablePrivateNodes=false` (node có
   IP public) và `enabledEncryptionVolume=false` (đĩa không mã hoá).
6. HARD GATE: chờ xác nhận rõ ràng (`ok`/`confirm`/`proceed`/...). Input khác = điều chỉnh, trình lại plan.
7. Gọi `create_nodegroup` với body dạng:
   `{{"name","numNodes","flavorId","diskSize","diskType":"<id từ list_volume_types>","subnetId":"<subnet đã chọn>","os":"ubuntu","enablePrivateNodes":false,"securityGroups":[...],"sshKeyId":...}}`
   Tuỳ chọn nâng cao: `labels`/`taints`/`tags`,
   `secondarySubnets`, `enabledEncryptionVolume`, `autoScaleConfig`
   (minSize/maxSize), `placementGroupConfigDto` (type=NEW + tên, hoặc
   type=EXISTING + id từ `list_placement_groups`), `upgradeConfig` (mặc định SURGE 1/0).
8. Poll `get_nodegroup` tới `ACTIVE` (~10 phút, báo mỗi lần đổi trạng thái). Timeout/`ERROR` → kiểm tra và báo nguyên nhân.

## Lưu ý
- Không có SSH key (`list_ssh_keys` rỗng) → dừng, hướng dẫn tạo key ở VNG Cloud console (vServer → SSH Keys), rồi resume với `list_ssh_keys refresh=true`.
- Scale/sửa: `update_nodegroup` nhận body **partial** — chỉ `numNodes`,
  `securityGroups`, `autoScaleConfig`, `upgradeConfig`; body rỗng bị từ chối.
  Đổi labels/tags/taints: dùng `update_nodegroup_metadata` (endpoint riêng).
  Cảnh báo scale-down làm gián đoạn workload; nếu có autoScaleConfig, numNodes
  thủ công có thể bị autoscaler ghi đè.
- Server read-only (không `--allow-write`) → tạo/scale sẽ lỗi; báo người dùng khởi động lại với `--allow-write`.
- Không bắt dán secret vào chat.
"""


class PromptsHandler:
    """Register portable VKS guidance prompts on the MCP server."""

    def __init__(self, mcp) -> None:
        self.mcp = mcp
        self.mcp.prompt(name="vks_getting_started")(self.vks_getting_started)
        self.mcp.prompt(name="vks_create_cluster")(self.vks_create_cluster)
        self.mcp.prompt(name="vks_create_nodegroup")(self.vks_create_nodegroup)

    async def vks_getting_started(self) -> str:
        """VKS onboarding: what it is, auth setup, regions, naming, tool routing."""
        return _GETTING_STARTED

    async def vks_create_cluster(self) -> str:
        """Guided cluster creation flow (discovery, defaults, validate, confirm gate)."""
        return _create_cluster_guidance()

    async def vks_create_nodegroup(self, cluster_id: str | None = None) -> str:
        """Guided node-group creation flow (discovery, smart defaults, confirm gate)."""
        return _create_nodegroup_guidance(cluster_id)
