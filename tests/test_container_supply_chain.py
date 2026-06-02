"""Static guardrails for the container bootstrap supply chain."""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CONTAINERFILE = REPO_ROOT / "container-bootstrap" / "Containerfile"


def _containerfile() -> str:
    return CONTAINERFILE.read_text(encoding="utf-8")


def _section(content: str, start: str, end: str) -> str:
    if start not in content or end not in content:
        raise ValueError(f"Section markers '{start}' or '{end}' not found in content")
    return content.split(start, 1)[1].split(end, 1)[0]


def test_container_base_images_are_digest_pinned():
    """Every image source must include an immutable digest."""
    from_lines = [line.strip() for line in _containerfile().splitlines() if line.startswith("FROM ")]

    assert from_lines, "Containerfile must declare at least one base image"
    for line in from_lines:
        parts = line.split()
        image_parts = [part for part in parts[1:] if not part.startswith("--")]
        assert image_parts, f"Could not find image name in FROM line: {line}"
        image = image_parts[0]
        assert "@sha256:" in image, f"Base image is not digest-pinned: {line}"
        assert ":latest" not in image, f"Base image still uses a mutable latest tag: {line}"


def test_openshift_client_default_tracks_supported_stream():
    """The bundled OpenShift client stream should stay on the current tested default."""
    assert "ARG OC_VERSION=4.21" in _containerfile()


def test_binary_checksum_args_cover_supported_architectures():
    """Downloaded tool checksums must be explicit for amd64 and arm64."""
    content = _containerfile()

    expected_args = {
        "ARG JQ_LINUX_AMD64_SHA256=",
        "ARG JQ_LINUX_ARM64_SHA256=",
        "ARG OPENSHIFT_CLIENT_LINUX_AMD64_SHA256=",
        "ARG OPENSHIFT_CLIENT_LINUX_ARM64_SHA256=",
    }

    for arg in expected_args:
        assert arg in content, f"Missing checksum build argument: {arg}"


def test_containerfile_does_not_pipe_curl_to_tar():
    """Archives must be verified before extraction."""
    content = _containerfile()

    assert not re.search(r"curl\b[^|]*\|\s*\\?\s*tar\b", content)


def test_jq_download_is_verified_before_install():
    """jq must be downloaded to /tmp and verified before becoming executable."""
    jq_section = _section(_containerfile(), "# Install jq for JSON processing", "# Install OpenShift CLI")

    assert "/tmp/jq-linux-${JQ_ARCH}" in jq_section
    assert "/tmp/jq-linux-${JQ_ARCH}.sha256" in jq_section
    assert re.search(r"sha256sum -c \"?/tmp/jq-linux-\$\{JQ_ARCH\}\.sha256\"?", jq_section)
    assert jq_section.index("sha256sum -c") < jq_section.index("/usr/local/bin/jq")


def test_openshift_client_archive_is_verified_before_extraction():
    """oc and kubectl must be extracted only after archive checksum verification."""
    oc_section = _section(_containerfile(), "# Install OpenShift CLI", "# Copy Python packages from builder")

    assert "/tmp/openshift-client-linux.tar.gz" in oc_section
    assert "/tmp/openshift-client-linux.tar.gz.sha256" in oc_section
    assert "sha256sum -c /tmp/openshift-client-linux.tar.gz.sha256" in oc_section
    assert oc_section.index("sha256sum -c") < oc_section.index("tar -xzf")


def test_unsupported_architectures_fail_fast():
    """Unknown architectures must not fall back to unverified artifact names."""
    content = _containerfile()

    assert "Unsupported architecture" in content
    assert content.count('echo "Unsupported architecture: ${ARCH}"') == 2
    assert content.count("exit 1;") == 2
