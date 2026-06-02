#!/usr/bin/env python3
"""SAcare 月對帳 — 上傳保險 Excel, 線上顯示異常 (Flask)。

啟動:  python3 server.py   然後開 http://127.0.0.1:5066
"""
from __future__ import annotations
import tempfile, traceback
from pathlib import Path
import pandas as pd
from flask import Flask, request, jsonify, send_file, Response

import sacare_reconcile as SR

ROOT = Path(__file__).resolve().parent
app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 32 * 1024 * 1024

# 每個分頁: (結果key, 標題, 欄位, 是否為異常)
TABS = [
    ("cat_tbl",   "品類數量比較", ["品類", "保險筆數", "EPB淨額", "差異"], False),
    ("daily_tbl", "逐日總量比較", ["日期", "保險筆數", "EPB淨額", "差異"], False),
    ("typo",      "差異1·序號登記差異", ["要保序號", "保單號", "保險起日", "產品種類", "保險序號", "EPB序號去S", "EPB單據", "相似度"], True),
    ("only_ins",  "差異2·保險有/EPB無", ["要保序號", "保單號", "保險起日", "產品種類", "序號", "EPB單據(參考)"], True),
    ("only_epb",  "差異3·EPB有/保險無", ["DOC_ID", "品類", "SAcare品項", "EPB序號去S", "要保序號"], True),
    ("excess",    "差異4·EPB多打/無序號", ["DOC_ID", "品類", "SAcare品項", "要保序號"], True),
    ("deposit",   "差異5·訂金", ["DOC_ID", "品類", "SAcare品項", "EPB序號去S", "保險已登記", "要保序號"], True),
    ("check_tbl", "檢測新機·數量核對", ["品類", "保險筆數", "EPB筆數", "相符"], False),
    ("epb_check_detail", "檢測新機·EPB明細", ["DOC_ID", "品類", "SAcare品項", "EPB序號去S"], False),
    ("matched",   "✅相符明細", ["保單號", "保險起日", "產品種類", "序號"], False),
]


def df_records(df: pd.DataFrame, cols):
    if df is None or len(df) == 0:
        return []
    use = [c for c in cols if c in df.columns]
    return df[use].astype(object).where(pd.notna(df[use]), "").to_dict("records")


@app.route("/")
def index():
    return send_file(ROOT / "index.html")


@app.route("/reconcile", methods=["POST"])
def reconcile():
    try:
        f = request.files.get("file")
        if not f:
            return jsonify(error="未收到檔案"), 400
        shop = request.form.get("shop", "004").strip() or "004"
        with tempfile.NamedTemporaryFile(suffix=".xls", delete=False) as tmp:
            f.save(tmp.name)
            path = tmp.name
        month = request.form.get("month", "").strip() or SR.detect_month(path)

        ins, R = SR.run(path, month, shop)
        dep_reg = int((R["deposit"]["保險已登記"] == "是").sum()) if len(R["deposit"]) else 0
        summary = {
            "月份": month, "門市": shop,
            "保險已繳": len(ins), "新機": int((~ins["檢測新機"]).sum()), "檢測新機": int(ins["檢測新機"].sum()),
            "EPB_SAcare單位": R["n_epb_units"],
            "相符": len(R["matched"]),
            "anomaly": {
                "序號登記差異": len(R["typo"]),
                "保險有EPB無": len(R["only_ins"]),
                "EPB有保險無": len(R["only_epb"]),
                "EPB多打無序號": len(R["excess"]),
                "訂金(已登記)": f"{len(R['deposit'])} ({dep_reg})",
            },
        }
        tabs = [{"key": k, "title": t, "cols": [c for c in cols], "anomaly": an,
                 "rows": df_records(R.get(k), cols)} for k, t, cols, an in TABS]
        return jsonify(summary=summary, tabs=tabs)
    except Exception as e:
        traceback.print_exc()
        return jsonify(error=f"{type(e).__name__}: {e}"), 500


if __name__ == "__main__":
    print("→ http://127.0.0.1:5066")
    app.run(host="127.0.0.1", port=5066, debug=False)
