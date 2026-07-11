"""Kubernetes handler for the GreenNode MCP Server."""

from __future__ import annotations

import logging
import os
import re
import yaml
from greennode.vks_mcp_server.client import VksClient
from greennode.vks_mcp_server.config import Region, VksConfig
from greennode.vks_mcp_server.k8s_apis import K8sApis
from greennode.vks_mcp_server.k8s_client_cache import K8sClientCache
from greennode.vks_mcp_server.models import (
    ApiVersionsData,
    ApplyYamlData,
    EventItem,
    EventsData,
    KubernetesResourceData,
    KubernetesResourceListData,
    Operation,
    PodLogsData,
    ResourceSummary,
)
from greennode.vks_mcp_server.tool_annotations import DESTRUCTIVE, READ, WRITE
from pydantic import Field
from typing import Any, Dict, Literal, Optional


logger = logging.getLogger(__name__)


class K8sHandler:
    """Handler for Kubernetes operations in the GreenNode MCP Server.

    This class provides tools for interacting with Kubernetes clusters, including
    listing resources, managing individual resources, applying YAML manifests,
    retrieving pod logs and events, and listing API versions.
    """

    def __init__(
        self,
        mcp,
        config: VksConfig,
        vks_client: VksClient,
        allow_write: bool = False,
        allow_sensitive_data_access: bool = False,
    ):
        """Initialize the Kubernetes handler.

        Args:
            mcp: The MCP server instance
            config: VKS configuration
            vks_client: VKS HTTP client
            allow_write: Whether to enable write access (default: False)
            allow_sensitive_data_access: Whether to allow access to sensitive data (default: False)
        """
        self.mcp = mcp
        self.config = config
        self.client_cache = K8sClientCache(vks_client)
        self.allow_write = allow_write
        self.allow_sensitive_data_access = allow_sensitive_data_access

        self.mcp.tool(name="list_k8s_resources", annotations=READ)(self.list_k8s_resources)
        self.mcp.tool(name="get_pod_logs", annotations=READ)(self.get_pod_logs)
        self.mcp.tool(name="get_k8s_events", annotations=READ)(self.get_k8s_events)
        self.mcp.tool(name="list_api_versions", annotations=READ)(self.list_api_versions)
        self.mcp.tool(name="manage_k8s_resource", annotations=DESTRUCTIVE)(
            self.manage_k8s_resource
        )
        self.mcp.tool(name="apply_yaml", annotations=WRITE)(self.apply_yaml)
        self.mcp.tool(name="generate_app_manifest", annotations=READ)(self.generate_app_manifest)

    async def get_client(self, cluster_id: str, region: str | None = None) -> K8sApis:
        """Get a Kubernetes client for the specified cluster.

        Args:
            cluster_id: ID of the VKS cluster
            region: Region override (optional)

        Returns:
            K8sApis instance
        """
        return await self.client_cache.get_client(cluster_id, region)

    def filter_null_values(self, data: Any) -> Any:
        """Recursively filter out null values from dictionaries and lists.

        Args:
            data: The data structure to filter (dict, list, or primitive)

        Returns:
            The filtered data structure with null values removed
        """
        if isinstance(data, dict):
            return {k: self.filter_null_values(v) for k, v in data.items() if v is not None}
        if isinstance(data, list):
            return [self.filter_null_values(item) for item in data if item is not None]
        return data

    def remove_managed_fields(self, resource: Dict[str, Any]) -> Dict[str, Any]:
        """Remove metadata.managed_fields from a Kubernetes resource.

        Args:
            resource: The Kubernetes resource dictionary

        Returns:
            The resource with metadata.managed_fields removed
        """
        if isinstance(resource, dict):
            if "metadata" in resource:
                resource["metadata"].pop("managedFields", None)
        return resource

    def cleanup_resource_response(self, resource: Any) -> Any:
        """Clean up a Kubernetes resource response by removing managed fields and null values.

        This method:
        1. Removes metadata.managed_fields which is typically large and not useful
        2. Recursively removes null values to reduce response size

        Args:
            resource: The Kubernetes resource to clean up

        Returns:
            The cleaned up resource
        """
        resource = self.remove_managed_fields(resource)
        return self.filter_null_values(resource)

    @staticmethod
    def _validate_dns_label(value: str, field: str) -> None:
        """Validate value as a Kubernetes RFC 1123 DNS label.

        Raises ValueError if invalid. Also prevents path traversal / YAML injection.
        """
        if len(value) > 63:
            raise ValueError(f'Invalid {field} "{value}": must be at most 63 characters long')
        if not re.match(r"^[a-z0-9]([a-z0-9\-]*[a-z0-9])?$", value):
            raise ValueError(
                f'Invalid {field} "{value}": must consist of lowercase alphanumeric '
                "characters or hyphens, and start and end with an alphanumeric character"
            )

    def _load_yaml_template(self, template_files: list[str], values: Dict[str, str]) -> str:
        """Load template files, substitute placeholders in a single pass, join with '---'.

        Single-pass regex substitution ensures a substituted VALUE that happens to
        contain a placeholder token is never re-substituted.
        """
        templates_dir = os.path.join(os.path.dirname(__file__), "templates", "k8s-templates")
        pattern = re.compile("|".join(re.escape(key) for key in values))
        contents = []
        for template_file in template_files:
            with open(os.path.join(templates_dir, template_file)) as f:
                content = f.read()
            content = pattern.sub(lambda m: values[m.group(0)], content)
            contents.append(content)
        return "\n---\n".join(contents)

    async def generate_app_manifest(
        self,
        app_name: str = Field(
            ..., description="Application name; used for Deployment/Service names and labels."
        ),
        image_uri: str = Field(
            ...,
            description="Full container image URI with tag, e.g. 'vcr.vngcloud.vn/<repo>:<tag>'.",
        ),
        output_dir: str = Field(
            ..., description="Absolute path to the directory to save the manifest file."
        ),
        port: int = Field(
            80, ge=1, le=65535, description="Container/Service port the application listens on."
        ),
        replicas: int = Field(2, ge=1, description="Number of replicas to deploy."),
        cpu: str = Field(
            "100m", description="CPU request/limit per container, e.g. '100m', '500m'."
        ),
        memory: str = Field(
            "128Mi", description="Memory request/limit per container, e.g. '128Mi', '1Gi'."
        ),
        namespace: str = Field("default", description="Kubernetes namespace to deploy to."),
        load_balancer_scheme: Literal["internet-facing", "internal"] = Field(
            "internal",
            description="VKS LoadBalancer scheme, rendered as the vks.vngcloud.vn/scheme "
            "annotation. 'internal' = private VPC only; 'internet-facing' = public.",
        ),
    ) -> str:
        """Generate a Kubernetes Deployment + LoadBalancer Service manifest for an app.

        Writes `<app_name>-manifest.yaml` to output_dir and returns the YAML, ready to
        deploy with the apply_yaml tool. Use this instead of hand-writing manifests.

        ## Requirements
        - The server must be run with the `--allow-write` flag
        - output_dir must be an absolute path

        ## Generated resources
        - Deployment: manages the app pods (replicas, resource requests/limits)
        - Service: type LoadBalancer, exposed via the VKS LoadBalancer Controller
          (vks.vngcloud.vn/scheme annotation controls internal vs internet-facing)

        ## Workflow
        1. generate_app_manifest  -> creates the YAML file
        2. (review/edit the file if needed)
        3. apply_yaml             -> applies it to the cluster
        """
        if not self.allow_write:
            raise RuntimeError(
                "Write access denied: generate_app_manifest requires --allow-write flag."
            )
        if not os.path.isabs(output_dir):
            raise RuntimeError(f"Path must be absolute: {output_dir}")
        self._validate_dns_label(app_name, "app_name")
        self._validate_dns_label(namespace, "namespace")

        template_values = {
            "__APP_NAME__": app_name,
            "__NAMESPACE__": namespace,
            "__REPLICAS__": str(replicas),
            "__IMAGE_URI__": image_uri,
            "__PORT__": str(port),
            "__CPU__": cpu,
            "__MEMORY__": memory,
            "__LOAD_BALANCER_SCHEME__": load_balancer_scheme,
        }
        combined_yaml = self._load_yaml_template(
            ["deployment.yaml", "service.yaml"], template_values
        )

        os.makedirs(output_dir, exist_ok=True)
        output_file_path = os.path.abspath(os.path.join(output_dir, f"{app_name}-manifest.yaml"))
        with open(output_file_path, "w") as f:
            f.write(combined_yaml)

        logger.info("Generated manifest for %s at %s", app_name, output_file_path)
        return (
            f"Successfully generated manifest for **{app_name}** "
            f"(image `{image_uri}`) and saved to `{output_file_path}`.\n\n"
            f"```yaml\n{combined_yaml}\n```"
        )

    async def list_k8s_resources(
        self,
        cluster_id: str = Field(..., description="VKS Cluster ID"),
        kind: str = Field(
            ...,
            description="Kind of the Kubernetes resources to list (e.g., 'Pod', 'Service', 'Deployment').\n            Use the list_api_versions tool to find available resource kinds.",
        ),
        api_version: str = Field(
            ...,
            description="API version of the Kubernetes resources (e.g., 'v1', 'apps/v1', 'networking.k8s.io/v1').\n            Use the list_api_versions tool to find available API versions.",
        ),
        namespace: Optional[str] = Field(
            None,
            description="Namespace of the Kubernetes resources to list.\n            If not provided, resources will be listed across all namespaces (for namespaced resources).",
        ),
        label_selector: Optional[str] = Field(
            None,
            description="Label selector to filter resources (e.g., 'app=nginx,tier=frontend').\n            Uses the same syntax as kubectl's --selector flag.",
        ),
        field_selector: Optional[str] = Field(
            None,
            description="Field selector to filter resources (e.g., 'metadata.name=my-pod,status.phase=Running').\n            Uses the same syntax as kubectl's --field-selector flag.",
        ),
        region: Region = Field("HCM-3", description="Region override"),
    ) -> KubernetesResourceListData:
        """List Kubernetes resources of a specific kind.

        This tool lists Kubernetes resources of a specified kind in a VKS cluster,
        with options to filter by namespace, labels, and fields. It returns a summary
        of each resource including name, namespace, creation time, and metadata, useful
        for listing pods in a namespace, finding services with specific labels, or
        checking resources in a specific state.

        IMPORTANT: Use this tool instead of 'kubectl get' commands.

        ## Response Information
        Returns a KubernetesResourceListData model with a summary of each resource including
        name, namespace, creation timestamp, labels, and annotations.

        ## Usage Tips
        - Use the list_api_versions tool first to find available API versions
        - For non-namespaced resources (like Nodes), the namespace parameter is ignored
        - Combine label and field selectors for more precise filtering
        - Results are summarized to avoid overwhelming responses
        """
        try:
            k8s_client = await self.get_client(cluster_id, region)
            response = k8s_client.list_resources(
                kind,
                api_version,
                namespace=namespace,
                label_selector=label_selector,
                field_selector=field_selector,
            )

            summaries = []
            for item in response.items:
                item_dict = self.cleanup_resource_response(item.to_dict())
                metadata = item_dict.get("metadata", {})
                creation_timestamp = metadata.get("creationTimestamp", "")
                summary = ResourceSummary(
                    name=metadata.get("name", ""),
                    namespace=metadata.get("namespace", None),
                    creation_timestamp=str(creation_timestamp),
                    labels=metadata.get("labels", None),
                    annotations=metadata.get("annotations", None),
                )
                summaries.append(summary)

            logger.info("Cleaned up resource responses for %s resources", len(summaries))

            resource_location = f"in {namespace}" if namespace else "all namespaces"
            logger.info("Listed %d %s resources %s", len(summaries), kind, resource_location)

            data = KubernetesResourceListData(
                kind=kind,
                api_version=api_version,
                namespace=namespace,
                count=len(summaries),
                items=summaries,
            )
            return data
        except Exception as e:
            error_msg = f"Failed to list {kind} resources: {str(e)}"
            logger.error(error_msg)
            raise RuntimeError(error_msg)

    async def get_pod_logs(
        self,
        cluster_id: str = Field(..., description="VKS Cluster ID"),
        namespace: str = Field(..., description="Kubernetes namespace where the pod is located."),
        pod_name: str = Field(..., description="Name of the pod to retrieve logs from."),
        container_name: Optional[str] = Field(
            None,
            description="Name of the specific container to get logs from. Required only if the pod contains multiple containers.",
        ),
        since_seconds: Optional[int] = Field(
            None,
            ge=0,
            description="Only return logs newer than this many seconds. Useful for getting recent logs without retrieving the entire history.",
        ),
        tail_lines: int = Field(
            100,
            ge=1,
            description="Number of lines to return from the end of the logs. Default: 100. Use higher values for more context.",
        ),
        limit_bytes: int = Field(
            10240,
            ge=1,
            description="Maximum number of bytes to return. Default: 10KB (10240 bytes). Prevents retrieving extremely large log files.",
        ),
        previous: bool = Field(
            False,
            description="Return previous terminated container logs. Default: false. Useful to get logs for pods that are restarting.",
        ),
        region: Region = Field("HCM-3", description="Region override"),
    ) -> PodLogsData:
        """Get logs from a pod in a Kubernetes cluster.

        This tool retrieves logs from a specified pod in a VKS cluster, with options
        to filter by container, time range, and size. It's useful for debugging application
        issues, monitoring behavior, investigating crashes, and verifying startup configuration.

        IMPORTANT: Use this tool instead of 'kubectl logs' commands.

        ## Requirements
        - The server must be run with the `--allow-sensitive-data-access` flag
        - The pod must exist and be accessible in the specified namespace
        - The VKS cluster must exist and be accessible

        ## Response Information
        Returns a PodLogsData model with pod name, namespace, container name (if specified),
        and log lines as an array of strings.
        """
        if not self.allow_sensitive_data_access:
            raise RuntimeError(
                "Access denied: reading pod logs requires --allow-sensitive-data-access flag."
            )

        try:
            k8s_client = await self.get_client(cluster_id, region)
            logs = k8s_client.get_pod_logs(
                pod_name=pod_name,
                namespace=namespace,
                container_name=container_name,
                since_seconds=since_seconds,
                tail_lines=tail_lines,
                limit_bytes=limit_bytes,
                previous=previous,
            )

            log_lines = logs.splitlines(keepends=False)
            if log_lines and log_lines[-1].endswith("\n"):
                log_lines.append("")

            container_info = f" (container: {container_name})" if container_name else ""
            logger.info(
                "Retrieved %d log lines from pod %s/%s%s",
                len(log_lines),
                namespace,
                pod_name,
                container_info,
            )

            data = PodLogsData(
                pod_name=pod_name,
                namespace=namespace,
                container_name=container_name,
                log_lines=log_lines,
            )
            return data
        except Exception as e:
            error_msg = f"Failed to get logs from pod {namespace}/{pod_name}: {str(e)}"
            logger.error(error_msg)
            raise RuntimeError(error_msg)

    async def get_k8s_events(
        self,
        cluster_id: str = Field(..., description="VKS Cluster ID"),
        kind: str = Field(
            ...,
            description='Kind of the involved object (e.g., "Pod", "Deployment", "Service"). Must match the resource kind exactly.',
        ),
        name: str = Field(..., description="Name of the involved object to get events for."),
        namespace: Optional[str] = Field(
            None,
            description="Namespace of the involved object. Required for namespaced resources (like Pods, Deployments).\n            Not required for cluster-scoped resources (like Nodes, PersistentVolumes).",
        ),
        region: Region = Field("HCM-3", description="Region override"),
    ) -> EventsData:
        """Get events related to a specific Kubernetes resource.

        This tool retrieves Kubernetes events related to a specific resource, providing
        detailed information about what has happened to the resource over time. Events
        are useful for troubleshooting pod startup failures, investigating deployment issues,
        understanding resource modifications, and diagnosing scheduling problems.

        IMPORTANT: Use this tool instead of 'kubectl describe' or 'kubectl get events' commands.

        ## Requirements
        - The server must be run with the `--allow-sensitive-data-access` flag
        - The resource must exist and be accessible in the specified namespace

        ## Response Information
        The response includes events with timestamps (first and last), occurrence counts,
        messages, reasons, reporting components, and event types (Normal or Warning).

        ## Usage Tips
        - Warning events often indicate problems that need attention
        - Normal events provide information about expected lifecycle operations
        - The count field shows how many times the same event has occurred
        - Recent events are most relevant for current issues
        """
        if not self.allow_sensitive_data_access:
            raise RuntimeError(
                "Access denied: reading Kubernetes events requires --allow-sensitive-data-access flag."
            )

        try:
            k8s_client = await self.get_client(cluster_id, region)
            events = k8s_client.get_events(kind=kind, name=name, namespace=namespace)

            resource_name = f"{namespace}/{name}" if namespace else name

            cleaned_events = [self.cleanup_resource_response(event) for event in events]

            event_items = [
                EventItem(
                    first_timestamp=event.get("first_timestamp"),
                    last_timestamp=event.get("last_timestamp"),
                    count=event.get("count"),
                    message=event.get("message", ""),
                    reason=event.get("reason", ""),
                    reporting_component=event.get("reporting_component", ""),
                    type=event.get("type", ""),
                )
                for event in cleaned_events
            ]

            logger.info("Retrieved %d events for %s %s", len(event_items), kind, resource_name)

            data = EventsData(
                involved_object_kind=kind,
                involved_object_name=name,
                involved_object_namespace=namespace,
                count=len(event_items),
                events=event_items,
            )
            return data
        except Exception as e:
            error_msg = f"Failed to get events for {kind} {name}: {str(e)}"
            logger.error(error_msg)
            raise RuntimeError(error_msg)

    async def list_api_versions(
        self,
        cluster_id: str = Field(..., description="VKS Cluster ID"),
        region: Region = Field("HCM-3", description="Region override"),
    ) -> ApiVersionsData:
        """List all available API versions in the Kubernetes cluster.

        This tool discovers all available API versions on the Kubernetes cluster,
        which is helpful for determining the correct apiVersion to use when
        managing Kubernetes resources. It returns both core APIs and API groups,
        useful for verifying API compatibility and discovering available resources.

        ## Response Information
        The response includes core APIs (like 'v1'), API groups with versions
        (like 'apps/v1'), extension APIs (like 'networking.k8s.io/v1'), and
        any Custom Resource Definition (CRD) APIs installed in the cluster.

        ## Usage Tips
        - Use this tool before creating or updating resources to ensure API compatibility
        - Different Kubernetes versions may have different available APIs
        - Some APIs may be deprecated or removed in newer Kubernetes versions
        - Custom resources will only appear if their CRDs are installed in the cluster
        """
        try:
            k8s_client = await self.get_client(cluster_id, region)
            api_versions = k8s_client.get_api_versions()

            logger.info("Retrieved %d API versions from cluster %s", len(api_versions), cluster_id)

            data = ApiVersionsData(
                cluster_id=cluster_id,
                api_versions=api_versions,
                count=len(api_versions),
            )
            return data
        except Exception as e:
            error_msg = f"Failed to get API versions from cluster {cluster_id}: {str(e)}"
            logger.error(error_msg)
            raise RuntimeError(error_msg)

    async def manage_k8s_resource(
        self,
        operation: Literal["create", "replace", "patch", "delete", "read"] = Field(
            ...,
            description="Operation to perform on the resource:\n            - create: Create a new resource\n            - replace: Replace an existing resource\n            - patch: Update specific fields of an existing resource\n            - delete: Delete an existing resource\n            - read: Get details of an existing resource\n            Use list_k8s_resources for listing multiple resources.",
        ),
        cluster_id: str = Field(..., description="VKS Cluster ID"),
        kind: str = Field(
            ...,
            description='Kind of the Kubernetes resource (e.g., "Pod", "Service", "Deployment").',
        ),
        api_version: str = Field(
            ...,
            description='API version of the Kubernetes resource (e.g., "v1", "apps/v1", "networking.k8s.io/v1").',
        ),
        name: Optional[str] = Field(
            None,
            description="Name of the Kubernetes resource. Required for all operations except create (where it can be specified in the body).",
        ),
        namespace: Optional[str] = Field(
            None,
            description="Namespace of the Kubernetes resource. Required for namespaced resources.\n            Not required for cluster-scoped resources (like Nodes, PersistentVolumes).",
        ),
        body: Optional[Dict[str, Any]] = Field(
            None,
            description="Resource definition as a dictionary. Required for create, replace, and patch operations.\n            For create and replace, this should be a complete resource definition.\n            For patch, this should contain only the fields to update.",
        ),
        region: Region = Field("HCM-3", description="Region override"),
    ) -> KubernetesResourceData:
        """Manage a single Kubernetes resource with various operations.

        This tool provides complete CRUD (Create, Read, Update, Delete) operations
        for Kubernetes resources in a VKS cluster. It supports all resource types
        and allows for precise control over individual resources, enabling you to create
        custom resources, update specific fields, read detailed information, and delete
        resources that are no longer needed.

        IMPORTANT: Use this tool instead of 'kubectl create', 'kubectl edit', 'kubectl patch',
        'kubectl delete', or 'kubectl get' commands.

        ## Requirements
        - The server must be run with the `--allow-write` flag for mutating operations
        - The server must be run with the `--allow-sensitive-data-access` flag for Secret resources
        - The VKS cluster must exist and be accessible

        ## Operations
        - **create**: Create a new resource with the provided definition
        - **replace**: Replace an existing resource with a new definition
        - **patch**: Update specific fields of an existing resource
        - **delete**: Remove an existing resource
        - **read**: Get details of an existing resource

        ## Usage Tips
        - Use list_api_versions to find available API versions
        - For namespaced resources, always provide the namespace
        - When creating resources, ensure the name in the body matches the name parameter
        - For patch operations, only include the fields you want to update
        """
        # Validate operation
        try:
            operation_enum = Operation(operation)
        except ValueError:
            valid_ops = ", ".join(op.value for op in Operation)
            raise ValueError(f"Invalid operation: {operation}. Valid operations are: {valid_ops}")

        # Check write permissions
        if operation_enum in (
            Operation.CREATE,
            Operation.REPLACE,
            Operation.PATCH,
            Operation.DELETE,
        ):
            if not self.allow_write:
                raise RuntimeError(
                    f"Write access denied: {operation} operation requires --allow-write flag."
                )

        # Check sensitive data access for Secrets
        if kind.lower() == "secret" and operation_enum == Operation.READ:
            if not self.allow_sensitive_data_access:
                raise RuntimeError(
                    "Access denied: reading Kubernetes Secrets requires --allow-sensitive-data-access flag."
                )

        try:
            k8s_client = await self.get_client(cluster_id, region)
            response = k8s_client.manage_resource(
                operation_enum,
                kind,
                api_version,
                name=name,
                namespace=namespace,
                body=body,
            )

            resource_name = f"{namespace}/{name}" if namespace else (name or "")
            operation_past_tense = {
                Operation.CREATE: "created",
                Operation.REPLACE: "replaced",
                Operation.PATCH: "patched",
                Operation.DELETE: "deleted",
                Operation.READ: "retrieved",
            }.get(operation_enum, operation)

            logger.info("%s %s %s", operation_past_tense.capitalize(), kind, resource_name)

            resource_data = None
            if operation_enum == Operation.READ:
                resource_data = self.cleanup_resource_response(response.to_dict())
                logger.info("Cleaned up resource response for %s %s", kind, resource_name)

            data = KubernetesResourceData(
                kind=kind,
                name=name or "",
                namespace=namespace,
                api_version=api_version,
                operation=operation_past_tense,
                resource=resource_data,
            )
            return data
        except Exception as e:
            error_msg = f"Failed to {operation} {kind} {name or ''}: {str(e)}"
            logger.error(error_msg)
            raise RuntimeError(error_msg)

    async def apply_yaml(
        self,
        yaml_path: str = Field(
            ...,
            description="Absolute path to the YAML file to apply.\n            IMPORTANT: Must be an absolute path (e.g., '/home/user/manifests/app.yaml') as the MCP client and server might not run from the same location.",
        ),
        cluster_id: str = Field(..., description="VKS Cluster ID"),
        namespace: str = Field(
            ...,
            description="Kubernetes namespace to apply resources to. Will be used for namespaced resources that do not specify a namespace.",
        ),
        force: bool = Field(
            True,
            description="Whether to update resources if they already exist (similar to kubectl apply). Set to false to only create new resources.",
        ),
        region: Region = Field("HCM-3", description="Region override"),
    ) -> ApplyYamlData:
        """Apply a Kubernetes YAML from a local file.

        This tool applies Kubernetes resources defined in a YAML file to a VKS cluster,
        similar to the `kubectl apply` command. It supports multi-document YAML files
        and can create or update resources, useful for deploying applications, creating
        Kubernetes resources, and applying complete application stacks.

        IMPORTANT: Use this tool instead of 'kubectl apply -f' commands.

        ## Requirements
        - The server must be run with the `--allow-write` flag
        - The YAML file must exist and be accessible to the server
        - The path must be absolute (e.g., '/home/user/manifests/app.yaml')
        - The VKS cluster must exist and be accessible

        ## Response Information
        The response includes the number of resources created, number of resources
        updated (when force=True), and whether force was applied.
        """
        if not self.allow_write:
            raise RuntimeError("Write access denied: apply_yaml requires --allow-write flag.")

        if not os.path.isabs(yaml_path):
            raise RuntimeError(f"Path must be absolute: {yaml_path}")

        try:
            k8s_client = await self.get_client(cluster_id, region)

            logger.info("Reading YAML content from file: %s", yaml_path)
            try:
                with open(yaml_path, "r") as yaml_file:
                    yaml_content = yaml_file.read()
            except FileNotFoundError:
                raise RuntimeError(f"YAML file not found: {yaml_path}")
            except IOError as e:
                raise RuntimeError(f"Error reading YAML file {yaml_path}: {str(e)}")

            yaml_objects = list(yaml.safe_load_all(yaml_content))
            yaml_objects = [doc for doc in yaml_objects if doc is not None]
            logger.info("Found %d resources in the manifest", len(yaml_objects))

            results, created_count, updated_count = k8s_client.apply_from_yaml(
                yaml_objects=yaml_objects,
                namespace=namespace,
                force=force,
            )

            success_msg = (
                f"Successfully applied all resources from YAML file {yaml_path}"
                f" ({created_count} created, {updated_count} updated)"
            )
            logger.info(success_msg)

            data = ApplyYamlData(
                force_applied=force,
                resources_created=created_count,
                resources_updated=updated_count,
            )
            return data
        except Exception as e:
            error_msg = f"Error applying YAML from file: {str(e)}"
            logger.error(error_msg)
            raise RuntimeError(error_msg)
