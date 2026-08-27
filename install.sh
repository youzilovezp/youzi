#!/usr/bin/env bash
# ============================================================================
# youzi · 竞品颠覆性分析 skill —— 一键安装 / 卸载 / 更新 / 状态查询
# 支持 4 个 AI 平台：Claude Code / opencode / Codex / EasyCode（自动探测）
#
# 用法：
#   ./install.sh install                        # 安装到全部已装的 AI 工具
#   ./install.sh install --platform claude      # 只装 Claude Code
#   ./install.sh install --platform opencode    # 只装 opencode
#   ./install.sh install --platform codex       # 只装 Codex
#   ./install.sh install --platform easycode    # 只装 EasyCode
#   ./install.sh uninstall / update / status    # 同理，作用于全部已装平台
#
# 安装位置（AgentSkills 标准，4 平台同一套 SKILL.md）：
#   Claude Code: ~/.claude/skills/youzi/
#   opencode:    ~/.config/opencode/skills/youzi/
#                + ~/.config/opencode/command/youzi.md（/ 补全入口）
#   Codex:       ~/.codex/skills/youzi/
#   EasyCode:    ~/.easycode/skills/youzi/
#
# 兼容：macOS（BSD tools）+ Linux（GNU tools）
# 输出：纯文本 + emoji，不使用 ANSI 转义
# ============================================================================

set -eo pipefail

# ---------- 纯文本样式 ----------
info()  { printf "  ℹ  %s\n" "$*"; }
ok()    { printf "  ✅  %s\n" "$*"; }
warn()  { printf "  ⚠️  %s\n" "$*"; }
err()   { printf "  ❌  %s\n" "$*" >&2; }
title() { printf "\n== %s ==\n" "$*"; }
hr()    { printf -- "----------------------------------------\n"; }

# ---------- 安全删除：symlink 用 rm 只删链接，目录才递归删 ----------
# 关键：rm -rf 跟随符号链接会删到源仓库，这里必须分开处理。
safe_rm() {
    local p="$1"
    if [[ -L "$p" ]]; then
        rm "$p"                              # 符号链接：只删链接本身
    elif [[ -d "$p" ]]; then
        /bin/rm -rf "$p"                     # 真目录：递归删
    elif [[ -e "$p" ]]; then
        rm -f "$p"                           # 文件：直接删
    fi
    # 不存在：什么都不做
}

# ---------- 检测脚本所在目录（用物理路径，避免符号链接误判） ----------
SCRIPT_PATH="${BASH_SOURCE[0]:-$0}"
SCRIPT_DIR="$(cd "$(dirname "$SCRIPT_PATH")" && pwd -P)"

# ---------- 默认配置 ----------
SKILL_NAME="youzi"
INSTALL_MODE="link"  # link | copy
PLATFORM="all"       # all | claude | opencode | codex | easycode

# skill 运行时需要的仓库内容（link 模式逐项软链；copy 模式逐项复制；
# 开发产物 .git / tests / .idea / 各类 cache 一律不进安装目录）
RUNTIME_PIECES=(
    SKILL.md
    scripts
    adapters
    references
    templates
    storage
    assets
    render.py
    verify.py
    gates.py
    network_gates.py
)

# 平台 → 安装目录映射。探测目录（或其父目录）存在才算该平台在用。
ALL_PLATFORMS="claude opencode codex easycode"

platform_dir() {
    case "$1" in
        claude)   echo "$HOME/.claude/skills" ;;
        opencode) echo "$HOME/.config/opencode/skills" ;;
        codex)    echo "$HOME/.codex/skills" ;;
        easycode) echo "$HOME/.easycode/skills" ;;
    esac
}

# 实际要处理的平台列表（all = 目录存在即装；显式指定 = 强制单平台）
active_platforms() {
    if [[ "$PLATFORM" != "all" ]]; then
        echo "$PLATFORM"
        return
    fi
    local found=0
    local pf dir
    for pf in $ALL_PLATFORMS; do
        dir="$(platform_dir "$pf")"
        # 目录存在 或 父工具明显在用（如 ~/.codex / ~/.easycode 存在）→ 装这里
        if [[ -d "$dir" || -d "$(dirname "$dir")" ]]; then
            echo "$pf"
            found=1
        fi
    done
    # 一个都没探测到：默认装 Claude Code（报错时用户可 --platform 指定）
    if [[ $found -eq 0 ]]; then
        echo claude
    fi
    return 0    # 末行 [[ ]] && echo 为假时退出码=1，set -e 会误杀调用方
}

# 每个平台独立的 INSTALL_DIR（在 install/uninstall/update/status 循环里赋值）
INSTALL_DIR=""
ACTIVE_PLATFORM=""

# ---------- 帮助 ----------
print_help() {
    cat <<EOF
youzi - 竞品颠覆性分析 skill · 一键安装 / 卸载 / 更新

用法:
  $(basename "$0") <command> [options]

命令:
  install      安装 youzi skill 到全部已装的 AI 工具（4 平台自动探测）
               ~/.claude/skills/youzi/
               ~/.config/opencode/skills/youzi/
               ~/.codex/skills/youzi/
               ~/.easycode/skills/youzi/
  uninstall    卸载已安装的 skill（全部平台）
  update       刷新安装（link 模式改源码即时生效，无需手动 update）
  status       查看安装状态 + 环境检查（全部平台）
  help         显示本帮助

选项（适用于 install/update/uninstall/status）:
  --platform <claude|opencode|codex|easycode|all>  目标平台（默认 all=自动探测）
  --dir <path>        自定义安装目录（覆盖默认；单平台时生效）
  --mode <link|copy>  安装方式（默认: link）
                      link - 软链源仓库，改源码即时生效
                      copy - 完整复制运行时文件（不便软链时用）

示例:
  $(basename "$0") install                          # 全平台自动探测安装
  $(basename "$0") install --platform opencode      # 只装 opencode
  $(basename "$0") install --platform codex         # 只装 Codex
  $(basename "$0") install --mode copy              # 复制模式
  $(basename "$0") status                           # 全平台状态 + 环境检查

EOF
}

# ---------- 参数解析 ----------
parse_args() {
    if [[ $# -eq 0 ]]; then
        print_help
        exit 0
    fi

    COMMAND="$1"; shift

    while [[ $# -gt 0 ]]; do
        case "$1" in
            # 支持 --dir=path 与 --dir path 两种形式
            --dir=*)
                INSTALL_DIR="${1#*=}"
                shift
                ;;
            --dir)
                if [[ $# -lt 2 ]]; then
                    err "--dir 需要一个参数值"
                    exit 1
                fi
                if [[ "$2" == -* ]]; then
                    err "--dir 的参数值不能以 - 开头: $2"
                    exit 1
                fi
                INSTALL_DIR="$2"
                shift 2
                ;;
            --mode=*)
                INSTALL_MODE="${1#*=}"
                shift
                ;;
            --mode)
                if [[ $# -lt 2 ]]; then
                    err "--mode 需要一个参数值"
                    exit 1
                fi
                if [[ "$2" != "link" && "$2" != "copy" ]]; then
                    err "--mode 必须是 link 或 copy，得到: $2"
                    exit 1
                fi
                INSTALL_MODE="$2"
                shift 2
                ;;
            --platform=*)
                PLATFORM="${1#*=}"
                shift
                ;;
            --platform)
                if [[ $# -lt 2 ]]; then
                    err "--platform 需要一个参数值（claude / opencode / codex / easycode / all）"
                    exit 1
                fi
                PLATFORM="$2"
                shift 2
                ;;
            -h|--help) print_help; exit 0 ;;
            *)
                err "未知参数: $1"
                print_help
                exit 1
                ;;
        esac
    done

    # 规范化：去掉尾斜杠，避免路径出现 //youzi 这种双斜杠
    INSTALL_DIR="${INSTALL_DIR%/}"

    case "$PLATFORM" in
        all|claude|opencode|codex|easycode) ;;
        *)
            err "--platform 必须是 claude / opencode / codex / easycode / all，得到: $PLATFORM"
            exit 1
            ;;
    esac

    # --dir 只对单平台明确；all 时目录归属歧义，直接要求指定平台
    if [[ -n "$INSTALL_DIR" && "$PLATFORM" == "all" ]]; then
        err "--dir 需要配合 --platform <具体平台> 使用（all 时目录归属不明确）"
        exit 1
    fi
}

# ---------- 安全覆盖 prompt（非 TTY 时默认 N，绝不覆盖用户数据） ----------
confirm_replace() {
    local target="$1"
    if [[ ! -t 0 ]]; then
        warn "非交互式 stdin，自动跳过覆盖：$target"
        return 1
    fi
    printf "  是否替换？[y/N] "
    # 用 || 兜底 stdin EOF / 错误
    local ans
    if ! read -r ans; then
        ans="N"
    fi
    case "${ans:-N}" in
        y|Y|yes|YES|Yes) return 0 ;;
        *) return 1 ;;
    esac
}

# ---------- 安装单个 skill ----------
install_one_skill() {
    local target="$INSTALL_DIR/$SKILL_NAME"

    # 检查目标
    if [[ -e "$target" || -L "$target" ]]; then
        warn "目标已存在：$target"
        if ! confirm_replace "$target"; then
            info "$SKILL_NAME 跳过"
            return 0
        fi
        # 用 safe_rm 区分 symlink / directory，绝不跟随 symlink 删用户数据
        safe_rm "$target"
    fi

    mkdir -p "$target"

    case "$INSTALL_MODE" in
        link)
            local p
            for p in "${RUNTIME_PIECES[@]}"; do
                ln -s "$SCRIPT_DIR/$p" "$target/$p"
            done
            ok "已创建 $SKILL_NAME（符号链接，改源码即时生效）"
            ;;
        copy)
            local p
            for p in "${RUNTIME_PIECES[@]}"; do
                if [[ -d "$SCRIPT_DIR/$p" ]]; then
                    cp -R "$SCRIPT_DIR/$p" "$target/$p"
                else
                    cp "$SCRIPT_DIR/$p" "$target/$p"
                fi
            done
            ok "已创建 $SKILL_NAME（完整复制）"
            ;;
        *)
            err "未知安装模式：$INSTALL_MODE"
            return 1
            ;;
    esac

    # opencode 专属：/ 命令补全入口（opencode 的 "/" 补全读 command/ 目录而非 skills/）
    if [[ "$ACTIVE_PLATFORM" == "opencode" ]]; then
        install_opencode_command
    fi
}

# opencode 的 / 补全在 ~/.config/opencode/command/youzi.md——
# 内容是一句转发（触发 youzi skill），description 从 SKILL.md 同步提取防漂移。
install_opencode_command() {
    local cmd_dir
    cmd_dir="$HOME/.config/opencode/command"
    local desc
    desc=$(sed -n 's/^description: *//p' "$SCRIPT_DIR/SKILL.md" | head -1 | cut -d'。' -f1)
    if [[ -z "$desc" ]]; then
        desc="youzi 竞品颠覆性分析"
    fi
    mkdir -p "$cmd_dir"
    printf -- '---\ndescription: %s\n---\n\n使用 youzi skill，按其 SKILL.md 的流程执行：解析主题与参数并回显；多角度搜索发现竞品；一次调用 scripts/fetch.py 统一取证（不逐 URL 手动爬）；基于 02-raw 逐字段提取 13 字段（每字段带 {值, source_url, quote} 证据三元组，绝不伪造）；render.py 渲染 + verify.py 双门禁通过后交付报告。主题参数：$ARGUMENTS。\n' \
        "$desc" > "$cmd_dir/$SKILL_NAME.md"
    ok "已生成 opencode 命令补全：$cmd_dir/$SKILL_NAME.md"
}

# ---------- install ----------
do_install() {
    title "安装 $SKILL_NAME skill"

    # 验证源目录：运行时文件缺一个都不装，避免留下半残安装
    local missing=()
    local p
    for p in "${RUNTIME_PIECES[@]}"; do
        if [[ ! -e "$SCRIPT_DIR/$p" ]]; then
            missing+=("$p")
        fi
    done
    if [[ ${#missing[@]} -gt 0 ]]; then
        err "当前目录不是有效的 skill 仓库：$SCRIPT_DIR"
        err "缺少以下文件/目录："
        for p in "${missing[@]}"; do
            err "  - $p"
        done
        return 1
    fi

    info "源目录：$SCRIPT_DIR"
    info "目标目录：$INSTALL_DIR"
    info "安装方式：$INSTALL_MODE"

    mkdir -p "$INSTALL_DIR"

    install_one_skill

    ok "安装完成！"
    echo
    title "下一步"
    hr
    cat <<'EOF'
  1. 重启 AI 工具（Claude Code / opencode / Codex / EasyCode，
     已运行的会话需要重启以加载 Skill）

  2. 在 AI 工具里输入：

         /youzi 在线协作工具

  3. 等 5-15 分钟，报告自动生成并提示打开

  4. （推荐）安装爬虫引擎提升抓取质量：

         pip install jinja2 playwright trafilatura newspaper3k lxml_html_clean
         playwright install chromium

EOF
    hr
}

# ---------- uninstall ----------
do_uninstall() {
    title "卸载 $SKILL_NAME skill"

    local target="$INSTALL_DIR/$SKILL_NAME"
    if [[ -e "$target" || -L "$target" ]]; then
        safe_rm "$target"      # 用 safe_rm 区分 symlink / 目录
        ok "已删除 $target"
    else
        info "未安装：$target"
    fi
    # opencode：同步删 / 命令补全文件
    if [[ "$ACTIVE_PLATFORM" == "opencode" ]]; then
        local cmd_file="$HOME/.config/opencode/command/$SKILL_NAME.md"
        if [[ -f "$cmd_file" ]]; then
            rm -f "$cmd_file"
            ok "已删除命令补全 $cmd_file"
        fi
    fi

    cat <<EOF

  提示：AI 工具一般在启动时读取 SKILL.md。
        如果之前加载过，建议重启对应工具。

EOF
}

# ---------- 检测已安装 skill 的真实模式 ----------
detect_install_mode() {
    local target="$1"
    # 优先检查内部 scripts 是不是 symlink（绝大多数情况）
    if [[ -L "$target/scripts" ]]; then
        echo "link"
    else
        echo "copy"
    fi
}

# ---------- update ----------
do_update() {
    title "更新 $SKILL_NAME skill"

    local target="$INSTALL_DIR/$SKILL_NAME"
    if [[ ! -e "$target" && ! -L "$target" ]]; then
        warn "未安装，跳过（请先执行 install）"
        return 0
    fi

    # 检测已安装的真实模式（避免 INSTALL_MODE 全局变量被覆盖）
    local actual_mode
    actual_mode=$(detect_install_mode "$target")

    if [[ "$actual_mode" == "link" ]]; then
        # link 模式：检查 scripts 是否指向当前仓库
        local cur
        cur="$(readlink "$target/scripts")"
        if [[ "$cur" == "$SCRIPT_DIR/scripts" ]]; then
            ok "已是最新（link 模式指向当前仓库，改源码即时生效）"
        else
            # scripts 指向别处 / 断了 → 重新安装（安全删除）
            safe_rm "$target"
            INSTALL_MODE="link"
            install_one_skill
        fi
    else
        # copy 模式：整体重装（覆盖本地副本）
        safe_rm "$target"
        INSTALL_MODE="copy"
        install_one_skill
        warn "copy 模式已覆盖本地副本（如改过安装目录内的文件，请到源仓库重新修改）"
    fi
}

# ---------- status ----------
do_status() {
    title "youzi 安装状态"
    hr
    printf "  源目录：    %s\n" "$SCRIPT_DIR"
    printf "  目标目录：  %s\n" "$INSTALL_DIR"
    hr

    local target="$INSTALL_DIR/$SKILL_NAME"
    printf "  [%s]\n" "$SKILL_NAME"
    if [[ -L "$target" ]]; then
        # target 本身是 symlink（旧版整仓软链装法）
        local link
        link="$(readlink "$target")"
        printf "    状态：    已安装（整仓软链 → %s）\n" "$link"
        if [[ "$link" == "$SCRIPT_DIR" ]]; then
            printf "    ✅ 指向当前仓库，修改即时生效\n"
        else
            printf "    ⚠️  指向其他位置，建议 update\n"
        fi
    elif [[ -L "$target/scripts" ]]; then
        local link
        link="$(readlink "$target/scripts")"
        printf "    状态：    已安装（符号链接）\n"
        printf "    scripts：%s\n" "$link"
        if [[ "$link" == "$SCRIPT_DIR/scripts" ]]; then
            printf "    ✅ 指向当前仓库，修改即时生效\n"
        else
            printf "    ⚠️  指向其他位置，建议 update\n"
        fi
    elif [[ -d "$target" ]]; then
        printf "    状态：    已安装（复制模式）\n"
        printf "    ⚠️  复制模式下需 update 同步\n"
    else
        printf "    状态：    未安装\n"
    fi
    if [[ -e "$target/SKILL.md" || -L "$target/SKILL.md" ]]; then
        printf "    SKILL.md：✅\n"
    else
        printf "    SKILL.md：❌ 缺失\n"
    fi
    # opencode：/ 补全入口
    if [[ "$ACTIVE_PLATFORM" == "opencode" ]]; then
        local cmd_file="$HOME/.config/opencode/command/$SKILL_NAME.md"
        if [[ -f "$cmd_file" ]]; then
            printf "    / 补全：  ✅ %s\n" "$cmd_file"
        else
            printf "    / 补全：  ❌ 缺失（update 可补）\n"
        fi
    fi
    echo

    # 环境检查
    echo "  环境检查："
    if command -v python3 >/dev/null 2>&1; then
        if python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)'; then
            echo "    ✅ python3: $(python3 --version 2>&1)"
        else
            echo "    ⚠️  python3: $(python3 --version 2>&1) 版本过低（需要 3.8+，python.org 下载新版）"
        fi
    else
        echo "    ⚠️  python3: 未安装（必须）"
    fi
    if command -v git >/dev/null 2>&1; then
        echo "    ✅ git: $(git --version 2>&1)"
    else
        echo "    ℹ️  git: 未安装（git clone 获取源码 / 后续更新需要）"
    fi
    if python3 -c "import jinja2" 2>/dev/null; then
        echo "    ✅ jinja2: $(python3 -c 'import jinja2; print(jinja2.__version__)')（渲染必需）"
    else
        echo "    ⚠️  jinja2: 未安装（render.py 需要；pip install jinja2）"
    fi
    # 爬虫引擎（可选，装得越多抓得越稳）
    local eng
    for eng in playwright trafilatura newspaper3k; do
        if python3 -c "import $eng" 2>/dev/null; then
            echo "    ✅ $eng: 已安装"
        else
            echo "    ℹ️  $eng: 未安装（可选引擎，装了抓取更稳）"
        fi
    done
    echo "    ℹ️  jina: 免 key 免安装，开箱即用"
    if [[ -n "${FIRECRAWL_API_KEY:-}" ]]; then
        echo "    ✅ firecrawl: FIRECRAWL_API_KEY 已配置（商业最强引擎自动启用）"
    else
        echo "    ℹ️  firecrawl: 未配置 FIRECRAWL_API_KEY（可选，firecrawl.dev 免费注册）"
    fi
}

# ---------- 主入口（平台循环）----------
platform_label() {
    case "$1" in
        claude)   echo "Claude Code" ;;
        opencode) echo "opencode" ;;
        codex)    echo "Codex" ;;
        easycode) echo "EasyCode" ;;
    esac
}

main() {
    parse_args "$@"

    # help 不依赖平台目录，直接输出
    if [[ "$COMMAND" == "help" || "$COMMAND" == "-h" ]]; then
        print_help
        exit 0
    fi

    local failed=0
    local platforms
    platforms="$(active_platforms)"
    # active_platforms 至少回显一个平台；此处仅兜底极端情况（如子 shell 异常）
    if [[ -z "${platforms//$'\n'/}" ]]; then
        platforms="claude"
    fi

    local pf
    for pf in $platforms; do
        ACTIVE_PLATFORM="$pf"
        # --dir 已在 parse_args 校验过必须搭配单平台；默认按平台取目录
        if [[ -z "$INSTALL_DIR" ]]; then
            INSTALL_DIR="$(platform_dir "$pf")"
        fi
        title "$(platform_label "$pf") -- $INSTALL_DIR"
        case "$COMMAND" in
            install)   do_install   || failed=1 ;;
            uninstall) do_uninstall || failed=1 ;;
            update)    do_update    || failed=1 ;;
            status)    do_status    || failed=1 ;;
            *)
                err "未知命令：$COMMAND"
                print_help
                exit 1
                ;;
        esac
        INSTALL_DIR=""  # 下一平台恢复默认目录
    done

    exit $failed
}

main "$@"
