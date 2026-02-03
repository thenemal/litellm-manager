"""Configuration constants for the LiteLLM management tool."""

# LiteLLM installation
LITELLM_DIR = "/opt/litellm"
LITELLM_ENV = "/opt/litellm/.env"
LITELLM_YAML = "/opt/litellm/litellm.yaml"
LITELLM_SERVICE = "litellm"
LITELLM_URL = "http://localhost:4000"
HEALTH_CHECK_URL = "http://localhost:4000/health"

# LXC config (readable from inside the container)
LXC_CONFIG_PATHS = [
    "/etc/hostname",
    "/etc/network/interfaces",
    "/etc/resolv.conf",
]

# Files to snapshot
SNAPSHOT_SOURCES = {
    "litellm-env": LITELLM_ENV,
    "litellm-yaml": LITELLM_YAML,
    "litellm-service": "/etc/systemd/system/litellm.service",
    "hostname": "/etc/hostname",
    "network": "/etc/network/interfaces",
    "resolv": "/etc/resolv.conf",
    "crontab": "/var/spool/cron/crontabs/root",
}

# Health check
HEALTH_CHECK_TIMEOUT = 10  # seconds

# Valid maintenance categories
CATEGORIES = [
    "general",
    "update",
    "config-change",
    "backup",
    "incident",
    "security",
    "performance",
]
