#!/usr/bin/env bash
# 手动运行全部单元测试（传输层插件化重构后）。
# 用法: ./test.sh  （可用 TEST_ARGS="--verbosity 2" 等追加参数）
set -uo pipefail
cd "$(dirname "$0")"

PY=${PYTHON:-python3}
FAILED=()

run() {
    echo
    echo "=== $PY manage.py test $* ${TEST_ARGS:-} ==="
    if ! $PY manage.py test "$@" ${TEST_ARGS:-}; then
        FAILED+=("$*")
    fi
}

# 本次重构的核心：插件注册表 / Channel 模型 / 动态配置解析
run hc.api.tests.test_plugins
run hc.api.tests.test_channel_model
run hc.api.tests.test_notify
run hc.api.tests.test_sendalerts

# api 与 front 全量
run hc.api.tests
run hc.front.tests

# 账户
run hc.accounts.tests

# 所有内置集成（hc/integrations/*/tests）
run hc.integrations

echo
if [ ${#FAILED[@]} -eq 0 ]; then
    echo "ALL TEST SUITES PASSED"
else
    echo "FAILED SUITES:"
    printf '  - %s\n' "${FAILED[@]}"
    exit 1
fi
