"""Namespace and resource validation checks."""

import logging
import shutil
from typing import Sequence, Tuple

from lib.constants import (
    ACM_NAMESPACE,
    BACKUP_NAMESPACE,
    OBSERVABILITY_NAMESPACE,
    THANOS_OBJECT_STORAGE_SECRET,
)
from lib.kube_client import KubeClient
from lib.validation import InputValidator, ValidationError

from .base_validator import BaseValidator

logger = logging.getLogger("acm_switchover")


class NamespaceValidator(BaseValidator):
    """Ensures required namespaces are present on both hubs."""

    REQUIRED_NAMESPACES: Sequence[str] = (
        ACM_NAMESPACE,
        BACKUP_NAMESPACE,
    )

    def run(self, primary: KubeClient, secondary: KubeClient) -> None:
        """Run validation with both clients.

        Args:
            primary: Primary hub KubeClient instance
            secondary: Secondary hub KubeClient instance
        """
        for namespace in self.REQUIRED_NAMESPACES:
            self._check_namespace(primary, namespace, "primary")
            self._check_namespace(secondary, namespace, "secondary")

    def _check_namespace(
        self,
        kube_client: KubeClient,
        namespace: str,
        hub_label: str,
    ) -> None:
        """Check if namespace exists on a hub.

        Args:
            kube_client: KubeClient instance
            namespace: Namespace name to check
            hub_label: Label for the hub (primary/secondary)
        """
        try:
            # Validate namespace name before checking existence
            InputValidator.validate_kubernetes_namespace(namespace)

            if kube_client.namespace_exists(namespace):
                self.add_result(
                    f"Namespace {namespace} ({hub_label})",
                    True,
                    "exists",
                    critical=True,
                )
            else:
                self.add_result(
                    f"Namespace {namespace} ({hub_label})",
                    False,
                    "not found",
                    critical=True,
                )
        except ValidationError as e:
            self.add_result(
                f"Namespace {namespace} ({hub_label})",
                False,
                f"invalid namespace name: {str(e)}",
                critical=True,
            )


class ObservabilityDetector(BaseValidator):
    """Detects whether ACM Observability is deployed on each hub."""

    def detect(self, primary: KubeClient, secondary: KubeClient) -> Tuple[bool, bool]:
        """Detect observability on both hubs.

        Args:
            primary: Primary hub KubeClient instance
            secondary: Secondary hub KubeClient instance

        Returns:
            Tuple of (primary_has_observability, secondary_has_observability)
        """
        try:
            # Validate namespace before checking existence
            InputValidator.validate_kubernetes_namespace(OBSERVABILITY_NAMESPACE)
        except ValidationError:
            # If observability namespace is invalid, it doesn't exist
            logger.debug("Observability namespace validation failed: %s", OBSERVABILITY_NAMESPACE)
            return False, False

        primary_has = primary.namespace_exists(OBSERVABILITY_NAMESPACE)
        secondary_has = secondary.namespace_exists(OBSERVABILITY_NAMESPACE)

        if primary_has and secondary_has:
            self.add_result(
                "ACM Observability",
                True,
                "detected on both hubs",
                critical=False,
            )
        elif primary_has:
            self.add_result(
                "ACM Observability",
                True,
                "detected on primary hub only",
                critical=False,
            )
        elif secondary_has:
            self.add_result(
                "ACM Observability",
                True,
                "detected on secondary hub only",
                critical=False,
            )
        else:
            self.add_result(
                "ACM Observability",
                True,
                "not detected (optional component)",
                critical=False,
            )

        return primary_has, secondary_has


class ObservabilityPrereqValidator(BaseValidator):
    """Checks additional Observability requirements on the secondary hub."""

    def run(self, secondary: KubeClient) -> None:
        """Run validation with secondary client.

        Args:
            secondary: Secondary hub KubeClient instance
        """
        try:
            # Validate namespace and secret name before checking existence
            InputValidator.validate_kubernetes_namespace(OBSERVABILITY_NAMESPACE)
            InputValidator.validate_kubernetes_name(THANOS_OBJECT_STORAGE_SECRET, "secret")
        except ValidationError as e:
            logger.debug("Observability validation failed: %s", e)
            return

        if not secondary.namespace_exists(OBSERVABILITY_NAMESPACE):
            return

        if secondary.secret_exists(OBSERVABILITY_NAMESPACE, THANOS_OBJECT_STORAGE_SECRET):
            self.add_result(
                "Observability object storage secret",
                True,
                f"{THANOS_OBJECT_STORAGE_SECRET} present on secondary hub",
                critical=True,
            )
        else:
            self.add_result(
                "Observability object storage secret",
                False,
                f"{THANOS_OBJECT_STORAGE_SECRET} missing on secondary hub",
                critical=True,
            )


class ToolingValidator(BaseValidator):
    """Validates required command-line tools exist for operator workflows."""

    def run(self) -> None:
        """Check for required CLI tools."""
        oc_path = shutil.which("oc")
        kubectl_path = shutil.which("kubectl")

        if oc_path or kubectl_path:
            binary = "oc" if oc_path else "kubectl"
            self.add_result(
                "Cluster CLI",
                True,
                f"{binary} found in PATH",
                critical=True,
            )
        else:
            self.add_result(
                "Cluster CLI",
                False,
                "Neither oc nor kubectl found in PATH",
                critical=True,
            )

        jq_path = shutil.which("jq")
        if jq_path:
            self.add_result(
                "jq availability",
                True,
                "jq found",
                critical=False,
            )
        else:
            self.add_result(
                "jq availability",
                False,
                "jq not found (optional but recommended)",
                critical=False,
            )
