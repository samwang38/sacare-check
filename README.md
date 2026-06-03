# sacare-check

SAcare 保險登記 vs EPB 結帳 **每月對帳工具**（士林門市 004）。

公司銷售 SAcare 保險：先在 EPB 系統結帳，再到保險公司網站登記。月初核對上個月兩邊是否有差異，本工具自動比對並分類異常。

## 比對邏輯

- **保險端**：保險公司匯出的 `.xls`（實為 HTML）。只取「繳費=已繳(Y)」且「保險起日」（=生效/登記日）落在指定月份。
- **比對鍵**：裝置序號。EPB 的 `SRN_ID` 開頭多一個 `S`，去掉後再比對。
- **多序號**：保險一筆可能有 主機序號 + Apple Pencil + 鍵盤序號，只取主機序號，配件忽略。
- **EPB 端**：抓該月含 `S.A CARE` 品項的單據，取同單主機序號（限 iPhone/iPad/Mac/Watch/AirPods，排除 Apple Pencil 等配件）。
- **跨單據淨額**：SAcare 依裝置序號跨單據淨額（賣 +1 / 退 −1）；淨額 ≤0 視為無有效 SAcare（例：賣→退→重售未結）。
- **檢測新機**：序號在 EPB 走「檢測新機活動代碼」，屬正常，另頁按數量核對。
- **訂金 (TRANS_TYPE=G)**：當月不算實際銷售；若保險已登記則列為異常。

## 異常分類

| 分類 | 說明 |
|---|---|
| 差異1 | 序號登記差異（相似度 ≥0.7，疑同一台序號打錯）|
| 差異2 | 保險有登記、EPB 無有效 SAcare（含已退淨額 0）|
| 差異3 | EPB 有結 SAcare、保險未登記 |
| 差異4 | EPB 一台裝置多打 SAcare／無主機序號 |
| 差異5 | 訂金（保險已登記但僅訂金未實際銷售）|

另含：**品類數量比較**、**逐日總量比較** 兩張健檢表快速定位差異。

## 使用方式

### HTML 介面（推薦）
```bash
python3 server.py          # 或雙擊「啟動SAcare對帳.command」
```
開 http://127.0.0.1:5066 → 上傳保險 Excel（月份可留空自動偵測）→ 分頁顯示異常。

### CLI（產出 Excel 報告）
```bash
python3 sacare_reconcile.py --insurance 保險匯出.xls --month 2026-06
```

## 安裝（同事端，一鍵）

1. 下載專案：
   ```bash
   git clone https://github.com/samwang38/sacare-check.git
   ```
2. 進資料夾，**雙擊 `setup.command`**（或終端機 `./setup.command`）。它會自動：
   - 安裝 Python 套件
   - 偵測 Java 8 與 EPBrowser
   - **自動從 EPBrowser 設定檔（`/Library/EPBrowser/EPB/Setting.xml` 的 `<EPB_URL>`）讀取本機 EPB 位址**，寫入本機 `.env`（讀不到才會請你手動輸入）
3. 完成後，**雙擊 `啟動SAcare對帳.command`**（開 http://127.0.0.1:5066 ）即可使用。

> 通常**全程免輸入**（位址自動偵測）；設定只需做一次，`.env` 存在本機、不會上傳 GitHub。

### 前置需求（同事電腦需具備）
- **Java 8**（JDK）
- **EPBrowser**（提供 `com.epb.ap.*` 的 `shell.jar`）
- 連得到 EPB 的**門市內網**

### 設定值（環境變數 / `.env`，範本見 `.env.example`）
`EPB_WSDL_URL`(WebService位址，向主管索取)、`EPB_LIVE_REPORT_ROOT`(本專案資料夾)、`EPB_JAVA`、`EPB_JAVAC`、`EPB_JAVA_CP`。
`epb_query.py` 與 `EPBReportQuery.java` 已隨 repo 附帶，會被自動找到。

> **網路需求**：必須連到門市網路（或 VPN）才能查詢，否則會 connect timed out。

## 使用方式（手動）

```bash
pip3 install -r requirements.txt        # 安裝套件(setup.command 會自動做)
python3 server.py                       # HTML 介面, 或雙擊 啟動SAcare對帳.command
python3 sacare_reconcile.py --insurance 保險匯出.xls --month 2026-06 --shop 004   # CLI 產 Excel
```

## 注意

- 產出的對帳 Excel、保險匯出檔、`.env`(含內網位址) 皆由 `.gitignore` 排除，請勿提交。
- 本工具需門市 EPB 環境（Java 8 + EPBrowser lib + 內網）才能實際查詢；僅 `pip install` 無法連線。
