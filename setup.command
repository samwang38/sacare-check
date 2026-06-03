#!/bin/bash
# SAcare 對帳工具 一鍵安裝 (雙擊執行)
cd "$(dirname "$0")" || exit 1
echo "============================================"
echo "  SAcare 月對帳工具 — 安裝設定"
echo "============================================"

# 1) 安裝 Python 套件
echo ""
echo "[1/3] 安裝 Python 套件…"
pip3 install -r requirements.txt || { echo "✗ 套件安裝失敗，請確認已安裝 Python 3"; exit 1; }

# 2) 偵測 Java 8 與 EPBrowser
echo ""
echo "[2/3] 偵測環境…"
JAVA_HOME8=$(/usr/libexec/java_home -v 1.8 2>/dev/null)
if [ -z "$JAVA_HOME8" ]; then
  echo "⚠ 找不到 Java 8，請先安裝 JDK 8 (查詢 EPB 需要)。安裝後重跑本程式。"
else
  echo "✓ Java 8: $JAVA_HOME8"
fi
EPB_SHELL="/Library/EPBrowser/EPB/Shell"
if [ -f "$EPB_SHELL/shell.jar" ]; then
  echo "✓ EPBrowser: $EPB_SHELL"
else
  echo "⚠ 找不到 $EPB_SHELL/shell.jar，請確認本機已安裝 EPBrowser。"
fi

# 3) 設定 .env
echo ""
echo "[3/3] 連線設定…"
if [ -f .env ] && grep -q "EPB_WSDL_URL=." .env; then
  echo "✓ 已有 .env 設定，略過。(要重設請先刪除 .env)"
else
  echo "請輸入 EPB WebService 位址 (向主管索取，格式如 http://192.168.x.x:8080/EPB_AP_EPB/EPB_AP?wsdl)："
  read -r WSDL
  ROOT="$(pwd)"
  cat > .env <<EOF
# SAcare 對帳工具 本機設定 (此檔不會上傳 GitHub)
EPB_WSDL_URL=$WSDL
EPB_LIVE_REPORT_ROOT=$ROOT
EPB_JAVA=$JAVA_HOME8/bin/java
EPB_JAVAC=$JAVA_HOME8/bin/javac
EPB_JAVA_CP=$ROOT:$EPB_SHELL/lib/*:$EPB_SHELL/shell.jar
EOF
  echo "✓ 已寫入 .env"
fi

echo ""
echo "============================================"
echo "  安裝完成！"
echo "  使用方式：雙擊「啟動SAcare對帳.command」"
echo "  (需連在門市網路才能查 EPB)"
echo "============================================"
