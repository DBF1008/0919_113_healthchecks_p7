#!/usr/bin/env bash
# Manual test script for the transport plugin architecture refactor.
#
# Prerequisites:
#   python -m pip install -r requirements.txt -r requirements-dev.txt
#
# Usage:
#   ./test.sh            # run all test groups below
#   ./test.sh plugins    # run only the new plugin architecture tests

set -euo pipefail
cd "$(dirname "$0")"

export SECRET_KEY="${SECRET_KEY:-dummy-key}"

GROUP="${1:-all}"

run() {
    echo
    echo "=== $* ==="
    python manage.py test "$@" -v 1
}

case "$GROUP" in
plugins)
    # New: plugin registry, entry point discovery, plugin interface,
    # dynamic config (conf_<kind> / config_json), notify dispatch
    run hc.api.tests.test_plugins
    # New: plugin-based add/edit channel views
    run hc.front.tests.test_plugin_views
    ;;
all)
    # 1. New plugin architecture unit tests
    run hc.api.tests.test_plugins
    run hc.front.tests.test_plugin_views

    # 2. Channel model regressions (TRANSPORTS, notify, config_json)
    run hc.api.tests.test_channel_model
    run hc.api.tests.test_sendalerts
    run hc.api.tests.test_notify
    run hc.api.tests.test_notification_status

    # 3. Front-end channel views regressions
    run hc.front.tests.test_channels
    run hc.front.tests.test_channel_checks

    # 4. Shell integration (now a native TransportPlugin)
    run hc.integrations.shell

    # 5. Full api + front suites
    run hc.api
    run hc.front
    ;;
*)
    echo "Unknown test group: $GROUP (expected: all | plugins)" >&2
    exit 1
    ;;
esac

echo
echo "Done."
