#!/usr/bin/env bash
# pm-resolve / web-install 两个块在 reusable-{test,image,release}.yml 里物理存在 4 份
# （release 有 static 与 glibc 两个 job）。本脚本把它们钉成一份：任何一处偏离即失败。
#
# 为什么不抽成 composite action：那样要跟 v1 移动 tag 一起发布，且 Dependabot 不会
# bump reusable workflow 的 ref，升级窗口更长。先上闸门，等真有第三类调用点再抽。
set -euo pipefail
cd "$(dirname "$0")/.."

FILES=(.github/workflows/reusable-test.yml
       .github/workflows/reusable-image.yml
       .github/workflows/reusable-release.yml)

fail() { echo "::error::$*"; exit 1; }

check_block() {
  local marker=$1 expected=$2
  local out=$(mktemp -d) trap_ret=1
  trap "rm -rf '$out'" RETURN

  # 块内容（不含标记行）按出现顺序写入 block-NN
  OUT="$out" awk -v m="$marker" '
    $0 ~ "# >>> " m { inf = 1; next }
    $0 ~ "# <<< " m { inf = 0; n++; next }
    inf { print > (ENVIRON["OUT"] "/block-" sprintf("%02d", n + 1) ".txt") }
  ' "${FILES[@]}"

  local count=$(find "$out" -name 'block-*.txt' | grep -c '' || true)
  [ "$count" -eq "$expected" ] || fail "$marker: 找到 $count 处，期望 $expected 处"

  local hashes=$(for f in $(find "$out" -name 'block-*.txt' | sort); do
                   shasum -a 256 "$f" | cut -d' ' -f1
                 done | sort -u | grep -c '')
  if [ "$hashes" -ne 1 ]; then
    echo "--- $marker 各处摘要（应全部相同）---" >&2
    for f in $(find "$out" -name 'block-*.txt' | sort); do
      echo "$(shasum -a 256 "$f" | cut -c1-12)  $(basename "$f")" >&2
    done
    fail "$marker: $count 处实现有 $hashes 个版本，必须逐字节一致"
  fi
  echo "ok: $marker × $count 处逐字节一致"
}

check_block pm-resolve 4
check_block web-install 4

# 已废除的入参不得再出现在被调 workflow 的契约里：调用方多传未声明的 input
# 会让整个 job 直接 startup_failure（没有 job 日志，只能看到 conclusion）。
for dep in package-manager web-install-flags node-version; do
  grep -n "^      $dep:" "${FILES[@]}" > /dev/null 2>&1 &&
    fail "reusable workflow 仍声明已废除的入参 $dep"
done
echo "ok: 已废除的入参未残留"

# 同理检查调用方传参：本仓库不含调用方，由各仓库 CI 保证；这里只守被调侧契约。
echo "all checks passed"
