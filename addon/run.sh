#!/usr/bin/with-contenv bashio
export LG_CLOUD_ENABLED="$(bashio::config 'cloud_enabled')"
export LG_DRY_RUN="$(bashio::config 'dry_run')"
export LG_DEMO_MODE="$(bashio::config 'demo_mode')"
export LG_POLL_ACTIVE_S="$(bashio::config 'poll_active_s')"
export LG_POLL_IDLE_MIN="$(bashio::config 'poll_idle_min')"
export LG_NOTIFY="$(bashio::config 'notify')"
export LG_APPROVAL_TIMEOUT_S="$(bashio::config 'approval_timeout_s')"
export LG_LOG_LEVEL="$(bashio::config 'log_level')"
export LG_HOSTNAME="$(bashio::addon.hostname)"
export LG_STATE_DIR="/config"          # app config folder: certificate, accounts, settings, log
# Bundle the integration (no HACS): only here, as root, only into its own folder in Home Assistant
bash /install_integration.sh || bashio::log.warning "Integration not installed"
bashio::log.info "Starting Leapmotor Gateway (cloud_enabled=${LG_CLOUD_ENABLED}, dry_run=${LG_DRY_RUN})"
# The folder belongs to the service user. The certificate key stays readable only for it.
chown -R gateway:gateway /config
chmod 0700 /config
[ -f /config/app_key.pem ] && chmod 0600 /config/app_key.pem
exec s6-setuidgid gateway python3 -m uvicorn gateway:app --host 0.0.0.0 --port 8789 --log-level "${LG_LOG_LEVEL:-info}"
