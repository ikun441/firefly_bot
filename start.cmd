@echo off
chcp 65001 >nul 2>&1
setlocal EnableDelayedExpansion
title 流萤 Firefly - 一键启动

REM ============================================================================
REM  流萤 Firefly · Windows 一键启动脚本
REM ----------------------------------------------------------------------------
REM  依次完成：检查 Python -> 创建/激活 venv(firefly) -> 安装依赖
REM            -> 安装 Playwright 内核 -> 检查配置 -> 启动服务
REM
REM  用法：双击本文件，或在命令行执行  start.cmd
REM ============================================================================

cd /d "%~dp0"

REM 虚拟环境目录名。
REM
REM 绝不能叫 "firefly"：项目自身的 Python 包目录就是 .\firefly\，
REM 两者路径完全相同。创建虚拟环境会把源码目录整个覆盖掉，
REM 且报错是 "No module named firefly.app"，很难联想到是命名冲突。
REM 实测已踩过一次，务必保持这个名字与包名不同。
set "VENV_NAME=firefly_env"
set "VENV_DIR=%~dp0%VENV_NAME%"
set "MIN_PY_MAJOR=3"
set "MIN_PY_MINOR=10"
set "PORT=2525"

echo.
echo ============================================================
echo             流 萤  Firefly  ·  一键启动
echo ============================================================
echo.

REM ---------------------------------------------------------------------------
REM  1. 检查 Python 环境
REM ---------------------------------------------------------------------------
echo [1/6] 检查 Python 环境...

set "PY_CMD="

REM 优先用 py 启动器（Windows 官方安装器会装），它能选出正确的版本
where py >nul 2>&1
if !errorlevel! equ 0 (
    py -%MIN_PY_MAJOR%.%MIN_PY_MINOR% -c "import sys" >nul 2>&1
    if !errorlevel! equ 0 (
        set "PY_CMD=py -%MIN_PY_MAJOR%.%MIN_PY_MINOR%"
    ) else (
        py -3 -c "import sys" >nul 2>&1
        if !errorlevel! equ 0 set "PY_CMD=py -3"
    )
)

if "!PY_CMD!"=="" (
    where python >nul 2>&1
    if !errorlevel! equ 0 set "PY_CMD=python"
)

if "!PY_CMD!"=="" (
    echo.
    echo   [错误] 未检测到 Python。
    echo.
    echo   请先安装 Python %MIN_PY_MAJOR%.%MIN_PY_MINOR% 或更高版本：
    echo     https://www.python.org/downloads/
    echo.
    echo   安装时务必勾选 "Add python.exe to PATH"。
    echo.
    pause
    exit /b 1
)

REM 校验版本号 >= 3.10（NoneBot2 2.5+ 强制要求）
!PY_CMD! -c "import sys; sys.exit(0 if sys.version_info >= (%MIN_PY_MAJOR%, %MIN_PY_MINOR%) else 1)" >nul 2>&1
if !errorlevel! neq 0 (
    echo.
    echo   [错误] Python 版本过低。
    !PY_CMD! -c "import sys; print('   当前版本：' + sys.version.split()[0])"
    echo   本项目要求 Python %MIN_PY_MAJOR%.%MIN_PY_MINOR% 或更高（推荐 3.11）。
    echo.
    echo   原因：NoneBot2 2.5+ 与部分依赖不支持 3.9。
    echo.
    pause
    exit /b 1
)

for /f "delims=" %%v in ('!PY_CMD! -c "import sys; print(sys.version.split()[0])"') do set "PY_VER=%%v"
echo       Python !PY_VER!  OK

REM ---------------------------------------------------------------------------
REM  2. 创建并激活虚拟环境
REM ---------------------------------------------------------------------------
echo.
echo [2/6] 准备虚拟环境 (%VENV_NAME%)...

if exist "%VENV_DIR%\Scripts\python.exe" (
    echo       已存在，跳过创建
) else (
    echo       未检测到，正在创建（首次约需 30 秒）...
    !PY_CMD! -m venv "%VENV_DIR%"
    if !errorlevel! neq 0 (
        echo.
        echo   [错误] 虚拟环境创建失败。
        echo   请确认 Python 安装完整（需包含 venv 模块）。
        echo.
        pause
        exit /b 1
    )
    echo       创建完成
)

set "VENV_PY=%VENV_DIR%\Scripts\python.exe"
set "VENV_PIP=%VENV_DIR%\Scripts\pip.exe"

if not exist "!VENV_PY!" (
    echo.
    echo   [错误] 虚拟环境不完整：找不到 !VENV_PY!
    echo   请删除目录 "%VENV_NAME%" 后重新运行本脚本。
    echo.
    pause
    exit /b 1
)

echo       已激活：!VENV_PY!

REM ---------------------------------------------------------------------------
REM  3. 检查并安装依赖
REM ---------------------------------------------------------------------------
echo.
echo [3/6] 检查依赖...

REM 用 requirements.txt 的哈希做标记：文件没变就跳过安装，避免每次启动都等几分钟
set "HASH_FILE=%VENV_DIR%\.requirements.sha256"
set "CUR_HASH="
for /f "skip=1 delims=" %%h in ('certutil -hashfile "requirements.txt" SHA256 2^>nul') do (
    if "!CUR_HASH!"=="" set "CUR_HASH=%%h"
)

set "NEED_INSTALL=1"
if exist "!HASH_FILE!" (
    set /p OLD_HASH=<"!HASH_FILE!"
    if "!OLD_HASH!"=="!CUR_HASH!" set "NEED_INSTALL=0"
)

REM 兜底：即使哈希一致，也要确认核心依赖真的能导入
if "!NEED_INSTALL!"=="0" (
    "!VENV_PY!" -c "import nonebot, fastapi, sqlmodel, uvicorn" >nul 2>&1
    if !errorlevel! neq 0 set "NEED_INSTALL=1"
)

if "!NEED_INSTALL!"=="1" (
    echo       正在安装依赖（首次约需 3-5 分钟，请耐心等待）...
    "!VENV_PY!" -m pip install --upgrade pip --quiet

    REM 优先使用清华镜像：国内网络直连 PyPI 极易超时
    echo       尝试国内镜像源...
    "!VENV_PY!" -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
    if !errorlevel! neq 0 (
        echo.
        echo       镜像源失败，改用官方源重试...
        "!VENV_PY!" -m pip install -r requirements.txt
        if !errorlevel! neq 0 (
            echo.
            echo   [错误] 依赖安装失败。
            echo.
            echo   常见原因与对策：
            echo     1. 网络超时     -^> 检查网络，或换用其它镜像源
            echo     2. 缺少编译工具 -^> 安装 Visual Studio Build Tools
            echo     3. 磁盘空间不足 -^> 清理空间后重试
            echo.
            pause
            exit /b 1
        )
    )

    REM 记录哈希，下次启动跳过安装
    >"!HASH_FILE!" echo !CUR_HASH!
    echo       依赖安装完成
) else (
    echo       依赖已就绪，跳过安装
)

REM ---------------------------------------------------------------------------
REM  4. 安装 Playwright 浏览器内核
REM ---------------------------------------------------------------------------
REM 图片渲染（htmlrender）依赖 Playwright 的 Chromium 内核。
REM pip 装好 playwright 包不等于浏览器可用：内核要单独下载，少这一步会在
REM 真正出图时报 "Executable doesn't exist"，而报错在业务代码里，很难定位。
echo.
echo [4/6] 安装 Playwright 浏览器内核...

REM 用 playwright 版本号做标记：版本没变就跳过，避免每次启动都白等一次检查。
set "PW_MARKER=%VENV_DIR%\.playwright.version"
set "PW_VER_FILE=%TEMP%\firefly_pw_ver.txt"
set "PW_VER="
"!VENV_PY!" -c "import importlib.metadata as m; print(m.version('playwright'))" >"!PW_VER_FILE!" 2>nul
if exist "!PW_VER_FILE!" (
    set /p PW_VER=<"!PW_VER_FILE!"
    del "!PW_VER_FILE!" >nul 2>&1
)

set "NEED_PW_INSTALL=1"
if not "!PW_VER!"=="" (
    if exist "!PW_MARKER!" (
        set /p OLD_PW_VER=<"!PW_MARKER!"
        if "!OLD_PW_VER!"=="!PW_VER!" set "NEED_PW_INSTALL=0"
    )
)

if "!NEED_PW_INSTALL!"=="0" (
    echo       内核已就绪（playwright !PW_VER!），跳过
) else (
    echo       正在下载 Chromium 内核（首次约需 1-3 分钟，国内网络较慢）...
    set "PW_RC=0"
    "!VENV_PY!" -m playwright install chromium
    set "PW_RC=!errorlevel!"

    REM playwright 只有环境变量这一个换源开关，没有对应的命令行参数
    if not "!PW_RC!"=="0" (
        echo       默认源失败，改用国内镜像重试...
        set "PLAYWRIGHT_DOWNLOAD_HOST=https://registry.npmmirror.com/-/binary/playwright"
        "!VENV_PY!" -m playwright install chromium
        set "PW_RC=!errorlevel!"
        set "PLAYWRIGHT_DOWNLOAD_HOST="
    )

    if not "!PW_RC!"=="0" (
        echo.
        echo       [警告] Chromium 内核安装失败，图片渲染功能将不可用。
        echo              其余功能不受影响，服务会继续启动。
        echo              可稍后手动执行：
        echo                  "!VENV_PY!" -m playwright install chromium
        echo              Linux 若缺少系统依赖库，再执行：
        echo                  "!VENV_PY!" -m playwright install-deps chromium
        echo.
    ) else (
        REM 安装成功才写标记，失败不会被误记为已完成，下次启动会重试
        >"!PW_MARKER!" echo !PW_VER!
        echo       内核安装完成
    )
)

REM ---------------------------------------------------------------------------
REM  5. 检查配置文件
REM ---------------------------------------------------------------------------
echo.
echo [5/6] 检查配置文件...

if not exist ".env" (
    if exist ".env.example" (
        copy /y ".env.example" ".env" >nul
        echo       已从 .env.example 生成 .env
        echo       [提示] 如需 AI 功能，请编辑 .env 填入 LLM_API_KEY
        echo              留空也能运行，自然语言记账会自动降级为规则引擎
    ) else (
        echo       [警告] 未找到 .env.example，跳过
    )
) else (
    echo       .env 已存在
)

REM ---------------------------------------------------------------------------
REM  6. 启动服务
REM ---------------------------------------------------------------------------
echo.
echo [6/6] 启动服务...
echo.

REM 端口占用检查：本项目只允许单实例，端口被占时给出明确提示而不是让启动中途失败
netstat -ano | findstr ":!PORT! " | findstr "LISTENING" >nul 2>&1
if !errorlevel! equ 0 (
    echo   [警告] 端口 !PORT! 已被占用，服务可能无法启动。
    echo.
    echo   可能是上一次的流萤还在运行。请先关闭它，或执行：
    echo       netstat -ano ^| findstr :!PORT!
    echo       taskkill /PID ^<上面查到的PID^> /F
    echo.
    pause
    exit /b 1
)

echo ============================================================
echo   服务启动中，请稍候...
echo.
echo   启动成功后可用地址：
echo.
echo     ^>^> 前端控制台   http://127.0.0.1:!PORT!/
echo     ^>^> API 文档     http://127.0.0.1:!PORT!/docs
echo.
echo   浏览器打开前端控制台即可开始使用。
echo   按 Ctrl+C 可停止服务。
echo ============================================================
echo.

REM 直接用 venv 解释器启动，不依赖 activate（避免不同 Shell 的批处理差异）
"!VENV_PY!" -m firefly.app

REM 服务正常退出或异常退出都会走到这里
echo.
echo ============================================================
echo   服务已停止。
echo ============================================================
echo.
pause
endlocal
