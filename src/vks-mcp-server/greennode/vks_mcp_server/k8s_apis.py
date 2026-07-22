"""Kubernetes API client for the GreenNode MCP Server."""

from __future__ import annotations

import base64
import logging
import os
import tempfile
from greennode.vks_mcp_server.models import Operation
from typing import Any, Dict, List, Optional


logger = logging.getLogger(__name__)


class K8sApis:
    """Class for managing Kubernetes API client.

    This class provides a simplified interface for interacting with the Kubernetes API
    using the official Kubernetes Python client.
    """

    def __init__(self, endpoint, token, ca_data=None):
        """Initialize Kubernetes API client.

        Args:
            endpoint: Kubernetes API endpoint
            token: Authentication token
            ca_data: CA certificate data (base64 encoded) - required for SSL verification
        """
        from kubernetes import client, dynamic

        configuration = client.Configuration()
        configuration.host = endpoint
        configuration.api_key = {"authorization": f"Bearer {token}"}
        self._ca_cert_file_path = None
        configuration.verify_ssl = True

        if ca_data:
            ca_cert_file = tempfile.NamedTemporaryFile(delete=False)
            ca_cert_data = base64.b64decode(ca_data)
            ca_cert_file.write(ca_cert_data)
            self._ca_cert_file_path = ca_cert_file.name
            setattr(configuration, "ssl_ca_cert", ca_cert_file.name)
        else:
            configuration.verify_ssl = False

        try:
            from greennode.vks_mcp_server.useragent import USER_AGENT

            self.api_client = client.ApiClient(configuration)
            self.api_client.user_agent = USER_AGENT
            self.dynamic_client = dynamic.DynamicClient(self.api_client)
        except ImportError:
            if hasattr(self, "_ca_cert_file_path") and self._ca_cert_file_path:
                if os.path.exists(self._ca_cert_file_path):
                    os.unlink(self._ca_cert_file_path)
            logger.error("kubernetes package not installed")
            raise

    @classmethod
    def from_api_client(cls, api_client):
        """Create a K8sApis instance from a pre-configured kubernetes ApiClient.

        This is used for kubeconfig-based authentication where the kubernetes
        library handles all authentication (OIDC, exec plugins, certificates, etc.).

        Args:
            api_client: A pre-configured kubernetes.client.ApiClient instance.

        Returns:
            K8sApis instance with the provided ApiClient.
        """
        from greennode.vks_mcp_server.useragent import USER_AGENT
        from kubernetes import dynamic

        instance = cls.__new__(cls)
        instance._ca_cert_file_path = None
        instance.api_client = api_client
        instance.api_client.user_agent = USER_AGENT
        instance.dynamic_client = dynamic.DynamicClient(api_client)
        return instance

    def _patch_resource(
        self,
        resource,
        body: Optional[Dict[str, Any]] = None,
        name: Optional[str] = None,
        namespace: Optional[str] = None,
    ) -> Any:
        """Patch a resource with strategic merge patch, falling back to merge patch if needed.

        Args:
            resource: The dynamic resource object
            body: The resource body to patch with
            name: Name of the resource
            namespace: Namespace of the resource (if namespaced)
            **kwargs: Additional arguments for the API call

        Returns:
            The API response
        """
        try:
            return resource.patch(
                body=body,
                name=name,
                namespace=namespace,
                content_type="application/strategic-merge-patch+json",
            )
        except Exception as e:
            if "415" in str(e) or "Unsupported Media Type" in str(e):
                logger.warning(
                    f"Strategic merge patch not supported for {resource.kind}"
                    ", falling back to merge patch"
                )
                return resource.patch(
                    body=body,
                    name=name,
                    namespace=namespace,
                    content_type="application/merge-patch+json",
                )
            raise

    def manage_resource(
        self,
        operation: Operation,
        kind: str,
        api_version: str,
        name: Optional[str] = None,
        namespace: Optional[str] = None,
        body: Optional[dict] = None,
    ) -> Any:
        """Manage a single Kubernetes resource with the specified operation using dynamic client.

        Args:
            operation: Operation to perform (Operation.CREATE, Operation.REPLACE, etc.)
            kind: Resource kind (e.g., 'Pod', 'Service')
            api_version: API version (e.g., 'v1', 'apps/v1')
            name: Resource name (required for replace, patch, delete, read)
            namespace: Namespace of the resource (optional)
            body: Resource body (required for create, replace, patch)
            **kwargs: Additional arguments for the API call

        Returns:
            The API response
        """
        if operation in (Operation.REPLACE, Operation.PATCH, Operation.DELETE, Operation.READ):
            if not name:
                raise ValueError(f"Resource name is required for {operation.value} operation")
        if operation in (Operation.CREATE, Operation.REPLACE, Operation.PATCH):
            if not body:
                raise ValueError(f"Resource body is required for {operation.value} operation")

        try:
            resource = self.dynamic_client.resources.get(api_version=api_version, kind=kind)

            if body:
                if "kind" not in body:
                    body["kind"] = kind
                if "apiVersion" not in body:
                    body["apiVersion"] = api_version
                if "metadata" not in body:
                    body["metadata"] = {}
                if name:
                    body["metadata"]["name"] = name
                if namespace:
                    body["metadata"]["namespace"] = namespace

            if operation == Operation.CREATE:
                return resource.create(body=body, namespace=namespace)
            elif operation == Operation.REPLACE:
                return resource.replace(body=body, name=name, namespace=namespace)
            elif operation == Operation.PATCH:
                return self._patch_resource(resource, body=body, name=name, namespace=namespace)
            elif operation == Operation.DELETE:
                return resource.delete(name=name, namespace=namespace)
            elif operation == Operation.READ:
                return resource.get(name=name, namespace=namespace)
            else:
                raise ValueError(f"Unsupported operation: {operation.value}")
        except Exception as e:
            raise ValueError(f"Error managing {kind} resource: {str(e)}")

    def list_resources(
        self,
        kind: str,
        api_version: str,
        namespace: Optional[str] = None,
        label_selector: Optional[str] = None,
        field_selector: Optional[str] = None,
    ) -> Any:
        """List Kubernetes resources of a specific kind using dynamic client.

        Args:
            kind: Resource kind (e.g., 'Pod', 'Service')
            api_version: API version (e.g., 'v1', 'apps/v1')
            namespace: Namespace to list resources from (optional)
            label_selector: Label selector to filter resources (optional)
            field_selector: Field selector to filter resources (optional)
            **kwargs: Additional arguments for the API call

        Returns:
            The API response containing the list of resources
        """
        try:
            resource = self.dynamic_client.resources.get(api_version=api_version, kind=kind)
            list_kwargs = {}
            if label_selector:
                list_kwargs["label_selector"] = label_selector
            if field_selector:
                list_kwargs["field_selector"] = field_selector
            if namespace:
                list_kwargs["namespace"] = namespace
            list_kwargs.update({})
            return resource.get(**list_kwargs)
        except Exception as e:
            raise ValueError(f"Error listing {kind} resources: {str(e)}")

    def apply_from_yaml(
        self,
        yaml_objects: list,
        namespace: str = "default",
        force: bool = True,
    ) -> tuple:
        """Apply YAML objects to the cluster with support for custom resources and updates.

        This method improves upon the standard create_from_yaml by:
        1. Supporting custom resources through the dynamic client
        2. Supporting updates to existing resources when force=True

        Args:
            yaml_objects: List of YAML objects to apply
            namespace: Default namespace to use for namespaced resources
            force: Whether to update resources if they already exist (like kubectl apply)
            **kwargs: Additional arguments for the API calls

        Returns:
            Tuple of (results, created_count, updated_count)
        """
        results = []
        created_count = 0
        updated_count = 0

        for obj in yaml_objects:
            kind = obj.get("kind")
            api_version = obj.get("apiVersion")
            metadata = obj.get("metadata", {})
            name = metadata.get("name")
            obj_namespace = metadata.get("namespace")

            if not kind or not api_version or not name:
                raise ValueError("Invalid resource: missing kind, apiVersion, or name")

            resource = self.dynamic_client.resources.get(api_version=api_version, kind=kind)

            if not obj_namespace and resource.namespaced:
                obj_namespace = namespace

            # Check if resource exists
            exists = False
            try:
                resource.get(name=name, namespace=obj_namespace)
                exists = True
            except Exception:
                pass

            try:
                if exists and force:
                    result = self._patch_resource(
                        resource, body=obj, name=name, namespace=obj_namespace
                    )
                    updated_count += 1
                else:
                    result = resource.create(body=obj, namespace=obj_namespace)
                    created_count += 1
                results.append(result)
            except Exception as e:
                resource_name = f"{kind}/{name}"
                raise ValueError(f"Error applying {resource_name} {name}: {str(e)}")

        return results, created_count, updated_count

    def get_events(
        self,
        kind: str,
        name: str,
        namespace: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Get events related to a specific Kubernetes resource.

        Args:
            kind: Kind of the involved object (e.g., 'Pod', 'Deployment')
            name: Name of the involved object
            namespace: Namespace of the involved object (optional for non-namespaced resources)

        Returns:
            List of events related to the specified object
        """
        try:
            event_resource = self.dynamic_client.resources.get(api_version="v1", kind="Event")
            field_selector = f"involvedObject.kind={kind},involvedObject.name={name}"

            if namespace:
                events_response = event_resource.get(
                    namespace=namespace, field_selector=field_selector
                )
            else:
                events_response = event_resource.get(field_selector=field_selector)

            result = []
            for event in events_response.items:
                event_dict = event.to_dict()
                first_timestamp = event_dict.get("firstTimestamp")
                last_timestamp = event_dict.get("lastTimestamp")
                source = event_dict.get("source", None)

                result.append(
                    {
                        "first_timestamp": first_timestamp,
                        "last_timestamp": last_timestamp,
                        "count": event_dict.get("count"),
                        "message": event_dict.get("message", ""),
                        "reason": event_dict.get("reason", ""),
                        "reporting_component": source.get("component", "") if source else "",
                        "type": event_dict.get("type", ""),
                    }
                )

            return result
        except Exception as e:
            resource_name = f"{kind}/{name}"
            raise ValueError(f"Error getting events for {resource_name} {name}: {str(e)}")

    def get_pod_logs(
        self,
        pod_name: str,
        namespace: str,
        container_name: Optional[str] = None,
        since_seconds: Optional[int] = None,
        tail_lines: Optional[int] = None,
        limit_bytes: Optional[int] = None,
        previous: Optional[bool] = None,
    ) -> str:
        """Get logs from a pod.

        Args:
            pod_name: Name of the pod
            namespace: Namespace of the pod
            container_name: Container name (optional, if pod contains more than one container)
            since_seconds: Only return logs newer than this many seconds (optional)
            tail_lines: Number of lines to return from the end of the logs (optional)
            limit_bytes: Maximum number of bytes to return (optional)
            previous: Return previous terminated container logs (optional)

        Returns:
            Pod logs as a string
        """
        try:
            from kubernetes import client as k8s_client

            core_v1_api = k8s_client.CoreV1Api(self.api_client)
            params = {}
            if container_name:
                params["container"] = container_name
            if since_seconds:
                params["since_seconds"] = since_seconds
            if tail_lines:
                params["tail_lines"] = tail_lines
            if limit_bytes:
                params["limit_bytes"] = limit_bytes
            if previous:
                params["previous"] = previous

            logs_response = core_v1_api.read_namespaced_pod_log(
                name=pod_name, namespace=namespace, **params
            )
            return logs_response
        except Exception as e:
            raise ValueError(f"Error getting logs from pod {namespace}/{pod_name}: {str(e)}")

    def get_api_versions(self) -> List[str]:
        """Get preferred API versions from the Kubernetes cluster.

        Returns only the preferred (stable) API version for each group, avoiding alpha/beta versions
        when stable versions are available.

        Returns:
            List of preferred API versions (e.g., ['v1', 'apps/v1', 'networking.k8s.io/v1'])
        """
        from kubernetes import client as k8s_client

        api_versions = set()

        # Get core API versions
        try:
            core_api = k8s_client.CoreApi(self.api_client)
            core_version_obj = core_api.get_api_versions()
            versions = getattr(core_version_obj, "versions", None)
            if isinstance(versions, list):
                for version in versions:
                    api_versions.add(str(version))
        except Exception as e:
            logger.warning(f"Error getting core API versions: {e}")

        # Get API group versions
        try:
            apis_api = k8s_client.ApisApi(self.api_client)
            api_groups_obj = apis_api.get_api_versions()
            groups = getattr(api_groups_obj, "groups", None)
            if isinstance(groups, list):
                for group in groups:
                    preferred_version = getattr(group, "preferred_version", None)
                    if preferred_version:
                        group_version = getattr(preferred_version, "group_version", None)
                        if group_version:
                            api_versions.add(str(group_version))
        except Exception as e:
            logger.warning(f"Error getting API groups: {e}")

        return sorted(api_versions)

    def __del__(self):
        """Clean up temporary files when the object is garbage collected."""
        if hasattr(self, "_ca_cert_file_path") and self._ca_cert_file_path:
            try:
                if os.path.exists(self._ca_cert_file_path):
                    os.unlink(self._ca_cert_file_path)
            except Exception:
                pass
