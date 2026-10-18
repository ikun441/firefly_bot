#!/usr/bin/env bash
# =============================================================================
#  流萤 Firefly · macOS / Linux 一键启动脚本
# -----------------------------------------------------------------------------
#  依次完成：检查 Python -> 创建/激活 venv(firefly) -> 安装依赖
#            -> 安装 Playwright 内核 -> 检查配置 -> 启动服务
#
#  用法：
#     chmod +x start.sh      # 首次需要赋予执行权限
#     ./start.sh
# =============================================================================

set -o pipefail

# 无论从哪里调用，都切到脚本所在目录
cd "$(dirname "$0")" || exit 1

# 虚拟环境目录名。
#
# ⚠️ 绝不能叫 "firefly"：项目自身的 Python 包目录就是 ./firefly/，
# 两者路径完全相同。创建虚拟环境会把源码目录整个覆盖掉，
# 且报错是 "No module named firefly.app"，很难联想到是命名冲突。
# 实测已踩过一次，务必保持这个名字与包名不同。
VENV_NAME="firefly_env"
VENV_DIR="$(pwd)/${VENV_NAME}"
MIN_PY_MAJOR=3
MIN_PY_MINOR=10
# 上界：README 与 requirements.txt 说明本项目在 3.11 上开发验证，
# 支持的组合是 3.10 ~ 3.12。再往上（3.13/3.14）部分依赖尚无预编译 wheel，
# 实测在 3.14 上会出现 NoneBot 配置解析相关的启动失败，因此必须拦住。
MAX_PY_MINOR=12
PORT=2525

# ---- 颜色（非终端环境下自动禁用，避免写进日志时满是转义码）-------------------
if [ -t 1 ]; then
    C_RESET='\033[0m';  C_BOLD='\033[1m'
    C_GREEN='\033[0;32m'; C_YELLOW='\033[0;33m'
    C_RED='\033[0;31m';   C_CYAN='\033[0;36m'
else
    C_RESET=''; C_BOLD=''; C_GREEN=''; C_YELLOW=''; C_RED=''; C_CYAN=''
fi

info()  { printf "      %s\n" "$1"; }
ok()    { printf "${C_GREEN}      %s${C_RESET}\n" "$1"; }
warn()  { printf "${C_YELLOW}      %s${C_RESET}\n" "$1"; }
step()  { printf "\n${C_BOLD}[%s] %s${C_RESET}\n" "$1" "$2"; }
die()   { printf "\n${C_RED}  [错误] %s${C_RESET}\n\n" "$1"; exit 1; }

printf "\n"
printf "============================================================\n"
printf "            流 萤  Firefly  ·  一键启动\n"
printf "============================================================\n"

# -----------------------------------------------------------------------------
#  0. 前置守卫：虚拟环境目录不能与源码包同名
# -----------------------------------------------------------------------------
# 项目包是 ./firefly/，若虚拟环境也叫 firefly，python -m venv 会往同一个
# 路径写 bin/lib/pyvenv.cfg，把源码目录覆盖掉。这类故障已经真实发生过：
# 源码消失、报错指向 "No module named firefly.app"，排查成本极高。
# 与其事后补救，不如在这里直接拦死。
if [ -d "${VENV_NAME}" ] && [ -f "${VENV_NAME}/app.py" ]; then
    printf "\n${C_RED}  [错误] 检测到虚拟环境目录 \"%s\" 与源码包同名且已冲突。${C_RESET}\n\n" "$VENV_NAME"
    printf "  请检查 %s/ 下是源码还是虚拟环境：\n" "$VENV_NAME"
    printf "    - 若是源码，请修改脚本里的 VENV_NAME 为其它名字（如 firefly_env）\n"
    printf "    - 若是虚拟环境残留，请删除该目录后重试\n\n"
    exit 1
fi

# -----------------------------------------------------------------------------
#  1. 检查 Python 环境
# -----------------------------------------------------------------------------
step "1/6" "检查 Python 环境..."

PY_CMD=""

# 版本区间判定：MIN_PY_MAJOR.MIN_PY_MINOR <= ver <= 3.MAX_PY_MINOR
# 上界必须有：过高的 Python（3.13/3.14）缺少部分依赖的预编译 wheel，
# 实测会出现 NoneBot 配置解析相关的启动失败，属于「装得上、跑不起来」的坑。
py_ver_ok() {
    "$1" -c "
import sys
v = sys.version_info
ok = (v.major, v.minor) >= (${MIN_PY_MAJOR}, ${MIN_PY_MINOR}) and v.major == 3 and v.minor <= ${MAX_PY_MINOR}
sys.exit(0 if ok else 1)
" >/dev/null 2>&1
}

# 逐版本探测：3.11 优先（README 明确说明本项目在 3.11 上开发验证），
# 其次 3.12、3.10，最后才是不带版本号的 python3 / python。
# 之所以不直接用 python3：macOS 自带的 /usr/bin/python3 是 3.9，
# 装了新版 Python 后它往往仍排在 PATH 前面，直接用会拿到过低版本。
for candidate in python3.11 python3.12 python3.10 python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
        if py_ver_ok "$candidate"; then
            PY_CMD="$candidate"
            break
        fi
    fi
done

# 回退：到 PATH 之外的常见安装位置去找。
#
# 为什么必须有这一步：conda / miniforge 装好后若没执行 conda init，
# 它们的 python 不会进 PATH，只探测 python3.xx 命令名就会漏掉，
# 于是明明装了 3.11 却报「版本过低」或「未检测到 Python」。
#
# 注意检索顺序是 3.11 -> 3.12 -> 3.10，不是从高到低：
# miniforge 的 base 环境往往是 3.13/3.14 这种很新的版本，从高到低会先命中它，
# 而它恰好不被支持。这里优先挑与项目验证过的版本一致的。
if [ -z "$PY_CMD" ]; then
    for prefix in \
        "${HOME}/miniforge3" "${HOME}/miniconda3" "${HOME}/anaconda3" \
        "${HOME}/mambaforge" "/opt/miniforge3" "/opt/miniconda3" \
        "/opt/homebrew" "/usr/local"
    do
        for ver in 3.11 3.12 3.10; do
            candidate="${prefix}/bin/python${ver}"
            if [ -x "$candidate" ] && py_ver_ok "$candidate"; then
                PY_CMD="$candidate"
                break 2
            fi
        done
        # 部分安装只提供不带版本号的 python3
        candidate="${prefix}/bin/python3"
        if [ -z "$PY_CMD" ] && [ -x "$candidate" ] && py_ver_ok "$candidate"; then
            PY_CMD="$candidate"
            break
        fi

        # conda 的**具名环境**里也常藏着合规版本（例如 envs/firefly 是 3.11，
        # 而 base 是 3.13/3.14）。不扫这一层的话，明明装了 3.11 仍会报错。
        if [ -d "${prefix}/envs" ]; then
            for envdir in "${prefix}"/envs/*/; do
                candidate="${envdir}bin/python"
                if [ -x "$candidate" ] && py_ver_ok "$candidate"; then
                    PY_CMD="$candidate"
                    break 2
                fi
            done
        fi
    done
fi

if [ -z "$PY_CMD" ]; then
    # 找得到 Python 但不在支持区间时，给出更具体的提示（比笼统报「未检测到」有用）
    FOUND_VER=""
    FOUND_CMD=""
    for candidate in python3 python python3.14 python3.13; do
        if command -v "$candidate" >/dev/null 2>&1; then
            FOUND_VER="$("$candidate" -c 'import sys; print(sys.version.split()[0])' 2>/dev/null)"
            FOUND_CMD="$candidate"
            break
        fi
    done

    # conda base 里的版本也提一下，很多人的 3.11 藏在环境里
    for prefix in "${HOME}/miniforge3" "${HOME}/miniconda3" "${HOME}/anaconda3"; do
        if [ -z "$FOUND_VER" ] && [ -x "${prefix}/bin/python3" ]; then
            FOUND_VER="$("${prefix}/bin/python3" -c 'import sys; print(sys.version.split()[0])' 2>/dev/null)"
            FOUND_CMD="${prefix}/bin/python3"
        fi
        if [ -d "${prefix}/envs" ]; then
            for envdir in "${prefix}"/envs/*/; do
                if [ -z "$PY_CMD" ] && [ -x "${envdir}bin/python" ] && py_ver_ok "${envdir}bin/python"; then
                    printf "\n${C_YELLOW}  [提示] 在 conda 环境里发现了可用的 Python：${C_RESET}\n"
                    printf "    %s  (%s)\n\n" "${envdir}bin/python" \
                        "$("${envdir}bin/python" -c 'import sys; print(sys.version.split()[0])')"
                    printf "  可以这样用它创建虚拟环境：\n"
                    printf "    %s -m venv %s\n" "${envdir}bin/python" "$VENV_NAME"
                    printf "    然后重新运行 ./start.sh\n\n"
                    break 2
                fi
            done
        fi
    done

    if [ -n "$FOUND_VER" ]; then
        printf "\n${C_RED}  [错误] Python 版本不在支持区间（检测到 %s，需要 3.%s ~ 3.%s）${C_RESET}\n\n" \
            "$FOUND_VER" "$MIN_PY_MINOR" "$MAX_PY_MINOR"
        printf "  原因：本项目在 Python 3.11 上开发验证，支持 3.10 ~ 3.12。\n"
        printf "        3.9 及以下不被 NoneBot2 2.5+ 支持；\n"
        printf "        3.13 及以上部分依赖缺少预编译包，可能出现启动失败。\n\n"
        printf "  安装推荐版本（3.11）：\n"
        printf "    macOS        brew install python@3.11\n"
        printf "    Ubuntu       sudo apt update && sudo apt install python3.11 python3.11-venv -y\n"
        printf "    CentOS       sudo dnf install python3.11 -y\n"
        printf "    conda        conda create -n firefly python=3.11 -y && conda activate firefly\n\n"
    else
        printf "\n${C_RED}  [错误] 未检测到 Python。${C_RESET}\n\n"
        printf "  请先安装 Python 3.11（本项目验证版本，支持 3.10 ~ 3.12）：\n"
        printf "    https://www.python.org/downloads/\n\n"
        printf "  或使用包管理器：\n"
        printf "    macOS        brew install python@3.11\n"
        printf "    Ubuntu       sudo apt install python3.11 python3.11-venv -y\n\n"
    fi
    exit 1
fi

PY_VER="$("$PY_CMD" -c 'import sys; print(sys.version.split()[0])')"
ok "$PY_CMD ($PY_VER)  OK"

# -----------------------------------------------------------------------------
#  2. 创建并激活虚拟环境
# -----------------------------------------------------------------------------
step "2/6" "准备虚拟环境 (${VENV_NAME})..."

if [ -x "${VENV_DIR}/bin/python" ]; then
    info "已存在，跳过创建"
else
    info "未检测到，正在创建（首次约需 30 秒）..."
    if ! "$PY_CMD" -m venv "$VENV_DIR"; then
        die "虚拟环境创建失败。请确认 Python 安装完整（需包含 venv 模块）。
       Debian/Ubuntu 用户可能需要：sudo apt install python3-venv"
    fi
    ok "创建完成"
fi

VENV_PY="${VENV_DIR}/bin/python"

if [ ! -x "$VENV_PY" ]; then
    die "虚拟环境不完整：找不到 $VENV_PY
       请删除目录 ${VENV_NAME} 后重新运行本脚本。"
fi

# shellcheck disable=SC1091
if [ -f "${VENV_DIR}/bin/activate" ]; then
    # shellcheck disable=SC1091
    . "${VENV_DIR}/bin/activate"
fi
ok "已激活：$VENV_PY"

# -----------------------------------------------------------------------------
#  3. 检查并安装依赖
# -----------------------------------------------------------------------------
step "3/6" "检查依赖..."

# 用 requirements.txt 的哈希做标记：文件没变就跳过安装，避免每次启动都等几分钟
HASH_FILE="${VENV_DIR}/.requirements.sha256"
if command -v shasum >/dev/null 2>&1; then
    CUR_HASH="$(shasum -a 256 requirements.txt | awk '{print $1}')"
elif command -v sha256sum >/dev/null 2>&1; then
    CUR_HASH="$(sha256sum requirements.txt | awk '{print $1}')"
else
    CUR_HASH=""
fi

NEED_INSTALL=1
if [ -n "$CUR_HASH" ] && [ -f "$HASH_FILE" ]; then
    if [ "$(cat "$HASH_FILE" 2>/dev/null)" = "$CUR_HASH" ]; then
        NEED_INSTALL=0
    fi
fi

# 兜底：即使哈希一致，也要确认核心依赖真的能导入
# （哈希文件在、但依赖被手动删过或有损坏时，光看哈希会误判）
if [ "$NEED_INSTALL" -eq 0 ]; then
    if ! "$VENV_PY" -c "import nonebot, fastapi, sqlmodel, uvicorn, openpyxl, defusedxml, socksio, markdown_it" >/dev/null 2>&1; then
        NEED_INSTALL=1
    fi
fi

if [ "$NEED_INSTALL" -eq 1 ]; then
    info "正在安装依赖（首次约需 3-5 分钟，请耐心等待）..."
    "$VENV_PY" -m pip install --upgrade pip --quiet

    # 优先使用清华镜像：国内网络直连 PyPI 极易超时
    info "尝试国内镜像源..."
    if ! "$VENV_PY" -m pip install -r requirements.txt \
            -i https://pypi.tuna.tsinghua.edu.cn/simple; then
        warn "镜像源失败，改用官方源重试..."
        if ! "$VENV_PY" -m pip install -r requirements.txt; then
            printf "\n${C_RED}  [错误] 依赖安装失败。${C_RESET}\n\n"
            printf "  常见原因与对策：\n"
            printf "    1. 网络超时      -> 检查网络，或换用其它镜像源\n"
            printf "    2. 缺少编译工具  -> Ubuntu: sudo apt install build-essential python3-dev -y\n"
            printf "                        （chromadb 等包需要编译）\n"
            printf "    3. 磁盘空间不足  -> 清理空间后重试\n\n"
            exit 1
        fi
    fi

    [ -n "$CUR_HASH" ] && printf '%s' "$CUR_HASH" > "$HASH_FILE"
    ok "依赖安装完成"
else
    ok "依赖已就绪，跳过安装"
fi

# -----------------------------------------------------------------------------
#  4. 安装 Playwright 浏览器内核
# -----------------------------------------------------------------------------
# 图片渲染（htmlrender）依赖 Playwright 的 Chromium 内核。
# pip 装好 playwright 包不等于浏览器可用：内核要单独下载，少这一步会在
# 真正出图时报 "Executable doesn't exist"，而报错在业务代码里，很难定位。
step "4/6" "安装 Playwright 浏览器内核..."

# 用 playwright 版本号做标记：版本没变就跳过，避免每次启动都白等一次检查。
# 标记只在安装成功后写入，失败不会被误记为已完成，下次启动会重试。
PW_MARKER="${VENV_DIR}/.playwright.version"
PW_VER="$("$VENV_PY" -c 'import importlib.metadata as m; print(m.version("playwright"))' 2>/dev/null || true)"

NEED_PW_INSTALL=1
if [ -n "$PW_VER" ] && [ -f "$PW_MARKER" ] \
        && [ "$(cat "$PW_MARKER" 2>/dev/null)" = "$PW_VER" ]; then
    NEED_PW_INSTALL=0
fi

if [ "$NEED_PW_INSTALL" -eq 0 ]; then
    info "内核已就绪（playwright ${PW_VER}），跳过"
else
    info "正在下载 Chromium 内核（首次约需 1-3 分钟，国内网络较慢）..."
    PW_OK=0
    "$VENV_PY" -m playwright install chromium && PW_OK=1

    # playwright 只有环境变量这一个换源开关，没有对应的命令行参数
    if [ "$PW_OK" -eq 0 ]; then
        warn "默认源失败，改用国内镜像重试..."
        PLAYWRIGHT_DOWNLOAD_HOST="https://registry.npmmirror.com/-/binary/playwright" \
            "$VENV_PY" -m playwright install chromium && PW_OK=1
    fi

    if [ "$PW_OK" -eq 1 ]; then
        [ -n "$PW_VER" ] && printf '%s' "$PW_VER" > "$PW_MARKER"
        ok "内核安装完成"
    else
        warn "[警告] Chromium 内核安装失败，图片渲染功能将不可用（其余功能不受影响）"
        info "       可稍后手动执行："
        info "         ${VENV_PY} -m playwright install chromium"
        info "       Linux 若缺少系统依赖库，再执行："
        info "         ${VENV_PY} -m playwright install-deps chromium"
    fi
fi

# -----------------------------------------------------------------------------
#  5. 检查配置文件
# -----------------------------------------------------------------------------
step "5/6" "检查配置文件..."

if [ ! -f ".env" ]; then
    if [ -f ".env.example" ]; then
        cp .env.example .env
        ok "已从 .env.example 生成 .env"
        warn "[提示] 如需 AI 功能，请编辑 .env 填入 LLM_API_KEY"
        info "       留空也能运行，自然语言记账会自动降级为规则引擎"
    else
        warn "未找到 .env.example，跳过"
    fi
else
    ok ".env 已存在"
fi

# 修掉会导致启动崩溃的空 SUPERUSERS。
#
# 这是实测踩到的坑：.env 里留一行**未注释的空值** `SUPERUSERS=`，
# NoneBot 会在启动早期直接读 .env 并对该变量做 JSON 解析，
# 空串解析失败 → SettingsError: error parsing env var "superusers"，
# 服务刚起来就退出，而报错指向 NoneBot 内部，很难联想到是 .env 的问题。
# .env.example 里这行本来就是注释状态，是手工编辑时容易误留。
if [ -f ".env" ] && grep -qE '^[[:space:]]*SUPERUSERS[[:space:]]*=[[:space:]]*$' .env; then
    warn "检测到空的 SUPERUSERS=，会导致启动失败，正在注释掉..."
    # 用临时文件改写，避免 macOS 与 GNU sed 的 -i 参数差异
    sed -E 's/^([[:space:]]*SUPERUSERS[[:space:]]*=[[:space:]]*)$/# \1  # 空值会导致 NoneBot 解析失败，已由 start 脚本自动注释/' \
        .env > .env.tmp && mv .env.tmp .env
    ok "已修复（如需设置超级用户，请填 QQ 号，多个用逗号分隔）"
fi

# -----------------------------------------------------------------------------
#  6. 启动服务
# -----------------------------------------------------------------------------
step "6/6" "启动服务..."

# 端口占用检查：本项目只允许单实例，端口被占时给出明确提示而不是让启动中途失败
if command -v lsof >/dev/null 2>&1; then
    PORT_PID="$(lsof -nP -iTCP:"${PORT}" -sTCP:LISTEN -t 2>/dev/null | head -1)"
elif command -v ss >/dev/null 2>&1; then
    PORT_PID="$(ss -lntp 2>/dev/null | grep ":${PORT} " | head -1 | sed -n 's/.*pid=\([0-9]*\).*/\1/p')"
else
    PORT_PID=""
fi

if [ -n "$PORT_PID" ]; then
    printf "\n${C_YELLOW}  [警告] 端口 %s 已被占用（PID %s），服务可能无法启动。${C_RESET}\n\n" \
        "$PORT" "$PORT_PID"
    printf "  可能是上一次的流萤还在运行。请先关闭它：\n"
    printf "      kill %s\n\n" "$PORT_PID"
    exit 1
fi

printf "\n============================================================\n"
printf "  服务启动中，请稍候...\n"
printf "\n"
printf "  启动成功后可用地址：\n"
printf "\n"
printf "    ${C_CYAN}>>${C_RESET} 前端控制台   ${C_BOLD}http://127.0.0.1:%s/${C_RESET}\n" "$PORT"
printf "    ${C_CYAN}>>${C_RESET} API 文档     ${C_BOLD}http://127.0.0.1:%s/docs${C_RESET}\n" "$PORT"
printf "\n"
printf "  浏览器打开前端控制台即可开始使用。\n"
printf "  按 Ctrl+C 可停止服务。\n"
printf "============================================================\n\n"

# 直接用 venv 解释器启动，不依赖 activate 的生效范围
"$VENV_PY" -m firefly.app
EXIT_CODE=$?

printf "\n============================================================\n"
if [ $EXIT_CODE -eq 0 ]; then
    printf "  服务已停止。\n"
else
    printf "  服务异常退出（退出码 %s）。\n" "$EXIT_CODE"
    printf "  请检查上方日志中的报错信息。\n"
fi
printf "============================================================\n\n"

exit $EXIT_CODE
